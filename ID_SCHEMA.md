# Mailbox ID Schema - Audit Trail Reference

**Date:** 2026-03-15  
**Source:** `/home/netjer/.openclaw/workspace/plane-a/projects/coms/mailbox/bin/uuid7_util.py` + ledger analysis  
**Purpose:** Complete ID taxonomy for auditable memory (SQL joins, QMD tags, traceability)

---

## UUIDv7 Doctrine

> **"Operational IDs use UUIDv7 with type prefixes. Semantic/knowledge IDs stay human-readable."**

UUIDv7 provides:
- **Time-ordered** — sortable by creation time
- **Globally unique** — 48-bit ms timestamp + 74 random bits
- **Prefix-tagged** — type-safe at a glance
- **Database-friendly** — monotonic, index-efficient

---

## ID Taxonomy

### 1. Communication IDs

| ID Type | Prefix | Generator | Purpose | Example |
|---------|--------|-----------|---------|---------|
| **envelope_id** | `env_` | `gen_envelope_id()` | Single message unit | `env_019cd718-3329-7xxx...` |
| **parent_id** | `env_` | Reference to envelope_id | Thread/reply linkage | Same as envelope_id |
| **thread_id** | `wi_` or custom | Usually same as work_item_id | Conversation grouping | `wi-2026-03-10-001` |

### 2. Task & Work IDs

| ID Type | Prefix | Generator | Purpose | Example |
|---------|--------|-----------|---------|---------|
| **work_item_id** | `wi_` | `gen_work_item_id()` | Trackable work unit | `wi-2026-03-10-lancedb-migration` |
| **task_id** | `tsk_` | `gen_task_id()` | Individual task | `tsk_019cd718-3329-...` |
| **event_id** | `evt_` | `gen_event_id()` | Discrete system event | `evt_019cd718-3329-...` |

### 3. Delivery & Receipt IDs

| ID Type | Prefix | Generator | Purpose | Example |
|---------|--------|-----------|---------|---------|
| **delivery_id** | `del_` | `gen_delivery_id()` | Delivery attempt record | `del-2026-03-08-213443-6e35` |
| **receipt_id** | `rcpt_` | `gen_receipt_id()` | Acknowledgment proof | `rcpt_019cd718-3329-...` |
| **copy_id** | `{envelope_id}-r{n}` | `f"{envelope_id}-r{i}"` | Reping copy | `env-xxx-r1` |

### 4. Document & Artifact IDs

| ID Type | Prefix | Generator | Purpose | Example |
|---------|--------|-----------|---------|---------|
| **doc_id** | `doc_` or semantic | Human-defined | Document reference | `doc_promotion_policy_v1` |
| **artifact_id** | `art_` | `gen_artifact_id()` | Generated artifact | `art_019cd718-3329-...` |
| **chunk_id** | `{doc_id}::{section}` | `f"{doc_id}::{section}"` | Document section | `doc_v1::section_name` |
| **promotion_id** | `promo_` | `gen_uuidv7("promo")` | Plane A→B promotion | `promo_019cd718-...` |

### 5. Session & Observation IDs

| ID Type | Prefix | Generator | Purpose | Example |
|---------|--------|-----------|---------|---------|
| **session_id** | `ses_` | `gen_session_id()` | Agent session | `ses_019cd718-3329-...` |
| **trace_id** | `trc_` | `gen_trace_id()` | Distributed trace | `trc_019cd718-3329-...` |
| **observation_id** | `obs_` | `gen_observation_id()` | Observer event | `obs_019cd718-3329-...` |
| **episode_id** | `epi_` | `gen_episode_id()` | Multi-turn episode | `epi_019cd718-3329-...` |
| **gate_id** | `gate_` | `gen_gate_id()` | Curation gate | `gate_019cd718-3329-...` |
| **handoff_id** | `hoff_` | `gen_uuidv7("hoff")` | Agent handoff | `hoff_019cd718-...` |

---

## Usage Patterns in Ledger

### Frequency Analysis (from deliveries.jsonl)

| ID Field | Count | % of Records | Purpose |
|----------|-------|--------------|---------|
| `envelope_id` | 410 | 100% | Primary key |
| `work_item_id` | 342 | 83% | Task grouping |
| `delivery_id` | 182 | 44% | Delivery tracking |
| `receipt_id` | 122 | 30% | Acknowledgment |
| `thread_id` | 61 | 15% | Conversation |
| `event_id` | 26 | 6% | Event correlation |
| `doc_id` | 3 | <1% | Document refs |

---

## SQL Join Patterns

### Link Envelope to Work Item
```sql
SELECT 
    e.envelope_id,
    e.subject,
    e.sender,
    e.recipient,
    e.work_item_id,
    r.receipt_id,
    r.receiver
FROM deliveries d
JOIN receipts r ON d.envelope_id = r.envelope_id
WHERE d.work_item_id = 'wi-2026-03-10-lancedb-migration'
ORDER BY d.ts;
```

### Thread Reconstruction
```sql
SELECT 
    envelope_id,
    type,
    from,
    to,
    subject,
    parent_id  -- Links to parent envelope
FROM envelopes
WHERE thread_id = 'wi-2026-03-10-lancedb-migration'
   OR work_item_id = 'wi-2026-03-10-lancedb-migration'
ORDER BY ts;
```

### Delivery Chain
```sql
SELECT 
    d.delivery_id,
    d.envelope_id,
    d.sender,
    d.recipient,
    d.ack_due_ts,
    r.receipt_id,
    r.receipt_type,
    v.event as violation_event
FROM deliveries d
LEFT JOIN receipts r ON d.envelope_id = r.envelope_id
LEFT JOIN violations v ON d.envelope_id = v.envelope_id
WHERE d.envelope_id = 'env-2026-03-10-051137-6a98';
```

---

## QMD Tag Usage

For Quarto documents tracking work items:

```yaml
---
title: "Brain Frame Migration"
doc_id: doc_brain_frame_v4
trust_zone: plane-b
work_item_id: wi-2026-03-10-lancedb-migration
thread_id: wi-2026-03-10-lancedb-migration
promotion_id: promo_019cd9d3-b2ba-72b5-9319-f147d79d258d
related_envelopes:
  - env_019cd9d3-b2ba-72b5-9319-f147d79d258d
  - env_019cd9e5-4c50-721d-8a4f-5d26d6d2b948
---
```

---

## Indexing Strategy

From `ledger_ingest.py`:

```sql
-- Primary lookups
CREATE INDEX idx_del_envelope_id ON deliveries(envelope_id);
CREATE INDEX idx_del_work_item ON deliveries(work_item_id);
CREATE INDEX idx_rcpt_envelope_id ON receipts(envelope_id);
CREATE INDEX idx_viol_envelope_id ON violations(envelope_id);

-- Time-series queries
CREATE INDEX idx_del_ts ON deliveries(ts);
CREATE INDEX idx_rcpt_ts ON receipts(ts);

-- Agent queries
CREATE INDEX idx_del_sender ON deliveries(sender);
CREATE INDEX idx_del_recipient ON deliveries(recipient);
```

---

## Violation Types (from violations.jsonl)

| Violation Type | ID Fields Logged |
|----------------|------------------|
| `TRUST_VIOLATION` | envelope_id, from, to, work_item_id |
| `ROUTING_ERROR` | envelope_id, from, to, work_item_id |
| `INVALID_ENVELOPE` | envelope_id, reason |

---

## Audit Trail Completeness

For full traceability, every envelope should have:
1. ✅ `envelope_id` — Unique message ID
2. ✅ `work_item_id` — Business context
3. ✅ `thread_id` — Conversation thread
4. ✅ `event_id` — System event correlation
5. ✅ `delivery_id` — Delivery attempt record
6. ✅ `receipt_id` — Acknowledgment proof (if delivered)
7. ✅ `parent_id` — Reply chain (if response)

---

## Prefix Summary Table

| Prefix | Meaning | Generator Function |
|--------|---------|-------------------|
| `env_` | Envelope | `gen_envelope_id()` |
| `evt_` | Event | `gen_event_id()` |
| `art_` | Artifact | `gen_artifact_id()` |
| `epi_` | Episode | `gen_episode_id()` |
| `gate_` | Gate | `gen_gate_id()` |
| `rcpt_` | Receipt | `gen_receipt_id()` |
| `tsk_` | Task | `gen_task_id()` |
| `wi_` | Work Item | `gen_work_item_id()` |
| `ses_` | Session | `gen_session_id()` |
| `trc_` | Trace | `gen_trace_id()` |
| `promo_` | Promotion | `gen_uuidv7("promo")` |
| `hoff_` | Handoff | `gen_uuidv7("hoff")` |
| `obs_` | Observation | `gen_observation_id()` |
| `del_` | Delivery | `gen_delivery_id()` |
| `doc_` | Document | Human-defined |

---

## Tags vs IDs — Critical Distinction

> **Tags are for labeling. IDs are for referencing.**

### The Problem

Draft tag systems use namespaced slash-tags like `work/task` or `comms/mailbox`. These are **not valid IDs** in the mailbox system.

| Aspect | Tags | IDs |
|--------|------|-----|
| **Format** | `work/task`, `comms/mailbox` | `wi-2026-03-10-001`, `env_019cd9d3...` |
| **Purpose** | Filter, group, categorize | Uniquely identify, join tables |
| **Slash safe?** | ✅ Yes (namespace/category) | ❌ No (filesystem/URL delimiter) |
| **Used in** | Tag registries, QMD frontmatter | Envelopes, database keys, filenames |
| **My dad's drafts** | `governance/doctrine` | *Not applicable* |

### Why Slashes Break IDs

```python
# Bad: Filesystem sees this as directory
work_item_id = "work/task/001"
# → Creates: work/task/001.json (wrong!)

# Bad: URL encoding required
http://api/work/task/001  # ambiguous path

# Bad: SQL needs escaping
SELECT * FROM work_items WHERE id = 'work/task/001';
```

### Correct Usage

**Tags (from dad's registry):**
```yaml
# In QMD frontmatter or tag registries
tags:
  - work/task           # Category: work, Type: task
  - comms/mailbox       # Category: comms, System: mailbox
  - plane-a             # Trust plane
```

**IDs (in mailbox system):**
```json
{
  "envelope_id": "env_019cd9d3-b2ba-72b5-9319-f147d79d258d",
  "work_item_id": "wi-2026-03-10-lancedb-migration",
  "delivery_id": "del-2026-03-08-213443-6e35",
  "receipt_id": "rcpt_019cd9e5-4c50-721d-8a4f-5d26d6d2b948",
  "thread_id": "wi-2026-03-10-lancedb-migration"
}
```

### Join Strategy

```sql
-- Query by ID (exact match)
SELECT * FROM deliveries
WHERE work_item_id = 'wi-2026-03-10-lancedb-migration';

-- Query by tag (filter/group)
SELECT * FROM documents
WHERE tags LIKE '%work/task%';  -- Tag is metadata, not primary key
```

### Mapping Tags to IDs

| Tag | ID Type | Example ID |
|-----|---------|------------|
| `work/task` | `work_item_id` | `wi-2026-03-10-001` |
| `comms/mailbox` | `envelope_id` | `env_019cd9d3...` |
| `comms/receipt` | `receipt_id` | `rcpt_019cd9e5...` |
| `event/envelope` | `event_id` | `evt_019cd9d3...` |
| `governance/doctrine` | *Use tag only, no ID* | — |
| `memory/plane-a` | *Use tag only* | — |

### Arbiter Ruling Applies

Per my dad's **namespace/alias law memo**:
- Narrow canonical tags (`doctrine`, `plane-a`) remain primary
- Namespaced tags (`governance/doctrine`) are **labels**, not IDs
- Dual canon prevented — tags don't become IDs just by existing

**Result:** IDs stay prefixed (`wi_`, `env_`, `rcpt_`). Tags stay namespaced (`work/task`). They work together but are not interchangeable.

---

_These IDs create a complete audit trail from envelope creation through delivery, receipt, and document promotion._ ✊🏾
