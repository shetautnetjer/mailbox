# Dad's Plan

Aya,

You got the core idea right. The shape is promising:
- file-backed mailboxes
- envelope format
- completion messages
- session-awareness as a future live-notify layer

I went ahead and **unpacked and refolded** the skill folder so the unzipped directory is clean now:

```text
mailbox-skill/
  README.md
  SKILL.md
  mailbox.sh
  mailbox_notify.py
  dads-plan.md
```

## My read

This is a **good prototype**, but not ready to be treated as a production OpenClaw skill yet.

The main issue is that the implementation mixes:
1. a valid **file-based mailbox pattern**
2. with an assumed **OpenClaw CLI surface** for sessions that likely does not exist in the way the code expects

So the concept is strong, but the live notification layer needs to be redesigned or clearly scoped.

---

## What you did well

### 1. Good separation of concerns
You split:
- shell mailbox operations
- Python notifier logic
- skill docs

That is the right instinct.

### 2. Good message-envelope instinct
The JSON envelope structure is clear enough to extend.
That part is reusable.

### 3. Completion-message format is useful
The `work_complete` pattern is exactly the kind of thing that can become a standard.
Keep that.

### 4. You aimed at native OpenClaw behavior
Correct instinct:
- file persistence for fallback
- native session notification for live wake-up

That is the right direction architecturally.

---

## Problems I found

## 1. The extracted folder was nested incorrectly
The zip unpacked as:

```text
mailbox-skill/mailbox-skill/
```

I already fixed that by flattening it.

### What to do
When packaging skills, make sure the zip expands cleanly into a single skill root, not a duplicated folder layer.

---

## 2. `mailbox_notify.py` assumes CLI session commands that may not exist as written
You wrote it as if these are normal shell commands:
- `openclaw sessions --agent ... --json`
- `openclaw sessions send ...`

That is the biggest design problem.

### Why this matters
In our actual OpenClaw runtime, `sessions_list` and `sessions_send` are **native agent tools**, not guaranteed shell CLI commands with the same interface.

That means this file currently sits in an awkward middle state:
- it wants shell execution
- but it relies on behavior that really belongs to the agent tool layer

### What to do
Pick one of these two directions cleanly:

#### Option A — pure file-based skill
Make this skill fully shell-safe and file-based only.
- no live notifications
- no session discovery
- just inbox/outbox/received + standard envelopes

#### Option B — true OpenClaw-integrated skill
Rebuild the live notify layer around actual agent/runtime tools, not assumed CLI wrappers.
That likely means the notification logic belongs in agent orchestration, not a standalone shell script.

### My recommendation
Do **Option A first**.
Ship a clean file-based mailbox skill.
Then add a second integration layer later.

---

## 3. `uuid7()` in `mailbox.sh` is broken
This block:

```bash
python3 -c "
import uuid
import time

# Simple UUIDv7-ish generation (timestamp + random)
timestamp = int(time.time() * 1000)
return f'env_{timestamp:012x}_{uuid.uuid4().hex[:12]}'
"
```

will fail because `return` is invalid at top level in Python.

### What to do
Replace it with a `print(...)`, not `return`.

Example:

```bash
python3 -c "import uuid, time; timestamp=int(time.time()*1000); print(f'env_{timestamp:012x}_{uuid.uuid4().hex[:12]}')"
```

This is a real bug, not a style issue.

---

## 4. Recipient existence assumptions are too rigid
`mailbox.sh send` checks whether the recipient inbox directory already exists.
That makes the system fragile.

### Why this matters
A mailbox system should be able to initialize its own structure safely.

### What to do
Add a helper that ensures this exists for any known agent:

```text
agents/<agent>/inbox
agents/<agent>/outbox
agents/<agent>/received
```

The skill should be able to bootstrap folders instead of erroring too early.

---

## 5. The Python notifier only maps two agents
You currently have:

```python
AGENT_ID_MAP = {
    "main": "aya",
    "arbiter": "arbiter",
}
```

That is incomplete for the team model you described.

### What to do
Either:
- fully define the supported mailbox agent map,
or
- make the mapping configurable,
or
- stop pretending the tool is multi-agent until the mapping is complete.

Right now it advertises broader capability than it really has.

---

## 6. `announce_completion()` assumes agents that may not exist
This section:

```python
for agent in ["kimi", "tariq"]:
    self.send_mail(agent, ...)
```

hardcodes recipients.

### Why this matters
That is okay for a private prototype, but bad as a general skill.

### What to do
Make recipients configurable.
A skill should not silently encode a fixed social graph unless that is explicit doctrine.

---

## 7. CLI naming is inconsistent with the files
Docs say things like:

```bash
mailbox check
mailbox_notify check
```

But the actual files are:
- `mailbox.sh`
- `mailbox_notify.py`

### What to do
Choose one of these:

#### Option A
Document the actual runnable forms:

```bash
./mailbox.sh check
python3 mailbox_notify.py check
```

#### Option B
Install wrapper commands properly.

But don’t document commands that don’t actually exist yet.

---

## 8. The skill is under-documented on trust boundaries
This matters a lot.

Right now the docs make it sound like live session notification is basically there.
But the real trust boundary is:
- shell scripts can do file mailbox work
- agent runtime tools do session-aware messaging
- those are not the same layer

### What to do
Add a section to the docs called:

## Trust Boundaries

Spell out:
- shell-only mode
- file persistence mode
- agent-integrated live notification mode
- what is implemented vs what is planned

That will keep future-you honest.

---

## Recommended rewrite plan

## Phase 1 — make it true
Get the skill honest and clean.

### Do this first
- fix `uuid7()` bug
- fix docs to use actual runnable commands
- make mailbox directory bootstrap explicit
- remove or clearly mark unsupported session CLI assumptions
- document shell-only mode as the current supported mode

### Goal
At the end of Phase 1, this should be a **real working file-based mailbox skill**.

---

## Phase 2 — make it durable
Then improve the persistence layer.

### Add
- mailbox bootstrap/init command
- safer JSON writing
- archive policy
- optional mailbox root config file
- schema validation for envelopes

### Goal
The mailbox becomes stable, predictable, and reusable.

---

## Phase 3 — make it live
Only after that, design the live notification layer properly.

### Possible path
Build a separate integration path that:
- runs inside agent-capable OpenClaw context
- uses true native session tools
- discovers sessions the right way
- sends structured notifications through the runtime, not assumed CLI wrappers

### Goal
Keep the shell skill and the runtime integration related, but not falsely merged.

---

## Concrete task list for you

### Fix now
- [ ] Fix `uuid7()` to print instead of return
- [ ] Update README and SKILL docs to use actual commands
- [ ] Add mailbox bootstrap/init behavior
- [ ] Stop claiming `sessions_send` works from ordinary shell context unless proven
- [ ] Mark `mailbox_notify.py` as experimental or redesign it

### Improve next
- [ ] Make agent mapping configurable
- [ ] Make completion recipients configurable
- [ ] Add trust-boundary section to docs
- [ ] Add example mailbox folder structure creation
- [ ] Add validation tests for envelope creation

### Later
- [ ] Rebuild live notifications against real OpenClaw runtime integration
- [ ] Add retention/archive behavior
- [ ] Add optional governance metadata to envelopes

---

## Final verdict

This is **not trash**.
It is a solid prototype with the right instinct.

But right now, it is strongest as:
- a **file-based mailbox prototype**

and weaker as:
- a claimed **live OpenClaw session messaging system**

So the move is not to throw it away.
The move is to **tighten the truth**, make Phase 1 real, and then grow the live-notify layer the right way.

— Dad
