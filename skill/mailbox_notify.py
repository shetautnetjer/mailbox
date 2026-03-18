#!/usr/bin/env python3
"""
Mailbox Session Notifier - assistive mailbox notification prototype.

This file documents a possible future session-inject path, but should not be
read as proof that ordinary shell runtime supports direct `sessions_send`.
Durable mailbox files remain the truth.
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime

# Configuration
MAILBOX_ROOT = Path(os.environ.get(
    "MAILBOX_ROOT", 
    Path.home() / ".openclaw/workspace/plane-a/projects/coms/mailbox"
))
MY_AGENT_ID = os.environ.get("MY_AGENT_ID", "aya")

class MailboxNotifier:
    """Handles cross-session mail notifications"""
    
    # Mapping from OpenClaw agent IDs to mailbox agent names
    AGENT_ID_MAP = {
        "main": "aya",
        "arbiter": "arbiter",
    }
    
    def __init__(self, agent_name: str = None, mailbox_root: Path = None):
        self.agent_name = agent_name or MY_AGENT_ID
        self.mailbox_root = Path(mailbox_root) if mailbox_root else MAILBOX_ROOT
        self.my_inbox = self.mailbox_root / "agents" / self.agent_name / "inbox"
        self.my_received = self.mailbox_root / "agents" / self.agent_name / "received"
        
    def get_openclaw_agent_id(self) -> str:
        """Get OpenClaw agent ID for this mailbox agent"""
        for oc_id, mb_name in self.AGENT_ID_MAP.items():
            if mb_name == self.agent_name:
                return oc_id
        return "main"  # Default
        
    def get_agent_inbox(self, agent_name: str) -> Path:
        """Get path to agent's inbox"""
        return self.mailbox_root / "agents" / agent_name / "inbox"
    
    def get_session_keys_for_agent(self, agent_name: str) -> list:
        """Get discovered session keys for an agent via CLI listing."""
        # Map mailbox agent name to OpenClaw agent ID
        oc_agent_id = "main"  # Default
        for oc_id, mb_name in self.AGENT_ID_MAP.items():
            if mb_name == agent_name:
                oc_agent_id = oc_id
                break
        
        try:
            # Use openclaw CLI to list sessions
            result = subprocess.run(
                ["openclaw", "sessions", "--agent", oc_agent_id, "--json"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                return []
            
            data = json.loads(result.stdout)
            sessions = data.get("sessions", [])
            
            # Return all session keys
            return [s.get("key") for s in sessions if s.get("key")]
        except Exception as e:
            print(f"Error listing sessions: {e}", file=sys.stderr)
            return []
    
    def send_session_notification(self, session_key: str, notification: str, timeout: int = 0) -> bool:
        """Prototype future session notification helper.

        Do not treat this as verified shell capability without runtime proof.
        """
        try:
            # Historical prototype: this attempted a direct session send path
            cmd = ["openclaw", "sessions", "send", session_key, notification]
            if timeout > 0:
                cmd.extend(["--timeout", str(timeout)])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30 if timeout == 0 else timeout + 10
            )
            if result.returncode != 0:
                print(f"direct session notify failed: {result.stderr}", file=sys.stderr)
            return result.returncode == 0
        except Exception as e:
            print(f"Failed to notify session {session_key}: {e}", file=sys.stderr)
            return False
    
    def notify_all_sessions(self, agent_id: str, notification: str) -> int:
        """Notify all active sessions for an agent"""
        session_keys = self.get_session_keys_for_agent(agent_id)
        
        if not session_keys:
            print(f"No active sessions found for {agent_id}")
            return 0
        
        notified = 0
        for session_key in session_keys:
            if self.send_session_notification(session_key, notification):
                notified += 1
                print(f"✅ Notified {agent_id} via {session_key}")
            else:
                print(f"⚠️  Failed to notify {session_key}")
        
        return notified
    
    def read_envelope(self, envelope_path: Path) -> dict:
        """Read and parse envelope JSON"""
        try:
            with open(envelope_path) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading envelope {envelope_path}: {e}", file=sys.stderr)
            return {}
    
    def format_notification(self, envelope: dict) -> str:
        """Format envelope as notification message"""
        from_agent = envelope.get("from", "unknown")
        subject = envelope.get("subject", "(no subject)")
        body = envelope.get("body", "")
        msg_type = envelope.get("type", "message")
        envelope_id = envelope.get("envelope_id", "unknown")
        
        if msg_type == "work_complete":
            work = envelope.get("work_item", {})
            return f"""📬 **WORK COMPLETE** from {from_agent}
**Subject:** {subject}
**Summary:** {work.get('summary', 'N/A')}
**Details:** {work.get('details', 'N/A')[:200]}

Reply: `mailbox reply {envelope_id} "your message"`
"""
        else:
            return f"""📬 **NEW MAIL** from {from_agent}
**Subject:** {subject}
**Body:** {body[:300]}{'...' if len(body) > 300 else ''}

Reply: `mailbox reply {envelope_id} "your message"`
"""
    
    def check_and_notify(self, auto_ack: bool = True) -> int:
        """Check inbox and notify all sessions of new messages"""
        if not self.my_inbox.exists():
            print(f"Inbox not found: {self.my_inbox}")
            return 0
        
        pending = list(self.my_inbox.glob("*.json"))
        
        if not pending:
            print("📭 No new messages")
            return 0
        
        notified_count = 0
        
        for msg_file in pending:
            envelope = self.read_envelope(msg_file)
            if not envelope:
                continue
            
            notification = self.format_notification(envelope)
            
            # Try to notify all my active sessions
            if self.notify_all_sessions(self.agent_name, notification):
                notified_count += 1
                
                # Move to received after successful notification
                if auto_ack:
                    self.my_received.mkdir(exist_ok=True)
                    received_path = self.my_received / msg_file.name
                    msg_file.rename(received_path)
                    print(f"📥 Moved to received: {msg_file.name}")
            else:
                print(f"⚠️  Could not notify, keeping in inbox: {msg_file.name}")
        
        return notified_count
    
    def watch_continuously(self, interval: int = 30):
        """Continuously watch for new mail and notify"""
        print(f"👀 Watching for mail (every {interval}s, Ctrl+C to stop)...")
        
        last_count = len(list(self.my_inbox.glob("*.json"))) if self.my_inbox.exists() else 0
        
        try:
            while True:
                time.sleep(interval)
                
                if not self.my_inbox.exists():
                    continue
                
                current_count = len(list(self.my_inbox.glob("*.json")))
                
                if current_count > last_count:
                    new_count = current_count - last_count
                    print(f"\n📬 {new_count} new message(s)!")
                    self.check_and_notify(auto_ack=True)
                    last_count = current_count
                    
        except KeyboardInterrupt:
            print("\n👋 Stopping mailbox watch")
    
    def send_mail(self, to: str, subject: str, body: str = "", msg_type: str = "message") -> str:
        """Send mail to another agent and notify their sessions"""
        recipient_inbox = self.get_agent_inbox(to)
        
        if not recipient_inbox.exists():
            print(f"❌ Agent '{to}' not found")
            return None
        
        # Generate envelope
        timestamp = datetime.utcnow().isoformat() + "Z"
        envelope_id = f"env_{int(time.time() * 1000)}_{os.urandom(6).hex()}"
        
        envelope = {
            "envelope_id": envelope_id,
            "from": self.agent_name,
            "to": to,
            "subject": subject,
            "body": body,
            "timestamp": timestamp,
            "type": msg_type,
            "status": "pending"
        }
        
        # Write to recipient inbox
        envelope_path = recipient_inbox / f"{envelope_id}.json"
        with open(envelope_path, "w") as f:
            json.dump(envelope, f, indent=2)
        
        # Copy to my outbox
        my_outbox = self.mailbox_root / "agents" / self.agent_name / "outbox"
        my_outbox.mkdir(parents=True, exist_ok=True)
        with open(my_outbox / f"{envelope_id}.json", "w") as f:
            json.dump(envelope, f, indent=2)
        
        print(f"✅ Sent to {to}: {subject}")
        
        # Try to notify recipient's sessions
        notification = f"📬 New mail from {self.agent_name}: {subject}\nCheck: mailbox check"
        notified = self.notify_all_sessions(to, notification)
        
        if notified:
            print(f"📨 Notified {notified} session(s) for {to}")
        
        return envelope_id
    
    def announce_completion(self, summary: str, details: str = "", deliverables: list = None):
        """Announce work completion to Arbiter and team"""
        deliverables = deliverables or []
        
        work_item = {
            "summary": summary,
            "details": details,
            "deliverables": deliverables,
            "status": "complete"
        }
        
        body = f"Work completed: {summary}"
        if details:
            body += f"\nDetails: {details}"
        if deliverables:
            body += f"\nDeliverables: {', '.join(deliverables)}"
        
        # Send to Arbiter
        self.send_mail("arbiter", f"Work Complete: {summary}", body, "work_complete")
        
        # Also notify other relevant agents
        for agent in ["kimi", "tariq"]:
            self.send_mail(agent, f"Complete: {summary}", body[:200], "work_complete")

def main():
    if len(sys.argv) < 2:
        # Default: check and notify
        notifier = MailboxNotifier()
        notifier.check_and_notify()
        return
    
    command = sys.argv[1]
    agent_name = sys.argv[2] if len(sys.argv) > 2 else MY_AGENT_ID
    
    notifier = MailboxNotifier(agent_name=agent_name)
    
    if command == "check":
        notifier.check_and_notify()
    
    elif command == "watch":
        interval = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        notifier.watch_continuously(interval)
    
    elif command == "send":
        if len(sys.argv) < 5:
            print("Usage: mailbox_notify send <to> <subject> [body]")
            sys.exit(1)
        to = sys.argv[2]
        subject = sys.argv[3]
        body = sys.argv[4] if len(sys.argv) > 4 else ""
        notifier.send_mail(to, subject, body)
    
    elif command == "complete":
        if len(sys.argv) < 3:
            print("Usage: mailbox_notify complete <summary> [details] [deliverables]")
            sys.exit(1)
        summary = sys.argv[2]
        details = sys.argv[3] if len(sys.argv) > 3 else ""
        deliverables = sys.argv[4].split(",") if len(sys.argv) > 4 else []
        notifier.announce_completion(summary, details, deliverables)
    
    elif command == "notify":
        # Direct session notification
        if len(sys.argv) < 4:
            print("Usage: mailbox_notify notify <session_key> <message>")
            sys.exit(1)
        session_key = sys.argv[2]
        message = sys.argv[3]
        if notifier.send_session_notification(session_key, message):
            print("✅ Notification sent")
        else:
            print("❌ Failed to send")
    
    else:
        print(f"Unknown command: {command}")
        print("Commands: check, watch, send, complete, notify")
        sys.exit(1)

if __name__ == "__main__":
    main()
