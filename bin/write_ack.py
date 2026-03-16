#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from mailbox_core import MailboxPaths, ensure_mailbox_layout, now_iso, read_json, write_json
from uuid7_util import gen_ack_id


DEFAULT_MAILBOX = Path(__file__).resolve().parent.parent


def find_envelope_for_agent(paths: MailboxPaths, agent: str, envelope_id: str) -> Path | None:
    for base in [paths.agent_inbox(agent), paths.agent_received(agent), paths.agent_responses(agent)]:
        candidate = base / f"{envelope_id}.json"
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Write an ACK record for a mailbox envelope")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--envelope-id", required=True)
    parser.add_argument("--status", choices=["accepted", "rejected"], default="accepted")
    parser.add_argument("--reason", default="")
    parser.add_argument("--mailbox-dir", type=Path, default=DEFAULT_MAILBOX)
    args = parser.parse_args()

    ensure_mailbox_layout(args.mailbox_dir)
    paths = MailboxPaths(args.mailbox_dir)
    ack = {
        "ack_id": gen_ack_id(),
        "envelope_id": args.envelope_id,
        "agent": args.agent,
        "status": args.status,
        "reason": args.reason,
        "received_ts": now_iso(),
    }
    write_json(paths.agent_acks(args.agent) / f"{args.envelope_id}.json", ack)

    inbox_path = paths.agent_inbox(args.agent) / f"{args.envelope_id}.json"
    if inbox_path.exists():
        envelope = read_json(inbox_path)
        write_json(paths.agent_received(args.agent) / inbox_path.name, envelope)
        inbox_path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
