#!/usr/bin/env python3
"""
qmd_new.py — QMD template generator.

Generates new .qmd files with proper YAML frontmatter following QMD_FRONTMATTER_SPEC_V1.md.
Auto-generates UUIDv7 for doc_id when not provided.

Usage:
  python3 qmd_new.py --type policy --title "My Policy" --out /path/to/file.qmd
  python3 qmd_new.py --type schema --title "Widget Schema" --tags widget,schema --agent jabari
  python3 qmd_new.py --type episode_record --title "Bug Fix: Tracker Dupe" --trust-zone canonical

Flags:
  --type          doc_type (required): policy|schema|research_packet|episode_record|
                  promotion_packet|design_decision|bug_fix_record|replay_summary|
                  handoff_note|canon_event
  --title         Human-readable title (becomes summary + filename base)
  --doc-id        Explicit doc_id (default: auto UUIDv7)
  --out           Output path (default: stdout)
  --trust-zone    trust_zone (default: local)
  --status        status (default: draft)
  --tags          Comma-separated tags
  --agent         agent_owner (default: current agent)
  --project       project field
  --related       Comma-separated related_docs
  --json          Output JSON with path + content instead of writing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

VALID_DOC_TYPES = [
    "policy", "schema", "research_packet", "episode_record",
    "promotion_packet", "design_decision", "bug_fix_record",
    "replay_summary", "handoff_note", "canon_event",
]

VALID_TRUST_ZONES = [
    "local", "shared_raw", "shared_library", "shared_staged",
    "canonical", "archived",
]

VALID_STATUSES = [
    "draft", "under_review", "reviewed", "active", "staged",
    "promoted", "superseded", "rejected", "archived",
]

# Body section templates per doc_type
BODY_TEMPLATES: dict[str, str] = {
    "policy": """## Rule

_(Define the rule here.)_

## Scope

_(What agents, workflows, or situations does this policy apply to?)_

## Enforcement

_(How is this enforced? What happens on violation?)_

## Evidence

_(Links to events, gates, or prior decisions that back this policy.)_

## Notes

_(Additional context.)_
""",
    "schema": """## Purpose

_(What is this schema for?)_

## Structure

_(Field definitions, types, required vs optional.)_

## Constraints

_(Validation rules, invariants, forbidden values.)_

## Usage

_(Examples, call sites, integration points.)_

## Change Notes

_(Version history and migration notes.)_
""",
    "research_packet": """## Question

_(What question does this packet answer?)_

## Findings

_(What was found?)_

## Sources

_(URLs, doc_ids, session references.)_

## Cross-Checks

_(How were findings verified or cross-referenced?)_

## Confidence

_(High / Medium / Low — and why.)_

## Notes

_(Additional context, caveats, follow-up questions.)_
""",
    "episode_record": """## Trigger

_(What event or observation triggered this episode?)_

## Problem

_(What was the problem?)_

## Actions Taken

_(What was done to investigate or resolve?)_

## Outcome

_(What was the result?)_

## Verification

_(How was the resolution verified?)_

## Lessons

_(What does the team now know that it didn't before?)_
""",
    "promotion_packet": """## Candidate Objects

_(doc_ids or artifact IDs being proposed for promotion.)_

## Evidence

_(Replay chains, receipts, gate results, or other evidence of value.)_

## Review

_(Who reviewed this and what they found.)_

## Decision

_(Promote / Hold / Reject — and rationale.)_

## Notes

_(Additional context.)_
""",
    "design_decision": """## Context

_(What situation prompted this decision?)_

## Decision

_(What was decided?)_

## Rationale

_(Why this option over alternatives?)_

## Alternatives Considered

_(Other options evaluated.)_

## Consequences

_(Expected outcomes, trade-offs, risks.)_
""",
    "bug_fix_record": """## Bug Description

_(What was the bug?)_

## Root Cause

_(What caused it?)_

## Fix Applied

_(What change was made?)_

## Verification

_(How was the fix verified?)_

## Affected Components

_(What files, scripts, or systems were changed?)_
""",
    "replay_summary": """## Session/Run Reference

_(Session ID, date range, or event chain being summarized.)_

## What Happened

_(Narrative of the event sequence.)_

## Key Events

_(Bullet list of significant envelope IDs, gate results, or decisions.)_

## Outcome

_(What was the final state?)_

## Notes

_(What is useful to recall from this replay?)_
""",
    "handoff_note": """## Context

_(What is being handed off and why?)_

## Current State

_(Where things stand right now.)_

## Next Steps

_(What the recipient should do.)_

## Open Questions

_(Unresolved items that need follow-up.)_

## References

_(doc_ids, envelope IDs, or artifact paths.)_
""",
    "canon_event": """## Summary

_(One-paragraph canonical summary of the event/pattern.)_

## Problem

_(What was the problem?)_

## Investigation

_(How was it investigated?)_

## Resolution

_(How was it resolved?)_

## Verification

_(Evidence that the resolution worked.)_

## Lessons

_(Durable lessons extracted.)_

## Source Events

_(Envelope IDs, work item IDs, receipt IDs that this was derived from.)_
""",
}


def gen_uuidv7() -> str:
    """Generate a UUIDv7 (time-ordered UUID). Falls back to UUID4 with timestamp prefix."""
    try:
        # Python 3.12+ has uuid.uuid7
        if hasattr(uuid, 'uuid7'):
            return str(uuid.uuid7())
        # Manual UUIDv7 construction
        ts_ms = int(time.time() * 1000)
        ts_hex = f"{ts_ms:012x}"
        rand_hex = uuid.uuid4().hex[12:]
        raw = ts_hex + rand_hex
        # Format as UUID with version 7 and variant bits
        u = (
            raw[:8] + "-" +
            raw[8:12] + "-" +
            "7" + raw[13:16] + "-" +
            hex((int(raw[16:18], 16) & 0x3F) | 0x80)[2:].zfill(2) +
            raw[18:20] + "-" +
            raw[20:32]
        )
        return u
    except Exception:
        return str(uuid.uuid4())


def slugify(title: str) -> str:
    """Convert title to a filesystem-friendly slug."""
    import re
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:80]


def build_qmd(doc_type: str, title: str, doc_id: str, trust_zone: str,
              status: str, tags: list[str], agent: str, project: str,
              related: list[str]) -> str:
    """Build QMD content string."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Build YAML frontmatter
    tags_yaml = "\n".join(f"  - {t}" for t in tags) if tags else "  []"
    related_yaml = "\n".join(f"  - {r}" for r in related) if related else "  []"

    fm_lines = [
        "---",
        f"doc_id: {doc_id}",
        f"doc_type: {doc_type}",
        f"trust_zone: {trust_zone}",
        f"status: {status}",
        f"created_at: {now}",
        f"updated_at: {now}",
        f"tags:",
        tags_yaml,
        f"canonical: false",
    ]

    if agent:
        fm_lines.append(f"agent_owner: {agent}")
    if project:
        fm_lines.append(f"project: {project}")
    fm_lines.append(f"summary: {title}")
    if related:
        fm_lines.append(f"related_docs:")
        fm_lines.append(related_yaml)
    else:
        fm_lines.append(f"related_docs: []")

    fm_lines.append(f"source_events: []")
    fm_lines.append(f"promotion_state: local-draft")
    fm_lines.append("---")

    body = BODY_TEMPLATES.get(doc_type, "_(No template available for this doc_type.)_\n")

    return "\n".join(fm_lines) + "\n\n" + body


def main() -> int:
    parser = argparse.ArgumentParser(description="QMD template generator")
    parser.add_argument("--type", required=True, choices=VALID_DOC_TYPES,
                        metavar="DOC_TYPE", help=f"doc_type: {' | '.join(VALID_DOC_TYPES)}")
    parser.add_argument("--title", required=True, help="Human-readable title")
    parser.add_argument("--doc-id", help="Explicit doc_id (default: auto UUIDv7)")
    parser.add_argument("--out", type=Path, help="Output .qmd file path (default: stdout)")
    parser.add_argument("--trust-zone", default="local", choices=VALID_TRUST_ZONES)
    parser.add_argument("--status", default="draft", choices=VALID_STATUSES)
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--agent", default=os.environ.get("AGENT_NAME", ""),
                        help="agent_owner")
    parser.add_argument("--project", default="", help="project field")
    parser.add_argument("--related", default="", help="Comma-separated related_docs")
    parser.add_argument("--json", action="store_true", help="Output JSON with path + content")
    args = parser.parse_args()

    doc_id = args.doc_id or slugify(args.title)
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    related = [r.strip() for r in args.related.split(",") if r.strip()]

    content = build_qmd(
        doc_type=args.type,
        title=args.title,
        doc_id=doc_id,
        trust_zone=args.trust_zone,
        status=args.status,
        tags=tags,
        agent=args.agent,
        project=args.project,
        related=related,
    )

    if args.json:
        out_path = str(args.out) if args.out else f"{doc_id}.qmd"
        print(json.dumps({"path": out_path, "doc_id": doc_id, "content": content}, indent=2))
        return 0

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(content, encoding="utf-8")
        print(f"Created: {args.out} (doc_id={doc_id})")
    else:
        print(content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
