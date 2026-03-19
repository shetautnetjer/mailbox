from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

NOTIFIER_MODES = {"none", "discover-only", "agent-turn-nudge"}
TRACKER_SCHEMA_VERSION = "mailbox-tracker-v3"
EVENT_SCHEMA_VERSION = "mailbox-event-v1"

DEFAULT_AGENT_NAMES = ["aya", "arbiter", "haiku", "heru", "jabari", "kimi", "tariq"]
VALID_TYPES = {"task", "response"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
VALID_TRUST_PLANES = {"plane-a", "plane-b"}
VALID_INTENT_CLASSES = {"propose", "investigate", "execute"}
VALID_RISKS = {"low", "medium", "high", "critical"}
VALID_TASK_TYPES = {
    "architecture-review",
    "implementation",
    "research",
    "compliance-check",
    "memory-curation",
    "escalation",
    "handoff",
    "status-report",
    "general",
}
VALID_RESPONSE_TYPES = {"result", "blocker", "status", "clarification", "escalation"}
VALID_STATUSES = {
    "completed",
    "failed",
    "rejected",
    "started",
    "in_progress",
    "partial_complete",
    "awaiting_input",
}

BASE_REQUIRED = [
    "envelope_id",
    "type",
    "from",
    "to",
    "ts",
    "work_item_id",
    "trust_plane",
    "intent_class",
    "risk",
    "subject",
    "body",
    "priority",
]

TYPE_REQUIRED: dict[str, list[str]] = {
    "task": ["task_type"],
    "response": ["parent_id", "response_type", "status"],
}

SESSION_MAP = {
    "aya": "agent:main:main",
    "jabari": "agent:jabari:main",
    "tariq": "agent:tariq:main",
    "kimi": "agent:kimi:main",
    "haiku": "agent:haiku:main",
    "heru": "agent:heru:main",
    "arbiter": "agent:arbiter:main",
}


@dataclass(slots=True)
class MailboxPaths:
    root: Path

    @property
    def intake_pending(self) -> Path:
        return self.root / "intake" / "pending"

    @property
    def intake_processed(self) -> Path:
        return self.root / "intake" / "processed"

    @property
    def intake_quarantine(self) -> Path:
        return self.root / "intake" / "quarantine"

    @property
    def ledger(self) -> Path:
        return self.root / "ledger"

    @property
    def deliveries_jsonl(self) -> Path:
        return self.ledger / "deliveries.jsonl"

    @property
    def receipts_jsonl(self) -> Path:
        return self.ledger / "receipts.jsonl"

    @property
    def acks_jsonl(self) -> Path:
        return self.ledger / "acks.jsonl"

    @property
    def violations_jsonl(self) -> Path:
        return self.ledger / "violations.jsonl"

    @property
    def timeouts_jsonl(self) -> Path:
        return self.ledger / "timeouts" / "timeouts.jsonl"

    @property
    def repings_jsonl(self) -> Path:
        return self.ledger / "repings" / "repings.jsonl"

    @property
    def escalations_jsonl(self) -> Path:
        return self.ledger / "escalations" / "escalations.jsonl"

    @property
    def tracking_dir(self) -> Path:
        return self.root / "haiku" / "tracking"

    def agent_dir(self, agent: str) -> Path:
        return self.root / "agents" / agent

    def agent_inbox(self, agent: str) -> Path:
        return self.agent_dir(agent) / "inbox"

    def agent_outbox(self, agent: str) -> Path:
        return self.agent_dir(agent) / "outbox"

    def agent_received(self, agent: str) -> Path:
        return self.agent_dir(agent) / "received"

    def agent_responses(self, agent: str) -> Path:
        return self.agent_dir(agent) / "responses"

    def agent_acks(self, agent: str) -> Path:
        return self.agent_dir(agent) / "acks"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def log(component: str, message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} [{component}] {message}")


def ensure_mailbox_layout(root: Path, agents: Iterable[str] = DEFAULT_AGENT_NAMES) -> None:
    paths = MailboxPaths(root)
    for p in [
        paths.intake_pending,
        paths.intake_processed,
        paths.intake_quarantine,
        paths.ledger,
        paths.tracking_dir,
        paths.ledger / "timeouts",
        paths.ledger / "repings",
        paths.ledger / "escalations",
    ]:
        p.mkdir(parents=True, exist_ok=True)

    for agent in agents:
        for p in [
            paths.agent_inbox(agent),
            paths.agent_outbox(agent),
            paths.agent_received(agent),
            paths.agent_responses(agent),
            paths.agent_acks(agent),
        ]:
            p.mkdir(parents=True, exist_ok=True)

    for file_path in [
        paths.deliveries_jsonl,
        paths.receipts_jsonl,
        paths.acks_jsonl,
        paths.violations_jsonl,
        paths.timeouts_jsonl,
        paths.repings_jsonl,
        paths.escalations_jsonl,
    ]:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch(exist_ok=True)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def mailbox_event(
    *,
    component: str,
    event_type: str,
    event_family: str,
    state_class: str,
    trust_plane: str = "plane-a",
    provenance_writer: str | None = None,
    tags: list[str] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "component": component,
        "event_type": event_type,
        "event_family": event_family,
        "state_class": state_class,
        "trust_plane": trust_plane,
        "provenance_writer": provenance_writer or component,
        "ts": fields.pop("ts", now_iso()),
    }
    record.update(fields)
    if tags:
        record["tags"] = tags
    return record


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_envelope(env: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in BASE_REQUIRED:
        if env.get(field) in (None, ""):
            errors.append(f"missing required field: {field}")

    if env.get("type") not in VALID_TYPES:
        errors.append(f"type must be one of {sorted(VALID_TYPES)}")
    if env.get("priority") not in VALID_PRIORITIES:
        errors.append(f"priority must be one of {sorted(VALID_PRIORITIES)}")
    if env.get("trust_plane") not in VALID_TRUST_PLANES:
        errors.append(f"trust_plane must be one of {sorted(VALID_TRUST_PLANES)}")
    if env.get("intent_class") not in VALID_INTENT_CLASSES:
        errors.append(f"intent_class must be one of {sorted(VALID_INTENT_CLASSES)}")
    if env.get("risk") not in VALID_RISKS:
        errors.append(f"risk must be one of {sorted(VALID_RISKS)}")

    env_type = env.get("type")
    for field in TYPE_REQUIRED.get(env_type, []):
        if env.get(field) in (None, ""):
            errors.append(f"type {env_type!r} requires field: {field}")

    if env_type == "task" and env.get("task_type") not in VALID_TASK_TYPES:
        errors.append(f"task_type must be one of {sorted(VALID_TASK_TYPES)}")
    if env_type == "response":
        if env.get("response_type") not in VALID_RESPONSE_TYPES:
            errors.append(f"response_type must be one of {sorted(VALID_RESPONSE_TYPES)}")
        if env.get("status") not in VALID_STATUSES:
            errors.append(f"status must be one of {sorted(VALID_STATUSES)}")

    recipients = envelope_recipients(env)
    if not recipients:
        errors.append("must provide to or to_all")

    return errors


def envelope_recipients(env: dict[str, Any]) -> list[str]:
    if env.get("to_all"):
        return [str(x).strip() for x in env["to_all"] if str(x).strip()]
    if env.get("to"):
        return [str(env["to"]).strip()]
    return []


def trust_violation(env: dict[str, Any]) -> str | None:
    # Keep this intentionally conservative.
    # Plane A can carry live work, but direct execute commands should be scrutinized.
    if env.get("trust_plane") == "plane-a" and env.get("intent_class") == "execute":
        return "plane-a/execute requires governance review before direct runtime execution"
    return None


def normalize_notifier_mode(mode: str | None) -> str:
    if not mode:
        return "agent-turn-nudge"
    mode = str(mode).strip().lower()
    if mode not in NOTIFIER_MODES:
        raise ValueError(f"notifier mode must be one of {sorted(NOTIFIER_MODES)}")
    return mode


def agent_turn_nudge(agent: str, message: str, openclaw_bin: str | None, timeout_s: int = 15) -> dict[str, Any]:
    """Best-effort agent-turn nudge via `openclaw agent`.

    This is not durable delivery and not direct session injection.
    It is only an assistive runtime nudge that may cause an agent turn.
    """
    result: dict[str, Any] = {
        "ok": False,
        "mode": "agent-turn-nudge",
        "component": "mailbox_core",
        "event_family": "comms/live-notify",
        "state_class": "live_notify_state",
        "trust_plane": "plane-a",
        "provenance_writer": "mailbox_core",
        "adapter": "openclaw-agent-cli",
        "semantic_layer": "live_notify",
        "delivery_truth": False,
        "agent": agent,
        "timeout_s": timeout_s,
    }
    if not openclaw_bin:
        result["reason"] = "openclaw_bin_not_configured"
        return result
    if not shutil.which(openclaw_bin) and not Path(openclaw_bin).exists():
        result["reason"] = "openclaw_bin_not_found"
        return result
    if agent not in SESSION_MAP:
        result["reason"] = "unknown_agent"
        return result
    try:
        proc = subprocess.run(
            [openclaw_bin, "agent", "--agent", agent, "--message", message, "--timeout", str(timeout_s)],
            capture_output=True,
            text=True,
            timeout=timeout_s + 5,
        )
        result.update(
            {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout.strip()[:500] if proc.stdout else "",
                "stderr": proc.stderr.strip()[:500] if proc.stderr else "",
                "reason": "agent_turn_completed" if proc.returncode == 0 else "agent_turn_failed",
            }
        )
        return result
    except Exception as exc:
        result["reason"] = "agent_turn_exception"
        result["error"] = str(exc)
        return result


def notifier_attempt(
    *,
    mode: str,
    agent: str,
    message: str,
    openclaw_bin: str | None,
    discovery: dict[str, Any] | None = None,
    timeout_s: int = 15,
) -> dict[str, Any]:
    mode = normalize_notifier_mode(mode)
    base: dict[str, Any] = {
        "mode": mode,
        "component": "mailbox_core",
        "event_family": "comms/live-notify",
        "state_class": "live_notify_state",
        "trust_plane": "plane-a",
        "provenance_writer": "mailbox_core",
        "semantic_layer": "live_notify",
        "agent": agent,
        "delivery_truth": False,
        "discovery": discovery,
    }
    if mode == "none":
        return {**base, "ok": False, "adapter": "disabled", "reason": "notifier_disabled"}
    if mode == "discover-only":
        return {**base, "ok": False, "adapter": "session_discovery", "reason": "discover_only_no_runtime_nudge"}

    nudge = agent_turn_nudge(agent=agent, message=message, openclaw_bin=openclaw_bin, timeout_s=timeout_s)
    nudge["discovery"] = discovery
    return nudge


def tracker_ack_state(tracker: dict[str, Any]) -> str:
    """Return normalized ack state, keeping legacy fields as compatibility only."""
    ack_state = tracker.get("ack_state")
    if ack_state:
        return str(ack_state)

    legacy = tracker.get("ack_status")
    if legacy in {"pending", "acked", "rejected", "timed_out"}:
        return str(legacy)
    if legacy == "escalated":
        return "timed_out"
    if tracker.get("escalated"):
        return "timed_out"
    return "unknown"


def tracker_delivery_state(tracker: dict[str, Any]) -> str:
    delivery_state = tracker.get("delivery_state")
    if delivery_state:
        return str(delivery_state)
    if tracker.get("delivered_ts") or tracker.get("file_delivery"):
        return "durably_delivered"
    return "unknown"


def tracker_live_notify_state(tracker: dict[str, Any]) -> str:
    live_notify_state = tracker.get("live_notify_state")
    if live_notify_state:
        return str(live_notify_state)

    notify_mode = tracker.get("notify_mode")
    if notify_mode == "none":
        return "disabled"
    if notify_mode == "discover-only":
        return "discovered_only"

    session_delivery = tracker.get("session_delivery")
    if session_delivery and session_delivery.get("method") == "session_discovery_only":
        return "discovered_only"
    if tracker.get("last_ping_ts"):
        return "attempted_legacy"
    return "not_attempted"


def tracker_schema_drift(tracker: dict[str, Any]) -> list[str]:
    drift: list[str] = []
    if tracker.get("schema_version") != TRACKER_SCHEMA_VERSION:
        drift.append("schema_version")
    if "ack_state" not in tracker and "ack_status" in tracker:
        drift.append("legacy_ack_status")
    if "delivery_state" not in tracker:
        drift.append("missing_delivery_state")
    if "live_notify_state" not in tracker:
        drift.append("missing_live_notify_state")
    if "event_family" not in tracker:
        drift.append("missing_event_family")
    if "state_class" not in tracker:
        drift.append("missing_state_class")
    if tracker.get("state_class") == "delivery_state" and tracker_ack_state(tracker) in {"acked", "rejected", "timed_out"}:
        drift.append("tracker_state_class_not_split")
    return drift


def normalized_tracker_view(tracker: dict[str, Any]) -> dict[str, Any]:
    return {
        **tracker,
        "ack_state": tracker_ack_state(tracker),
        "delivery_state": tracker_delivery_state(tracker),
        "live_notify_state": tracker_live_notify_state(tracker),
        "schema_drift": tracker_schema_drift(tracker),
    }


def best_effort_openclaw_ping(agent: str, message: str, openclaw_bin: str | None) -> bool:
    """Legacy compatibility wrapper. Prefer notifier_attempt()/agent_turn_nudge()."""
    return bool(agent_turn_nudge(agent, message, openclaw_bin).get("ok"))
