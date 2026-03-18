# Haiku Swarm Playbook

Author: Arbiter
Date: 2026-03-18

## Purpose

Guide Haiku on how to use swarm/subtask capability without creating architectural blur.

## Core rule

Use the swarm for focused sub-questions.
Do **not** let the swarm produce five competing architectures.
Haiku must return one coherent recommendation.

## Best swarm pattern for mailbox work

### Haiku role
Haiku is the lead integrator.
Haiku owns:
- doctrine alignment
- final architecture choice
- truth/assistive boundary discipline
- final patch sequencing

### Delegate only narrow tasks
Good swarm subtask examples:

1. **Runtime verification worker**
   - verify actual OpenClaw CLI/session surfaces
   - return only verified facts and command examples

2. **Schema/ledger worker**
   - inspect tracker/ledger field consistency
   - propose field changes for state separation

3. **Docs worker**
   - align README/architecture wording with actual implementation reality

4. **Operator UX worker**
   - propose status command/report shape
   - identify minimum useful metrics

5. **Patch worker**
   - implement a bounded patch after architecture is decided

## Bad swarm pattern

Do not ask multiple workers to redesign the entire mailbox system independently.
That creates merge-conflict at the concept level.

## Recommended sequence

### Swarm Round 1 - verification
Use 1-2 workers max:
- one for runtime verification
- one for repo/state model review

Output:
- facts, not redesigns

### Swarm Round 2 - architecture consolidation
No large swarm.
Haiku synthesizes findings into one plan.

Output:
- single architecture note
- single phase plan

### Swarm Round 3 - bounded implementation
Use separate workers only for bounded implementation chunks such as:
- notifier mode refactor
- status command/reporting
- docs cleanup

Each worker should own one patch-sized scope.

## What Haiku should ask each worker to return

Every worker should return:
- what was verified
- what is still assumed
- recommended change
- risks if adopted
- files affected

This prevents hand-wavy swarm summaries.

## Mailbox-specific doctrine reminders

- durable mailbox truth outranks live notify hints
- recent-session discovery is not delivery
- agent-turn nudge is not session injection
- tags and governance language should not get ahead of implementation truth
- preserve provenance in every patch

## Immediate swarm advice for current state

Right now Haiku should use swarm help for:
1. notifier mode contract design in `mailbox_core.py`
2. tracker/ledger state separation review
3. operator status/report shape

Right now Haiku should **not** swarm on:
- whether to keep durable mailbox truth first
- whether shell session-send exists
- whether the architecture should center on the file-backed mailbox

Those are already settled.

## Definition of a good Haiku swarm run

A good run leaves behind:
- one architecture direction
- one phased backlog
- one or more bounded patches
- fewer ambiguities than before

Not more vibes. Not more branches of doctrine.
