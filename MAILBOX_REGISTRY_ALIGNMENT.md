# Mailbox Registry Alignment Note

Author: Arbiter
Date: 2026-03-18

## Purpose

Define how the mailbox system should align with comms registry shape, tag discipline, and governance doctrine without prematurely pretending Plane A operational artifacts are Plane B canon.

This note is intentionally practical.
It is not a giant abstract policy treatise.

## Constitutional frame

- Plane A = working / operational / provisional
- Plane B = canonical / curated / promoted
- files are authored truth
- SQL/registry is the lineage/join/state spine
- vectors are indexes, not truth
- tags and namespaces are governance-sensitive
- model/runtime hints must not outrank recorded evidence

For mailbox work specifically:
- envelopes, trackers, ledgers, receipts, repings, and escalations are Plane A operational records
- registry alignment should make them easier to query, validate, and promote later
- registry alignment must not erase provenance or flatten distinct lifecycle states into one blur

## Goal

Make mailbox events and tracker state legible to a future registry/comms layer by standardizing:
- event families
- state classes
- provenance fields
- notifier/adaptor semantics
- optional tag annotations

## Do not do this

- do not pretend runtime mailbox artifacts are already Plane B canon
- do not casually mint canonical tags without governance
- do not use tags as a substitute for structured fields
- do not collapse delivery, ack, and live-notify into one status field

## Required structural fields

For major mailbox ledger events and trackers, converge on these fields where relevant:

- `schema_version`
- `component`
- `event_type`
- `event_family`
- `state_class`
- `trust_plane`
- `trace_id` or equivalent lineage link when available
- `work_item_id`
- `thread_id`
- `envelope_id`
- `sender`
- `recipient`
- `ts`
- `provenance_writer`
- `notify_mode` (if live-assist logic involved)
- `adapter` (if notify adapter used)
- optional `tags[]`

### Field intent

- `schema_version`
  - allows evolution without rewriting history
- `component`
  - who wrote this record (`haiku_mailman`, `receipt_watcher`, `smart_mailman`, etc.)
- `event_family`
  - broader comms grouping for registry/query use
- `state_class`
  - what kind of state is being represented
- `provenance_writer`
  - concrete writer identity for lineage and audit
- `notify_mode`
  - distinguishes delivery truth from assistive notify behavior
- `adapter`
  - identifies concrete integration path such as `agent-turn-nudge`

## Canonical event families (proposed)

These are proposed registry-facing comms classes. Treat them as implementation targets, not silently admitted eternal canon.

- `comms/envelope`
- `comms/delivery`
- `comms/ack`
- `comms/timeout`
- `comms/escalation`
- `comms/live-notify`
- `comms/session-discovery`
- `comms/violation`
- `comms/status-report`

## Canonical state classes (proposed)

- `delivery_state`
- `ack_state`
- `live_notify_state`
- `routing_state`
- `violation_state`

The first three are the immediate minimum.

## Event-family mapping examples

### Envelope intake
- `event_type`: `ENVELOPE_ACCEPTED`
- `event_family`: `comms/envelope`
- `state_class`: `routing_state`

### Durable delivery
- `event_type`: `DELIVERY_CONFIRMED`
- `event_family`: `comms/delivery`
- `state_class`: `delivery_state`

### Ack observed
- `event_type`: `ACK_RECORDED`
- `event_family`: `comms/ack`
- `state_class`: `ack_state`

### Ack overdue / timeout
- `event_type`: `ACK_TIMEOUT`
- `event_family`: `comms/timeout`
- `state_class`: `ack_state`

### Escalation
- `event_type`: `ACK_ESCALATION`
- `event_family`: `comms/escalation`
- `state_class`: `ack_state`

### Session discovery
- `event_type`: `SESSION_DISCOVERY`
- `event_family`: `comms/session-discovery`
- `state_class`: `live_notify_state`

### Live notify opportunity / nudge
- `event_type`: `LIVE_NOTIFY_OPPORTUNITY` or `AGENT_TURN_NUDGE_ATTEMPT`
- `event_family`: `comms/live-notify`
- `state_class`: `live_notify_state`

## Notify mode registry semantics

Use explicit notify modes:
- `none`
- `discover-only`
- `agent-turn-nudge`
- future `session-inject` only if verified

### Rules
- `notify_mode` must never change durable delivery truth
- successful nudge does not imply message delivery into inbox
- session discovery does not imply runtime reachability
- if the adapter is unverified, say so in the event semantics

## Recommended provenance fields by component

### `haiku_mailman.py`
Should stamp:
- `component: haiku_mailman`
- `provenance_writer: haiku_mailman`
- relevant `event_family`
- relevant `state_class`

### `receipt_watcher.py`
Should stamp:
- `component: receipt_watcher`
- `provenance_writer: receipt_watcher`
- `event_family: comms/ack`
- `state_class: ack_state`

### `smart_mailman.py`
Should stamp:
- `component: smart_mailman`
- `provenance_writer: smart_mailman`
- `event_family: comms/session-discovery` or `comms/live-notify`
- `state_class: live_notify_state`

## Tag mapping guidance

Structured fields remain primary.
Tags are secondary annotations for registry/search/alignment.

### Safe mappings
- trust plane:
  - `plane-a`
  - `plane-b`
- mode where relevant:
  - `mode:system`
  - `mode:ui`
  - `mode:trading`
- risk where relevant:
  - `risk:low`
  - `risk:medium`
  - `risk:high`
  - `risk:critical`

### Avoid premature over-tagging
Do not attach large tag bundles to every mailbox event just because tags exist.
Add tags where they serve real retrieval, policy, or registry use.

## Minimal implementation target

To count as registry-aligned enough for the next stage, mailbox runtime should achieve this minimum:

1. trackers distinguish:
   - `delivery_state`
   - `ack_state`
   - `live_notify_state`
2. major ledger events include:
   - `schema_version`
   - `component`
   - `event_family`
   - `state_class`
3. notify mode is explicit when live-assist logic is used
4. status/reporting can summarize state using those distinctions

## Operator retrieval surface

Mailbox operator search/status should support exact-match retrieval over these structured fields:

- `work_item_id`
- `thread_id`
- `event_family`
- `state_class`
- `trust_plane`
- optional `tags[]`

`tags[]` remain secondary annotations.
They do not replace the structured fields above.

### `project_id` decision

Do not add a distinct canonical `project_id` field at this stage.

Current mailbox data does not show a stable independent project semantic beyond `work_item_id`.
For operator retrieval, a derived project-equivalent alias may be exposed, but it must be documented as:

- derived from `work_item_id`
- retrieval-only
- not canonical duplication

### Conservative tag guidance

Only use tags where they add retrieval or governance value beyond the structured fields.
Recommended conservative tags:

- `comms/mailbox`
- `comms/work-item-link`
- `projects/mailbox-runtime`
- specific `policy/*` tags only when policy routing/governance is materially involved

## Suggested implementation order

1. update tracker schema
2. update ledger event writers in `haiku_mailman.py`, `receipt_watcher.py`, `smart_mailman.py`
3. align status reporting with the new state classes
4. only then consider deeper SQL/registry integration work

## Bottom line

Mailbox is already doctrinally aligned in spirit.
This note defines how to make it registry-aligned in structure.

The objective is not bureaucracy.
The objective is to make communications state queryable, auditable, and governable without losing the truth of what actually happened.
