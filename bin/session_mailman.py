#!/usr/bin/env python3
"""
Deprecated compatibility wrapper for mailbox session awareness.

This script does not perform direct session-send.
It only exposes:
- known session key mappings
- honest messaging about what is and is not verified

Use `smart_mailman.py` for current discovery/notifier behavior.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

MAILBOX_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MAILBOX_DIR / "bin"))

from mailbox_core import SESSION_MAP, ensure_mailbox_layout


def list_sessions() -> list[dict]:
    return [{"agent": agent, "session_key": session_key} for agent, session_key in SESSION_MAP.items()]


def explain(agent: str, message: str, timeout: int = 30) -> int:
    session_key = SESSION_MAP.get(agent)
    if not session_key:
        print(f"ERROR: No session mapping for agent: {agent}")
        return 1
    print(f"Known session key for {agent}: {session_key}")
    print("This wrapper does NOT inject into that session.")
    print("Verified shell behavior today:")
    print("- session discovery via `openclaw sessions --all-agents --json`")
    print("- agent-turn nudge via `openclaw agent --agent <id> --message ...`")
    print("Not verified here:")
    print("- direct session injection into an existing session")
    print(f"Message preview: {message[:100]}")
    print(f"Timeout hint: {timeout}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Deprecated mailbox session wrapper")
    parser.add_argument("--list-sessions", action="store_true", help="List known session mappings")
    parser.add_argument("--send-to", help="Explain what would be needed to reach agent")
    parser.add_argument("--message", default="Test message from mailbox system", help="Message preview")
    parser.add_argument("--mailbox-dir", type=Path, default=MAILBOX_DIR, help="Mailbox directory")
    args = parser.parse_args()

    ensure_mailbox_layout(args.mailbox_dir)
    if args.list_sessions:
        print("Known agent sessions:")
        for sess in list_sessions():
            print(f"  {sess['agent']:10} -> {sess['session_key']}")
        return 0
    if args.send_to:
        return explain(args.send_to, args.message)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
