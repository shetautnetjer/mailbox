#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import time
from datetime import timedelta
from pathlib import Path

from mailbox_core import (
    TRACKER_SCHEMA_VERSION,
    MailboxPaths,
    append_jsonl,
    notifier_attempt,
    normalize_notifier_mode,
    ensure_mailbox_layout,
    envelope_recipients,
    log,
    now_iso,
    parse_iso,
    read_json,
    trust_violation,
    validate_envelope,
    write_json,
)
from uuid7_util import gen_delivery_id, gen_receipt_id, gen_event_id


DEFAULT_MAILBOX = Path(__file__).resolve().parent.parent
LOOP_INTERVAL_SECS = 5


def create_tracker(paths: MailboxPaths, env: dict, recipient: str, delivered_ts: str) -> Path | None:
    ack_policy = env.get("ack_policy", {})
    if not ack_policy.get("ack_required", True):
        return None

    timeout_s = int(ack_policy.get("ack_timeout_s", 300))
    ack_due_ts = (parse_iso(delivered_ts) + timedelta(seconds=timeout_s)).isoformat(timespec="seconds")
    tracker = {
        "schema_version": TRACKER_SCHEMA_VERSION,
        "component": "haiku_mailman",
        "delivery_id": gen_delivery_id(),
        "envelope_id": env["envelope_id"],
        "thread_id": env.get("thread_id", env.get("work_item_id")),
        "work_item_id": env.get("work_item_id"),
        "sender": env.get("from"),
        "recipient": recipient,
        "delivered_ts": delivered_ts,
        "delivery_state": "durably_delivered",
        "ack_state": "pending",
        "ack_due_ts": ack_due_ts,
        "ack_ts": None,
        "live_notify_state": "not_attempted",
        "live_notify": None,
        "last_ping_ts": None,
        "reping_count": 0,
        "max_repings": int(ack_policy.get("max_repings", 2)),
        "reping_interval_s": int(ack_policy.get("reping_interval_s", 300)),
        "notify_on_delivery": bool(ack_policy.get("notify_on_delivery", True)),
        "notify_on_ack": bool(ack_policy.get("notify_on_ack", True)),
        "notify_on_timeout": bool(ack_policy.get("notify_on_timeout", True)),
        "escalation_target": ack_policy.get("escalation_target", "aya"),
        "escalated": False,
        "receipt_id": None,
    }
    tracker_path = paths.tracking_dir / f"{tracker['delivery_id']}.json"
    write_json(tracker_path, tracker)
    return tracker_path


def notify_delivery(paths: MailboxPaths, tracker_path: Path | None, env: dict, recipient: str, openclaw_bin: str | None, notifier_mode: str) -> None:
    message = (
        f"📬 New mail: [{env['subject']}] from [{env['from']}]. "
        f"Check mailbox inbox. ({env['envelope_id']} / {env['work_item_id']})"
    )
    notify_result = notifier_attempt(mode=notifier_mode, agent=recipient, message=message, openclaw_bin=openclaw_bin)
    delivery_id = None
    ts = now_iso()
    if tracker_path and tracker_path.exists():
        tracker = read_json(tracker_path)
        tracker["last_ping_ts"] = ts
        tracker["live_notify_state"] = "nudge_attempted"
        tracker["live_notify"] = notify_result
        write_json(tracker_path, tracker)
        delivery_id = tracker["delivery_id"]

    append_jsonl(
        paths.deliveries_jsonl,
        {
            "event_type": "AGENT_TURN_NUDGE_ATTEMPTED",
            "ts": ts,
            "component": "haiku_mailman",
            "adapter": "openclaw-agent-cli",
            "semantic_layer": "live_notify",
            "delivery_truth": False,
            "delivery_id": delivery_id,
            "envelope_id": env["envelope_id"],
            "recipient": recipient,
            "message": message,
            "notify_mode": notifier_mode,
            "ok": notify_result.get("ok", False),
            "notify_result": notify_result,
        },
    )


def deliver_to_recipient(paths: MailboxPaths, env_path: Path, env: dict, recipient: str, openclaw_bin: str | None, notifier_mode: str) -> None:
    delivered_ts = now_iso()
    recipient_inbox_path = paths.agent_inbox(recipient) / env_path.name
    sender_outbox_path = paths.agent_outbox(env["from"]) / env_path.name

    shutil.copy2(env_path, recipient_inbox_path)
    shutil.copy2(env_path, sender_outbox_path)

    tracker_path = create_tracker(paths, env, recipient, delivered_ts)
    receipt_id = gen_receipt_id()

    append_jsonl(
        paths.deliveries_jsonl,
        {
            "event_type": "DELIVERY_CONFIRMED",
            "ts": delivered_ts,
            "delivery_id": read_json(tracker_path)["delivery_id"] if tracker_path else None,
            "event_id": env.get("event_id", gen_event_id()),
            "envelope_id": env["envelope_id"],
            "sender": env["from"],
            "recipient": recipient,
            "work_item_id": env.get("work_item_id"),
        },
    )

    append_jsonl(
        paths.receipts_jsonl,
        {
            "event_type": "DELIVERY_RECEIPT",
            "ts": delivered_ts,
            "receipt_id": receipt_id,
            "envelope_id": env["envelope_id"],
            "delivery_observed_by": "haiku_mailman",
            "from_agent": env["from"],
            "receiver": recipient,
            "work_item_id": env.get("work_item_id"),
            "kind": "inbox_copy_observed",
        },
    )

    if tracker_path and tracker_path.exists():
        tracker = read_json(tracker_path)
        tracker["receipt_id"] = receipt_id
        write_json(tracker_path, tracker)

    notify_delivery(paths, tracker_path, env, recipient, openclaw_bin, notifier_mode)


def quarantine(paths: MailboxPaths, env_path: Path, env: dict, reason: str) -> None:
    dest = paths.intake_quarantine / env_path.name
    shutil.move(str(env_path), str(dest))
    append_jsonl(
        paths.violations_jsonl,
        {
            "event": "QUARANTINED",
            "ts": now_iso(),
            "envelope_id": env.get("envelope_id"),
            "from": env.get("from"),
            "to": env.get("to"),
            "work_item_id": env.get("work_item_id"),
            "violation_type": "INVALID_ENVELOPE",
            "reason": reason,
        },
    )


def process_envelope(paths: MailboxPaths, env_path: Path, openclaw_bin: str | None, notifier_mode: str) -> None:
    env = read_json(env_path)
    errors = validate_envelope(env)
    if errors:
        quarantine(paths, env_path, env, "; ".join(errors))
        return

    violation = trust_violation(env)
    if violation:
        quarantine(paths, env_path, env, violation)
        return

    recipients = envelope_recipients(env)
    for recipient in recipients:
        deliver_to_recipient(paths, env_path, env, recipient, openclaw_bin, notifier_mode)

    processed = paths.intake_processed / env_path.name
    shutil.move(str(env_path), str(processed))
    log("mailman", f"Delivered {env['envelope_id']} to {', '.join(recipients)}")


def scan_pending_acks(paths: MailboxPaths, openclaw_bin: str | None, notifier_mode: str) -> None:
    for tracker_path in sorted(paths.tracking_dir.glob("*.json")):
        tracker = read_json(tracker_path)
        if tracker.get("ack_state", tracker.get("ack_status")) != "pending":
            continue

        now = parse_iso(now_iso())
        ack_due = parse_iso(tracker["ack_due_ts"])
        if now < ack_due:
            continue

        if tracker["reping_count"] < tracker["max_repings"]:
            tracker["reping_count"] += 1
            tracker["last_ping_ts"] = now_iso()
            write_json(tracker_path, tracker)

            msg = (
                f"⏰ Ack overdue for {tracker['envelope_id']}. "
                f"Please check inbox/acks. Work item: {tracker['work_item_id']}"
            )
            notify_result = notifier_attempt(mode=notifier_mode, agent=tracker["recipient"], message=msg, openclaw_bin=openclaw_bin)
            append_jsonl(
                paths.repings_jsonl,
                {
                    "event_type": "ACK_REPING_SENT",
                    "ts": now_iso(),
                    "delivery_id": tracker["delivery_id"],
                    "envelope_id": tracker["envelope_id"],
                    "recipient": tracker["recipient"],
                    "reping_count": tracker["reping_count"],
                    "notify_mode": notifier_mode,
                    "notify_ok": notify_result.get("ok", False),
                    "notify_result": notify_result,
                },
            )
            tracker["ack_due_ts"] = (
                parse_iso(now_iso()) + timedelta(seconds=tracker["reping_interval_s"])
            ).isoformat(timespec="seconds")
            write_json(tracker_path, tracker)
            continue

        tracker["ack_state"] = "timed_out"
        tracker["ack_status"] = "timed_out"
        tracker["ack_ts"] = now_iso()
        write_json(tracker_path, tracker)
        append_jsonl(
            paths.timeouts_jsonl,
            {
                "event_type": "ACK_TIMEOUT",
                "ts": now_iso(),
                "delivery_id": tracker["delivery_id"],
                "envelope_id": tracker["envelope_id"],
                "recipient": tracker["recipient"],
                "work_item_id": tracker["work_item_id"],
            },
        )

        escalation_target = tracker.get("escalation_target")
        if escalation_target and escalation_target != "none" and not tracker.get("escalated"):
            msg = (
                f"🚨 Mailbox timeout: {tracker['recipient']} never acked {tracker['envelope_id']} "
                f"for {tracker['work_item_id']}"
            )
            notify_result = notifier_attempt(mode=notifier_mode, agent=escalation_target, message=msg, openclaw_bin=openclaw_bin)
            append_jsonl(
                paths.escalations_jsonl,
                {
                    "event_type": "ACK_ESCALATION",
                    "ts": now_iso(),
                    "delivery_id": tracker["delivery_id"],
                    "envelope_id": tracker["envelope_id"],
                    "recipient": tracker["recipient"],
                    "escalation_target": escalation_target,
                    "notify_mode": notifier_mode,
                    "notify_ok": notify_result.get("ok", False),
                    "notify_result": notify_result,
                },
            )
            tracker["escalated"] = True
            write_json(tracker_path, tracker)


def run_loop(paths: MailboxPaths, once: bool, openclaw_bin: str | None, notifier_mode: str) -> None:
    while True:
        for env_path in sorted(paths.intake_pending.glob("*.json")):
            try:
                process_envelope(paths, env_path, openclaw_bin, notifier_mode)
            except Exception as exc:
                append_jsonl(
                    paths.violations_jsonl,
                    {
                        "event": "PROCESSING_ERROR",
                        "ts": now_iso(),
                        "envelope_id": env_path.stem,
                        "reason": str(exc),
                        "violation_type": "ROUTING_ERROR",
                    },
                )
        scan_pending_acks(paths, openclaw_bin, notifier_mode)
        if once:
            return
        time.sleep(LOOP_INTERVAL_SECS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mailbox intake processor")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--mailbox-dir", type=Path, default=DEFAULT_MAILBOX)
    parser.add_argument("--openclaw-bin", default=None, help="Optional OpenClaw CLI path")
    parser.add_argument("--notifier-mode", default="agent-turn-nudge", help="none | discover-only | agent-turn-nudge")
    args = parser.parse_args()

    ensure_mailbox_layout(args.mailbox_dir)
    notifier_mode = normalize_notifier_mode(args.notifier_mode)
    run_loop(MailboxPaths(args.mailbox_dir), once=args.once, openclaw_bin=args.openclaw_bin, notifier_mode=notifier_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
