#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from mailbox_core import MailboxPaths, ensure_mailbox_layout

DEFAULT_MAILBOX = Path(__file__).resolve().parent.parent

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
    UNIQUE(envelope_id, ts, event_type, recipient)
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    ts TEXT NOT NULL,
    envelope_id TEXT,
    receipt_id TEXT,
    from_agent TEXT,
    receiver TEXT,
    work_item_id TEXT,
    kind TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(envelope_id, ts, event_type, receiver)
);

CREATE TABLE IF NOT EXISTS acks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    ts TEXT NOT NULL,
    ack_id TEXT,
    envelope_id TEXT,
    receiver TEXT,
    status TEXT,
    reason TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(envelope_id, ts, event_type, receiver)
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
    UNIQUE(envelope_id, ts, event, violation_type)
);

CREATE INDEX IF NOT EXISTS idx_del_envelope_id ON deliveries(envelope_id);
CREATE INDEX IF NOT EXISTS idx_del_work_item ON deliveries(work_item_id);
CREATE INDEX IF NOT EXISTS idx_del_ts ON deliveries(ts);
CREATE INDEX IF NOT EXISTS idx_del_sender ON deliveries(sender);
CREATE INDEX IF NOT EXISTS idx_del_recipient ON deliveries(recipient);
CREATE INDEX IF NOT EXISTS idx_rcpt_envelope_id ON receipts(envelope_id);
CREATE INDEX IF NOT EXISTS idx_ack_envelope_id ON acks(envelope_id);
CREATE INDEX IF NOT EXISTS idx_viol_envelope_id ON violations(envelope_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def insert_delivery(conn: sqlite3.Connection, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO deliveries
        (event_type, ts, delivery_id, envelope_id, event_id, sender, recipient, work_item_id, ack_due_ts, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.get("event_type") or rec.get("event") or "UNKNOWN",
            rec.get("ts", ""),
            rec.get("delivery_id"),
            rec.get("envelope_id"),
            rec.get("event_id"),
            rec.get("sender") or rec.get("from"),
            rec.get("recipient") or rec.get("to"),
            rec.get("work_item_id"),
            rec.get("ack_due_ts"),
            json.dumps(rec),
        ),
    )


def insert_receipt(conn: sqlite3.Connection, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO receipts
        (event_type, ts, envelope_id, receipt_id, from_agent, receiver, work_item_id, kind, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.get("event_type") or rec.get("event") or "DELIVERY_RECEIPT",
            rec.get("ts", ""),
            rec.get("envelope_id"),
            rec.get("receipt_id"),
            rec.get("from_agent"),
            rec.get("receiver"),
            rec.get("work_item_id"),
            rec.get("kind"),
            json.dumps(rec),
        ),
    )


def insert_ack(conn: sqlite3.Connection, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO acks
        (event_type, ts, ack_id, envelope_id, receiver, status, reason, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.get("event_type") or rec.get("event") or "ACK_CONFIRMED",
            rec.get("ts", ""),
            rec.get("ack_id"),
            rec.get("envelope_id"),
            rec.get("receiver"),
            rec.get("status"),
            rec.get("reason"),
            json.dumps(rec),
        ),
    )


def insert_violation(conn: sqlite3.Connection, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO violations
        (event, ts, envelope_id, reason, sender, recipient, violation_type, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.get("event") or rec.get("event_type") or "VIOLATION",
            rec.get("ts", ""),
            rec.get("envelope_id"),
            rec.get("reason", ""),
            rec.get("sender") or rec.get("from"),
            rec.get("recipient") or rec.get("to"),
            rec.get("violation_type"),
            json.dumps(rec),
        ),
    )


def ingest_jsonl(conn: sqlite3.Connection, file_path: Path, kind: str) -> int:
    if not file_path.exists():
        return 0
    inserted_before = conn.total_changes
    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kind == "deliveries":
                insert_delivery(conn, rec)
            elif kind == "receipts":
                insert_receipt(conn, rec)
            elif kind == "acks":
                insert_ack(conn, rec)
            elif kind == "violations":
                insert_violation(conn, rec)
    conn.commit()
    return conn.total_changes - inserted_before


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest mailbox JSONL ledgers into SQLite")
    parser.add_argument("--mailbox-dir", type=Path, default=DEFAULT_MAILBOX)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--query")
    args = parser.parse_args()

    ensure_mailbox_layout(args.mailbox_dir)
    paths = MailboxPaths(args.mailbox_dir)
    db_path = args.db or (paths.ledger / "ledger.sqlite")
    conn = sqlite3.connect(str(db_path))
    ensure_schema(conn)

    if args.query:
        cur = conn.execute(args.query)
        cols = [d[0] for d in cur.description] if cur.description else []
        if cols:
            print("\t".join(cols))
        for row in cur.fetchall():
            print("\t".join(str(x) if x is not None else "" for x in row))
        conn.close()
        return 0

    if args.stats:
        for table in ["deliveries", "receipts", "acks", "violations"]:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {count}")
        conn.close()
        return 0

    total = 0
    total += ingest_jsonl(conn, paths.deliveries_jsonl, "deliveries")
    total += ingest_jsonl(conn, paths.timeouts_jsonl, "deliveries")
    total += ingest_jsonl(conn, paths.repings_jsonl, "deliveries")
    total += ingest_jsonl(conn, paths.escalations_jsonl, "deliveries")
    total += ingest_jsonl(conn, paths.receipts_jsonl, "receipts")
    total += ingest_jsonl(conn, paths.acks_jsonl, "acks")
    total += ingest_jsonl(conn, paths.violations_jsonl, "violations")
    print(f"Inserted or updated {total} rows into {db_path}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
