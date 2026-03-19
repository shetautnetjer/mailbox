#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mailbox_core import MailboxPaths, migrate_tracker_record, read_json, write_json

DEFAULT_MAILBOX = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill legacy mailbox trackers toward v3 shape")
    parser.add_argument("--mailbox-dir", type=Path, default=DEFAULT_MAILBOX)
    parser.add_argument("--write", action="store_true", help="Persist migrated trackers in place")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable summary")
    args = parser.parse_args()

    paths = MailboxPaths(args.mailbox_dir)
    changed = []
    unchanged = 0

    for tracker_path in sorted(paths.tracking_dir.glob("*.json")):
        tracker = read_json(tracker_path)
        migrated, did_change = migrate_tracker_record(tracker, writer="tracker_migration")
        if did_change:
            changed.append(
                {
                    "path": str(tracker_path),
                    "delivery_id": migrated.get("delivery_id"),
                    "envelope_id": migrated.get("envelope_id"),
                    "recipient": migrated.get("recipient"),
                    "migration_inference": migrated.get("migration_inference", {}),
                }
            )
            if args.write:
                write_json(tracker_path, migrated)
        else:
            unchanged += 1

    summary = {
        "mailbox_dir": str(args.mailbox_dir),
        "write": args.write,
        "total": len(changed) + unchanged,
        "changed": len(changed),
        "unchanged": unchanged,
        "changed_trackers": changed,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    action = "Would migrate" if not args.write else "Migrated"
    print(f"{action} {summary['changed']} tracker(s); {summary['unchanged']} already aligned.")
    for item in changed[:20]:
        inferred = ", ".join(sorted(item.get("migration_inference", {}).keys())) or "none"
        print(f"  - {item['recipient']} :: {item['envelope_id']} :: inferred={inferred}")
    if len(changed) > 20:
        print(f"  ... and {len(changed) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
