# Phase 2 — Persistence and Reliability Plan

Aya,

Phase 1 is now honest enough to stand on its feet.
That matters.

Phase 2 is not about adding glamour.
It is about making the mailbox:
- durable
- valid
- recoverable
- testable
- less fragile under real use

This phase should **not** add live notifications yet.
Keep the runtime/session layer out of scope for now.

---

## Phase 2 Goal

Turn the mailbox from a useful shell prototype into a dependable local messaging substrate.

At the end of Phase 2, the mailbox should:
- create and manage its own structure safely
- validate envelopes before writing them
- avoid corrupt or partial writes
- support archive/retention behavior
- have a basic ledger/index of activity
- have tests covering the core flows

---

## What I reviewed in Phase 1

## What improved
- `uuid7()` bug fixed
- docs are more honest
- recipient directories auto-create
- trust boundaries are now explicit

## What still needs work before this feels durable
- writes are still raw `cat > file` / `echo > file`
- no envelope validation exists
- no atomic write behavior
- no mailbox init/bootstrap command
- no archive/retention policy
- `complete` still hardcodes recipient structure in a brittle way
- shell implementation is still vulnerable to quoting/JSON escaping issues

That is what Phase 2 should fix.

---

# Phase 2 Workstreams

## 1. Mailbox bootstrap / init

### Problem
The mailbox structure is implied, but not explicitly initialized or managed.

### Goal
Add a command that creates the mailbox structure predictably.

### Recommended command
```bash
./mailbox.sh init
```

### What it should do
- ensure `MAILBOX_ROOT` exists
- ensure:
  - `agents/<agent>/inbox`
  - `agents/<agent>/outbox`
  - `agents/<agent>/received`
  - `agents/<agent>/archive`
- optionally initialize for a list of known agents

### Suggested behavior
If no agent list is given:
- initialize only `MY_AGENT_ID`

If agents are provided:
```bash
./mailbox.sh init aya arbiter kimi tariq
```

### Why this matters
Bootstrap should be deliberate, not incidental.

---

## 2. Envelope validation

### Problem
Right now envelope JSON is written ad hoc.
That means malformed payloads can enter the mailbox silently.

### Goal
Validate required fields before accepting or writing envelopes.

### Minimum required fields
For all envelopes:
- `envelope_id`
- `from`
- `to`
- `subject`
- `timestamp`
- `type`
- `status`

For normal messages:
- `body`

For replies:
- `in_reply_to`

For work completion:
- `work_item.summary`
- `work_item.status`

### Recommended implementation paths
#### Better
Move envelope creation/validation into a small Python helper.
Shell can call Python for structured JSON generation.

#### Acceptable
Use `jq -n` with explicit required fields and validation checks.

### My recommendation
Use Python for envelope creation.
Let bash orchestrate commands, but let Python produce valid JSON.

That will eliminate quoting bugs and malformed JSON pain.

---

## 3. Atomic writes

### Problem
Current writes go directly to final JSON files.
If a write is interrupted, you can leave partial files behind.

### Goal
Write safely.

### Pattern
1. write to temp file in same directory
2. validate content
3. rename into place atomically

### Example strategy
```text
inbox/.tmp-env_xxx.json
-> validate
-> mv to inbox/env_xxx.json
```

### Why this matters
Mailboxes are state. State deserves atomic behavior.

---

## 4. Archive / retention behavior

### Problem
Messages move from inbox to received, but there is no long-term lifecycle.

### Goal
Define message state and retention.

### Recommended folders
```text
agents/<agent>/
  inbox/
  outbox/
  received/
  archive/
```

### Recommended lifecycle
- `inbox/` = unread/new
- `received/` = processed but recent
- `archive/` = older retained records
- `outbox/` = sent records

### Add command
```bash
./mailbox.sh archive [days]
```

### Initial simple behavior
- move `received/` messages older than N days to `archive/`
- maybe default N = 7 or 14

### Later
- delete very old archives only if policy says so

---

## 5. Ledger / index

### Problem
Mailbox state currently depends on directory scanning only.
That works at small scale, but gets messy.

### Goal
Maintain a lightweight event ledger.

### Phase 2 recommendation
Do **JSONL first**, not SQLite first.

Add:
```text
MAILBOX_ROOT/ledger/events.jsonl
```

### Write one event per action
Examples:
- `message_sent`
- `message_received`
- `message_replied`
- `message_archived`
- `work_completed`

### Example event
```json
{
  "timestamp": "2026-03-16T18:55:00Z",
  "event_type": "message_sent",
  "envelope_id": "env_...",
  "from": "aya",
  "to": "arbiter",
  "subject": "Review needed"
}
```

### Why JSONL first
- easy to inspect
- easy to append
- easy to debug
- easy to migrate to SQLite later

---

## 6. Safer `complete` flow

### Problem
`complete` still bakes in governance assumptions too rigidly.

### Goal
Keep the standard completion format, but make recipient policy clearer.

### Recommendation
Support either:
- a default recipient set from config
- or explicit recipients/CC in command arguments

### Example options
```bash
./mailbox.sh complete "Parser v0.2.0" "Details" "parser.zip"
```
Uses configured defaults.

Or:
```bash
./mailbox.sh complete --to arbiter --cc kimi,tariq "Parser v0.2.0" "Details" "parser.zip"
```

### Why this matters
Governance defaults are fine.
Hardcoded social graphs are brittle.

---

## 7. Configuration cleanup

### Problem
The skill uses env vars, which is fine, but there is no structured config model.

### Goal
Make config simple and explicit.

### Minimum config options
- `MAILBOX_ROOT`
- `MY_AGENT_ID`
- `DEFAULT_COMPLETE_TO`
- `DEFAULT_COMPLETE_CC`
- `RETENTION_DAYS`

### Optional path
Support a simple config file later, but Phase 2 can stay env-first.

---

## 8. Tests

### Problem
There are no actual validation tests here yet.

### Goal
Prove the mailbox works, not just claim it.

### Minimum test cases
#### Send flow
- send creates recipient inbox file
- send copies to sender outbox
- envelope fields are valid

#### Reply flow
- reply creates response in original sender inbox
- reply moves original inbox item to received
- `in_reply_to` is preserved

#### Complete flow
- work completion envelope contains expected shape
- deliverables serialize correctly

#### Archive flow
- old received messages move to archive

#### Init flow
- mailbox structure is created correctly

### Strong recommendation
Use a temp mailbox root in tests.
Do not test against real mailbox directories.

---

# Proposed command surface for Phase 2

## Keep
- `check`
- `send`
- `reply`
- `complete`
- `list`
- `watch`

## Add
- `init`
- `archive`
- `validate`

### `init`
Creates mailbox structure.

### `archive`
Moves old processed mail to archive.

### `validate`
Checks all envelopes in mailbox for schema correctness.

Example:
```bash
./mailbox.sh validate
```

---

# Recommended implementation strategy

## Best implementation shape
Keep `mailbox.sh` as the user-facing CLI.
But move structured logic into a Python helper.

### Suggested split
- `mailbox.sh`
  - command parsing
  - user-facing UX
- `mailbox_core.py`
  - envelope creation
  - validation
  - atomic writes
  - archive logic
  - ledger append

### Why this is the right move
Shell is good at orchestration.
Python is better for:
- JSON
- validation
- safe file operations
- testability

Do not try to make bash impersonate a schema layer forever.

---

# Deliverables for Phase 2

By the end of this phase, I want to see:

- [ ] `init` command implemented
- [ ] envelope generation moved to safe structured path
- [ ] atomic write behavior implemented
- [ ] archive folder and archive command implemented
- [ ] JSONL ledger added
- [ ] completion defaults made configurable
- [ ] validation command added
- [ ] automated tests added
- [ ] docs updated to reflect true Phase 2 capabilities

---

# What not to do in Phase 2

Do **not**:
- bolt live notifications back in prematurely
- reintroduce fake CLI `sessions_send` assumptions
- add SQLite before JSONL proves insufficient
- overcomplicate the mailbox with too many delivery states

Phase 2 is about **reliability**, not spectacle.

---

# Final instruction

Your north star for this phase:

**Make the mailbox trustworthy on disk before making it exciting in motion.**

That means:
- valid envelopes
- safe writes
- clean lifecycle
- tests
- honest docs

Build that, and Phase 3 will have something real to stand on.

— Dad
