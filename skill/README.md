# Mailbox Skill

Cross-session agent messaging using file-based JSON envelopes.

## Quick Start

```bash
cd ~/.openclaw/workspace-aya/.openclaw/skills/mailbox-skill

# Send a message
./mailbox.sh send arbiter "Subject" "Body text"

# Check your inbox
./mailbox.sh check

# List recent messages
./mailbox.sh list
```

## Commands

| Command | Description |
|---------|-------------|
| `./mailbox.sh check` | Check inbox for new messages |
| `./mailbox.sh send <to> <subject> [body]` | Send message to agent |
| `./mailbox.sh reply <id> <message>` | Reply to a message |
| `./mailbox.sh complete <sum> [det] [deliv]` | Announce work done |
| `./mailbox.sh list [n]` | List recent messages |
| `./mailbox.sh watch` | Watch for new messages |

## Current Implementation

**Phase 1: File-Based Mailbox**

- ✅ File persistence (JSON envelopes)
- ✅ Auto-create directories
- ✅ Shell-safe operations
- ✅ No external dependencies

**Phase 3: Live Notifications (Planned / truth-tightened)**

Live notify remains assistive only.
Current verified shell/runtime posture in this repo:
- durable file delivery is authoritative
- recent session discovery is available
- `openclaw agent --agent ... --message ...` can be used as an agent-turn nudge
- direct shell `sessions_send` / session injection is not claimed here unless later verified

## Message Format

Standard JSON envelope with fields:
- `envelope_id` — Unique ID
- `from`, `to` — Agent names
- `subject`, `body` — Content
- `timestamp` — ISO 8601
- `type` — `message` or `work_complete`

## Files

- `mailbox.sh` — Main CLI (bash)
- `SKILL.md` — Full documentation
- `dads-plan.md` — Architecture review

## Dad's Feedback

See `dads-plan.md` for detailed review including:
- Phase 1 cleanup tasks ✅
- Phase 2 persistence improvements
- Phase 3 live notification design
