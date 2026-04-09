#!/bin/zsh

set -euo pipefail

PROJECT_ROOT="${0:A:h}"
SUPERVISOR_PATH="$PROJECT_ROOT/paper_agent_watch_supervisor.sh"
WORKFLOW_ROOT="$PROJECT_ROOT/paper_to_markdown"
CONFIG_PATH="$WORKFLOW_ROOT/settings.json"
WATCH_SCRIPT_PATH="$WORKFLOW_ROOT/watch_folder_resilient.py"

read_json_key() {
  /usr/bin/plutil -extract "$1" raw -o - "$CONFIG_PATH" 2>/dev/null || true
}

ensure_dir() {
  mkdir -p "$1"
}

require_path() {
  if [[ -z "$1" || ! -e "$1" ]]; then
    print -u2 -- "Missing required path: $1"
    exit 1
  fi
}

pid_is_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  /bin/kill -0 "$pid" 2>/dev/null
}

command_for_pid() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  /bin/ps -p "$pid" -o command= 2>/dev/null || true
}

log() {
  local message="$1"
  local level="${2:-INFO}"
  local timestamp
  timestamp="$(/bin/date '+%Y-%m-%d %H:%M:%S')"
  print -r -- "$timestamp | $level | $message" >> "$SUPERVISOR_LOG_PATH"
}

is_supervisor_pid() {
  local pid="${1:-}"
  pid_is_running "$pid" || return 1
  local command
  command="$(command_for_pid "$pid")"
  [[ "$command" == *"$SUPERVISOR_PATH"* ]]
}

watcher_state_pid() {
  if [[ ! -f "$WATCHER_STATE_PATH" ]]; then
    return 0
  fi
  /usr/bin/plutil -extract pid raw -o - "$WATCHER_STATE_PATH" 2>/dev/null || true
}

is_watcher_pid() {
  local pid="${1:-}"
  pid_is_running "$pid" || return 1
  local command
  command="$(command_for_pid "$pid")"
  [[ "$command" == *"$WATCH_SCRIPT_PATH"* ]]
}

save_watcher_state() {
  local pid="$1"
  cat > "$WATCHER_STATE_PATH" <<EOF
{
  "pid": $pid,
  "started_at": "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')"
}
EOF
}

cleanup_lock() {
  rm -f "$SUPERVISOR_PID_PATH"
}

start_watcher() {
  local existing_pid
  existing_pid="$(watcher_state_pid)"
  if is_watcher_pid "$existing_pid"; then
    return 0
  fi

  log "Starting paper watch process from $WATCH_SCRIPT_PATH."
  "$PYTHON_PATH" "$WATCH_SCRIPT_PATH" --config "$CONFIG_PATH" >> "$WATCHER_STDOUT_PATH" 2>> "$WATCHER_STDERR_PATH" &
  local pid=$!

  /bin/sleep 2
  if ! pid_is_running "$pid"; then
    local exit_code=1
    wait "$pid" || exit_code=$?
    log "watch_folder_resilient.py exited immediately with code $exit_code" "ERROR"
    return 1
  fi

  save_watcher_state "$pid"
  log "Started paper watch process with PID $pid."
  return 0
}

require_path "$WORKFLOW_ROOT"
require_path "$WATCH_SCRIPT_PATH"
require_path "$CONFIG_PATH"

PYTHON_PATH="$(read_json_key python_path)"
OUTPUT_ROOT="$(read_json_key output_root)"
MODEL_CACHE_DIR="$(read_json_key model_cache_dir)"

require_path "$PYTHON_PATH"
if [[ -z "$OUTPUT_ROOT" ]]; then
  print -u2 -- "Missing required config key: output_root"
  exit 1
fi

LOG_ROOT="$OUTPUT_ROOT/logs"
SUPERVISOR_LOG_PATH="$LOG_ROOT/paper_agent_watch_supervisor.log"
WATCHER_STATE_PATH="$LOG_ROOT/paper_agent_watch_supervisor_state.json"
SUPERVISOR_PID_PATH="$LOG_ROOT/paper_agent_watch_supervisor.pid"
WATCHER_STDOUT_PATH="$LOG_ROOT/paper_agent_watch_stdout.log"
WATCHER_STDERR_PATH="$LOG_ROOT/paper_agent_watch_stderr.log"
WATCHER_CHECK_INTERVAL_SECONDS=15

ensure_dir "$LOG_ROOT"
touch "$SUPERVISOR_LOG_PATH" "$WATCHER_STDOUT_PATH" "$WATCHER_STDERR_PATH"

if [[ -n "$MODEL_CACHE_DIR" ]]; then
  ensure_dir "$MODEL_CACHE_DIR"
  export MODEL_CACHE_DIR="$MODEL_CACHE_DIR"
fi

if [[ -f "$SUPERVISOR_PID_PATH" ]]; then
  existing_supervisor_pid="$(<"$SUPERVISOR_PID_PATH")"
  if is_supervisor_pid "$existing_supervisor_pid"; then
    log "Another supervisor instance is already running." "WARN"
    exit 0
  fi
fi

print -r -- "$$" > "$SUPERVISOR_PID_PATH"
trap cleanup_lock EXIT INT TERM

log "Supervisor started. Workflow root: $WORKFLOW_ROOT"
log "Config path: $CONFIG_PATH"
log "Log root: $LOG_ROOT"

while true; do
  if ! start_watcher; then
    log "Watcher start/check failed." "ERROR"
  fi
  /bin/sleep "$WATCHER_CHECK_INTERVAL_SECONDS"
done
