#!/usr/bin/env python3
"""
receipt_watcher.py — Watches received/ dirs, writes receipts, resolves trackers.
Contract: workspace-jabari/contracts/receipt_schema_v1.md
          workspace-jabari/contracts/delivery_tracker_schema_v1.md

Patches 5+6: When a receipt is detected, find the delivery tracker by envelope_id,
update ack_status (acked or rejected), and notify sender if configured.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_MAILBOX = HERE.parent
OC_BIN = "/home/netjer/.npm-global/bin/openclaw"

# UUIDv7 for receipt_id
try:
    sys.path.insert(0, str(HERE))
    from uuid7_util import gen_uuidv7 as _gen_uuidv7
    def _make_receipt_id() -> str:
        return _gen_uuidv7("rcpt")
except ImportError:
    _make_receipt_id = None  # fall back to sequential

LOOP_INTERVAL_SECS = 30

SESSION_MAP = {
    "aya":     "agent:main:main",
    "jabari":  "agent:jabari:main",
    "tariq":   "agent:tariq:main",
    "kimi":    "agent:kimi:main",
    "haiku":   "agent:haiku:main",
    "heru":    "agent:heru:main",
    "arbiter": "agent:arbiter:main",
}


def send_session_ping(recipient: str, message: str) -> bool:
    """Best-effort session ping. Never blocks receipt processing."""
    if not SESSION_MAP.get(recipient):
        print(f"[PING SKIP] No session map for '{recipient}'", file=sys.stderr)
        return False
    try:
        result = subprocess.run(
            [OC_BIN, "agent", "--agent", recipient,
             "--message", message, "--timeout", "15"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            print(f"[PING OK → {recipient}]", file=sys.stderr)
            return True
        print(f"[PING FAIL → {recipient}] rc={result.returncode}", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print(f"[PING TIMEOUT → {recipient}]", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[PING ERROR → {recipient}] {e}", file=sys.stderr)
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} [receipt_watcher] {msg}", file=sys.stderr)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_issued_receipts(receipts_jsonl: Path) -> set[str]:
    issued: set[str] = set()
    if not receipts_jsonl.exists():
        return issued
    for line in receipts_jsonl.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if "envelope_id" in rec:
                issued.add(rec["envelope_id"])
        except json.JSONDecodeError:
            pass
    return issued


def gen_receipt_id(mailbox: Path, prefer_uuid7: bool = True) -> str:
    """Generate receipt_id. Prefers UUIDv7 (rcpt_ prefix) if available, else sequential."""
    if prefer_uuid7 and _make_receipt_id is not None:
        return _make_receipt_id()
    # Legacy sequential fallback
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    receipts_file = mailbox / "ledger" / "receipts.jsonl"
    count = 1
    if receipts_file.exists():
        for line in receipts_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("receipt_id", "").startswith(f"rcpt-{today}"):
                    count += 1
            except json.JSONDecodeError:
                pass
    return f"rcpt-{today}-{count:03d}"


def find_tracker(mailbox: Path, envelope_id: str) -> tuple[Path, dict] | tuple[None, None]:
    """Find delivery tracker by envelope_id. Returns (path, tracker) or (None, None)."""
    tracking_dir = mailbox / "haiku" / "tracking"
    if not tracking_dir.exists():
        return None, None
    for tracker_path in tracking_dir.glob("*.json"):
        try:
            tracker = json.loads(tracker_path.read_text())
            if tracker.get("envelope_id") == envelope_id:
                return tracker_path, tracker
        except Exception:
            pass
    return None, None


def resolve_tracker(mailbox: Path, envelope_id: str, receipt: dict,
                    receipts_jsonl: Path) -> None:
    """
    Patch 5+6: Update tracker on receipt. Never touches work_item state.

    Ack states:
      - status == 'accepted' → ack_status = 'acked'
      - status == 'rejected' → ack_status = 'rejected' (no re-ping loop)
    """
    tracker_path, tracker = find_tracker(mailbox, envelope_id)
    if tracker is None:
        return  # No tracker — ack_required was false, nothing to update

    receipt_status = receipt.get("status", "accepted")
    received_ts = receipt.get("received_ts", now_iso())

    if receipt_status == "rejected":
        # Patch 6: rejected receipt — close tracker, no re-ping
        tracker["ack_status"] = "rejected"
        tracker["ack_ts"] = received_ts
        tracker["receipt_path"] = str(receipts_jsonl)
        tracker_path.write_text(json.dumps(tracker, indent=2))

        append_jsonl(receipts_jsonl, {
            "event_type": "ACK_CONFIRMED",
            "ts": now_iso(),
            "delivery_id": tracker["delivery_id"],
            "envelope_id": envelope_id,
            "work_item_id": tracker.get("work_item_id"),
            "receiver": tracker["recipient"],
            "receipt_status": "rejected",
            "reason": receipt.get("reason", ""),
        })

        sender = tracker.get("sender", "")
        if sender:
            reject_msg = (
                f"❌ Mail rejected: [{tracker['recipient']}] rejected {envelope_id}. "
                f"Reason: {receipt.get('reason', 'no reason given')}. "
                f"Work item: {tracker.get('work_item_id', '?')}"
            )
            print(
                f"[REJECT NOTIFY → {sender}] {tracker['recipient']} REJECTED "
                f"{envelope_id}: {receipt.get('reason', 'no reason given')}",
                file=sys.stderr,
            )
            send_session_ping(sender, reject_msg)
        log(f"TRACKER {tracker['delivery_id']} → rejected for {envelope_id}")

    else:
        # Patch 5: accepted receipt — mark acked
        tracker["ack_status"] = "acked"
        tracker["ack_ts"] = received_ts
        tracker["receipt_path"] = str(receipts_jsonl)
        tracker_path.write_text(json.dumps(tracker, indent=2))

        append_jsonl(receipts_jsonl, {
            "event_type": "ACK_CONFIRMED",
            "ts": now_iso(),
            "delivery_id": tracker["delivery_id"],
            "envelope_id": envelope_id,
            "work_item_id": tracker.get("work_item_id"),
            "receiver": tracker["recipient"],
            "receipt_status": "acked",
        })

        # Notify sender if configured
        sender = tracker.get("sender", "")
        if sender and tracker.get("notify_on_ack", True):
            ack_msg = (
                f"✅ Mail acknowledged: [{tracker['recipient']}] acked {envelope_id}. "
                f"Work item: {tracker.get('work_item_id', '?')}"
            )
            print(
                f"[ACK NOTIFY → {sender}] {tracker['recipient']} acknowledged "
                f"{envelope_id}",
                file=sys.stderr,
            )
            send_session_ping(sender, ack_msg)
        log(f"TRACKER {tracker['delivery_id']} → acked for {envelope_id}")


def check_for_completion(envelope: dict, receipt: dict, mailbox: Path) -> None:
    """
    Phase 1b: If this is a result/completion response, emit WORK_ITEM_COMPLETED
    and ping the whole work item chain.

    Rules:
    - Only fires on type=response + status=completed (or response_type=result + status=completed)
    - Pings immediate requester (envelope.to) AND original creator (work_items/{id}.created_by)
    - Never pings the agent who completed the work
    - Non-completion envelopes: silent no-op
    """
    response_type = envelope.get("response_type", "")
    status = envelope.get("status", "")

    is_completion = (
        (response_type == "result" and status == "completed")
        or (envelope.get("type") == "response" and status == "completed")
    )
    if not is_completion:
        return

    work_item_id = envelope.get("work_item_id", "")
    completed_by = envelope.get("from", "")
    immediate_requester = envelope.get("to", "")
    subject = envelope.get("subject", "")
    envelope_id = envelope.get("envelope_id", "")

    # Load original creator from work_items/
    created_by = None
    work_item_path = mailbox / "work_items" / f"{work_item_id}.json"
    if work_item_path.exists():
        try:
            wi = json.loads(work_item_path.read_text())
            created_by = wi.get("created_by")
        except Exception:
            pass

    # Build notification set: immediate requester + original creator, minus completer
    notify_agents: set[str] = set()
    if immediate_requester:
        notify_agents.add(immediate_requester)
    if created_by and created_by != immediate_requester:
        notify_agents.add(created_by)
    notify_agents.discard(completed_by)  # don't ping yourself

    # Write WORK_ITEM_COMPLETED to ledger
    append_jsonl(mailbox / "ledger" / "deliveries.jsonl", {
        "event_type": "WORK_ITEM_COMPLETED",
        "ts": now_iso(),
        "work_item_id": work_item_id,
        "completed_by": completed_by,
        "immediate_requester": immediate_requester,
        "created_by": created_by,
        "notified": sorted(notify_agents),
        "envelope_id": envelope_id,
        "subject": subject,
    })
    log(f"WORK_ITEM_COMPLETED {work_item_id} by {completed_by} → notify {sorted(notify_agents)}")

    # Ping everyone in the chain
    msg = (
        f"🏁 Task complete: [{subject}] by {completed_by}. "
        f"Work item {work_item_id} is done."
    )
    for agent in sorted(notify_agents):
        send_session_ping(agent, msg)


def run_pass(mailbox: Path) -> dict:
    receipts_jsonl = mailbox / "ledger" / "receipts.jsonl"
    issued = load_issued_receipts(receipts_jsonl)
    new_receipts = 0

    for received_dir in sorted((mailbox / "agents").glob("*/received")):
        if not received_dir.is_dir():
            continue
        agent_name = received_dir.parent.name

        for env_file in sorted(received_dir.glob("*.json")):
            if env_file.name.startswith("."):
                continue

            try:
                env = json.loads(env_file.read_text())
            except (json.JSONDecodeError, OSError) as e:
                log(f"ERROR reading {env_file}: {e}")
                continue

            envelope_id = env.get("envelope_id", env_file.stem)

            if envelope_id in issued:
                continue

            receipt_id = gen_receipt_id(mailbox)
            received_ts = now_iso()

            receipt = {
                "receipt_id": receipt_id,
                "envelope_id": envelope_id,
                "work_item_id": env.get("work_item_id", ""),
                "thread_id": env.get("thread_id", env.get("work_item_id", "")),
                "receiver": agent_name,
                "received_ts": received_ts,
                "receipt_type": "filesystem_move",
                "status": env.get("status", "accepted"),  # honour explicit status if set
                "reason": env.get("reason", ""),
                "next_action": env.get("next_action", ""),
                "attempt_no": env.get("attempt_no", 1),
            }

            append_jsonl(receipts_jsonl, receipt)
            issued.add(envelope_id)
            new_receipts += 1

            log(f"RECEIPT {receipt_id} for {envelope_id} (receiver={agent_name})")

            # Also log receipt_detected event to deliveries
            append_jsonl(mailbox / "ledger" / "deliveries.jsonl", {
                "event": "receipt_detected",
                "envelope_id": envelope_id,
                "receipt_id": receipt_id,
                "receiver": agent_name,
                "received_ts": received_ts,
                "receipt_type": "filesystem_move",
                "work_item_id": env.get("work_item_id", ""),
            })

            # Patch 5+6: resolve delivery tracker
            resolve_tracker(mailbox, envelope_id, receipt, receipts_jsonl)

            # Phase 1b: task completion notifications
            check_for_completion(env, receipt, mailbox)

    summary = {"pass_ts": now_iso(), "new_receipts": new_receipts}
    if new_receipts:
        log(f"pass complete: {new_receipts} new receipt(s)")
    return summary


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Receipt watcher — inbox→received detector")
    parser.add_argument("--once", action="store_true", help="Run one pass and exit")
    parser.add_argument("--mailbox-dir", type=Path, default=DEFAULT_MAILBOX)
    args = parser.parse_args()

    if args.once:
        run_pass(args.mailbox_dir)
        return 0

    log("starting receipt watcher loop")
    while True:
        try:
            run_pass(args.mailbox_dir)
        except Exception as e:
            log(f"ERROR in pass: {e}")
        time.sleep(LOOP_INTERVAL_SECS)


if __name__ == "__main__":
    sys.exit(main())
