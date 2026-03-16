#!/usr/bin/env python3
"""
qmd_ingest.py — QMD ingestion pipeline for Plane A/B memory system.

Reads .qmd files, parses YAML frontmatter + markdown body sections,
writes to:
  - SQLite registry (plane-a/memory/registry.sqlite)
  - LanceDB canonical chunks (plane-b/lancedb/indexes/)

Usage:
  python3 qmd_ingest.py --dir /path/to/qmd/dir [--plane-a] [--plane-b] [--embed]
  python3 qmd_ingest.py --file /path/to/file.qmd [--plane-a] [--plane-b] [--embed]
  python3 qmd_ingest.py --dir /path --plane-a --plane-b --embed --dry-run

Flags:
  --plane-a       Write to SQLite registry (documents, tags, aliases, links, chunks)
  --plane-b       Write to LanceDB canonical_chunks
  --embed         Generate vector embeddings (requires Qwen3-Embedding-4B model)
  --dry-run       Parse + validate only, no writes
  --overwrite     Re-ingest existing doc_ids (default: skip)
  --json          Output JSON result summary
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Use brain-venv.", file=sys.stderr)
    sys.exit(1)

# ── Defaults ──────────────────────────────────────────────────────────────────
WORKSPACE = Path("/home/netjer/.openclaw/workspace")
SQLITE_PATH = WORKSPACE / "plane-a/memory/registry.sqlite"
LANCEDB_PATH = WORKSPACE / "plane-b/lancedb/indexes"
LANCEDB_TABLE = "canonical_chunks"
MODEL_PATH = Path("/home/netjer/.node-llama-cpp/models")
EMBED_MODEL = "Qwen3-Embedding-4B"  # will search for matching .gguf
GPU_EMBED_URL = "http://127.0.0.1:8013/embed"  # Nemotron 2048-dim

VALID_DOC_TYPES = {
    "policy", "schema", "research_packet", "episode_record",
    "promotion_packet", "design_decision", "bug_fix_record",
    "replay_summary", "handoff_note", "canon_event",
}
VALID_TRUST_ZONES = {
    "local", "shared_raw", "shared_library", "shared_staged",
    "canonical", "archived",
}
VALID_STATUSES = {
    "draft", "under_review", "reviewed", "active", "staged",
    "promoted", "superseded", "rejected", "archived",
}


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_qmd(path: Path) -> tuple[dict, str]:
    """Parse QMD file → (frontmatter dict, body string)."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path}: no YAML frontmatter (expected '---' at start)")

    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{path}: malformed frontmatter (need opening + closing '---')")

    fm = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return fm, body


def parse_md(path: Path) -> tuple[dict, str]:
    """
    Parse a plain .md file with no QMD frontmatter.
    Generates minimal synthetic frontmatter from filename + path context.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    # Derive doc_id from filename
    stem = path.stem.lower()
    doc_id = re.sub(r"[^a-z0-9_-]", "-", stem).strip("-")

    # Guess doc_type from path/name
    name_lower = stem.lower()
    if "schema" in name_lower or "spec" in name_lower:
        doc_type = "schema"
    elif "policy" in name_lower or "doctrine" in name_lower:
        doc_type = "policy"
    elif "episode" in name_lower or "bug" in name_lower:
        doc_type = "episode_record"
    elif "checklist" in name_lower or "plan" in name_lower or "phase" in name_lower:
        doc_type = "design_decision"
    elif "migration" in name_lower:
        doc_type = "design_decision"
    else:
        doc_type = "design_decision"

    # Extract title from first H1
    title = doc_id
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # Tags from path components
    tags = []
    for part in path.parts:
        part_clean = re.sub(r"[^a-z0-9-]", "", part.lower())
        if part_clean and len(part_clean) > 2:
            tags.append(part_clean)
    tags = list(dict.fromkeys(tags))[:10]  # dedup, cap at 10

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fm = {
        "doc_id": doc_id,
        "doc_type": doc_type,
        "trust_zone": "shared_library",
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "tags": tags,
        "canonical": False,
        "summary": title,
        "agent_owner": "",
        "related_docs": [],
        "aliases": [],
        "source_events": [],
        "_synthetic": True,  # flag: no real frontmatter
    }
    return fm, text


def generate_gpu_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate embeddings via GPU Nemotron server at :8013. Returns list of vectors."""
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            # OpenAI-compatible format: {"data": [{"embedding": [...]}]}
            if "data" in result:
                return [item["embedding"] for item in result["data"]]
            # Direct format: {"embeddings": [[...]]}
            if "embeddings" in result:
                return result["embeddings"]
            print(f"[embed] WARNING: unexpected response format: {list(result.keys())}", file=sys.stderr)
            return [[] for _ in texts]
    except Exception as e:
        print(f"[embed] ERROR calling GPU embed: {e}", file=sys.stderr)
        return [[] for _ in texts]


def validate_frontmatter(fm: dict, path: Path) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errors = []
    required = ["doc_id", "doc_type", "trust_zone", "status", "created_at", "updated_at", "tags", "canonical"]
    for field in required:
        if field not in fm:
            errors.append(f"missing required field: {field}")

    if "doc_type" in fm and fm["doc_type"] not in VALID_DOC_TYPES:
        errors.append(f"invalid doc_type '{fm['doc_type']}' (valid: {sorted(VALID_DOC_TYPES)})")
    if "trust_zone" in fm and fm["trust_zone"] not in VALID_TRUST_ZONES:
        errors.append(f"invalid trust_zone '{fm['trust_zone']}'")
    if "status" in fm and fm["status"] not in VALID_STATUSES:
        errors.append(f"invalid status '{fm['status']}'")
    return errors


def chunk_body(body: str, doc_id: str, fm: dict) -> list[dict]:
    """Split body into sections. Each ## heading = one chunk."""
    chunks = []
    current_section = "__preamble__"
    current_lines: list[str] = []

    def flush(section: str, lines: list[str]) -> None:
        text = "\n".join(lines).strip()
        if not text:
            return
        content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        chunk_id = f"{doc_id}::{section.lower().replace(' ', '_')}"
        chunks.append({
            "chunk_id": chunk_id,
            "doc_id": doc_id,
            "doc_type": fm.get("doc_type", ""),
            "doc_path": "",  # filled in by caller
            "trust_zone": fm.get("trust_zone", ""),
            "status": fm.get("status", ""),
            "section_name": section,
            "text": text,
            "tags": json.dumps(fm.get("tags", [])),
            "version": str(fm.get("version", "")),
            "canonical": bool(fm.get("canonical", False)),
            "created_at": str(fm.get("created_at", "")),
            "updated_at": str(fm.get("updated_at", "")),
            "content_hash": content_hash,
            "chunk_index": len(chunks),
        })

    for line in body.splitlines():
        if line.startswith("## "):
            flush(current_section, current_lines)
            current_section = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush(current_section, current_lines)
    return chunks


# ── SQLite ────────────────────────────────────────────────────────────────────

def ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            doc_path TEXT NOT NULL DEFAULT '',
            doc_type TEXT NOT NULL DEFAULT '',
            trust_zone TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            version TEXT,
            canonical INTEGER NOT NULL DEFAULT 0,
            agent_owner TEXT,
            project TEXT,
            summary TEXT,
            content_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            indexed_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS document_tags (
            doc_id TEXT, tag TEXT,
            PRIMARY KEY (doc_id, tag)
        );
        CREATE TABLE IF NOT EXISTS document_aliases (
            doc_id TEXT, alias TEXT,
            PRIMARY KEY (doc_id, alias)
        );
        CREATE TABLE IF NOT EXISTS document_links (
            src_doc_id TEXT, rel_type TEXT, dst_ref TEXT,
            PRIMARY KEY (src_doc_id, rel_type, dst_ref)
        );
        CREATE TABLE IF NOT EXISTS document_chunks (
            chunk_id TEXT PRIMARY KEY, doc_id TEXT,
            section_name TEXT, chunk_index INTEGER, text_content TEXT, content_hash TEXT
        );
        CREATE TABLE IF NOT EXISTS replay_artifact_links (
            doc_id TEXT, source_event_id TEXT, link_type TEXT,
            PRIMARY KEY (doc_id, source_event_id, link_type)
        );
    """)
    # Add any missing columns to existing tables (migration)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "indexed_at" not in existing_cols:
        conn.execute("ALTER TABLE documents ADD COLUMN indexed_at TEXT NOT NULL DEFAULT (datetime('now'))")
    conn.commit()


def upsert_sqlite(conn: sqlite3.Connection, fm: dict, chunks: list[dict],
                  path: Path, content_hash: str, overwrite: bool) -> str:
    """Write document + metadata to SQLite. Returns 'inserted'|'updated'|'skipped'."""
    doc_id = fm["doc_id"]
    existing = conn.execute("SELECT doc_id FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    if existing and not overwrite:
        return "skipped"

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute("""
        INSERT OR REPLACE INTO documents
        (doc_id, doc_type, trust_zone, status, created_at, updated_at, indexed_at,
         version, agent_owner, project, summary, canonical, doc_path, content_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        doc_id, fm.get("doc_type"), fm.get("trust_zone"), fm.get("status"),
        str(fm.get("created_at", "")), str(fm.get("updated_at", "")), now,
        str(fm.get("version", "")), fm.get("agent_owner"), fm.get("project"),
        fm.get("summary"), int(bool(fm.get("canonical", False))),
        str(path), content_hash,
    ))

    # Tags
    conn.execute("DELETE FROM document_tags WHERE doc_id=?", (doc_id,))
    for tag in fm.get("tags", []):
        conn.execute("INSERT OR IGNORE INTO document_tags (doc_id, tag) VALUES (?,?)", (doc_id, tag))

    # Aliases
    conn.execute("DELETE FROM document_aliases WHERE doc_id=?", (doc_id,))
    for alias in fm.get("aliases", []):
        conn.execute("INSERT OR IGNORE INTO document_aliases (doc_id, alias) VALUES (?,?)", (doc_id, alias))

    # Related docs links (existing schema: src_doc_id, rel_type, dst_ref)
    conn.execute("DELETE FROM document_links WHERE src_doc_id=?", (doc_id,))
    for rel in fm.get("related_docs", []):
        conn.execute(
            "INSERT OR IGNORE INTO document_links (src_doc_id, rel_type, dst_ref) VALUES (?,?,?)",
            (doc_id, "related", rel)
        )

    # Source event links (existing schema: doc_id, source_event_id, link_type)
    conn.execute("DELETE FROM replay_artifact_links WHERE doc_id=?", (doc_id,))
    for evt in fm.get("source_events", []):
        conn.execute(
            "INSERT OR IGNORE INTO replay_artifact_links (doc_id, source_event_id, link_type) VALUES (?,?,?)",
            (doc_id, evt, "source_event")
        )

    # Chunks (existing schema: chunk_id, doc_id, section_name, chunk_index, text_content, content_hash)
    conn.execute("DELETE FROM document_chunks WHERE doc_id=?", (doc_id,))
    for i, chunk in enumerate(chunks):
        conn.execute("""
            INSERT OR REPLACE INTO document_chunks
            (chunk_id, doc_id, section_name, chunk_index, text_content, content_hash)
            VALUES (?,?,?,?,?,?)
        """, (chunk["chunk_id"], doc_id, chunk["section_name"],
              i, chunk["text"], chunk["content_hash"]))

    conn.commit()
    return "updated" if existing else "inserted"


# ── LanceDB ───────────────────────────────────────────────────────────────────

def upsert_lancedb(chunks: list[dict], path: Path, overwrite: bool,
                   embed: bool = False) -> tuple[str, int]:
    """Write chunks to LanceDB. Returns (status, count)."""
    try:
        import lancedb
    except ImportError:
        return "error:lancedb_not_installed", 0

    db = lancedb.connect(str(LANCEDB_PATH))

    # Set doc_path in chunks
    for c in chunks:
        c["doc_path"] = str(path)

    if embed:
        vectors = generate_embeddings([c["text"] for c in chunks])
        for i, c in enumerate(chunks):
            c["vector"] = vectors[i] if i < len(vectors) else []

    if LANCEDB_TABLE not in db.table_names():
        table = db.create_table(LANCEDB_TABLE, chunks)
        return "created", len(chunks)

    table = db.open_table(LANCEDB_TABLE)
    doc_id = chunks[0]["doc_id"] if chunks else None

    if doc_id:
        try:
            table.delete(f"doc_id = '{doc_id}'")
        except Exception:
            pass

    table.add(chunks)
    return "upserted", len(chunks)


# ── Embeddings ────────────────────────────────────────────────────────────────

def find_embed_model() -> Path | None:
    """Find Qwen3 embedding GGUF in model dir."""
    if not MODEL_PATH.exists():
        return None
    for f in MODEL_PATH.glob("*.gguf"):
        if "qwen3" in f.name.lower() and "embed" in f.name.lower():
            return f
        if "embedding" in f.name.lower():
            return f
    return None


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate embeddings via Nemotron GPU server (:8013) or fallback to llama-cpp-python."""
    import urllib.request
    # Try GPU endpoint first (Nemotron-Embed-VL-1B on :8013)
    try:
        payload = json.dumps({"texts": texts, "mode": "document"}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8013/embed",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            vectors = result.get("embeddings", [])
            if vectors and len(vectors) == len(texts):
                print(f"[embed] GPU Nemotron: {len(vectors)} vectors, dim={len(vectors[0])}", file=sys.stderr)
                return vectors
    except Exception as e:
        print(f"[embed] GPU endpoint unavailable ({e}), trying CPU fallback...", file=sys.stderr)

    # Fallback to llama-cpp-python
    try:
        from llama_cpp import Llama
        model_path = find_embed_model()
        if not model_path:
            print("[embed] WARNING: No embedding model found, skipping vectors", file=sys.stderr)
            return [[] for _ in texts]
        llm = Llama(model_path=str(model_path), embedding=True, verbose=False, n_ctx=512)
        vectors = []
        for text in texts:
            result = llm.create_embedding(text[:1024])
            vec = result["data"][0]["embedding"]
            vectors.append(vec)
        return vectors
    except ImportError:
        print("[embed] WARNING: llama-cpp-python not available, skipping vectors", file=sys.stderr)
        return [[] for _ in texts]
    except Exception as e:
        print(f"[embed] ERROR: {e}", file=sys.stderr)
        return [[] for _ in texts]


# ── Main ──────────────────────────────────────────────────────────────────────

def ingest_file(path: Path, sqlite_conn: sqlite3.Connection | None,
                plane_b: bool, embed: bool, overwrite: bool,
                dry_run: bool) -> dict:
    """Ingest one QMD or MD file. Returns result dict."""
    result: dict[str, Any] = {"path": str(path), "status": "ok", "errors": [], "actions": []}

    # Auto-detect: .md with no frontmatter → synthetic FM
    is_md = path.suffix.lower() == ".md"
    is_json = path.suffix.lower() == ".json"

    if is_json:
        # JSON schemas: wrap as schema doc
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            stem = path.stem.lower()
            doc_id = re.sub(r"[^a-z0-9_-]", "-", stem).strip("-")
            fm = {
                "doc_id": doc_id,
                "doc_type": "schema",
                "trust_zone": "shared_library",
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tags": ["schema", "json"],
                "canonical": False,
                "summary": doc_id,
                "agent_owner": "",
                "related_docs": [],
                "aliases": [],
                "source_events": [],
                "_synthetic": True,
            }
            body = raw
        except Exception as e:
            result["status"] = "parse_error"
            result["errors"].append(str(e))
            return result
    elif is_md:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if text.startswith("---"):
                # Has QMD frontmatter — parse normally
                fm, body = parse_qmd(path)
                is_md = False  # treat as QMD
            else:
                fm, body = parse_md(path)
        except Exception as e:
            result["status"] = "parse_error"
            result["errors"].append(str(e))
            return result
    else:
        try:
            fm, body = parse_qmd(path)
        except Exception as e:
            result["status"] = "parse_error"
            result["errors"].append(str(e))
            return result

    # Only validate frontmatter for real QMD files (not synthetic)
    if not fm.get("_synthetic"):
        errors = validate_frontmatter(fm, path)
        if errors:
            result["status"] = "validation_error"
            result["errors"] = errors
            return result

    result["doc_id"] = fm["doc_id"]
    result["doc_type"] = fm.get("doc_type")

    body_hash = hashlib.sha256(body.encode()).hexdigest()[:16]
    chunks = chunk_body(body, fm["doc_id"], fm)

    if dry_run:
        result["actions"].append(f"dry_run: would write {len(chunks)} chunks")
        result["chunk_count"] = len(chunks)
        return result

    # Plane A — SQLite
    if sqlite_conn is not None:
        sqlite_status = upsert_sqlite(sqlite_conn, fm, chunks, path, body_hash, overwrite)
        result["actions"].append(f"sqlite:{sqlite_status}")

    # Plane B — LanceDB
    if plane_b:
        lb_status, lb_count = upsert_lancedb(chunks, path, overwrite, embed=embed)
        result["actions"].append(f"lancedb:{lb_status}:{lb_count}")

    result["chunk_count"] = len(chunks)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="QMD ingestion pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", type=Path, help="Directory to scan for .qmd files")
    group.add_argument("--file", type=Path, help="Single .qmd file to ingest")
    parser.add_argument("--plane-a", action="store_true", help="Write to SQLite registry")
    parser.add_argument("--plane-b", action="store_true", help="Write to LanceDB")
    parser.add_argument("--embed", action="store_true", help="Generate vector embeddings")
    parser.add_argument("--dry-run", action="store_true", help="Parse+validate only, no writes")
    parser.add_argument("--overwrite", action="store_true", help="Re-ingest existing doc_ids")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    parser.add_argument("--sqlite", type=Path, default=SQLITE_PATH, help="SQLite path override")
    parser.add_argument("--lancedb", type=Path, default=LANCEDB_PATH, help="LanceDB path override")
    parser.add_argument("--md", action="store_true", help="Also index .md and .json files (not just .qmd)")
    args = parser.parse_args()

    # Collect files
    if args.file:
        files = [args.file]
    else:
        files = sorted(args.dir.rglob("*.qmd"))
        if args.md:
            md_files = sorted(args.dir.rglob("*.md"))
            json_files = sorted(args.dir.rglob("*.json"))
            files = sorted(files + md_files + json_files, key=lambda p: p.name)

    if not files:
        print(f"No .qmd files found", file=sys.stderr)
        return 0

    # Open SQLite
    sqlite_conn = None
    if args.plane_a and not args.dry_run:
        args.sqlite.parent.mkdir(parents=True, exist_ok=True)
        sqlite_conn = sqlite3.connect(str(args.sqlite))
        ensure_sqlite_schema(sqlite_conn)

    results = []
    ok = skipped = errors = 0
    for path in files:
        r = ingest_file(path, sqlite_conn, args.plane_b, args.embed, args.overwrite, args.dry_run)
        results.append(r)
        if r["status"] == "ok":
            ok += 1
        elif r["status"] in ("validation_error", "parse_error"):
            errors += 1
            if not args.json:
                print(f"ERROR {path}: {r['errors']}", file=sys.stderr)
        if "sqlite:skipped" in r.get("actions", []):
            skipped += 1

    if sqlite_conn:
        sqlite_conn.close()

    summary = {
        "total": len(files),
        "ok": ok,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Ingested: {ok}/{len(files)} files | skipped: {skipped} | errors: {errors}")
        for r in results:
            if r["status"] == "ok":
                actions = " ".join(r.get("actions", []))
                print(f"  ✅ {r.get('doc_id', Path(r['path']).stem)} [{r.get('chunk_count',0)} chunks] {actions}")
            elif r["status"] == "validation_error":
                print(f"  ❌ {Path(r['path']).name}: {', '.join(r['errors'])}")
            elif r["status"] == "parse_error":
                print(f"  ⚠️  {Path(r['path']).name}: {r['errors']}")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
