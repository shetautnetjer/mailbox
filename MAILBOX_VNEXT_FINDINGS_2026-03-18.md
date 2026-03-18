# Mailbox vNext findings - 2026-03-18

Author: Haiku subagent acting for Arbiter/Haiku mailbox work.

## Scope

Reviewed:
- `MAILBOX_PRD.md`
- `brain-harness-notes.md`
- mailbox repo code and docs
- actual local OpenClaw CLI help / behavior on this host

Doctrine applied:
- durable file/ledger truth first
- live session behavior is assistive, not truth
- explicit separation of verified behavior vs assumptions

## Verified runtime facts

### Verified
- `openclaw sessions --all-agents --json` exists and returns stored session records.
- Returned session records include at least: `key`, `updatedAt`, `ageMs`, `sessionId`, `agentId`, `kind`, and often model/token metadata.
- `openclaw sessions` is a listing/maintenance surface, not a send surface.
- `openclaw agent --help` shows a way to run an agent turn via the Gateway using:
  - `--agent <id>`
  - `--message <text>`
  - optional `--session-id <id>`
  - optional reply delivery flags
- `openclaw system presence` exists, but it reports system/node presence, not per-agent mailbox reachability.

### Not verified
- No verified shell CLI equivalent of `sessions_send` was found.
- No verified `openclaw sessions send ...` path was found.
- No verified direct shell command was found for injecting a message into an already-running agent session by session key.

## Honest repo assessment

### Strong parts
- The repo already has a real durable mailbox pattern.
- `send_mail.py` produces structured envelopes into intake.
- `haiku_mailman.py` performs durable delivery and ack/timeout/reping/escalation tracking.
- `receipt_watcher.py` resolves ack files into tracker state and ledger rows.
- `mailbox_core.py` provides a useful shared foundation for paths, validation, and JSONL append behavior.
- The system already reflects the right doctrine more than the docs sometimes admit: files and ledgers are the operational source of truth.

### Weak / misleading parts
- `smart_mailman.py` described itself as if direct session-send were available from shell context.
- `session_mailman.py` is still largely a stub/documentation shim, not real delivery code.
- `best_effort_openclaw_ping()` in `mailbox_core.py` uses `openclaw agent --agent <id> --message ...`, which is a real CLI surface, but it is not the same thing as sending into an existing known session. It starts/routes an agent turn through the Gateway.
- Session presence was being treated too casually:
  - one session overwrote another per agent
  - staleness was not explicitly bounded in the discovery call
  - output language implied "online" rather than "recently active stored session"

## Real integration surface vs assumptions

### Current code assumptions that were false or too strong
1. `openclaw sessions --all-agents --json` means "online now"
   - Not exactly. It means stored sessions, optionally filterable by recent activity.
2. there is a shell `sessions_send`
   - Not verified.
3. having a session key means shell code can directly poke that session
   - Not verified.
4. presence discovery and message delivery are the same layer
   - False. Discovery is currently available; direct session injection from shell is not established.

### What *is* reasonable today
- Use mailbox files + ledger as the durable delivery path.
- Use `openclaw sessions --json` as a recent-session discovery signal.
- Optionally use `openclaw agent --agent ... --message ...` as a separate assistive nudge path, but document it honestly as a new/routed agent turn, not a mailbox-delivery truth event.

## vNext recommendation

## Architectural recommendation

Adopt a three-layer model:

1. **Core durable mailbox layer**
   - envelope creation
   - intake -> inbox/outbox delivery
   - tracker files
   - JSONL ledger events
   - ack/timeout/reping/escalation lifecycle
   - this is authoritative Plane A operational truth

2. **Session discovery layer**
   - recent-session discovery via `openclaw sessions --all-agents --active N --json`
   - presence classification as `recently_active` / `no_recent_session`
   - discovery logged as assistive context only

3. **Live notify adapter layer**
   - explicit adapters with capability labels:
     - `none`
     - `agent_turn_nudge` via `openclaw agent --agent ... --message ...`
     - future `session_inject` only after verified runtime support exists
   - ledger must record adapter type and whether a real send happened vs only an opportunity/discovery event

## Recommended component reshaping
- `send_mail.py`: keep as envelope authoring CLI
- `haiku_mailman.py`: keep as primary durable delivery processor
- `smart_mailman.py`: narrow to session discovery + honest reporting + optional assistive notify adapter selection
- `session_mailman.py`: either deprecate or convert into a tiny adapter helper; do not leave it pretending to be a real send path
- `mailbox_core.py`: central place for capability flags / notifier modes / event helpers

## First safe backlog

### Phase 1 - truth tightening
- [x] make `smart_mailman.py` honest about discovery vs delivery
- [x] use `openclaw sessions --active N --json`
- [x] keep freshest session per agent rather than arbitrary overwrite
- [x] rename UI language from "online" to "recently active" semantics
- [ ] document notifier modes in README / architecture docs

### Phase 2 - explicit notifier adapters
- [ ] add config for `session_integration_mode = none|discover-only|agent-turn-nudge`
- [ ] move `best_effort_openclaw_ping()` behind a named adapter contract
- [ ] log nudge attempts distinctly from durable delivery

### Phase 3 - observability
- [ ] add `mailbox status` style report for:
  - pending intake
  - recent deliveries
  - overdue acks
  - escalations
  - recently active agents
- [ ] add CLI or report script for overdue tracker inspection

### Phase 4 - schema / governance hygiene
- [ ] add explicit event schema/version fields for tracker and ledger events
- [ ] distinguish `delivery_state`, `ack_state`, and `live_notify_state`
- [ ] add provenance fields for which component wrote each event

## Concrete patch started today

Updated `bin/smart_mailman.py` to:
- use `openclaw sessions --all-agents --active <minutes> --json`
- keep the freshest session record per agent
- stop claiming direct shell `session_send`
- log `SESSION_NOTIFY_OPPORTUNITY` instead of fake delivery success
- present presence as recent-session discovery, not guaranteed live reachability
- expose `--active-minutes`

## Recommendation to Arbiter / Haiku

Do not merge durable mailbox truth with speculative session injection.

Treat vNext as:
- **durable delivery system first**
- **recent-session discovery second**
- **live nudges as adapter-specific assists**

That line keeps doctrine clean and leaves room for a future verified OpenClaw-native session injector without rewriting history.
