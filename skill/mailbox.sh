#!/bin/bash
# Mailbox Skill - Phase 2
# Shell orchestrates, Python does structured work

set -euo pipefail

MAILBOX_ROOT="${MAILBOX_ROOT:-$HOME/.openclaw/workspace/plane-a/projects/coms/mailbox}"
MY_AGENT_ID="${MY_AGENT_ID:-aya}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_PY="$SCRIPT_DIR/mailbox_core.py"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[mailbox]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }
success() { echo -e "${GREEN}[success]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

# Check Python core exists
check_core() {
    if [[ ! -f "$CORE_PY" ]]; then
        error "mailbox_core.py not found at $CORE_PY"
        exit 1
    fi
}

# Initialize mailbox
cmd_init() {
    check_core
    local agents="$*"
    if [[ -z "$agents" ]]; then
        agents="$MY_AGENT_ID"
    fi
    
    python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" init $agents
}

# Send message
cmd_send() {
    check_core
    local to="$1"
    local subject="$2"
    local body="${3:-}"
    
    if [[ -z "$to" || -z "$subject" ]]; then
        error "Usage: mailbox send <to> <subject> [body]"
        exit 1
    fi
    
    python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" send "$to" "$subject" --body "$body"
}

# Reply to message
cmd_reply() {
    check_core
    local original_id="$1"
    local body="$2"
    
    if [[ -z "$original_id" || -z "$body" ]]; then
        error "Usage: mailbox reply <envelope_id> <body>"
        exit 1
    fi
    
    python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" reply "$original_id" "$body"
}

# Check inbox
cmd_check() {
    check_core
    local count
    count=$(python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" list --folder inbox --limit 100 | wc -l)
    
    if [[ $count -eq 0 ]]; then
        log "No new messages"
        return 0
    fi
    
    success "$count message(s) in inbox:"
    python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" list --folder inbox --limit 10
}

# List messages
cmd_list() {
    check_core
    local folder="${1:-inbox}"
    local limit="${2:-10}"
    
    log "Messages in $folder (last $limit):"
    python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" list --folder "$folder" --limit "$limit"
}

# Announce work completion
cmd_complete() {
    check_core
    local summary="$1"
    local details="${2:-}"
    local deliverables="${3:-}"
    
    if [[ -z "$summary" ]]; then
        error "Usage: mailbox complete <summary> [details] [deliverables]"
        exit 1
    fi
    
    # Create work completion envelope
    local work_item
    work_item=$(python3 -c "
import json
print(json.dumps({
    'summary': '$summary',
    'details': '${details:-$summary}',
    'deliverables': '${deliverables}'.split(',') if '${deliverables}' else [],
    'status': 'complete'
}))
")
    
    # Send to arbiter (using Python core with work type)
    python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" send arbiter "$summary" \
        --body "${details:-Work completed: $summary}" \
        --type "work_complete"
    
    success "Work completion announced: $summary"
}

# Archive old messages
cmd_archive() {
    check_core
    local days="${1:-7}"
    
    log "Archiving messages older than $days days..."
    python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" archive --days "$days"
}

# Validate all envelopes
cmd_validate() {
    check_core
    log "Validating all envelopes..."
    python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" validate
}

# Watch for new mail
cmd_watch() {
    check_core
    local interval="${1:-30}"
    
    log "Watching for mail (every ${interval}s, Ctrl+C to stop)..."
    
    local last_count
    last_count=$(python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" list --folder inbox --limit 1000 | wc -l)
    
    while true; do
        sleep "$interval"
        
        local current_count
        current_count=$(python3 "$CORE_PY" --mailbox-root "$MAILBOX_ROOT" --agent "$MY_AGENT_ID" list --folder inbox --limit 1000 | wc -l)
        
        if [[ $current_count -gt $last_count ]]; then
            local new_count=$((current_count - last_count))
            success "$new_count new message(s)!"
            cmd_check
            last_count=$current_count
        fi
    done
}

# Show help
cmd_help() {
    cat << EOF
Mailbox Skill - Phase 2 (File-based messaging)

Commands:
  init [agents...]        Initialize mailbox structure
  send <to> <subj> [body] Send message to agent
  reply <id> <body>      Reply to a message
  check                   Check inbox for new messages
  list [folder] [n]       List messages (default: inbox, 10)
  complete <sum> [det] [deliv]  Announce work completion
  archive [days]          Archive old messages (default: 7 days)
  validate                Validate all envelope JSON
  watch [interval]        Watch for new mail
  help                    Show this help

Examples:
  ./mailbox.sh init aya arbiter kimi
  ./mailbox.sh send arbiter "Review needed" "Please check the code"
  ./mailbox.sh check
  ./mailbox.sh complete "Parser v0.2.0" "Details" "parser.zip"
  ./mailbox.sh archive 14

Environment:
  MAILBOX_ROOT    Path to mailbox (default: ~/.openclaw/workspace/plane-a/projects/coms/mailbox)
  MY_AGENT_ID     Your agent name (default: aya)

Phase 2 Features:
  - Atomic writes (temp file + rename)
  - Envelope validation
  - JSONL event ledger
  - Archive/retention
  - Schema checking
EOF
}

# Main dispatcher
case "${1:-help}" in
    init)
        shift
        cmd_init "$@"
        ;;
    send)
        shift
        cmd_send "$@"
        ;;
    reply)
        shift
        cmd_reply "$@"
        ;;
    check)
        cmd_check
        ;;
    list)
        shift
        cmd_list "$@"
        ;;
    complete)
        shift
        cmd_complete "$@"
        ;;
    archive)
        shift
        cmd_archive "$@"
        ;;
    validate)
        cmd_validate
        ;;
    watch)
        shift
        cmd_watch "$@"
        ;;
    help|--help|-h)
        cmd_help
        ;;
    *)
        error "Unknown command: $1"
        echo "Run './mailbox.sh help' for usage"
        exit 1
        ;;
esac
