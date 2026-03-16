#!/usr/bin/env python3
"""
haiku_mailman.py — Haiku's intake processor. Validates, delivers, and tracks ack SLA.
Contract: workspace-jabari/contracts/hook_lifecycle_spec_v1.md
          workspace-jabari/contracts/task_envelope_schema_v1.md
          workspace-jabari/contracts/ack_policy_schema_v1.md
          workspace-jabari/contracts/delivery_tracker_schema_v1.md

CRITICAL: Ack timeout NEVER touches work_item state. Delivery SLA only.

Usage:
  python3 haiku_mailman.py [--once] [--mailbox-dir /path/to/mailbox]
"""
from __future__ import annotations

import json
import random
import shutil
import string
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_MAILBOX = HERE.parent
OC_BIN = "/home/netjer/.npm-global/bin/openclaw"

SESSION_MAP = {
    "aya":     "agent:main:main",
    "jabari":  "agent:jabari:main",
    "tariq":   "agent:tariq:main",
    "kimi":    "agent:kimi:main",
    "haiku":   "agent:haiku:main",
    "heru":    "agent:heru:main",
    "arbiter": "agent:arbiter:main",
}

BASE_REQUIRED = [
    "envelope_id", "type", "from", "to", "ts",
    "work_item_id", "trust_plane", "intent_class", "risk",
    "subject", "body", "priority",
]

LOOP_INTERVAL_SECS = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def log(msg: str) -> None:
    ts = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} [mailman] {msg}", file=sys.stderr)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def send_session_ping(recipient: str, message: str) -> bool:
    """
    Send real-time nudge via OpenClaw sessions_send.
    Best-effort only — failure never blocks the mail loop.
    Belt-and-suspenders: print stubs stay alongside this.
    """
    session_key = SESSION_MAP.get(recipient)
    if not session_key:
        print(f"[PING SKIP] No session map for '{recipient}'", file=sys.stderr)
        return False
    try:
        result = subprocess.run(
            [OC_BIN, "agent", "--agent", recipient,
             "--message", message, "--timeout", "15"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            print(f"[PING OK → {recipient}]", file=sys.stderr)
            return True
        else:
            print(f"[PING FAIL → {recipient}] rc={result.returncode}", file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        print(f"[PING TIMEOUT → {recipient}]", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[PING ERROR → {recipient}] {e}", file=sys.stderr)
        return False


def gen_delivery_id() -> str:
    """Generate delivery_id using UUIDv7 with del_ prefix."""
    try:
        from uuid7_util import gen_uuidv7
        return gen_uuidv7("del")
    except ImportError:
        # Fallback to legacy format if uuid7_util not importable
        now = now_utc()
        rand_hex = "".join(random.choices(string.hexdigits[:16], k=4)).lower()
        return f"del-{now.strftime('%Y-%m-%d')}-{now.strftime('%H%M%S')}-{rand_hex}"


# ── Validation ─────────────────────────────────────────────────────────────────

def validate_base_fields(env: dict) -> list[str]:
    errors = []
    for field in BASE_REQUIRED:
        if field not in env or env[field] is None or env[field] == "":
            errors.append(f"missing or empty field: '{field}'")
    return errors


def is_trust_violation(env: dict) -> bool:
    return (env.get("trust_plane") == "plane-a" and
            env.get("intent_class") == "execute")


# ── Delivery tracker (Patches 2+3) ─────────────────────────────────────────────

def create_tracker(mailbox: Path, env: dict, recipient: str,
                   delivered_ts: str) -> str | None:
    """
    Create a delivery tracker record if ack_required.
    Returns delivery_id or None if tracking skipped.
    """
    ack_policy = env.get("ack_policy", {})
    if not ack_policy.get("ack_required", True):
        return None

    timeout_s = ack_policy.get("ack_timeout_s", 300)
    ack_due = (parse_iso(delivered_ts) +
               timedelta(seconds=timeout_s)).isoformat(timespec="seconds")

    delivery_id = gen_delivery_id()
    tracker = {
        "delivery_id": delivery_id,
        "envelope_id": env.get("envelope_id"),
        "thread_id": env.get("thread_id", env.get("work_item_id", "")),
        "work_item_id": env.get("work_item_id", ""),
        "sender": env.get("from", ""),
        "recipient": recipient,
        "delivered_ts": delivered_ts,
        "ack_due_ts": ack_due,
        "ack_status": "pending",
        "ack_ts": None,
        "last_ping_ts": None,
        "reping_count": 0,
        "max_repings": ack_policy.get("max_repings", 2),
        "reping_interval_s": ack_policy.get("reping_interval_s", 300),
        "notify_on_delivery": ack_policy.get("notify_on_delivery", True),
        "notify_on_ack": ack_policy.get("notify_on_ack", True),
        "notify_on_timeout": ack_policy.get("notify_on_timeout", True),
        "escalation_target": ack_policy.get("escalation_target", "aya"),
        "escalated": False,
        "receipt_path": None,
    }

    tracking_dir = mailbox / "haiku" / "tracking"
    tracking_dir.mkdir(parents=True, exist_ok=True)
    tracker_path = tracking_dir / f"{delivery_id}.json"
    tracker_path.write_text(json.dumps(tracker, indent=2))

    # Log DELIVERY_CREATED
    append_jsonl(mailbox / "ledger" / "deliveries.jsonl", {
        "event_type": "DELIVERY_CREATED",
        "ts": now_iso(),
        "delivery_id": delivery_id,
        "envelope_id": env.get("envelope_id"),
        "event_id": env.get("event_id"),  # UUIDv7 event spine
        "sender": env.get("from"),
        "recipient": recipient,
        "work_item_id": env.get("work_item_id"),
        "ack_due_ts": ack_due,
    })

    log(f"TRACKER created {delivery_id} for {env.get('envelope_id')} (ack_due={ack_due})")
    return delivery_id


def send_delivery_ping(mailbox: Path, env: dict, recipient: str,
                       delivery_id: str, tracker_path: Path) -> None:
    """
    Send initial delivery notification (Patch 3).
    v1: print stub + ledger entry. sessions_send wired later.
    """
    envelope_id = env.get("envelope_id", "")
    sender = env.get("from", "")
    subject = env.get("subject", "")
    work_item_id = env.get("work_item_id", "")

    msg = (f"📬 New mail: [{subject}] from [{sender}]. "
           f"Check your inbox. ({envelope_id} / {work_item_id})")
    print(f"[PING → {recipient}] {msg}", file=sys.stderr)

    ping_ts = now_iso()
    ping_ok = send_session_ping(recipient, msg)

    append_jsonl(mailbox / "ledger" / "deliveries.jsonl", {
        "event_type": "SESSION_PING_SENT",
        "ts": ping_ts,
        "delivery_id": delivery_id,
        "envelope_id": envelope_id,
        "recipient": recipient,
        "message": msg,
        "ping_ok": ping_ok,
    })

    # Update tracker last_ping_ts
    try:
        tracker = json.loads(tracker_path.read_text())
        tracker["last_ping_ts"] = ping_ts
        tracker_path.write_text(json.dumps(tracker, indent=2))
    except Exception as e:
        log(f"WARNING: could not update tracker last_ping_ts: {e}")


# ── Ack timeout scanner (Patch 4) ──────────────────────────────────────────────

def scan_pending_acks(mailbox: Path) -> dict:
    """
    Scan active trackers for overdue acks. Fire re-pings or escalations.
    NEVER touches work_item state.
    """
    tracking_dir = mailbox / "haiku" / "tracking"
    if not tracking_dir.exists():
        return {}

    timeouts_log = mailbox / "ledger" / "timeouts" / "timeouts.jsonl"
    repings_log = mailbox / "ledger" / "repings" / "repings.jsonl"
    escalations_log = mailbox / "ledger" / "escalations" / "escalations.jsonl"

    now = now_utc()
    stats: dict[str, int] = {"checked": 0, "ok": 0, "timeout": 0, "reping": 0, "escalated": 0}

    for tracker_path in sorted(tracking_dir.glob("*.json")):
        if tracker_path.name.startswith("."):
            continue
        try:
            tracker = json.loads(tracker_path.read_text())
        except Exception as e:
            log(f"ERROR reading tracker {tracker_path.name}: {e}")
            continue

        stats["checked"] += 1

        # Only act on pending trackers
        if tracker.get("ack_status") != "pending":
            continue

        ack_due_str = tracker.get("ack_due_ts", "")
        try:
            ack_due = parse_iso(ack_due_str)
        except Exception:
            log(f"WARNING: invalid ack_due_ts in {tracker_path.name}")
            continue

        if now < ack_due:
            stats["ok"] += 1
            continue

        # ── TIMEOUT ──────────────────────────────────────────────────────────────
        stats["timeout"] += 1
        delivery_id = tracker["delivery_id"]
        envelope_id = tracker["envelope_id"]
        recipient = tracker["recipient"]
        reping_count = tracker["reping_count"]
        max_repings = tracker["max_repings"]

        append_jsonl(timeouts_log, {
            "event_type": "ACK_TIMEOUT",
            "ts": now_iso(),
            "delivery_id": delivery_id,
            "envelope_id": envelope_id,
            "work_item_id": tracker.get("work_item_id"),
            "recipient": recipient,
            "reping_count": reping_count,
        })

        if reping_count < max_repings:
            # ── RE-PING ──────────────────────────────────────────────────────────
            reping_count += 1
            reping_interval = tracker.get("reping_interval_s", 300)
            new_ack_due = (now + timedelta(seconds=reping_interval)).isoformat(timespec="seconds")

            tracker["reping_count"] = reping_count
            tracker["last_ping_ts"] = now_iso()
            tracker["ack_due_ts"] = new_ack_due
            tracker_path.write_text(json.dumps(tracker, indent=2))

            append_jsonl(repings_log, {
                "event_type": "REPING_SENT",
                "ts": now_iso(),
                "delivery_id": delivery_id,
                "envelope_id": envelope_id,
                "work_item_id": tracker.get("work_item_id"),
                "recipient": recipient,
                "reping_count": reping_count,
                "max_repings": max_repings,
                "next_ack_due": new_ack_due,
            })

            reping_msg = (
                f"⏰ Reminder: unread mail from [{tracker.get('sender', '?')}] "
                f"awaits your ack. ({envelope_id} / {tracker.get('work_item_id', '?')}) "
                f"[{reping_count}/{max_repings}]"
            )
            print(
                f"[REPING → {recipient}] Ack overdue for {envelope_id} "
                f"({reping_count}/{max_repings})",
                file=sys.stderr,
            )
            if tracker.get("notify_on_timeout", True):
                send_session_ping(recipient, reping_msg)
            log(f"REPING {reping_count}/{max_repings} → {recipient} for {envelope_id}")
            stats["reping"] += 1

        else:
            # ── ESCALATE ─────────────────────────────────────────────────────────
            escalation_target = tracker.get("escalation_target", "aya")
            tracker["ack_status"] = "escalated"
            tracker["escalated"] = True
            tracker_path.write_text(json.dumps(tracker, indent=2))

            append_jsonl(escalations_log, {
                "event_type": "DELIVERY_ESCALATED",
                "ts": now_iso(),
                "delivery_id": delivery_id,
                "envelope_id": envelope_id,
                "work_item_id": tracker.get("work_item_id"),
                "recipient": recipient,
                "escalation_target": escalation_target,
                "reping_count": reping_count,
            })

            escalation_msg = (
                f"🚨 Ack SLA failed: [{recipient}] has not acknowledged "
                f"{envelope_id} after {reping_count} re-pings. "
                f"Work item: {tracker.get('work_item_id', '?')}"
            )
            print(
                f"[ESCALATE → {escalation_target}] Ack SLA failed for "
                f"{envelope_id} (recipient={recipient})",
                file=sys.stderr,
            )
            send_session_ping(escalation_target, escalation_msg)
            log(f"ESCALATED {envelope_id} → {escalation_target} after {reping_count} repings")
            stats["escalated"] += 1

    return stats


# ── Envelope processing ─────────────────────────────────────────────────────────

def process_envelope(env_path: Path, mailbox: Path,
                     ledger_deliveries: Path, ledger_violations: Path,
                     violations_dir: Path) -> str:
    """Process one envelope. Returns outcome string."""
    # Parse
    try:
        env = json.loads(env_path.read_text())
    except Exception as e:
        log(f"PARSE ERROR {env_path.name}: {e}")
        shutil.move(str(env_path), str(violations_dir / env_path.name))
        append_jsonl(ledger_violations, {
            "event": "quarantined", "envelope_id": env_path.stem,
            "reason": f"json parse error: {e}",
            "violation_type": "PARSE_ERROR", "ts": now_iso(),
        })
        return "rejected_parse_error"

    envelope_id = env.get("envelope_id", env_path.stem)

    # Validate base fields
    errors = validate_base_fields(env)
    if errors:
        log(f"INVALID {envelope_id}: {errors}")
        shutil.move(str(env_path), str(violations_dir / env_path.name))
        append_jsonl(ledger_violations, {
            "event": "quarantined", "envelope_id": envelope_id,
            "reason": f"validation failed: {errors}",
            "violation_type": "INVALID_ENVELOPE", "ts": now_iso(),
        })
        return "rejected_invalid"

    # Trust check
    if is_trust_violation(env):
        log(f"TRUST VIOLATION {envelope_id}: plane-a + execute → quarantine")
        shutil.move(str(env_path), str(violations_dir / env_path.name))
        append_jsonl(ledger_violations, {
            "event": "quarantined", "envelope_id": envelope_id,
            "from": env.get("from"), "to": env.get("to"),
            "reason": "trust_plane=plane-a with intent_class=execute is forbidden",
            "violation_type": "TRUST_VIOLATION", "ts": now_iso(),
            "work_item_id": env.get("work_item_id"),
        })
        return "rejected_trust"

    # Multi-recipient fanout
    if "to_all" in env and env["to_all"]:
        recipients = env["to_all"]
        delivered_count = 0
        for i, recipient in enumerate(recipients, start=1):
            agent_inbox = mailbox / "agents" / recipient / "inbox"
            if not agent_inbox.is_dir():
                log(f"UNKNOWN RECIPIENT {recipient} (fanout of {envelope_id})")
                append_jsonl(ledger_violations, {
                    "event": "quarantined", "envelope_id": f"{envelope_id}-r{i}",
                    "reason": f"unknown recipient: {recipient}",
                    "violation_type": "ROUTING_ERROR", "ts": now_iso(),
                })
                continue

            copy_id = f"{envelope_id}-r{i}"
            delivered_ts = now_iso()
            copy_env = {**env, "envelope_id": copy_id, "to": recipient,
                        "delivered_ts": delivered_ts}
            copy_env.pop("to_all", None)
            dest = agent_inbox / f"{copy_id}.json"
            dest.write_text(json.dumps(copy_env, indent=2))
            append_jsonl(ledger_deliveries, {
                "event": "delivered", "envelope_id": copy_id,
                "original_id": envelope_id, "from": env.get("from"),
                "to": recipient, "delivered_ts": delivered_ts,
                "work_item_id": env.get("work_item_id"),
            })

            # Tracker + ping for each fanout copy
            delivery_id = create_tracker(mailbox, copy_env, recipient, delivered_ts)
            if delivery_id:
                tracker_path = mailbox / "haiku" / "tracking" / f"{delivery_id}.json"
                if copy_env.get("ack_policy", {}).get("notify_on_delivery", True):
                    send_delivery_ping(mailbox, copy_env, recipient, delivery_id, tracker_path)

            log(f"DELIVERED (fanout) {copy_id} → {recipient}")
            delivered_count += 1

        archive_dir = mailbox / "intake" / "processed"
        archive_dir.mkdir(exist_ok=True)
        shutil.move(str(env_path), str(archive_dir / env_path.name))
        return f"fanout_{delivered_count}"

    # Single recipient
    recipient = env.get("to", "")
    agent_inbox = mailbox / "agents" / recipient / "inbox"

    if not agent_inbox.is_dir():
        log(f"UNKNOWN RECIPIENT {recipient}: {envelope_id}")
        shutil.move(str(env_path), str(violations_dir / env_path.name))
        append_jsonl(ledger_violations, {
            "event": "quarantined", "envelope_id": envelope_id,
            "from": env.get("from"), "to": recipient,
            "reason": f"unknown recipient: '{recipient}'",
            "violation_type": "ROUTING_ERROR", "ts": now_iso(),
            "work_item_id": env.get("work_item_id"),
        })
        return "rejected_recipient"

    # Deliver
    delivered_ts = now_iso()
    env["delivered_ts"] = delivered_ts
    dest = agent_inbox / env_path.name
    dest.write_text(json.dumps(env, indent=2))

    append_jsonl(ledger_deliveries, {
        "event": "delivered", "envelope_id": envelope_id,
        "from": env.get("from"), "to": recipient,
        "delivered_ts": delivered_ts,
        "work_item_id": env.get("work_item_id"),
    })
    log(f"DELIVERED {envelope_id} → {recipient}")

    # Patch 2: create tracker
    delivery_id = create_tracker(mailbox, env, recipient, delivered_ts)

    # Patch 3: initial delivery ping
    if delivery_id and env.get("ack_policy", {}).get("notify_on_delivery", True):
        tracker_path = mailbox / "haiku" / "tracking" / f"{delivery_id}.json"
        send_delivery_ping(mailbox, env, recipient, delivery_id, tracker_path)

    env_path.unlink()
    return "delivered"


def process_agent_outboxes(mailbox: Path, intake_pending: Path) -> int:
    moved = 0
    for outbox in (mailbox / "agents").glob("*/outbox/*.json"):
        dest = intake_pending / outbox.name
        shutil.move(str(outbox), str(dest))
        log(f"OUTBOX→INTAKE {outbox.parent.parent.name}: {outbox.name}")
        moved += 1
    return moved


def run_pass(mailbox: Path) -> dict:
    intake_pending = mailbox / "intake" / "pending"
    ledger_deliveries = mailbox / "ledger" / "deliveries.jsonl"
    ledger_violations = mailbox / "ledger" / "violations.jsonl"
    violations_dir = mailbox / "agents" / "haiku" / "violations"
    violations_dir.mkdir(parents=True, exist_ok=True)

    outbox_moved = process_agent_outboxes(mailbox, intake_pending)

    results: dict[str, int] = {}
    pending = sorted(intake_pending.glob("*.json"))

    for env_path in pending:
        if env_path.name.startswith("."):
            continue
        outcome = process_envelope(
            env_path, mailbox, ledger_deliveries,
            ledger_violations, violations_dir
        )
        results[outcome] = results.get(outcome, 0) + 1

    # Patch 4: scan for overdue acks on every pass
    ack_stats = scan_pending_acks(mailbox)

    summary = {
        "pass_ts": now_iso(),
        "pending_found": len(pending),
        "outbox_moved": outbox_moved,
        "results": results,
        "ack_scan": ack_stats,
    }
    if pending or outbox_moved or ack_stats.get("timeout", 0):
        log(f"pass complete: {summary}")
    return summary


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Haiku mailman — intake processor")
    parser.add_argument("--once", action="store_true", help="Run one pass and exit")
    parser.add_argument("--mailbox-dir", type=Path, default=DEFAULT_MAILBOX)
    args = parser.parse_args()

    if args.once:
        run_pass(args.mailbox_dir)
        return 0

    log("starting mailman loop")
    while True:
        try:
            run_pass(args.mailbox_dir)
        except Exception as e:
            log(f"ERROR in pass: {e}")
        time.sleep(LOOP_INTERVAL_SECS)


if __name__ == "__main__":
    sys.exit(main())
