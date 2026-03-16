#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from pathlib import Path

from mailbox_core import (
    MailboxPaths,
    append_jsonl,
    best_effort_openclaw_ping,
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


def process_ack_file(paths: MailboxPaths, ack_path: Path, openclaw_bin: str | None) -> None:
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
        tracker["ack_status"] = "acked" if ack.get("status") == "accepted" else "rejected"
        tracker["ack_ts"] = ack.get("received_ts", now_iso())
        write_json(tracker_path, tracker)

        if tracker.get("notify_on_ack", True):
            sender = tracker.get("sender")
            if sender:
                if ack.get("status") == "accepted":
                    msg = f"✅ Ack received: {recipient} accepted {envelope_id}"
                else:
                    msg = f"❌ Ack received: {recipient} rejected {envelope_id} ({ack.get('reason', '')})"
                best_effort_openclaw_ping(sender, msg, openclaw_bin)

    ack_path.unlink(missing_ok=True)


def run_loop(paths: MailboxPaths, once: bool, openclaw_bin: str | None) -> None:
    while True:
        for agent_dir in (paths.root / "agents").glob("*"):
            ack_dir = agent_dir / "acks"
            if not ack_dir.exists():
                continue
            for ack_path in sorted(ack_dir.glob("*.json")):
                try:
                    process_ack_file(paths, ack_path, openclaw_bin)
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
    args = parser.parse_args()

    ensure_mailbox_layout(args.mailbox_dir)
    run_loop(MailboxPaths(args.mailbox_dir), once=args.once, openclaw_bin=args.openclaw_bin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
