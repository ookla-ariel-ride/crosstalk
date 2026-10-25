#!/bin/sh
# Install (or uninstall) the Crosstalk server and housekeeper as user
# LaunchAgents. No sudo: the jobs run as the current user, bound to
# 127.0.0.1.
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
UID_NUM=$(id -u)

SERVE_LABEL="local.crosstalk"
SERVE_TEMPLATE="$SCRIPT_DIR/launchd/crosstalk.plist"
HOUSEKEEPER_LABEL="local.crosstalk-housekeeper"
HOUSEKEEPER_TEMPLATE="$SCRIPT_DIR/launchd/crosstalk-housekeeper.plist"
LOG_DIR="$HOME/Library/Logs"

cmd="${1:-install}"

install_job() {
    label="$1"
    template="$2"
    dest="$HOME/Library/LaunchAgents/$label.plist"
    mkdir -p "$(dirname "$dest")" "$LOG_DIR"
    python3 "$SCRIPT_DIR/launchd/render_plist.py" "$template" "$dest" "$REPO_ROOT" "$HOME"
    # Boot out any stale instance first (ignore failure when none is loaded),
    # then load the fresh plist.
    launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_NUM" "$dest"
    echo "installed $label (repo $REPO_ROOT)"
}

uninstall_job() {
    label="$1"
    launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$label.plist"
    echo "uninstalled $label"
}

case "$cmd" in
  install)
    install_job "$SERVE_LABEL" "$SERVE_TEMPLATE"
    install_job "$HOUSEKEEPER_LABEL" "$HOUSEKEEPER_TEMPLATE"
    ;;
  uninstall)
    uninstall_job "$SERVE_LABEL"
    uninstall_job "$HOUSEKEEPER_LABEL"
    ;;
  *)
    echo "usage: $0 [install|uninstall]" >&2
    exit 2
    ;;
esac
