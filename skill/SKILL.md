# Mailbox Skill - Phase 2

**Status: IMPLEMENTED** ✅  
File-based agent messaging with durability guarantees.

## What's New in Phase 2

### Core Improvements
- ✅ **Atomic writes** — Temp file + validate + rename
- ✅ **Envelope validation** — Required fields checked before write
- ✅ **JSONL ledger** — Event log for all mailbox operations
- ✅ **Archive command** — Move old messages to archive/
- ✅ **Validate command** — Check all envelope JSON for correctness
- ✅ **Python core** — Structured logic, shell orchestrates

### Architecture
```
mailbox.sh (CLI) 
    ↓ calls
mailbox_core.py (structured operations)
    ↓ does
Atomic writes, validation, ledger, archive
```

## Quick Start

```bash
cd ~/.openclaw/workspace-aya/.openclaw/skills/mailbox-skill

# Initialize mailbox for agents
./mailbox.sh init aya arbiter kimi

# Send a message
./mailbox.sh send arbiter "Review needed" "Please check the parser"

# Check inbox
./mailbox.sh check

# List recent messages
./mailbox.sh list inbox 10

# Validate all envelopes
./mailbox.sh validate

# Archive old messages (older than 14 days)
./mailbox.sh archive 14
```

## Commands

| Command | Description |
|---------|-------------|
| `init [agents...]` | Create mailbox structure |
| `send <to> <subject> [body]` | Send message |
| `reply <id> <body>` | Reply to message |
| `check` | Check inbox |
| `list [folder] [n]` | List messages |
| `complete <sum> [det] [deliv]` | Announce work done |
| `archive [days]` | Archive old messages |
| `validate` | Check envelope JSON |
| `watch [interval]` | Watch for new mail |

## Mailbox Structure

```
mailbox/agents/<agent>/
  inbox/       # New messages
  outbox/      # Sent messages
  received/    # Processed messages
  archive/     # Old messages
  .tmp/        # Atomic write temp files

mailbox/ledger/
  events.jsonl # Event log (append-only)
```

## Envelope Format

```json
{
  "envelope_id": "env_019cf8b1caf5_095d06a74dff",
  "from": "aya",
  "to": "arbiter",
  "subject": "Review needed",
  "body": "Please check the code",
  "timestamp": "2026-03-16T22:08:47Z",
  "type": "message",
  "status": "pending"
}
```

## Ledger Events

```jsonl
{"timestamp": "2026-03-16T22:08:47Z", "event_type": "message_sent", "envelope_id": "...", "to_agent": "arbiter"}
{"timestamp": "2026-03-16T22:09:15Z", "event_type": "message_received", "envelope_id": "..."}
{"timestamp": "2026-03-16T22:10:00Z", "event_type": "message_archived", "envelope_id": "..."}
```

## Phase 2 vs Phase 1

| Feature | Phase 1 | Phase 2 |
|---------|---------|---------|
| File persistence | ✅ | ✅ |
| Atomic writes | ❌ | ✅ |
| Validation | ❌ | ✅ |
| Ledger | ❌ | ✅ |
| Archive | ❌ | ✅ |
| Validation command | ❌ | ✅ |
| Python core | ❌ | ✅ |

## Trust Boundaries

| Layer | Status |
|-------|--------|
| File-based messaging | ✅ Working |
| Atomic writes | ✅ Working |
| Schema validation | ✅ Working |
| Event ledger | ✅ Working |
| Live notifications | 📋 Phase 3 |

## Configuration

Environment variables:
- `MAILBOX_ROOT` — Path to mailbox
- `MY_AGENT_ID` — Your agent name

## Files

- `mailbox.sh` — Bash CLI (orchestration)
- `mailbox_core.py` — Python operations (structure, validation, atomic writes)
- `SKILL.md` — This documentation
- `README.md` — Quick reference
- `dads-plan.md` — Architecture review
- `phase-2.md` — Phase 2 specification

## Roadmap

### Phase 3 — Live Notifications (Planned)
- Native `sessions_send` integration
- Session discovery
- Real-time wake-up

## License

MIT / Apache-2.0
