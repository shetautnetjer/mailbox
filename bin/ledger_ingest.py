#!/usr/bin/env python3
"""
ledger_ingest.py — Mailbox ledger JSONL → SQLite ingestion.

Ingests all 6 mailbox ledger JSONL files into normalized SQLite tables.
Supports incremental mode, stats, and arbitrary SQL queries.

Output: plane-a/mailbox/ledger/ledger.sqlite

Usage:
  python3 ledger_ingest.py                        # full ingest
  python3 ledger_ingest.py --file deliveries.jsonl
  python3 ledger_ingest.py --incremental          # only new lines since last run
  python3 ledger_ingest.py --stats                # counts per table
  python3 ledger_ingest.py --query "SELECT * FROM violations ORDER BY ts DESC LIMIT 5"
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

MAILBOX = Path("/home/netjer/.openclaw/workspace/plane-a/mailbox")
LEDGER_DIR = MAILBOX / "ledger"
DB_PATH = LEDGER_DIR / "ledger.sqlite"
STATE_FILE = LEDGER_DIR / ".ingest_state.json"

LEDGER_FILES = [
    ("deliveries",  LEDGER_DIR / "deliveries.jsonl"),
    ("deliveries",  LEDGER_DIR / "timeouts" / "timeouts.jsonl"),
    ("deliveries",  LEDGER_DIR / "repings" / "repings.jsonl"),
    ("deliveries",  LEDGER_DIR / "escalations" / "escalations.jsonl"),
    ("receipts",    LEDGER_DIR / "receipts.jsonl"),
    ("violations",  LEDGER_DIR / "violations.jsonl"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Schema ─────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    ts TEXT NOT NULL,
    delivery_id TEXT,
    envelope_id TEXT,
    event_id TEXT,
    sender TEXT,
    recipient TEXT,
    work_item_id TEXT,
    ack_due_ts TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(envelope_id, ts, event_type)
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    ts TEXT NOT NULL,
    envelope_id TEXT,
    receipt_id TEXT,
    receipt_type TEXT,
    from_agent TEXT,
    receiver TEXT,
    reason TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(envelope_id, ts, event_type)
);

CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    ts TEXT NOT NULL,
    envelope_id TEXT,
    reason TEXT,
    sender TEXT,
    recipient TEXT,
    violation_type TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(envelope_id, ts, event)
);

CREATE INDEX IF NOT EXISTS idx_del_envelope_id ON deliveries(envelope_id);
CREATE INDEX IF NOT EXISTS idx_del_ts ON deliveries(ts);
CREATE INDEX IF NOT EXISTS idx_del_sender ON deliveries(sender);
CREATE INDEX IF NOT EXISTS idx_del_recipient ON deliveries(recipient);
CREATE INDEX IF NOT EXISTS idx_del_work_item ON deliveries(work_item_id);
CREATE INDEX IF NOT EXISTS idx_del_event_type ON deliveries(event_type);
CREATE INDEX IF NOT EXISTS idx_rcpt_envelope_id ON receipts(envelope_id);
CREATE INDEX IF NOT EXISTS idx_rcpt_ts ON receipts(ts);
CREATE INDEX IF NOT EXISTS idx_viol_envelope_id ON violations(envelope_id);
CREATE INDEX IF NOT EXISTS idx_viol_ts ON violations(ts);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


# ── Insertion helpers ──────────────────────────────────────────────────────────

def insert_delivery(conn: sqlite3.Connection, rec: dict) -> bool:
    """Returns True if inserted, False if duplicate."""
    event_type = (
        rec.get("event_type")
        or rec.get("event")
        or "UNKNOWN"
    )
    ts = rec.get("ts", "")
    try:
        conn.execute("""
            INSERT OR IGNORE INTO deliveries
            (event_type, ts, delivery_id, envelope_id, event_id,
             sender, recipient, work_item_id, ack_due_ts, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            event_type, ts,
            rec.get("delivery_id"),
            rec.get("envelope_id"),
            rec.get("event_id"),
            rec.get("sender") or rec.get("from"),
            rec.get("recipient") or rec.get("to"),
            rec.get("work_item_id"),
            rec.get("ack_due_ts"),
            json.dumps(rec),
        ))
        return conn.execute("SELECT changes()").fetchone()[0] > 0
    except sqlite3.Error as e:
        print(f"[ledger_ingest] delivery insert error: {e}", file=sys.stderr)
        return False


def insert_receipt(conn: sqlite3.Connection, rec: dict) -> bool:
    event_type = rec.get("event_type") or rec.get("event") or "RECEIPT"
    ts = rec.get("ts") or rec.get("received_ts", "")
    try:
        conn.execute("""
            INSERT OR IGNORE INTO receipts
            (event_type, ts, envelope_id, receipt_id, receipt_type,
             from_agent, receiver, reason, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            event_type, ts,
            rec.get("envelope_id"),
            rec.get("receipt_id"),
            rec.get("receipt_type"),
            rec.get("from_agent") or rec.get("from"),
            rec.get("receiver"),
            rec.get("reason", ""),
            json.dumps(rec),
        ))
        return conn.execute("SELECT changes()").fetchone()[0] > 0
    except sqlite3.Error as e:
        print(f"[ledger_ingest] receipt insert error: {e}", file=sys.stderr)
        return False


def insert_violation(conn: sqlite3.Connection, rec: dict) -> bool:
    event = rec.get("event") or rec.get("event_type") or "quarantined"
    ts = rec.get("ts", "")
    try:
        conn.execute("""
            INSERT OR IGNORE INTO violations
            (event, ts, envelope_id, reason, sender, recipient, violation_type, raw_json)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            event, ts,
            rec.get("envelope_id"),
            rec.get("reason", ""),
            rec.get("from") or rec.get("sender"),
            rec.get("to") or rec.get("recipient"),
            rec.get("violation_type"),
            json.dumps(rec),
        ))
        return conn.execute("SELECT changes()").fetchone()[0] > 0
    except sqlite3.Error as e:
        print(f"[ledger_ingest] violation insert error: {e}", file=sys.stderr)
        return False


# ── State tracking for incremental mode ───────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Core ingest logic ──────────────────────────────────────────────────────────

def ingest_file(conn: sqlite3.Connection, table: str, path: Path,
                start_pos: int = 0) -> tuple[int, int]:
    """
    Ingest a JSONL file into the specified table.
    Returns (inserted_count, new_file_position).
    """
    if not path.exists():
        return 0, 0

    inserted = 0
    pos = start_pos

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(pos)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if table == "deliveries":
                if insert_delivery(conn, rec):
                    inserted += 1
            elif table == "receipts":
                if insert_receipt(conn, rec):
                    inserted += 1
            elif table == "violations":
                if insert_violation(conn, rec):
                    inserted += 1

        pos = f.tell()

    conn.commit()
    return inserted, pos


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_ingest(conn: sqlite3.Connection, files: list[tuple[str, Path]],
               incremental: bool) -> dict:
    state = load_state() if incremental else {}
    results = {}
    new_state = {}

    for table, path in files:
        key = str(path)
        start_pos = state.get(key, 0) if incremental else 0
        inserted, new_pos = ingest_file(conn, table, path, start_pos)
        new_state[key] = new_pos
        label = path.name
        results[label] = inserted
        if inserted > 0:
            print(f"  {label}: +{inserted} rows")

    save_state({**state, **new_state})
    return results


def cmd_stats(conn: sqlite3.Connection) -> None:
    tables = ["deliveries", "receipts", "violations"]
    print(f"{'Table':<20} {'Rows':>8}")
    print("-" * 30)
    for t in tables:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            count = "N/A"
        print(f"{t:<20} {count:>8}")

    # Breakdown by event_type for deliveries
    print("\nDeliveries by event_type:")
    try:
        rows = conn.execute(
            "SELECT event_type, COUNT(*) FROM deliveries GROUP BY event_type ORDER BY COUNT(*) DESC"
        ).fetchall()
        for row in rows:
            print(f"  {row[0]:<35} {row[1]:>6}")
    except sqlite3.Error:
        pass


def cmd_query(conn: sqlite3.Connection, sql: str) -> None:
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        if cols:
            print("\t".join(cols))
            print("-" * (len("\t".join(cols)) + 10))
        for row in rows:
            print("\t".join(str(v) if v is not None else "" for v in row))
        print(f"\n({len(rows)} rows)")
    except sqlite3.Error as e:
        print(f"Query error: {e}", file=sys.stderr)
        sys.exit(1)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Ledger JSONL → SQLite ingestion")
    parser.add_argument("--file", help="Specific JSONL filename (e.g. deliveries.jsonl)")
    parser.add_argument("--incremental", action="store_true",
                        help="Only ingest new lines since last run")
    parser.add_argument("--stats", action="store_true",
                        help="Print counts per table and exit")
    parser.add_argument("--query", help="Run SQL query and print results")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite path override")
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.db))
    ensure_schema(conn)

    if args.stats:
        cmd_stats(conn)
        conn.close()
        return 0

    if args.query:
        cmd_query(conn, args.query)
        conn.close()
        return 0

    # Select files to ingest
    if args.file:
        # Find which table to use
        target = args.file.lower()
        matched = [(t, p) for t, p in LEDGER_FILES if p.name == target or str(p).endswith(args.file)]
        if not matched:
            print(f"File not found in ledger: {args.file}", file=sys.stderr)
            conn.close()
            return 1
        files = matched
    else:
        files = LEDGER_FILES

    mode = "incremental" if args.incremental else "full"
    print(f"Ledger ingest ({mode}) → {args.db}")
    results = cmd_ingest(conn, files, args.incremental)
    total = sum(results.values())
    print(f"Total inserted: {total} rows across {len(results)} files")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
