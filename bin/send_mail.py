#!/usr/bin/env python3
"""
send_mail.py — CLI helper for agents to create and drop envelopes.
Contract: workspace-jabari/contracts/task_envelope_schema_v1.md
          workspace-jabari/contracts/response_envelope_schema_v1.md

Usage:
  # Task envelope
  python3 send_mail.py --from aya --to tariq --type task \
    --work-item wi-2026-03-08-001 --subject "Build mailbox" \
    --body "Create the directory tree per spec" \
    --priority normal --trust-plane plane-a --intent propose \
    --task-type implementation

  # Response envelope
  python3 send_mail.py --from tariq --to aya --type response \
    --work-item wi-2026-03-08-001 --subject "Re: Build mailbox" \
    --body "Done. Tree verified." --priority normal \
    --trust-plane plane-a --intent propose \
    --parent-id env-2026-03-08-090000-abcd --response-type result --status completed

Writes to: plane-a/mailbox/intake/pending/{envelope_id}.json
Prints envelope_id to stdout.
"""
from __future__ import annotations

import argparse
import json
import random
import string
import sys
import time as _time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
MAILBOX = HERE.parent
INTAKE_PENDING = MAILBOX / "intake" / "pending"

# ── Required base fields (ALL envelope types) ───────────────────────────────────
BASE_REQUIRED = [
    "envelope_id", "type", "from", "to", "ts",
    "work_item_id", "trust_plane", "intent_class", "risk",
    "subject", "body", "priority",
]

# ── Type-specific required fields ───────────────────────────────────────────────
TYPE_REQUIRED: dict[str, list[str]] = {
    "task":     ["task_type"],
    "response": ["parent_id", "response_type", "status"],
}

VALID_TYPES = {"task", "response"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
VALID_TRUST_PLANES = {"plane-a", "plane-b"}
VALID_INTENT_CLASSES = {"propose", "investigate", "execute"}
VALID_RISKS = {"low", "medium", "high", "critical"}
VALID_TASK_TYPES = {
    "architecture-review", "implementation", "research",
    "compliance-check", "memory-curation", "escalation",
    "handoff", "status-report", "general",
}
VALID_RESPONSE_TYPES = {"result", "blocker", "status", "clarification", "escalation"}
VALID_STATUSES = {
    "completed", "failed", "rejected", "started",
    "in_progress", "partial_complete", "awaiting_input",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gen_uuidv7(prefix: str = "") -> str:
    """Generate a UUIDv7 (time-ordered UUID) with optional type prefix.
    UUIDv7 doctrine: operational IDs use UUIDv7 with debug prefixes.
    Format: {prefix}_{uuidv7} or just {uuidv7} if no prefix.
    """
    try:
        # Python 3.12+ has uuid.uuid7 — try native first
        if hasattr(uuid, 'uuid7'):
            raw = str(uuid.uuid7())
        else:
            # Manual UUIDv7 construction
            ts_ms = int(_time.time() * 1000)
            ts_hex = f"{ts_ms:012x}"
            rand_hex = uuid.uuid4().hex[12:]
            raw_hex = ts_hex + rand_hex
            # Format as UUID with version 7 and variant bits
            raw = (
                raw_hex[:8] + "-" +
                raw_hex[8:12] + "-" +
                "7" + raw_hex[13:16] + "-" +
                hex((int(raw_hex[16:18], 16) & 0x3F) | 0x80)[2:].zfill(2) +
                raw_hex[18:20] + "-" +
                raw_hex[20:32]
            )
    except Exception:
        raw = str(uuid.uuid4())

    return f"{prefix}_{raw}" if prefix else raw


def gen_envelope_id() -> str:
    """Generate envelope_id using UUIDv7 with env_ prefix.
    Legacy format: env-YYYY-MM-DD-HHMMSS-XXXX
    New format:    env_019cda8f-xxxx-7xxx-xxxx-xxxxxxxxxxxx (UUIDv7)
    """
    return gen_uuidv7("env")


def gen_event_id() -> str:
    """Generate event_id using UUIDv7 with evt_ prefix."""
    return gen_uuidv7("evt")


def validate_envelope(env: dict) -> list[str]:
    """Validate base + type-specific required fields. Returns error list."""
    errors: list[str] = []

    for field in BASE_REQUIRED:
        if field not in env or env[field] is None:
            errors.append(f"missing required field: '{field}'")

    if env.get("type") not in VALID_TYPES:
        errors.append(f"type must be one of {sorted(VALID_TYPES)}, got: '{env.get('type')}'")

    if env.get("priority") not in VALID_PRIORITIES:
        errors.append(f"priority must be one of {sorted(VALID_PRIORITIES)}, got: '{env.get('priority')}'")

    if env.get("trust_plane") not in VALID_TRUST_PLANES:
        errors.append(f"trust_plane must be one of {sorted(VALID_TRUST_PLANES)}")

    if env.get("intent_class") not in VALID_INTENT_CLASSES:
        errors.append(f"intent_class must be one of {sorted(VALID_INTENT_CLASSES)}")

    if env.get("risk") not in VALID_RISKS:
        errors.append(f"risk must be one of {sorted(VALID_RISKS)}")

    env_type = env.get("type", "")
    for extra_field in TYPE_REQUIRED.get(env_type, []):
        if not env.get(extra_field):
            errors.append(f"type '{env_type}' requires field: '{extra_field}'")

    if env_type == "task" and env.get("task_type") not in VALID_TASK_TYPES:
        errors.append(f"task_type must be one of {sorted(VALID_TASK_TYPES)}")

    if env_type == "response":
        if env.get("response_type") not in VALID_RESPONSE_TYPES:
            errors.append(f"response_type must be one of {sorted(VALID_RESPONSE_TYPES)}")
        if env.get("status") not in VALID_STATUSES:
            errors.append(f"status must be one of {sorted(VALID_STATUSES)}")

    return errors


def build_envelope(args: argparse.Namespace) -> dict:
    """Build envelope dict from parsed args."""
    envelope_id = gen_envelope_id()
    event_id = gen_event_id()
    env: dict = {
        "envelope_id": envelope_id,
        "event_id": event_id,
        "type": args.type,
        "from": getattr(args, "from"),
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

    # Type-specific fields
    if args.type == "task":
        env["task_type"] = args.task_type
        if args.constraints:
            env["constraints"] = [c.strip() for c in args.constraints.split(";")]
        if args.acceptance_criteria:
            env["acceptance_criteria"] = [a.strip() for a in args.acceptance_criteria.split(";")]

    elif args.type == "response":
        env["parent_id"] = args.parent_id
        env["response_type"] = args.response_type
        env["status"] = args.status
        env["blocker_flag"] = args.blocker_flag
        if args.blocker_reason:
            env["blocker_reason"] = args.blocker_reason
        if args.next_action:
            env["next_action"] = args.next_action

    # Phase 4b: causal chain fields (optional)
    if getattr(args, "caused_by", None):
        env["caused_by"] = args.caused_by
    if getattr(args, "trace_id", None):
        env["trace_id"] = args.trace_id

    # Multi-recipient
    if args.to_all:
        del env["to"]
        env["to_all"] = [r.strip() for r in args.to_all.split(",")]

    # Ack policy (always injected)
    ack_required = getattr(args, "ack_required", True)
    if ack_required is False or str(ack_required).lower() == "false":
        env["ack_policy"] = {"ack_required": False}
    else:
        env["ack_policy"] = {
            "ack_required": True,
            "ack_timeout_s": getattr(args, "ack_timeout", 300),
            "max_repings": getattr(args, "max_repings", 2),
            "reping_interval_s": getattr(args, "reping_interval", 300),
            "escalation_target": getattr(args, "escalation_target", "aya"),
            "notify_on_delivery": True,
            "notify_on_ack": True,
            "notify_on_timeout": True,
        }

    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and drop a mail envelope")

    # Base required
    parser.add_argument("--from", dest="from_", metavar="FROM", required=True,
                        help="Sender agent name")
    parser.add_argument("--to", required=False, help="Recipient agent name")
    parser.add_argument("--to-all", required=False,
                        help="Comma-separated multi-recipient list")
    parser.add_argument("--type", required=True, choices=sorted(VALID_TYPES))
    parser.add_argument("--work-item", required=True, help="work_item_id")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--priority", required=True,
                        choices=sorted(VALID_PRIORITIES))
    parser.add_argument("--trust-plane", required=True,
                        choices=sorted(VALID_TRUST_PLANES))
    parser.add_argument("--intent", required=True,
                        choices=sorted(VALID_INTENT_CLASSES),
                        help="intent_class: propose|investigate|execute")
    parser.add_argument("--risk", default="low",
                        choices=sorted(VALID_RISKS))
    parser.add_argument("--thread-id", help="Thread id (defaults to work-item)")

    # Task-specific
    parser.add_argument("--task-type", choices=sorted(VALID_TASK_TYPES),
                        help="Required for type=task")
    parser.add_argument("--constraints", help="Semicolon-separated list")
    parser.add_argument("--acceptance-criteria", help="Semicolon-separated list")

    # Response-specific
    # Phase 4b: causal chain fields (optional, backward compatible)
    parser.add_argument("--caused-by", help="Parent event_id this envelope was caused by")
    parser.add_argument("--trace-id", help="Shared trace_id across a causal chain")
    parser.add_argument("--parent-id", help="Required for type=response")
    parser.add_argument("--response-type", choices=sorted(VALID_RESPONSE_TYPES))
    parser.add_argument("--status", choices=sorted(VALID_STATUSES))
    parser.add_argument("--blocker-flag", action="store_true", default=False)
    parser.add_argument("--blocker-reason", help="Enum from blocker_reason_enum_v1")
    parser.add_argument("--next-action", help="What happens next")

    # Ack policy
    parser.add_argument("--ack-required", default=True,
                        help="Require ack tracking (true/false, default: true)")
    parser.add_argument("--ack-timeout", type=int, default=300,
                        help="Seconds before first ack timeout (default: 300)")
    parser.add_argument("--max-repings", type=int, default=2,
                        help="Max re-pings before escalation (default: 2)")
    parser.add_argument("--reping-interval", type=int, default=300,
                        help="Seconds between re-pings (default: 300)")
    parser.add_argument("--escalation-target", default="aya",
                        choices=["aya", "jabari", "arbiter", "human", "none"],
                        help="Who to escalate to on SLA failure (default: aya)")

    # Output
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and print without writing")
    parser.add_argument("--output", choices=["id", "json"], default="id")
    parser.add_argument("--mailbox-dir", type=Path, default=MAILBOX,
                        help="Override mailbox root directory")

    args = parser.parse_args()

    # Normalize --from alias
    setattr(args, "from", args.from_)

    # Must have --to or --to-all
    if not args.to and not args.to_all:
        print("ERROR: must provide --to or --to-all", file=sys.stderr)
        return 1

    env = build_envelope(args)
    errors = validate_envelope(env)

    if errors:
        print("ERROR: envelope validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps(env, indent=2))
        return 0

    # Write to intake/pending/
    intake_dir = args.mailbox_dir / "intake" / "pending"
    intake_dir.mkdir(parents=True, exist_ok=True)
    out_file = intake_dir / f"{env['envelope_id']}.json"
    out_file.write_text(json.dumps(env, indent=2))

    if args.output == "json":
        print(json.dumps(env, indent=2))
    else:
        print(env["envelope_id"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
