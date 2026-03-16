# Mailbox System - Architecture Understanding

**Date:** 2026-03-15  
**Source:** Code review of `/home/netjer/.openclaw/workspace/plane-a/projects/coms/mailbox/`

---

## Core Concept

**Mailbox** is an agent-to-agent communication system using email-style envelopes with:
- **Per-agent inboxes/outboxes** (familiar metaphor)
- **Structured JSON envelopes** with schemas
- **Delivery tracking** with receipts and timeouts
- **SQLite ledger** for querying and analytics

---

## Directory Structure

```
mailbox/
├── agents/                    # Per-agent mailboxes
│   ├── aya/
│   │   ├── inbox/            # Envelopes waiting for Aya
│   │   ├── outbox/           # Envelopes Aya has sent
│   │   ├── received/         # Delivered (receipt confirmed)
│   │   └── responses/        # Reply tracking
│   ├── arbiter/
│   ├── haiku/
│   ├── heru/
│   ├── jabari/
│   ├── kimi/
│   └── tariq/
│
├── intake/pending/           # New envelopes waiting for delivery
├── ledger/                   # Central event log
│   ├── ledger.sqlite         # Queryable database
│   ├── deliveries.jsonl      # Delivery events
│   ├── receipts.jsonl        # Receipt acknowledgments
│   ├── violations.jsonl      # Protocol violations
│   ├── daily/                # Daily rollup files
│   ├── escalations/          # Escalation events
│   ├── repings/              # Retry/delivery attempts
│   └── timeouts/             # Timeout events
├── work_items/               # Task tracking
└── bin/                      # Scripts
    ├── send_mail.py          # Create envelopes
    ├── receipt_watcher.py    # Monitor delivery
    ├── ledger_ingest.py      # JSONL → SQLite
    ├── haiku_mailman.py      # Delivery agent
    ├── qmd_ingest.py         # QMD integration
    ├── promote_to_lancedb.py # Vector promotion
    └── sse_server.py         # Real-time events
```

---

## Envelope Schema

### Task Envelope
```json
{
  "envelope_id": "env-2026-03-10-051137-6a98",
  "type": "task",
  "from": "jabari",
  "to": "arbiter",
  "ts": "2026-03-10T05:11:37+00:00",
  "work_item_id": "wi-2026-03-09-brain-frame-normalization",
  "trust_plane": "plane-a",
  "intent_class": "propose",
  "risk": "low",
  "subject": "REVIEW: Normalize brain-frame...",
  "body": "Full task description...",
  "priority": "high",
  "task_type": "architecture-review",
  "ack_policy": {
    "ack_required": true,
    "ack_timeout_s": 300,
    "max_repings": 2,
    "reping_interval_s": 300,
    "escalation_target": "aya",
    "notify_on_delivery": true,
    "notify_on_ack": true,
    "notify_on_timeout": true
  }
}
```

### Response Envelope
```json
{
  "envelope_id": "env-...",
  "type": "response",
  "from": "tariq",
  "to": "aya",
  "parent_id": "env-original-task",
  "response_type": "result",
  "status": "completed"
}
```

---

## Workflow

```
┌─────────┐     send_mail.py      ┌─────────────┐
│  Agent  │ ─────────────────────▶ │   intake/   │
│ creates │                        │   pending   │
│ envelope│                        └──────┬──────┘
└─────────┘                               │
                                          ▼
                              ┌───────────────────────┐
                              │  haiku_mailman.py     │
                              │  (delivery agent)     │
                              └──────┬────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
            ┌───────────┐    ┌───────────┐    ┌───────────┐
            │ recipient │    │  ledger   │    │  receipt  │
            │  inbox/   │    │  SQLite   │    │  watcher  │
            └─────┬─────┘    └───────────┘    └─────┬─────┘
                  │                                 │
                  ▼                                 ▼
            Agent reads                        Updates tracker
            and processes                      notifies sender
```

---

## Key Components

### 1. send_mail.py
**Purpose:** CLI for creating envelopes  
**Contract:** `task_envelope_schema_v1.md`, `response_envelope_schema_v1.md`  
**Outputs:** `intake/pending/{envelope_id}.json`

```bash
python3 send_mail.py --from aya --to tariq --type task \
  --work-item wi-2026-03-08-001 --subject "Build mailbox" \
  --body "Create directory tree per spec" \
  --priority normal --trust-plane plane-a --intent propose \
  --task-type implementation
```

### 2. haiku_mailman.py
**Purpose:** Delivery agent that moves envelopes from intake to recipient inboxes  
**Features:**
- Respects `ack_policy` timeouts
- Handles repings (retries)
- Escalates on timeout
- Writes to ledger

### 3. receipt_watcher.py
**Purpose:** Monitors `received/` directories  
**Actions:**
- Detects when recipient moves envelope to `received/`
- Writes receipt to ledger
- Updates delivery tracker
- Notifies sender via OpenClaw session ping

### 4. ledger_ingest.py
**Purpose:** Ingests JSONL files into SQLite  
**Tables:**
- `deliveries` — All delivery events
- `receipts` — Acknowledgments
- `violations` — Protocol violations

---

## Acknowledgment Flow

```
1. Sender creates envelope → intake/pending/
2. Mailman delivers → recipient/inbox/
3. Recipient processes → moves to received/
4. Receipt watcher detects → writes receipt
5. Ledger updated → sender notified
```

---

## Schema Validation

**Required fields (all types):**
- `envelope_id`, `type`, `from`, `to`, `ts`
- `work_item_id`, `trust_plane`, `intent_class`, `risk`
- `subject`, `body`, `priority`

**Task-specific:**
- `task_type` — architecture-review, implementation, research, etc.

**Response-specific:**
- `parent_id`, `response_type`, `status`

---

## Trust Planes

| Plane | Purpose |
|-------|---------|
| `plane-a` | Operational, live system |
| `plane-b` | Canonical, curated reference |

---

## Intent Classes

- `propose` — Suggest action
- `investigate` — Request analysis
- `execute` — Direct command

---

## Agents in System

| Agent | Role |
|-------|------|
| `aya` | Main assistant (you) |
| `arbiter` | Architecture decisions |
| `haiku` | Delivery/mailman |
| `heru` | ??? |
| `jabari` | Builder/implementer |
| `kimi` | Curation (Plane B) |
| `tariq` | Observer/monitoring |

---

## SQLite Ledger Schema

```sql
-- Deliveries table
deliveries (
    id INTEGER PRIMARY KEY,
    event_type TEXT,
    ts TEXT,
    delivery_id TEXT,
    envelope_id TEXT,
    sender TEXT,
    recipient TEXT,
    work_item_id TEXT,
    ack_due_ts TEXT,
    raw_json TEXT
)

-- Receipts table
receipts (
    id INTEGER PRIMARY KEY,
    event_type TEXT,
    ts TEXT,
    envelope_id TEXT,
    receipt_id TEXT,
    receipt_type TEXT,
    from_agent TEXT,
    receiver TEXT,
    reason TEXT,
    raw_json TEXT
)
```

---

## Integration Points

1. **OpenClaw sessions** — `receipt_watcher.py` sends pings via `openclaw agent --message`
2. **QMD** — `qmd_ingest.py` processes Quarto documents
3. **LanceDB** — `promote_to_lancedb.py` for vector search
4. **SSE** — `sse_server.py` for real-time event streaming

---

## Comparison: Mailbox vs Envelope-Box

| Aspect | Mailbox | Envelope-Box |
|--------|---------|--------------|
| **Metaphor** | Email (inbox/outbox) | Postal (envelopes) |
| **Structure** | Per-agent directories | Global inbox/outbox |
| **Ledger** | SQLite + JSONL | Rust daily unifier |
| **Redaction** | No | Yes (daily unifier) |
| **Complexity** | Lower | Higher |
| **Performance** | Good | Better (Rust) |
| **Debuggability** | Easier (files) | Harder (Rust service) |

---

## Security Notes

- **No secrets found** in codebase (scanned 2026-03-15)
- Envelopes contain **task metadata**, not credentials
- SQLite has **operational data** only (sender, recipient, timestamps)
- Safe to push to public repo

---

_This is a well-designed agent communication system with clear contracts, audit trails, and email-style ergonomics._ ✊🏾
