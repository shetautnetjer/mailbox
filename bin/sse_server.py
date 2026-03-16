#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

from mailbox_core import MailboxPaths, ensure_mailbox_layout, now_iso

DEFAULT_MAILBOX = Path(__file__).resolve().parent.parent
TAIL_INTERVAL = 0.5
REPLAY_BUFFER = 1000


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter = 0
        self._buffer: deque[dict] = deque(maxlen=REPLAY_BUFFER)
        self._subscribers: list[queue.Queue] = []

    def publish(self, event_type: str, data: dict) -> int:
        with self._lock:
            self._counter += 1
            event = {"id": self._counter, "event": event_type, "data": data}
            self._buffer.append(event)
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass
            return self._counter

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def replay_from(self, last_id: int) -> list[dict]:
        with self._lock:
            return [e for e in self._buffer if e["id"] > last_id]

    @property
    def total(self) -> int:
        with self._lock:
            return self._counter

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._subscribers)


class SSEServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def derive_event_type(record: dict, file_path: Path) -> str:
    return str(record.get("event_type") or record.get("event") or file_path.stem).upper()


BUS = EventBus()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            self.handle_health()
            return
        if self.path.startswith("/events"):
            self.handle_events()
            return
        self.send_response(404)
        self.end_headers()

    def handle_health(self) -> None:
        body = json.dumps({"status": "ok", "events_total": BUS.total, "clients": BUS.clients, "ts": now_iso()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_events(self) -> None:
        try:
            last_id = int(self.headers.get("Last-Event-ID", "0"))
        except ValueError:
            last_id = 0

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = BUS.subscribe()
        try:
            for event in BUS.replay_from(last_id):
                self.write_event(event)
            last_keepalive = time.monotonic()
            while True:
                try:
                    event = q.get(timeout=1.0)
                    self.write_event(event)
                except queue.Empty:
                    pass
                if time.monotonic() - last_keepalive > 15:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_keepalive = time.monotonic()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            BUS.unsubscribe(q)

    def write_event(self, event: dict) -> None:
        payload = (
            f"id: {event['id']}\n"
            f"event: {event['event']}\n"
            f"data: {json.dumps(event['data'])}\n\n"
        ).encode()
        self.wfile.write(payload)
        self.wfile.flush()


def tail_files(paths: MailboxPaths) -> None:
    watch_files = [
        paths.deliveries_jsonl,
        paths.receipts_jsonl,
        paths.acks_jsonl,
        paths.violations_jsonl,
        paths.timeouts_jsonl,
        paths.repings_jsonl,
        paths.escalations_jsonl,
    ]
    positions: dict[Path, int] = {}
    while True:
        for file_path in watch_files:
            if not file_path.exists():
                continue
            size = file_path.stat().st_size
            pos = positions.get(file_path, size)
            if size < pos:
                pos = 0
            if size == pos:
                positions[file_path] = pos
                continue
            with file_path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                positions[file_path] = f.tell()
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                BUS.publish(derive_event_type(record, file_path), record)
        time.sleep(TAIL_INTERVAL)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mailbox SSE mirror")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8191)
    parser.add_argument("--mailbox-dir", type=Path, default=DEFAULT_MAILBOX)
    args = parser.parse_args()

    ensure_mailbox_layout(args.mailbox_dir)
    paths = MailboxPaths(args.mailbox_dir)
    threading.Thread(target=tail_files, args=(paths,), daemon=True).start()
    server = SSEServer((args.host, args.port), Handler)
    print(f"[sse_server] listening on {args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
