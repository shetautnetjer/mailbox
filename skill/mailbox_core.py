#!/usr/bin/env python3
"""
Mailbox Core - Structured operations for file-based agent messaging
Phase 2: Durability, validation, atomic writes, ledger
"""

import os
import sys
import json
import time
import shutil
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from contextlib import contextmanager

@dataclass
class Envelope:
    """Standard mailbox envelope"""
    envelope_id: str
    from_agent: str
    to_agent: str
    subject: str
    body: str
    timestamp: str
    msg_type: str = "message"
    status: str = "pending"
    in_reply_to: Optional[str] = None
    work_item: Optional[Dict] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        data = {
            "envelope_id": self.envelope_id,
            "from": self.from_agent,
            "to": self.to_agent,
            "subject": self.subject,
            "body": self.body,
            "timestamp": self.timestamp,
            "type": self.msg_type,
            "status": self.status,
        }
        if self.in_reply_to:
            data["in_reply_to"] = self.in_reply_to
        if self.work_item:
            data["work_item"] = self.work_item
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> "Envelope":
        """Create from dictionary"""
        return cls(
            envelope_id=data["envelope_id"],
            from_agent=data.get("from", data.get("from_agent", "unknown")),
            to_agent=data.get("to", data.get("to_agent", "unknown")),
            subject=data["subject"],
            body=data["body"],
            timestamp=data["timestamp"],
            msg_type=data.get("type", "message"),
            status=data.get("status", "pending"),
            in_reply_to=data.get("in_reply_to"),
            work_item=data.get("work_item"),
        )

class MailboxCore:
    """Core mailbox operations with durability guarantees"""
    
    REQUIRED_FIELDS = ["envelope_id", "from", "to", "subject", "timestamp", "type", "status"]
    
    def __init__(self, mailbox_root: Path, my_agent_id: str):
        self.mailbox_root = Path(mailbox_root)
        self.my_agent_id = my_agent_id
        self.ledger_path = self.mailbox_root / "ledger" / "events.jsonl"
        
    def _agent_dir(self, agent_id: str) -> Path:
        """Get agent's mailbox directory"""
        return self.mailbox_root / "agents" / agent_id
    
    def _inbox(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "inbox"
    
    def _outbox(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "outbox"
    
    def _received(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "received"
    
    def _archive(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "archive"
    
    def _tmp_dir(self, agent_id: str) -> Path:
        """Temporary directory for atomic writes"""
        tmp = self._agent_dir(agent_id) / ".tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        return tmp
    
    def init_mailbox(self, agents: List[str] = None) -> List[Path]:
        """Initialize mailbox structure for given agents"""
        if agents is None:
            agents = [self.my_agent_id]
        
        created = []
        for agent in agents:
            for subdir in ["inbox", "outbox", "received", "archive"]:
                path = self._agent_dir(agent) / subdir
                path.mkdir(parents=True, exist_ok=True)
                created.append(path)
        
        # Ensure ledger directory exists
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        
        return created
    
    def validate_envelope(self, data: dict) -> Tuple[bool, List[str]]:
        """Validate envelope has required fields"""
        errors = []
        
        for field in self.REQUIRED_FIELDS:
            if field not in data:
                errors.append(f"Missing required field: {field}")
        
        # Type-specific validation
        msg_type = data.get("type", "message")
        
        if msg_type == "reply" and "in_reply_to" not in data:
            errors.append("Reply must have in_reply_to field")
        
        if msg_type == "work_complete":
            work = data.get("work_item", {})
            if "summary" not in work:
                errors.append("work_complete must have work_item.summary")
            if "status" not in work:
                errors.append("work_complete must have work_item.status")
        
        return len(errors) == 0, errors
    
    def _atomic_write(self, path: Path, data: dict) -> bool:
        """Write JSON atomically using temp file + rename"""
        tmp_dir = path.parent / ".tmp"
        tmp_dir.mkdir(exist_ok=True)
        
        tmp_path = tmp_dir / f"{path.name}.tmp"
        
        try:
            # Write to temp
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            
            # Validate by reading back
            with open(tmp_path) as f:
                json.load(f)
            
            # Atomic rename
            shutil.move(str(tmp_path), str(path))
            return True
            
        except Exception as e:
            # Clean up temp on failure
            if tmp_path.exists():
                tmp_path.unlink()
            raise e
    
    def _write_ledger(self, event_type: str, envelope_id: str, details: dict = None):
        """Append event to JSONL ledger"""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        
        event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "envelope_id": envelope_id,
            "from_agent": self.my_agent_id,
        }
        if details:
            event.update(details)
        
        with open(self.ledger_path, "a") as f:
            f.write(json.dumps(event) + "\n")
    
    def send_envelope(self, to_agent: str, envelope: Envelope) -> str:
        """Send envelope to recipient with atomic write and ledger"""
        # Validate
        data = envelope.to_dict()
        is_valid, errors = self.validate_envelope(data)
        if not is_valid:
            raise ValueError(f"Invalid envelope: {', '.join(errors)}")
        
        # Ensure directories exist
        self.init_mailbox([to_agent, self.my_agent_id])
        
        # Write to recipient inbox (atomic)
        recipient_path = self._inbox(to_agent) / f"{envelope.envelope_id}.json"
        self._atomic_write(recipient_path, data)
        
        # Copy to sender outbox (atomic)
        sender_path = self._outbox(self.my_agent_id) / f"{envelope.envelope_id}.json"
        self._atomic_write(sender_path, data)
        
        # Ledger event
        self._write_ledger("message_sent", envelope.envelope_id, {
            "to_agent": to_agent,
            "subject": envelope.subject,
        })
        
        return envelope.envelope_id
    
    def reply_to(self, original_id: str, body: str) -> str:
        """Create reply to existing envelope"""
        # Find original
        original = None
        original_path = None
        
        for folder in [self._inbox(self.my_agent_id), self._received(self.my_agent_id)]:
            if not folder.exists():
                continue
            for f in folder.glob("*.json"):
                try:
                    with open(f) as fp:
                        data = json.load(fp)
                    if data.get("envelope_id") == original_id:
                        original = data
                        original_path = f
                        break
                except:
                    continue
            if original:
                break
        
        if not original:
            raise ValueError(f"Original envelope not found: {original_id}")
        
        # Create reply
        reply = Envelope(
            envelope_id=self._generate_id(),
            from_agent=self.my_agent_id,
            to_agent=original.get("from", "unknown"),
            subject=f"Re: {original.get('subject', '')}",
            body=body,
            timestamp=datetime.utcnow().isoformat() + "Z",
            msg_type="reply",
            in_reply_to=original_id,
        )
        
        # Send reply
        self.send_envelope(reply.to_agent, reply)
        
        # Move original to received
        if original_path and original_path.parent == self._inbox(self.my_agent_id):
            new_path = self._received(self.my_agent_id) / original_path.name
            shutil.move(str(original_path), str(new_path))
            self._write_ledger("message_received", original_id)
        
        return reply.envelope_id
    
    def archive_old(self, days: int = 7) -> int:
        """Archive messages older than N days from received"""
        cutoff = datetime.utcnow() - timedelta(days=days)
        archived = 0
        
        received_dir = self._received(self.my_agent_id)
        archive_dir = self._archive(self.my_agent_id)
        
        if not received_dir.exists():
            return 0
        
        archive_dir.mkdir(parents=True, exist_ok=True)
        
        for f in received_dir.glob("*.json"):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                
                ts_str = data.get("timestamp", "")
                try:
                    msg_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if msg_time.replace(tzinfo=None) < cutoff:
                        shutil.move(str(f), str(archive_dir / f.name))
                        self._write_ledger("message_archived", data.get("envelope_id", "unknown"))
                        archived += 1
                except:
                    continue
                    
            except Exception:
                continue
        
        return archived
    
    def list_messages(self, folder: str = "inbox", limit: int = 10) -> List[Envelope]:
        """List messages from a folder"""
        if folder == "inbox":
            path = self._inbox(self.my_agent_id)
        elif folder == "received":
            path = self._received(self.my_agent_id)
        elif folder == "outbox":
            path = self._outbox(self.my_agent_id)
        elif folder == "archive":
            path = self._archive(self.my_agent_id)
        else:
            raise ValueError(f"Unknown folder: {folder}")
        
        if not path.exists():
            return []
        
        envelopes = []
        for f in sorted(path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                envelopes.append(Envelope.from_dict(data))
            except:
                continue
        
        return envelopes[:limit]
    
    def validate_all(self) -> Tuple[int, int, List[str]]:
        """Validate all envelopes in mailbox"""
        valid = 0
        invalid = 0
        errors = []
        
        for folder in ["inbox", "outbox", "received", "archive"]:
            path = self._agent_dir(self.my_agent_id) / folder
            if not path.exists():
                continue
            
            for f in path.glob("*.json"):
                try:
                    with open(f) as fp:
                        data = json.load(fp)
                    
                    is_valid, errs = self.validate_envelope(data)
                    if is_valid:
                        valid += 1
                    else:
                        invalid += 1
                        errors.append(f"{f.name}: {', '.join(errs)}")
                        
                except json.JSONDecodeError as e:
                    invalid += 1
                    errors.append(f"{f.name}: Invalid JSON - {e}")
                except Exception as e:
                    invalid += 1
                    errors.append(f"{f.name}: {e}")
        
        return valid, invalid, errors
    
    @staticmethod
    def _generate_id() -> str:
        """Generate UUIDv7-like ID"""
        timestamp = int(time.time() * 1000)
        random_part = uuid.uuid4().hex[:12]
        return f"env_{timestamp:012x}_{random_part}"


def main():
    """CLI entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Mailbox Core Operations")
    parser.add_argument("--mailbox-root", default=os.environ.get("MAILBOX_ROOT", "~/.openclaw/workspace/plane-a/projects/coms/mailbox"))
    parser.add_argument("--agent", default=os.environ.get("MY_AGENT_ID", "aya"))
    
    subparsers = parser.add_subparsers(dest="command")
    
    # Init command
    init_parser = subparsers.add_parser("init", help="Initialize mailbox")
    init_parser.add_argument("agents", nargs="*", help="Agents to initialize")
    
    # Send command
    send_parser = subparsers.add_parser("send", help="Send message")
    send_parser.add_argument("to", help="Recipient agent")
    send_parser.add_argument("subject", help="Message subject")
    send_parser.add_argument("--body", default="", help="Message body")
    send_parser.add_argument("--type", default="message", help="Message type")
    
    # Reply command
    reply_parser = subparsers.add_parser("reply", help="Reply to message")
    reply_parser.add_argument("original_id", help="Original envelope ID")
    reply_parser.add_argument("body", help="Reply body")
    
    # Archive command
    archive_parser = subparsers.add_parser("archive", help="Archive old messages")
    archive_parser.add_argument("--days", type=int, default=7, help="Archive messages older than N days")
    
    # List command
    list_parser = subparsers.add_parser("list", help="List messages")
    list_parser.add_argument("--folder", default="inbox", help="Folder to list")
    list_parser.add_argument("--limit", type=int, default=10, help="Max messages")
    
    # Validate command
    subparsers.add_parser("validate", help="Validate all envelopes")
    
    args = parser.parse_args()
    
    mailbox_root = Path(args.mailbox_root).expanduser()
    core = MailboxCore(mailbox_root, args.agent)
    
    if args.command == "init":
        agents = args.agents or [args.agent]
        created = core.init_mailbox(agents)
        print(f"Initialized mailbox for: {', '.join(agents)}")
        for path in created:
            print(f"  {path}")
    
    elif args.command == "send":
        envelope = Envelope(
            envelope_id=core._generate_id(),
            from_agent=args.agent,
            to_agent=args.to,
            subject=args.subject,
            body=args.body,
            timestamp=datetime.utcnow().isoformat() + "Z",
            msg_type=args.type,
        )
        env_id = core.send_envelope(args.to, envelope)
        print(f"Sent: {env_id}")
    
    elif args.command == "reply":
        reply_id = core.reply_to(args.original_id, args.body)
        print(f"Reply sent: {reply_id}")
    
    elif args.command == "archive":
        count = core.archive_old(args.days)
        print(f"Archived {count} message(s)")
    
    elif args.command == "list":
        messages = core.list_messages(args.folder, args.limit)
        for msg in messages:
            print(f"[{msg.status}] {msg.from_agent}: {msg.subject}")
    
    elif args.command == "validate":
        valid, invalid, errors = core.validate_all()
        print(f"Valid: {valid}, Invalid: {invalid}")
        if errors:
            print("\nErrors:")
            for err in errors[:10]:
                print(f"  - {err}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
