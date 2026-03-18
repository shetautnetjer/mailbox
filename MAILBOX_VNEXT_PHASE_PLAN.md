# Mailbox vNext - Phase Plan

Author: Arbiter
Date: 2026-03-18

## Purpose

Translate Haiku's findings into a practical phased execution plan.
This plan keeps doctrine clean:
- durable mailbox truth first
- session discovery second
- live notify adapters as assistive only

## Current architectural stance

### Layer 1 - Durable mailbox truth
Authoritative Plane A operational truth:
- envelope files
- inbox/outbox copies
- tracking files
- receipts
- ack/reping/timeout/escalation state
- JSONL ledgers

Primary files:
- `bin/send_mail.py`
- `bin/haiku_mailman.py`
- `bin/receipt_watcher.py`
- `bin/mailbox_core.py`

### Layer 2 - Session discovery
Assistive context only:
- recent session discovery
- freshness windows
- recently-active visibility

Primary file:
- `bin/smart_mailman.py`

### Layer 3 - Live notify adapters
Non-authoritative assists only:
- `none`
- `discover-only`
- `agent-turn-nudge`
- future `session-inject` only after verified runtime support

## Phase 0 - Freeze the truth model

Goal:
- establish language and invariants before more implementation drift

Required invariants:
- durable file/ledger state is the source of operational truth
- presence/discovery is not delivery
- nudges are not delivery
- mailbox delivery success must never be inferred from recent session discovery alone
- all ledger events should indicate the component and semantic layer involved

Outputs:
- this phase plan
- docs language aligned to these rules

## Phase 1 - Truth tightening (finish and commit)

Goal:
- complete the current honesty patch and commit it cleanly

Tasks:
- finalize `smart_mailman.py`
- update README/docs to remove any claim of direct shell session-send
- explicitly describe presence as recent-session discovery
- explicitly describe live notify as assistive, not authoritative
- commit the patch with a clear message

Definition of done:
- no docs or CLI output imply verified session injection from shell
- code and docs use the same vocabulary

## Phase 2 - Notifier mode architecture

Goal:
- replace ad-hoc ping logic with explicit notifier modes

Design target:
- centralize notifier mode selection in `mailbox_core.py`
- supported modes:
  - `none`
  - `discover-only`
  - `agent-turn-nudge`
- reserve `session-inject` as future/unimplemented unless verified

Tasks:
- rename/reframe `best_effort_openclaw_ping()` to something like `agent_turn_nudge()` or route through a notifier adapter
- define return shape for notifier attempts
- distinguish:
  - discovery result
  - nudge attempt
  - nudge result
- ensure trackers and ledgers log notify state separately from delivery state

Definition of done:
- notifier mode is explicit in code path and event logs
- no one can mistake nudge success for mailbox delivery truth

## Phase 3 - State model cleanup

Goal:
- separate operational states so reporting becomes honest and easy

Required state separation:
- `delivery_state`
- `ack_state`
- `live_notify_state`

Tasks:
- update tracker structure
- update delivery ledger rows
- add provenance fields such as:
  - `component`
  - `adapter`
  - `schema_version`
- avoid ambiguous booleans like `recipient_online`

Definition of done:
- durable delivery, ack lifecycle, and live notify are distinct everywhere

## Phase 4 - Operator reporting

Goal:
- give Haiku and operators a real operational view

Desired reports:
- pending intake count and items
- recent deliveries
- overdue acks
- repings sent
- escalations
- recently active agents

Tasks:
- add a `status` or equivalent report command/script
- add overdue tracker inspection
- add concise machine-readable output mode if easy

Definition of done:
- operator can answer "what is stuck?" in one command

## Phase 5 - Governance hygiene

Goal:
- prepare for future promotion/governance without pretending we are already there

Tasks:
- add schema/version markers to key ledger and tracker events
- add provenance fields on writes
- review field naming against Brain-harness doctrine
- keep Plane A labeling explicit

Definition of done:
- the mailbox system remains operationally useful now and governable later

## Proposed file ownership

- `mailbox_core.py`
  - paths
  - validation
  - notifier mode contract
  - event helper primitives
- `haiku_mailman.py`
  - durable intake processing
  - delivery/tracker creation
  - timeout/escalation lifecycle
- `receipt_watcher.py`
  - ack reconciliation
- `smart_mailman.py`
  - discovery/reporting/assistive layer only
- `session_mailman.py`
  - deprecate or reduce to tiny adapter wrapper; do not let it pretend to be core runtime truth

## Sequencing advice

Do not try to do all phases at once.

Recommended order:
1. finish and commit truth-tightening patch
2. implement explicit notifier modes
3. separate live notify state from delivery/ack state
4. add operator status/reporting
5. only then do deeper schema hygiene

## Constitutional note

This mailbox is not a chat toy.
It is communications infrastructure inside a governed nervous system.
The architecture must preserve provenance and must not blur discovery, delivery, and execution authority into one vague layer.
