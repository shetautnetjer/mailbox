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

## State separation

Trackers are moving toward explicit separation of:
- `delivery_state`
- `ack_state`
- `live_notify_state`

Older tracker files may still contain legacy fields such as `ack_status`; operator reporting treats those as migration compatibility only while the schema converges.
Notify mode and recent-session discovery are reported as assistive context, not mailbox delivery truth.

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
