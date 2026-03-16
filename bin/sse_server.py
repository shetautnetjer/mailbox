#!/usr/bin/env python3
"""
sse_server.py — SSE telemetry event stream for the mailbox system.
Tails JSONL ledger files. Broadcasts events on 127.0.0.1:8191/events.

Layer 3 in the notification architecture:
  Layer 1: Mailbox  → carries commands
  Layer 2: Ledger   → records truth
  Layer 3: SSE      → broadcasts state changes  ← THIS FILE
  Layer 4: sessions_send → convenience nudge

GOLDEN RULE: SSE only announces what already happened.
  - NEVER carries commands
  - NEVER promotes truth
  - NEVER bypasses envelopes
  - Read-only mirror of the ledger. Nothing more.

Endpoints:
  GET /events          Server-Sent Events stream (text/event-stream)
  GET /health          {"status":"ok","events_total":N,"clients":N}

Replay:
  Clients send Last-Event-ID header to resume after disconnect.
  Rolling buffer of last 1000 events kept in memory.

Usage:
  python3 sse_server.py [--port 8191] [--host 127.0.0.1] [--mailbox /path/to/mailbox]
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Optional

DEFAULT_PORT = 8191
DEFAULT_HOST = "127.0.0.1"
DEFAULT_MAILBOX = Path("/home/netjer/.openclaw/workspace/plane-a/mailbox")

LEDGER_FILES = [
    "ledger/deliveries.jsonl",
    "ledger/receipts.jsonl",
    "ledger/timeouts/timeouts.jsonl",
    "ledger/repings/repings.jsonl",
    "ledger/escalations/escalations.jsonl",
    "ledger/violations.jsonl",
]

TAIL_INTERVAL = 0.5   # seconds between ledger polls
REPLAY_BUFFER = 1000  # max events kept for replay


# ── Global shared state ────────────────────────────────────────────────────────

class EventBus:
    """Thread-safe event bus with replay buffer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter = 0
        self._buffer: deque[dict] = deque(maxlen=REPLAY_BUFFER)
        self._subscribers: list[queue.Queue] = []

    def publish(self, event_type: str, data: dict) -> int:
        with self._lock:
            self._counter += 1
            event_id = self._counter
            event = {"id": event_id, "event": event_type, "data": data}
            self._buffer.append(event)
            for sub in self._subscribers:
                try:
                    sub.put_nowait(event)
                except queue.Full:
                    pass  # slow client — drop event, don't block
            return event_id

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def replay_from(self, last_id: int) -> list[dict]:
        """Return buffered events with id > last_id."""
        with self._lock:
            return [e for e in self._buffer if e["id"] > last_id]

    @property
    def total(self) -> int:
        with self._lock:
            return self._counter

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


# ── Ledger tailer ──────────────────────────────────────────────────────────────

def derive_event_type(record: dict, source_file: str) -> str:
    """Extract event type from a ledger record."""
    # Prefer explicit event_type field
    if "event_type" in record:
        return str(record["event_type"])
    # Fall back to event field (older format)
    if "event" in record:
        return str(record["event"]).upper()
    # Derive from source file
    name = Path(source_file).stem.upper()
    return name


def tail_ledger_files(mailbox: Path, bus: EventBus) -> None:
    """Background thread: tail all ledger files and publish new lines to bus."""
    file_positions: dict[str, int] = {}

    while True:
        for rel_path in LEDGER_FILES:
            ledger_path = mailbox / rel_path
            if not ledger_path.exists():
                continue

            try:
                stat = ledger_path.stat()
                pos = file_positions.get(str(ledger_path), stat.st_size)

                if stat.st_size < pos:
                    # File was truncated/rotated — reset
                    pos = 0

                if stat.st_size == pos:
                    file_positions[str(ledger_path)] = pos
                    continue

                with open(ledger_path, "r") as f:
                    f.seek(pos)
                    new_data = f.read()
                    file_positions[str(ledger_path)] = f.tell()

                for line in new_data.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        event_type = derive_event_type(record, rel_path)
                        bus.publish(event_type, record)
                    except json.JSONDecodeError:
                        pass

            except OSError:
                pass  # file temporarily unavailable — retry next cycle

        time.sleep(TAIL_INTERVAL)


# ── HTTP handler ───────────────────────────────────────────────────────────────

_bus: EventBus = EventBus()


class SSEHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args) -> None:
        # Suppress default HTTP logging to keep stderr clean
        pass

    def do_GET(self) -> None:
        if self.path == "/health" or self.path.startswith("/health?"):
            self._handle_health()
        elif self.path == "/events" or self.path.startswith("/events?"):
            self._handle_events()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def _handle_health(self) -> None:
        body = json.dumps({
            "status": "ok",
            "events_total": _bus.total,
            "clients": _bus.client_count,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_events(self) -> None:
        # Parse Last-Event-ID
        last_id_str = self.headers.get("Last-Event-ID", "0")
        try:
            last_id = int(last_id_str)
        except ValueError:
            last_id = 0

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = _bus.subscribe()

        try:
            # Replay buffered events after last_id
            for event in _bus.replay_from(last_id):
                self._write_event(event)

            # Send keepalive comment every 15s, stream new events
            last_keepalive = time.monotonic()
            while True:
                try:
                    event = q.get(timeout=1.0)
                    self._write_event(event)
                except queue.Empty:
                    pass

                now = time.monotonic()
                if now - last_keepalive > 15:
                    # SSE keepalive
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_keepalive = now

        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected
        finally:
            _bus.unsubscribe(q)

    def _write_event(self, event: dict) -> None:
        try:
            lines = [
                f"id: {event['id']}",
                f"event: {event['event']}",
                f"data: {json.dumps(event['data'])}",
                "",  # blank line = end of event
                "",
            ]
            payload = "\n".join(lines).encode()
            self.wfile.write(payload)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    global _bus

    parser = argparse.ArgumentParser(description="Mailbox SSE telemetry server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--mailbox", type=Path, default=DEFAULT_MAILBOX)
    args = parser.parse_args()

    _bus = EventBus()

    # Start ledger tailer in background
    tailer = threading.Thread(
        target=tail_ledger_files,
        args=(args.mailbox, _bus),
        daemon=True,
        name="ledger-tailer",
    )
    tailer.start()

    class ThreadingSSEServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadingSSEServer((args.host, args.port), SSEHandler)
    print(
        f"[sse_server] Listening on {args.host}:{args.port} | "
        f"mailbox: {args.mailbox}",
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
