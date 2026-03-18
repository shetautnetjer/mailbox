#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from mailbox_core import MailboxPaths, SESSION_MAP, parse_iso, read_json
from smart_mailman import SessionAwareMailman

DEFAULT_MAILBOX = Path(__file__).resolve().parent.parent


def summarize_trackers(paths: MailboxPaths) -> dict:
    counts = {
        "total": 0,
        "pending_ack": 0,
        "acked": 0,
        "rejected": 0,
        "timed_out": 0,
        "escalated": 0,
    }
    overdue = []
    recent = []
    now = datetime.now(timezone.utc)

    for tracker_path in sorted(paths.tracking_dir.glob('*.json')):
        tracker = read_json(tracker_path)
        counts["total"] += 1
        ack_state = tracker.get("ack_state", tracker.get("ack_status", "unknown"))
        if ack_state == "pending":
            counts["pending_ack"] += 1
        elif ack_state == "acked":
            counts["acked"] += 1
        elif ack_state == "rejected":
            counts["rejected"] += 1
        elif ack_state == "timed_out":
            counts["timed_out"] += 1
        if tracker.get("escalated"):
            counts["escalated"] += 1

        due = tracker.get("ack_due_ts")
        if ack_state == "pending" and due:
            try:
                due_dt = parse_iso(due)
                if due_dt < now:
                    overdue.append({
                        "delivery_id": tracker.get("delivery_id"),
                        "envelope_id": tracker.get("envelope_id"),
                        "recipient": tracker.get("recipient"),
                        "work_item_id": tracker.get("work_item_id"),
                        "ack_due_ts": due,
                        "reping_count": tracker.get("reping_count"),
                        "live_notify_state": tracker.get("live_notify_state"),
                    })
            except Exception:
                pass

        recent.append({
            "delivery_id": tracker.get("delivery_id"),
            "envelope_id": tracker.get("envelope_id"),
            "recipient": tracker.get("recipient"),
            "delivery_state": tracker.get("delivery_state"),
            "ack_state": ack_state,
            "live_notify_state": tracker.get("live_notify_state"),
            "delivered_ts": tracker.get("delivered_ts"),
        })

    recent = sorted(recent, key=lambda x: x.get("delivered_ts") or "", reverse=True)[:10]
    overdue = sorted(overdue, key=lambda x: x.get("ack_due_ts") or "")
    return {"counts": counts, "overdue": overdue, "recent_deliveries": recent}


def main() -> int:
    parser = argparse.ArgumentParser(description='Mailbox operator status view')
    parser.add_argument('--mailbox-dir', type=Path, default=DEFAULT_MAILBOX)
    parser.add_argument('--active-minutes', type=int, default=120)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    paths = MailboxPaths(args.mailbox_dir)
    intake_pending = sorted([p.name for p in paths.intake_pending.glob('*.json') if p.name != '.gitkeep'])
    tracker_summary = summarize_trackers(paths)
    presence = SessionAwareMailman(args.mailbox_dir, active_minutes=args.active_minutes).get_agent_presence()
    recently_active = [
        {
            "agent": agent,
            "last_seen": info.get("last_seen"),
            "age_ms": info.get("age_ms"),
            "kind": info.get("kind"),
        }
        for agent, info in sorted(presence.items()) if info.get("recently_active")
    ]

    report = {
        "mailbox_dir": str(args.mailbox_dir),
        "intake_pending_count": len(intake_pending),
        "intake_pending": intake_pending[:20],
        "tracker_counts": tracker_summary["counts"],
        "overdue_acks": tracker_summary["overdue"],
        "recent_deliveries": tracker_summary["recent_deliveries"],
        "recently_active_agents": recently_active,
        "known_agents": sorted(SESSION_MAP.keys()),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print('\n📬 Mailbox Status\n')
    print(f"Pending intake: {report['intake_pending_count']}")
    if report['intake_pending']:
        for name in report['intake_pending']:
            print(f"  - {name}")
    counts = report['tracker_counts']
    print('\nTrackers:')
    for key in ['total', 'pending_ack', 'acked', 'rejected', 'timed_out', 'escalated']:
        print(f"  - {key}: {counts[key]}")
    print('\nOverdue acks:')
    if report['overdue_acks']:
        for item in report['overdue_acks'][:10]:
            print(f"  - {item['recipient']} :: {item['envelope_id']} :: due {item['ack_due_ts']} :: repings {item['reping_count']}")
    else:
        print('  - none')
    print('\nRecent deliveries:')
    for item in report['recent_deliveries'][:10]:
        print(f"  - {item['recipient']} :: {item['envelope_id']} :: delivery={item['delivery_state']} ack={item['ack_state']} notify={item['live_notify_state']}")
    print('\nRecently active agents:')
    if report['recently_active_agents']:
        for item in report['recently_active_agents']:
            print(f"  - {item['agent']} :: age_ms={item['age_ms']} kind={item['kind']}")
    else:
        print('  - none')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
