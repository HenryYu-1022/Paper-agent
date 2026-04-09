#!/bin/zsh

set -euo pipefail

LABEL="${1:-com.paper.agent.watch}"
PROJECT_ROOT="${0:A:h}"
SUPERVISOR_PATH="$PROJECT_ROOT/paper_agent_watch_supervisor.sh"
WORKFLOW_ROOT="$PROJECT_ROOT/paper_to_markdown"
CONFIG_PATH="$WORKFLOW_ROOT/settings.json"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

read_json_key() {
  /usr/bin/plutil -extract "$1" raw -o - "$CONFIG_PATH" 2>/dev/null || true
}

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  value="${value//\"/&quot;}"
  value="${value//\'/&apos;}"
  print -r -- "$value"
}

require_path() {
  if [[ -z "$1" || ! -e "$1" ]]; then
    print -u2 -- "Missing required path: $1"
    exit 1
  fi
}

stop_project_processes() {
  local patterns=(
    "$SUPERVISOR_PATH"
    "$WORKFLOW_ROOT/watch_folder_resilient.py"
  )

  local pattern
  for pattern in "${patterns[@]}"; do
    local pid
    while IFS= read -r pid; do
      [[ -n "$pid" && "$pid" != "$$" ]] || continue
      /bin/kill "$pid" 2>/dev/null || true
    done < <(/usr/bin/pgrep -f "$pattern" 2>/dev/null || true)
  done
}

require_path "$SUPERVISOR_PATH"
require_path "$WORKFLOW_ROOT"
require_path "$CONFIG_PATH"

PYTHON_PATH="$(read_json_key python_path)"
OUTPUT_ROOT="$(read_json_key output_root)"

require_path "$PYTHON_PATH"
if [[ -z "$OUTPUT_ROOT" ]]; then
  print -u2 -- "Missing required config key: output_root"
  exit 1
fi

LOG_ROOT="$OUTPUT_ROOT/logs"
mkdir -p "$HOME/Library/LaunchAgents" "$LOG_ROOT"

ESCAPED_LABEL="$(xml_escape "$LABEL")"
ESCAPED_SUPERVISOR_PATH="$(xml_escape "$SUPERVISOR_PATH")"
ESCAPED_PROJECT_ROOT="$(xml_escape "$PROJECT_ROOT")"
ESCAPED_STDOUT_PATH="$(xml_escape "$LOG_ROOT/paper_agent_watch_launchagent_stdout.log")"
ESCAPED_STDERR_PATH="$(xml_escape "$LOG_ROOT/paper_agent_watch_launchagent_stderr.log")"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$ESCAPED_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$ESCAPED_SUPERVISOR_PATH</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ESCAPED_PROJECT_ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$ESCAPED_STDOUT_PATH</string>
  <key>StandardErrorPath</key>
  <string>$ESCAPED_STDERR_PATH</string>
</dict>
</plist>
EOF

/bin/launchctl bootout "gui/$UID" "$PLIST_PATH" 2>/dev/null || true
stop_project_processes
/bin/launchctl bootstrap "gui/$UID" "$PLIST_PATH"
/bin/launchctl kickstart -k "gui/$UID/$LABEL"
/bin/launchctl print "gui/$UID/$LABEL" | /usr/bin/grep -E "state =|pid =|path ="
