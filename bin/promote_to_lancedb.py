#!/usr/bin/env python3
"""
promote_to_lancedb.py — Promote staged/reviewed docs from SQLite registry to LanceDB.

Phase 5b+5c of the memory pipeline:
  - Reads documents with status=staged or status=reviewed from registry.sqlite
  - Embeds chunks via GPU Nemotron at 127.0.0.1:8013 (2048-dim)
  - Writes to LanceDB canonical_chunks table with full canonical pointers
  - Updates document status to "promoted" in SQLite
  - Logs promotion event to ledger/deliveries.jsonl

5c guarantee: every LanceDB chunk has:
  - doc_id       (semantic knowledge ID)
  - doc_path     (absolute path to source file — LanceDB is index, files are truth)
  - chunk_index  (ordinal position in document)
  - trust_zone   (from document registry)

Usage:
  python3 promote_to_lancedb.py                  # promote all staged/reviewed
  python3 promote_to_lancedb.py --dry-run         # show what would be promoted
  python3 promote_to_lancedb.py --doc-id foo      # promote specific doc
  python3 promote_to_lancedb.py --status staged   # only staged docs
  python3 promote_to_lancedb.py --stats           # show promotion counts
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
WORKSPACE = Path("/home/netjer/.openclaw/workspace")
SQLITE_PATH = WORKSPACE / "plane-a/memory/registry.sqlite"
LANCEDB_PATH = WORKSPACE / "plane-b/lancedb/indexes"
LANCEDB_TABLE = "canonical_chunks"
LEDGER_PATH = WORKSPACE / "plane-a/mailbox/ledger/deliveries.jsonl"
GPU_EMBED_URL = "http://127.0.0.1:8013/embed"

# Promotion-eligible statuses
PROMOTABLE_STATUSES = {"staged", "reviewed"}
PROMOTED_STATUS = "promoted"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_ledger(record: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── GPU Embeddings ─────────────────────────────────────────────────────────────

def check_gpu_health() -> bool:
    try:
        with urllib.request.urlopen(GPU_EMBED_URL.replace("/embed", "/health"), timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed via GPU Nemotron :8013. Returns list of 2048-dim vectors."""
    if not texts:
        return []
    payload = json.dumps({"texts": texts, "mode": "document"}).encode()
    req = urllib.request.Request(
        GPU_EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            if "data" in result:
                return [item["embedding"] for item in result["data"]]
            if "embeddings" in result:
                return result["embeddings"]
            raise ValueError(f"Unexpected embed response format: {list(result.keys())}")
    except Exception as e:
        print(f"[promote] GPU embed error: {e}", file=sys.stderr)
        raise


# ── SQLite helpers ─────────────────────────────────────────────────────────────

def fetch_promotable_docs(conn: sqlite3.Connection, status_filter: set[str],
                          doc_id: str | None = None) -> list[dict]:
    """Fetch documents eligible for promotion."""
    placeholders = ",".join("?" * len(status_filter))
    params: list = list(status_filter)
    sql = f"SELECT doc_id, doc_type, trust_zone, status, doc_path, summary, canonical FROM documents WHERE status IN ({placeholders})"
    if doc_id:
        sql += " AND doc_id = ?"
        params.append(doc_id)
    rows = conn.execute(sql, params).fetchall()
    cols = ["doc_id", "doc_type", "trust_zone", "status", "doc_path", "summary", "canonical"]
    return [dict(zip(cols, row)) for row in rows]


def fetch_doc_chunks(conn: sqlite3.Connection, doc_id: str) -> list[dict]:
    """Fetch all chunks for a document from SQLite."""
    rows = conn.execute(
        "SELECT chunk_id, section_name, text_content, content_hash FROM document_chunks WHERE doc_id = ? ORDER BY chunk_id",
        (doc_id,)
    ).fetchall()
    return [
        {"chunk_id": r[0], "section_name": r[1], "text": r[2], "content_hash": r[3]}
        for r in rows
    ]


def update_doc_status(conn: sqlite3.Connection, doc_id: str, new_status: str) -> None:
    conn.execute(
        "UPDATE documents SET status = ?, updated_at = ? WHERE doc_id = ?",
        (new_status, now_iso(), doc_id)
    )
    conn.commit()


# ── LanceDB helpers ────────────────────────────────────────────────────────────

def ensure_chunk_index_column(table) -> bool:
    """Add chunk_index column to LanceDB table if missing. Returns True if added."""
    if "chunk_index" in table.schema.names:
        return False
    # LanceDB doesn't support ALTER TABLE — need to recreate via pandas
    try:
        import pyarrow as pa
        schema = table.schema
        new_field = pa.field("chunk_index", pa.int32())
        # Add column filled with -1 (unknown for legacy rows)
        table.add_columns({"chunk_index": "CAST(-1 AS INT)"},)
        return True
    except Exception as e:
        print(f"[promote] WARNING: could not add chunk_index column: {e}", file=sys.stderr)
        return False


def upsert_chunks_to_lancedb(table, chunks: list[dict]) -> int:
    """Delete existing doc chunks and insert new ones. Returns count written."""
    if not chunks:
        return 0
    doc_id = chunks[0]["doc_id"]

    # Only keep columns that exist in the table schema
    schema_cols = set(table.schema.names)
    filtered = [{k: v for k, v in chunk.items() if k in schema_cols} for chunk in chunks]

    try:
        table.delete(f"doc_id = '{doc_id}'")
    except Exception:
        pass
    table.add(filtered)
    return len(filtered)


# ── Promotion logic ────────────────────────────────────────────────────────────

def promote_document(doc: dict, conn: sqlite3.Connection, lancedb_table,
                     dry_run: bool = False) -> dict:
    """
    Promote one document from SQLite → LanceDB with embeddings.
    Returns result dict with status and counts.
    """
    doc_id = doc["doc_id"]
    result = {
        "doc_id": doc_id,
        "status": "ok",
        "chunks_written": 0,
        "errors": [],
    }

    # 1. Fetch chunks from SQLite
    chunks = fetch_doc_chunks(conn, doc_id)
    if not chunks:
        result["status"] = "skip"
        result["errors"].append("no chunks in SQLite — run qmd_ingest first")
        return result

    # 2. Short-circuit for dry run (before GPU call)
    if dry_run:
        result["chunks_written"] = len(chunks)
        result["status"] = "dry_run"
        return result

    # 3. Embed chunk texts via GPU Nemotron
    texts = [c["text"] for c in chunks]
    try:
        vectors = embed_texts(texts)
    except Exception as e:
        result["status"] = "embed_error"
        result["errors"].append(str(e))
        return result

    if len(vectors) != len(chunks):
        result["status"] = "embed_error"
        result["errors"].append(f"vector count mismatch: {len(vectors)} vs {len(chunks)} chunks")
        return result

    # 4. Build LanceDB records (5c: all canonical pointer fields present)
    source_path = doc.get("doc_path", "") or ""
    if source_path and not source_path.startswith("/"):
        source_path = str(WORKSPACE / source_path)

    lancedb_rows = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        lancedb_rows.append({
            "chunk_id": chunk["chunk_id"],
            "doc_id": doc_id,                         # 5c: semantic knowledge ID
            "doc_type": doc.get("doc_type", ""),
            "doc_path": source_path,                  # 5c: absolute path to source file
            "trust_zone": doc.get("trust_zone", ""),  # 5c: from document registry
            "status": PROMOTED_STATUS,
            "section_name": chunk["section_name"],
            "chunk_index": i,                         # 5c: ordinal position in document
            "text": chunk["text"],
            "tags": json.dumps([]),
            "version": "",
            "canonical": bool(doc.get("canonical", False)),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "content_hash": chunk["content_hash"] or hashlib.sha256(chunk["text"].encode()).hexdigest()[:16],
            "vector": vector,
            "promoted_at": now_iso(),
            "promotion_source": "promote_to_lancedb.py",
        })

    # 5. Write to LanceDB
    written = upsert_chunks_to_lancedb(lancedb_table, lancedb_rows)
    result["chunks_written"] = written

    # 6. Update SQLite status → promoted
    update_doc_status(conn, doc_id, PROMOTED_STATUS)

    # 7. Log promotion event to ledger
    append_ledger({
        "event_type": "PROMOTION_COMPLETED",
        "ts": now_iso(),
        "doc_id": doc_id,
        "doc_type": doc.get("doc_type"),
        "from_status": doc.get("status"),
        "to_status": PROMOTED_STATUS,
        "chunks_written": written,
        "trust_zone": doc.get("trust_zone"),
        "source_path": source_path,
        "promoter": "promote_to_lancedb.py",
    })

    return result


# ── Stats ──────────────────────────────────────────────────────────────────────

def cmd_stats(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT status, COUNT(*) FROM documents GROUP BY status ORDER BY COUNT(*) DESC"
    ).fetchall()
    print(f"{'Status':<20} {'Docs':>6}")
    print("-" * 28)
    for status, count in rows:
        print(f"{status:<20} {count:>6}")

    try:
        import lancedb
        db = lancedb.connect(str(LANCEDB_PATH))
        table = db.open_table(LANCEDB_TABLE)
        promoted_count = len(table.search().where("status = 'promoted'").limit(10000).to_list())
        print(f"\nLanceDB promoted chunks: {promoted_count}")
    except Exception:
        pass


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Promote staged docs to LanceDB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be promoted, no writes")
    parser.add_argument("--doc-id", help="Promote a specific doc_id only")
    parser.add_argument("--status", help="Comma-separated statuses to promote (default: staged,reviewed)")
    parser.add_argument("--stats", action="store_true", help="Show promotion stats")
    parser.add_argument("--sqlite", type=Path, default=SQLITE_PATH)
    parser.add_argument("--lancedb", type=Path, default=LANCEDB_PATH)
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.sqlite))

    if args.stats:
        cmd_stats(conn)
        conn.close()
        return 0

    status_filter = PROMOTABLE_STATUSES
    if args.status:
        status_filter = set(s.strip() for s in args.status.split(","))

    # Fetch candidates
    docs = fetch_promotable_docs(conn, status_filter, doc_id=args.doc_id)
    if not docs:
        print(f"No promotable documents found (status in {status_filter})")
        conn.close()
        return 0

    print(f"Found {len(docs)} promotable document(s):")
    for d in docs:
        print(f"  {d['doc_id']} [{d['status']} → {PROMOTED_STATUS}]")

    if not args.dry_run:
        # Check GPU health
        if not check_gpu_health():
            print("ERROR: GPU embed server :8013 not reachable", file=sys.stderr)
            conn.close()
            return 1

    # Open LanceDB
    try:
        import lancedb
    except ImportError:
        print("ERROR: lancedb not installed. Use brain-venv.", file=sys.stderr)
        conn.close()
        return 1

    db = lancedb.connect(str(args.lancedb))
    if LANCEDB_TABLE not in db.table_names():
        print(f"ERROR: LanceDB table '{LANCEDB_TABLE}' not found. Run qmd_ingest first.", file=sys.stderr)
        conn.close()
        return 1
    table = db.open_table(LANCEDB_TABLE)

    # Add chunk_index column if missing
    if not args.dry_run:
        ensure_chunk_index_column(table)

    # Promote each document
    ok = skipped = errors = total_chunks = 0
    for doc in docs:
        result = promote_document(doc, conn, table, dry_run=args.dry_run)
        total_chunks += result.get("chunks_written", 0)

        if result["status"] in ("ok", "dry_run"):
            ok += 1
            flag = "[DRY]" if result["status"] == "dry_run" else "✅"
            print(f"  {flag} {result['doc_id']}: {result['chunks_written']} chunks promoted")
        elif result["status"] == "skip":
            skipped += 1
            print(f"  ⏭  {result['doc_id']}: {result['errors']}")
        else:
            errors += 1
            print(f"  ❌ {result['doc_id']}: {result['errors']}", file=sys.stderr)

    conn.close()

    mode = "DRY RUN — " if args.dry_run else ""
    print(f"\n{mode}Promoted: {ok} docs, {total_chunks} chunks | skipped: {skipped} | errors: {errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
