# Mailbox System vNext

Mailbox is file-backed communications infrastructure.

## Truth model

Authoritative operational truth:
- intake envelopes
- inbox/outbox copies
- tracker files
- ack files
- JSONL ledgers

Assistive only:
- recent-session discovery
- live notify nudges

Do not blur them:
- discovery is not delivery
- nudges are not delivery
- this repo does not claim verified direct shell session injection

## Notifier modes

Supported notifier modes in runtime code:
- `none`
- `discover-only`
- `agent-turn-nudge`

Meaning:
- `none` — durable mailbox only
- `discover-only` — keep discovery context, no runtime nudge
- `agent-turn-nudge` — use `openclaw agent --agent ... --message ...` as an assistive new-turn nudge
- `session-inject` — reserved for future use only if runtime support is verified

## Key scripts

- `bin/send_mail.py` — author envelopes
- `bin/haiku_mailman.py` — durable intake processing + tracker lifecycle
- `bin/receipt_watcher.py` — ack reconciliation
- `bin/smart_mailman.py` — recent-session discovery and honest assistive reporting
- `bin/mailbox_status.py` — operator status view
- `bin/session_mailman.py` — non-authoritative helper / future adapter placeholder

## Completion semantics

Preferred modern completion shape:
- `type=response`
- `response_type=result`
- `status=completed`
- linked via `parent_id` plus normal `work_item_id` / `thread_id`

This is the primary way to represent completed work.
It keeps work completion inside the shared task/response model instead of preserving `work_complete` as a forever-parallel type.

Legacy compatibility:
- `send_mail.py --type work_complete` is accepted as a compatibility alias
- it is normalized at authoring time into `type=response`, `response_type=result`, `status=completed`
- if legacy callers omit `parent_id`, authoring falls back to `thread_id` and then `work_item_id` so the envelope still lands inside the structured response path
- agents and docs should prefer the structured response shape in new work

Sender follow-up semantics:
- delivery/ack receipts stay receipt/ack semantics only
- when a completed result envelope is durably delivered, the runtime now records a distinct result-arrival event and sends a nudge that explicitly says follow-up is needed
- that follow-up owner is the envelope recipient, typically the requester or current task owner reviewing the result

## State separation

Trackers are moving toward explicit separation of:
- `delivery_state`
- `ack_state`
- `live_notify_state`

Older tracker files may still contain legacy fields such as `ack_status`; operator reporting treats those as migration compatibility only while the schema converges.
Notify mode and recent-session discovery are reported as assistive context, not mailbox delivery truth.
Legacy `live_notify_state=attempted_legacy` history is preserved in tracker records; operator views normalize those raw values into clearer legacy classifications such as `legacy_nudge_attempted` or `legacy_discovery_only` without rewriting tracker provenance.

A bounded legacy backfill tool is available:

```bash
python3 bin/migrate_trackers.py --write
```

It normalizes legacy trackers toward v3, preserves legacy fields under `legacy_fields_preserved`, and records inferred/backfilled values under `migration_inference` plus migration lineage fields.

## Operator view

Human-readable:

```bash
python3 bin/mailbox_status.py
```

Machine-readable:

```bash
python3 bin/mailbox_status.py --json
```

Recent-session discovery view:

```bash
python3 bin/smart_mailman.py --presence --active-minutes 120
```
