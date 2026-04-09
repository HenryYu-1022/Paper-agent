#!/bin/zsh

set -euo pipefail

LABEL="${1:-com.paper.agent.watch}"
PROJECT_ROOT="${0:A:h}"
SUPERVISOR_PATH="$PROJECT_ROOT/paper_agent_watch_supervisor.sh"
WORKFLOW_ROOT="$PROJECT_ROOT/paper_to_markdown"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

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

if [[ -f "$PLIST_PATH" ]]; then
  /bin/launchctl bootout "gui/$UID" "$PLIST_PATH" 2>/dev/null || true
  rm -f "$PLIST_PATH"
fi

stop_project_processes
