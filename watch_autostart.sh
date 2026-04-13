#!/bin/zsh

set -euo pipefail

ACTION="${1:-install}"
LABEL="${2:-com.paper.agent.watch}"
PROJECT_ROOT="${0:A:h}"
AUTOSTART_ROOT="$PROJECT_ROOT/autostart"
INSTALL_SCRIPT_PATH="$AUTOSTART_ROOT/install_or_update_launch_agent.sh"
REMOVE_SCRIPT_PATH="$AUTOSTART_ROOT/remove_launch_agent.sh"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

case "$ACTION" in
  install)
    "$INSTALL_SCRIPT_PATH" "$LABEL"
    ;;

  remove)
    "$REMOVE_SCRIPT_PATH" "$LABEL"
    print -r -- "LaunchAgent '$LABEL' has been removed."
    ;;

  status)
    if [[ ! -f "$PLIST_PATH" ]]; then
      print -r -- "LaunchAgent '$LABEL' is not installed."
      exit 0
    fi

    print -r -- "Plist: $PLIST_PATH"
    if ! /bin/launchctl print "gui/$UID/$LABEL" 2>/dev/null | /usr/bin/grep -E "state =|pid =|path ="; then
      print -r -- "LaunchAgent '$LABEL' is installed, but not currently loaded."
    fi
    ;;

  *)
    print -u2 -- "Usage: zsh ./watch_autostart.sh [install|remove|status] [label]"
    exit 1
    ;;
esac
