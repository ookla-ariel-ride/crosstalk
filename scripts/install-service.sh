#!/bin/sh
# Install (or uninstall) the Crosstalk server as a user LaunchAgent.
# No sudo: the job runs as the current user, bound to 127.0.0.1.
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
UID_NUM=$(id -u)

LABEL="local.crosstalk"
PLIST_TEMPLATE="$SCRIPT_DIR/launchd/crosstalk.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"

cmd="${1:-install}"

case "$cmd" in
  install)
    mkdir -p "$(dirname "$PLIST_DEST")" "$LOG_DIR"
    python3 "$SCRIPT_DIR/launchd/render_plist.py" "$PLIST_TEMPLATE" "$PLIST_DEST" "$REPO_ROOT" "$HOME"
    # Boot out any stale instance first (ignore failure when none is loaded),
    # then load the fresh plist.
    launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_NUM" "$PLIST_DEST"
    echo "installed $LABEL (repo $REPO_ROOT)"
    ;;
  uninstall)
    launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "uninstalled $LABEL"
    ;;
  *)
    echo "usage: $0 [install|uninstall]" >&2
    exit 2
    ;;
esac
