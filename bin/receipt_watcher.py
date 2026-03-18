#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from pathlib import Path

from mailbox_core import (
    MailboxPaths,
    append_jsonl,
    notifier_attempt,
    normalize_notifier_mode,
    ensure_mailbox_layout,
    log,
    now_iso,
    read_json,
    write_json,
)

DEFAULT_MAILBOX = Path(__file__).resolve().parent.parent
LOOP_INTERVAL_SECS = 5


def find_tracker(paths: MailboxPaths, envelope_id: str, recipient: str) -> tuple[Path | None, dict | None]:
    for tracker_path in paths.tracking_dir.glob("*.json"):
        tracker = read_json(tracker_path)
        if tracker.get("envelope_id") == envelope_id and tracker.get("recipient") == recipient:
            return tracker_path, tracker
    return None, None


def process_ack_file(paths: MailboxPaths, ack_path: Path, openclaw_bin: str | None, notifier_mode: str) -> None:
    ack = read_json(ack_path)
    recipient = ack["agent"]
    envelope_id = ack["envelope_id"]
    tracker_path, tracker = find_tracker(paths, envelope_id, recipient)

    append_jsonl(
        paths.acks_jsonl,
        {
            "event_type": "ACK_CONFIRMED",
            "ts": now_iso(),
            "ack_id": ack.get("ack_id"),
            "envelope_id": envelope_id,
            "receiver": recipient,
            "status": ack.get("status", "accepted"),
            "reason": ack.get("reason", ""),
        },
    )

    if tracker_path and tracker:
        tracker["ack_state"] = "acked" if ack.get("status") == "accepted" else "rejected"
        tracker["ack_status"] = tracker["ack_state"]
        tracker["ack_ts"] = ack.get("received_ts", now_iso())
        write_json(tracker_path, tracker)

        if tracker.get("notify_on_ack", True):
            sender = tracker.get("sender")
            if sender:
                if ack.get("status") == "accepted":
                    msg = f"✅ Ack received: {recipient} accepted {envelope_id}"
                else:
                    msg = f"❌ Ack received: {recipient} rejected {envelope_id} ({ack.get('reason', '')})"
                notify_result = notifier_attempt(mode=notifier_mode, agent=sender, message=msg, openclaw_bin=openclaw_bin)
                tracker["live_notify_state"] = "attempted"
                tracker["live_notify"] = notify_result
                write_json(tracker_path, tracker)

    ack_path.unlink(missing_ok=True)


def run_loop(paths: MailboxPaths, once: bool, openclaw_bin: str | None, notifier_mode: str) -> None:
    while True:
        for agent_dir in (paths.root / "agents").glob("*"):
            ack_dir = agent_dir / "acks"
            if not ack_dir.exists():
                continue
            for ack_path in sorted(ack_dir.glob("*.json")):
                try:
                    process_ack_file(paths, ack_path, openclaw_bin, notifier_mode)
                except Exception as exc:
                    append_jsonl(
                        paths.violations_jsonl,
                        {
                            "event": "ACK_PROCESSING_ERROR",
                            "ts": now_iso(),
                            "reason": str(exc),
                            "violation_type": "ACK_ERROR",
                            "envelope_id": ack_path.stem,
                        },
                    )
        if once:
            return
        time.sleep(LOOP_INTERVAL_SECS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch agent ack directories and resolve trackers")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--mailbox-dir", type=Path, default=DEFAULT_MAILBOX)
    parser.add_argument("--openclaw-bin", default=None)
    parser.add_argument("--notifier-mode", default="agent-turn-nudge", help="none | discover-only | agent-turn-nudge")
    args = parser.parse_args()

    ensure_mailbox_layout(args.mailbox_dir)
    notifier_mode = normalize_notifier_mode(args.notifier_mode)
    run_loop(MailboxPaths(args.mailbox_dir), once=args.once, openclaw_bin=args.openclaw_bin, notifier_mode=notifier_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
