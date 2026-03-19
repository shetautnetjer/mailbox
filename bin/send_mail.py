#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mailbox_core import (
    MailboxPaths,
    VALID_INTENT_CLASSES,
    VALID_PRIORITIES,
    VALID_RESPONSE_TYPES,
    VALID_RISKS,
    VALID_STATUSES,
    VALID_TASK_TYPES,
    VALID_TRUST_PLANES,
    VALID_TYPES,
    ensure_mailbox_layout,
    validate_envelope,
    write_json,
    now_iso,
)
from uuid7_util import gen_envelope_id, gen_event_id


DEFAULT_MAILBOX = Path(__file__).resolve().parent.parent
LEGACY_TYPE_ALIASES = {"work_complete": "response"}


def build_envelope(args: argparse.Namespace) -> dict:
    resolved_type = LEGACY_TYPE_ALIASES.get(args.type, args.type)
    env: dict = {
        "envelope_id": gen_envelope_id(),
        "event_id": gen_event_id(),
        "type": resolved_type,
        "from": args.sender,
        "to": args.to,
        "ts": now_iso(),
        "work_item_id": args.work_item,
        "thread_id": args.thread_id or args.work_item,
        "trust_plane": args.trust_plane,
        "intent_class": args.intent,
        "risk": args.risk,
        "subject": args.subject,
        "body": args.body,
        "priority": args.priority,
    }

    if args.to_all:
        env.pop("to", None)
        env["to_all"] = [x.strip() for x in args.to_all.split(",") if x.strip()]

    if resolved_type == "task":
        env["task_type"] = args.task_type
        if args.constraints:
            env["constraints"] = [x.strip() for x in args.constraints.split(";") if x.strip()]
        if args.acceptance_criteria:
            env["acceptance_criteria"] = [
                x.strip() for x in args.acceptance_criteria.split(";") if x.strip()
            ]
    else:
        env["parent_id"] = args.parent_id or (
            args.thread_id or args.work_item if args.type == "work_complete" else None
        )
        env["response_type"] = args.response_type or ("result" if args.type == "work_complete" else None)
        env["status"] = args.status or ("completed" if args.type == "work_complete" else None)
        env["blocker_flag"] = bool(args.blocker_flag)
        if args.blocker_reason:
            env["blocker_reason"] = args.blocker_reason
        if args.next_action:
            env["next_action"] = args.next_action

    if args.caused_by:
        env["caused_by"] = args.caused_by
    if args.trace_id:
        env["trace_id"] = args.trace_id

    ack_required = str(args.ack_required).lower() not in {"false", "0", "no"}
    if ack_required:
        env["ack_policy"] = {
            "ack_required": True,
            "ack_timeout_s": args.ack_timeout,
            "max_repings": args.max_repings,
            "reping_interval_s": args.reping_interval,
            "escalation_target": args.escalation_target,
            "notify_on_delivery": True,
            "notify_on_ack": True,
            "notify_on_timeout": True,
        }
    else:
        env["ack_policy"] = {"ack_required": False}

    return env


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and drop a mailbox envelope")
    parser.add_argument("--from", dest="sender", required=True, help="Sender agent name")
    parser.add_argument("--to", required=False, help="Recipient agent name")
    parser.add_argument("--to-all", required=False, help="Comma-separated list of recipients")
    parser.add_argument("--type", required=True, choices=sorted(VALID_TYPES | set(LEGACY_TYPE_ALIASES)))
    parser.add_argument("--work-item", required=True, help="work_item_id")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--priority", required=True, choices=sorted(VALID_PRIORITIES))
    parser.add_argument("--trust-plane", required=True, choices=sorted(VALID_TRUST_PLANES))
    parser.add_argument("--intent", required=True, choices=sorted(VALID_INTENT_CLASSES))
    parser.add_argument("--risk", default="low", choices=sorted(VALID_RISKS))
    parser.add_argument("--thread-id")

    parser.add_argument("--task-type", choices=sorted(VALID_TASK_TYPES))
    parser.add_argument("--constraints")
    parser.add_argument("--acceptance-criteria")

    parser.add_argument("--caused-by")
    parser.add_argument("--trace-id")
    parser.add_argument("--parent-id")
    parser.add_argument("--response-type", choices=sorted(VALID_RESPONSE_TYPES))
    parser.add_argument("--status", choices=sorted(VALID_STATUSES))
    parser.add_argument("--blocker-flag", action="store_true")
    parser.add_argument("--blocker-reason")
    parser.add_argument("--next-action")

    parser.add_argument("--ack-required", default="true")
    parser.add_argument("--ack-timeout", type=int, default=300)
    parser.add_argument("--max-repings", type=int, default=2)
    parser.add_argument("--reping-interval", type=int, default=300)
    parser.add_argument("--escalation-target", default="aya")

    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", choices=["id", "json"], default="id")
    parser.add_argument("--mailbox-dir", type=Path, default=DEFAULT_MAILBOX)
    return parser


def main() -> int:
    args = make_parser().parse_args()
    ensure_mailbox_layout(args.mailbox_dir)

    if not args.to and not args.to_all:
        print("ERROR: must provide --to or --to-all", file=sys.stderr)
        return 1

    env = build_envelope(args)
    errors = validate_envelope(env)
    if errors:
        print("ERROR: envelope validation failed:", file=sys.stderr)
        for err in errors:
            print(f" - {err}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps(env, indent=2))
        return 0

    paths = MailboxPaths(args.mailbox_dir)
    out_path = paths.intake_pending / f"{env['envelope_id']}.json"
    write_json(out_path, env)

    if args.output == "json":
        print(json.dumps(env, indent=2))
    else:
        print(env["envelope_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
