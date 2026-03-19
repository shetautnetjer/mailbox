#!/usr/bin/env python3
"""
Smart Mailbox Router - session discovery + honest file-first delivery.

Verified on this host:
- `openclaw sessions --all-agents --json` lists stored sessions
- session freshness can be inferred from `updatedAt` / `ageMs`
- `openclaw agent --agent <id> --message ...` can trigger an agent turn nudge

Not verified / not currently implemented from this shell script:
- direct session injection into an existing known agent session

So this module treats live session integration as assistive only.
Durable file delivery remains the operational truth.
Discovery is not delivery. Nudges are not delivery.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add mailbox to path for imports
MAILBOX_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MAILBOX_DIR / "bin"))

from mailbox_core import (
    TRACKER_SCHEMA_VERSION,
    MailboxPaths,
    SESSION_MAP,
    append_jsonl,
    ensure_mailbox_layout,
    envelope_recipients,
    mailbox_event,
    normalize_notifier_mode,
    normalized_tracker_view,
    operator_live_notify_state,
    notifier_attempt,
    now_iso,
    read_json,
    trust_violation,
    validate_envelope,
    write_json,
)
from uuid7_util import gen_delivery_id


class SessionAwareMailman:
    """Mailman that distinguishes durable delivery from discovery and nudges."""

    def __init__(
        self,
        mailbox_dir: Path,
        active_minutes: int = 120,
        notifier_mode: str = "discover-only",
        openclaw_bin: str | None = None,
    ):
        self.paths = MailboxPaths(mailbox_dir)
        self.session_cache: dict[str, dict] = {}
        self.cache_timestamp: datetime | None = None
        self.cache_ttl_seconds = 30
        self.active_minutes = active_minutes
        self.notifier_mode = normalize_notifier_mode(notifier_mode)
        self.openclaw_bin = openclaw_bin or shutil.which("openclaw") or "openclaw"

    def refresh_session_cache(self) -> dict[str, dict]:
        """Query OpenClaw for recently active stored sessions."""
        try:
            result = subprocess.run(
                [
                    "openclaw",
                    "sessions",
                    "--all-agents",
                    "--active",
                    str(self.active_minutes),
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                print(f"Warning: session discovery failed: {result.stderr}", file=sys.stderr)
                return self.session_cache

            data = json.loads(result.stdout)
            sessions = data.get("sessions", [])
            self.session_cache = {}
            for sess in sessions:
                agent_id = sess.get("agentId")
                if agent_id == "main":
                    agent_id = "aya"
                if not agent_id:
                    continue

                prior = self.session_cache.get(agent_id)
                if prior and (prior.get("updatedAt") or 0) >= (sess.get("updatedAt") or 0):
                    continue

                self.session_cache[agent_id] = {
                    "session_key": sess.get("key", ""),
                    "session_id": sess.get("sessionId"),
                    "model": sess.get("model"),
                    "updated_at": sess.get("updatedAt"),
                    "kind": sess.get("kind"),
                    "age_ms": sess.get("ageMs"),
                    "recently_active": True,
                }

            self.cache_timestamp = datetime.now(timezone.utc)
            append_jsonl(
                self.paths.ledger / "session_discovery.jsonl",
                mailbox_event(
                    component="smart_mailman",
                    event_type="SESSION_DISCOVERY",
                    event_family="comms/session-discovery",
                    state_class="live_notify_state",
                    semantic_layer="discovery",
                    active_minutes=self.active_minutes,
                    agents_recently_active=sorted(self.session_cache.keys()),
                    count=len(self.session_cache),
                ),
            )
            return self.session_cache
        except Exception as e:
            print(f"Warning: Session discovery failed: {e}", file=sys.stderr)
            return self.session_cache

    def is_agent_recently_active(self, agent: str) -> tuple[bool, dict[str, Any] | None]:
        if self.cache_timestamp is None or (datetime.now(timezone.utc) - self.cache_timestamp).seconds > self.cache_ttl_seconds:
            self.refresh_session_cache()
        sess = self.session_cache.get(agent)
        return (bool(sess), sess)

    def get_agent_presence(self) -> dict[str, dict]:
        self.refresh_session_cache()
        presence = {}
        for agent in SESSION_MAP.keys():
            active, cached = self.is_agent_recently_active(agent)
            cached = cached or {}
            presence[agent] = {
                "recently_active": active,
                "status": "recently_active" if active else "no_recent_session",
                "session_key": cached.get("session_key"),
                "expected_key": SESSION_MAP[agent],
                "last_seen": cached.get("updated_at"),
                "age_ms": cached.get("age_ms"),
                "kind": cached.get("kind"),
            }
        return presence

    def notify_agent(self, agent: str, message: str, timeout: int = 15) -> dict[str, Any]:
        active, discovered = self.is_agent_recently_active(agent)
        discovery = {
            "component": "smart_mailman",
            "semantic_layer": "discovery",
            "recently_active": active,
            "session_key": discovered.get("session_key") if discovered else None,
            "updated_at": discovered.get("updated_at") if discovered else None,
            "age_ms": discovered.get("age_ms") if discovered else None,
        }
        result = notifier_attempt(
            mode=self.notifier_mode,
            agent=agent,
            message=message,
            openclaw_bin=self.openclaw_bin,
            discovery=discovery,
            timeout_s=timeout,
        )
        result_payload = dict(result)
        result_payload.pop("component", None)
        result_payload.pop("event_family", None)
        result_payload.pop("state_class", None)
        result_payload.pop("trust_plane", None)
        result_payload.pop("provenance_writer", None)
        append_jsonl(
            self.paths.ledger / "live_notify.jsonl",
            mailbox_event(
                component="smart_mailman",
                event_type="LIVE_NOTIFY_ATTEMPT",
                event_family="comms/live-notify",
                state_class="live_notify_state",
                semantic_layer="live_notify",
                notify_mode=result.get("mode"),
                adapter=result.get("adapter"),
                delivery_truth=False,
                sender=None,
                recipient=agent,
                **result_payload,
            ),
        )
        return result

    def deliver_envelope(self, envelope_id: str, use_sessions: bool = True) -> dict:
        env_path = self.paths.intake_pending / f"{envelope_id}.json"
        if not env_path.exists():
            return {"success": False, "error": f"Envelope not found: {envelope_id}"}

        env = read_json(env_path)
        errors = validate_envelope(env)
        if errors:
            return {"success": False, "error": f"Invalid envelope: {errors}"}

        violation = trust_violation(env)
        if violation:
            return {"success": False, "error": f"Trust violation: {violation}"}

        recipients = envelope_recipients(env)
        results = [self._deliver_to_recipient(env, recipient, use_sessions) for recipient in recipients]

        processed_path = self.paths.intake_processed / env_path.name
        shutil.move(str(env_path), str(processed_path))
        return {"success": True, "envelope_id": envelope_id, "results": results}

    def _deliver_to_recipient(self, env: dict, recipient: str, use_sessions: bool) -> dict:
        delivered_ts = now_iso()
        recipient_inbox = self.paths.agent_inbox(recipient)
        sender_outbox = self.paths.agent_outbox(env["from"])
        recipient_inbox.mkdir(parents=True, exist_ok=True)
        sender_outbox.mkdir(parents=True, exist_ok=True)

        env_path = self.paths.intake_pending / f"{env['envelope_id']}.json"
        shutil.copy2(env_path, recipient_inbox / env_path.name)
        shutil.copy2(env_path, sender_outbox / env_path.name)

        message = self._format_notification(env, recipient)
        live_notify = self.notify_agent(recipient, message) if use_sessions else {
            "ok": False,
            "mode": "none",
            "adapter": "disabled",
            "reason": "live_notify_disabled_for_call",
            "delivery_truth": False,
            "discovery": None,
        }
        discovery = live_notify.get("discovery")

        tracker = {
            "schema_version": TRACKER_SCHEMA_VERSION,
            "component": "smart_mailman",
            "event_family": "comms/delivery",
            "state_class": "delivery_state",
            "trust_plane": env.get("trust_plane", "plane-a"),
            "provenance_writer": "smart_mailman",
            "notify_mode": live_notify.get("mode"),
            "adapter": live_notify.get("adapter"),
            "delivery_id": gen_delivery_id(),
            "envelope_id": env["envelope_id"],
            "thread_id": env.get("thread_id", env.get("work_item_id")),
            "work_item_id": env.get("work_item_id"),
            "sender": env.get("from"),
            "recipient": recipient,
            "delivered_ts": delivered_ts,
            "delivery_state": "durably_delivered",
            "ack_state": "not_applicable_yet",
            "ack_ts": None,
            "live_notify_state": self._live_notify_state(live_notify),
            "live_notify": live_notify,
            "delivery_provenance": {
                "component": "smart_mailman",
                "semantic_layer": "durable_delivery",
            },
            "discovery_state": discovery,
        }
        tracker_path = self.paths.tracking_dir / f"{tracker['delivery_id']}.json"
        write_json(tracker_path, tracker)

        append_jsonl(
            self.paths.deliveries_jsonl,
            mailbox_event(
                component="smart_mailman",
                event_type="DELIVERY_CONFIRMED",
                event_family="comms/delivery",
                state_class="delivery_state",
                ts=delivered_ts,
                semantic_layer="durable_delivery",
                delivery_truth=True,
                delivery_id=tracker["delivery_id"],
                envelope_id=env["envelope_id"],
                thread_id=env.get("thread_id", env.get("work_item_id")),
                work_item_id=env.get("work_item_id"),
                sender=env["from"],
                recipient=recipient,
                notify_mode=tracker.get("notify_mode"),
                adapter=tracker.get("adapter"),
                delivery_state=tracker["delivery_state"],
                ack_state=tracker["ack_state"],
                live_notify_state=tracker["live_notify_state"],
            ),
        )
        return {
            "recipient": recipient,
            "delivery_state": tracker["delivery_state"],
            "ack_state": tracker["ack_state"],
            "live_notify_state": tracker["live_notify_state"],
            "live_notify": live_notify,
            "tracker_id": tracker["delivery_id"],
        }

    def _live_notify_state(self, live_notify: dict[str, Any]) -> str:
        mode = live_notify.get("mode")
        if mode == "none":
            return "disabled"
        if mode == "discover-only":
            return "discovered_only"
        if live_notify.get("ok"):
            return "nudge_sent"
        return "nudge_failed"

    def _format_notification(self, env: dict, recipient: str) -> str:
        subject = env.get("subject", "No subject")
        sender = env.get("from", "Unknown")
        work_item = env.get("work_item_id", "No work item")
        envelope_id = env.get("envelope_id", "unknown")
        if env.get("type") == "response" and env.get("response_type") == "result" and env.get("status") == "completed":
            action = "Review completed result, then send next task/response if needed"
            prefix = "✅ Completed result"
        else:
            action = f"Check inbox at agents/{recipient}/inbox/"
            prefix = "📬 Mailbox"
        return (
            f"{prefix}: [{subject}]\n"
            f"From: {sender}\n"
            f"Work item: {work_item}\n"
            f"Envelope: {envelope_id}\n"
            f"Action: {action}"
        )

    def scan_pending(self) -> list[dict]:
        processed = []
        for env_path in sorted(self.paths.intake_pending.glob("*.json")):
            if env_path.name == ".gitkeep":
                continue
            try:
                processed.append(self.deliver_envelope(env_path.stem))
            except Exception as e:
                processed.append({"success": False, "envelope_id": env_path.stem, "error": str(e)})
        return processed

    def status_snapshot(self) -> dict[str, Any]:
        tracking = []
        for tracker_path in sorted(self.paths.tracking_dir.glob("*.json")):
            try:
                tracking.append(normalized_tracker_view(read_json(tracker_path)))
            except Exception:
                continue

        pending_intake = sorted(
            [p.name for p in self.paths.intake_pending.glob("*.json") if p.name != ".gitkeep"]
        )
        presence = self.get_agent_presence()

        def count_where(key: str, value: str) -> int:
            return sum(1 for t in tracking if t.get(key) == value)

        live_notify_state_counts: dict[str, int] = {}
        for tracker in tracking:
            state = operator_live_notify_state(tracker)
            live_notify_state_counts[state] = live_notify_state_counts.get(state, 0) + 1

        return {
            "notifier_mode": self.notifier_mode,
            "pending_intake_count": len(pending_intake),
            "pending_intake": pending_intake,
            "tracking_count": len(tracking),
            "delivery_state_counts": {
                "durably_delivered": count_where("delivery_state", "durably_delivered"),
            },
            "ack_state_counts": {
                "pending": count_where("ack_state", "pending"),
                "acked": count_where("ack_state", "acked"),
                "rejected": count_where("ack_state", "rejected"),
                "timed_out": count_where("ack_state", "timed_out"),
                "not_applicable_yet": count_where("ack_state", "not_applicable_yet"),
            },
            "live_notify_state_counts": live_notify_state_counts,
            "schema_drift_counts": {
                "legacy_compat_only": sum(1 for t in tracking if "legacy_ack_status" in t.get("schema_drift", [])),
                "schema_drifted": sum(1 for t in tracking if t.get("schema_drift")),
            },
            "overdue_acks": [
                {
                    "delivery_id": t.get("delivery_id"),
                    "envelope_id": t.get("envelope_id"),
                    "recipient": t.get("recipient"),
                    "ack_due_ts": t.get("ack_due_ts"),
                    "reping_count": t.get("reping_count"),
                    "live_notify_state": t.get("live_notify_state"),
                    "live_notify_state_normalized": operator_live_notify_state(t),
                    "schema_drift": t.get("schema_drift"),
                }
                for t in tracking
                if t.get("ack_state") == "pending" and t.get("ack_due_ts") and t.get("ack_due_ts") < now_iso()
            ],
            "recently_active_agents": [agent for agent, info in sorted(presence.items()) if info.get("recently_active")],
        }

    def print_presence(self):
        presence = self.get_agent_presence()
        print("\n📡 Agent Presence (recent session discovery only; not delivery truth):\n")
        print(f"{'Agent':<12} {'Status':<18} {'Session Key':<40} {'Last Seen'}")
        print("-" * 92)
        for agent, info in sorted(presence.items()):
            status = "🟢 Recent" if info["recently_active"] else "⚪ None"
            session_key = info["session_key"] or "N/A"
            last_seen = "N/A"
            if info["last_seen"]:
                try:
                    dt = datetime.fromtimestamp(info["last_seen"] / 1000, timezone.utc)
                    last_seen = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                except Exception:
                    last_seen = str(info["last_seen"])[:24]
            print(f"{agent:<12} {status:<18} {session_key:<40} {last_seen}")
        online_count = sum(1 for p in presence.values() if p["recently_active"])
        print(f"\n{online_count}/{len(presence)} agents recently active (window: {self.active_minutes}m)")

    def print_status(self) -> None:
        snap = self.status_snapshot()
        print("\n📬 Mailbox Operator Status\n")
        print(f"Notifier mode: {snap['notifier_mode']}")
        print(f"Pending intake: {snap['pending_intake_count']}")
        print(f"Tracking records: {snap['tracking_count']}")
        print(f"Recently active agents: {', '.join(snap['recently_active_agents']) or 'none'}")
        print("\nDelivery states:")
        for key, value in snap["delivery_state_counts"].items():
            print(f"  {key}: {value}")
        print("\nAck states:")
        for key, value in snap["ack_state_counts"].items():
            print(f"  {key}: {value}")
        print("\nLive notify states:")
        for key, value in snap["live_notify_state_counts"].items():
            print(f"  {key}: {value}")
        print("\nSchema drift:")
        for key, value in snap["schema_drift_counts"].items():
            print(f"  {key}: {value}")
        print("\nOverdue acks:")
        if not snap["overdue_acks"]:
            print("  none")
        else:
            for item in snap["overdue_acks"]:
                print(f"  {item['delivery_id']} {item['recipient']} due {item['ack_due_ts']} repings={item['reping_count']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart Mailbox Router - honest session discovery + notifier modes")
    parser.add_argument("--presence", action="store_true", help="Show recent session discovery")
    parser.add_argument("--status", action="store_true", help="Show operator status view")
    parser.add_argument("--status-json", action="store_true", help="Show operator status as JSON")
    parser.add_argument("--deliver", metavar="ENVELOPE_ID", help="Deliver specific envelope by ID")
    parser.add_argument("--scan", action="store_true", help="Scan and deliver all pending envelopes")
    parser.add_argument("--no-sessions", action="store_true", help="Disable live notify for this call")
    parser.add_argument("--notifier-mode", default="discover-only", choices=["none", "discover-only", "agent-turn-nudge"], help="Assistive notifier mode")
    parser.add_argument("--openclaw-bin", default=None, help="Optional OpenClaw CLI path for agent-turn nudge mode")
    parser.add_argument("--mailbox-dir", type=Path, default=MAILBOX_DIR, help="Mailbox directory")
    parser.add_argument("--active-minutes", type=int, default=120, help="Treat sessions updated within this window as recently active")
    args = parser.parse_args()

    ensure_mailbox_layout(args.mailbox_dir)
    notifier_mode = "none" if args.no_sessions else args.notifier_mode
    mailman = SessionAwareMailman(
        args.mailbox_dir,
        active_minutes=args.active_minutes,
        notifier_mode=notifier_mode,
        openclaw_bin=args.openclaw_bin,
    )

    if args.presence:
        mailman.print_presence()
        return 0
    if args.status_json:
        print(json.dumps(mailman.status_snapshot(), indent=2))
        return 0
    if args.status:
        mailman.print_status()
        return 0
    if args.deliver:
        result = mailman.deliver_envelope(args.deliver, use_sessions=not args.no_sessions)
        print(json.dumps(result, indent=2))
        return 0 if result.get("success") else 1
    if args.scan:
        results = mailman.scan_pending()
        print(f"Processed {len(results)} envelopes:")
        for r in results:
            status = "✅" if r.get("success") else "❌"
            env_id = r.get("envelope_id", "unknown")[:20]
            print(f"  {status} {env_id}")
        return 0

    mailman.print_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
