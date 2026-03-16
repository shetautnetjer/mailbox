from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

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


def best_effort_openclaw_ping(agent: str, message: str, openclaw_bin: str | None) -> bool:
    if not openclaw_bin:
        return False
    if not shutil.which(openclaw_bin) and not Path(openclaw_bin).exists():
        return False
    if agent not in SESSION_MAP:
        return False
    try:
        result = subprocess.run(
            [openclaw_bin, "agent", "--agent", agent, "--message", message, "--timeout", "15"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    except Exception:
        return False
