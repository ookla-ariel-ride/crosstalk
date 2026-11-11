#!/bin/sh
# Install (or uninstall) the Crosstalk server and housekeeper as user
# LaunchAgents; `buildhost` installs the optional build-host poller. No sudo: the jobs run as the current user, bound to
# 127.0.0.1.
#
#   install-service.sh [install|buildhost|uninstall] [--data-root DIR]
#
# The jobs run relay.py of this folder. `--data-root DIR` (or CROSSTALK_DATA
# at install time; the flag wins) makes each job pass `--data-root DIR` to it,
# unless DIR is this folder. CROSSTALK_RENDER_ONLY=<dir> writes the plists to
# <dir> and loads nothing (the tests use it).
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
UID_NUM=$(id -u)

SERVE_LABEL="local.crosstalk"
SERVE_TEMPLATE="$SCRIPT_DIR/launchd/crosstalk.plist"
HOUSEKEEPER_LABEL="local.crosstalk-housekeeper"
HOUSEKEEPER_TEMPLATE="$SCRIPT_DIR/launchd/crosstalk-housekeeper.plist"
BUILDHOST_LABEL="local.crosstalk-buildhost"
BUILDHOST_TEMPLATE="$SCRIPT_DIR/launchd/crosstalk-buildhost.plist"
LOG_DIR="$HOME/Library/Logs"

cmd=install
data_arg=""
data_set=0
while [ $# -gt 0 ]; do
    case "$1" in
      --data-root) [ $# -ge 2 ] || { echo "$0: --data-root needs a folder" >&2; exit 2; }
                   data_arg="$2"; data_set=1; shift 2 ;;
      --data-root=*) data_arg="${1#--data-root=}"; data_set=1; shift ;;
      -*) echo "usage: $0 [install|buildhost|uninstall] [--data-root DIR]" >&2; exit 2 ;;
      *) cmd="$1"; shift ;;
    esac
done
if [ "$data_set" -eq 0 ]; then
    data_arg="${CROSSTALK_DATA:-}"
fi
RENDER_ONLY="${CROSSTALK_RENDER_ONLY:-}"
AGENTS_DIR="${RENDER_ONLY:-$HOME/Library/LaunchAgents}"

# With no data root given, keep the one the installed jobs carry, so a rerun
# (`relay.py deploy --restart` among them) does not move the service back to
# the data of the code folder. `--data-root <this folder>` clears it.
if [ -z "$data_arg" ] && [ "$cmd" != uninstall ]; then
    data_arg=$(python3 - "$AGENTS_DIR" "$SERVE_LABEL" "$HOUSEKEEPER_LABEL" "$BUILDHOST_LABEL" <<'PY' 2>/dev/null || true
import plistlib, sys
for label in sys.argv[2:]:
    try:
        with open("%s/%s.plist" % (sys.argv[1], label), "rb") as f:
            args = plistlib.load(f).get("ProgramArguments") or []
        i = args.index("--data-root")
        print(args[i + 1])
        break
    except Exception:  # no such job, not a plist, or no data root in it
        continue
PY
)
fi

# The data root the jobs carry: empty when none is set or it is this folder.
DATA_ROOT=""
if [ -n "$data_arg" ] && [ "$cmd" != uninstall ]; then
    if ! DATA_ROOT=$(cd "$data_arg" 2>/dev/null && pwd -P); then
        echo "$0: the data root $data_arg is not a folder" >&2
        exit 2
    fi
    if [ "$DATA_ROOT" = "$(cd "$REPO_ROOT" && pwd -P)" ]; then
        DATA_ROOT=""
    fi
fi
install_job() {
    label="$1"
    template="$2"
    if [ -n "$RENDER_ONLY" ]; then
        dest="$RENDER_ONLY/$label.plist"
        mkdir -p "$RENDER_ONLY"
    else
        dest="$HOME/Library/LaunchAgents/$label.plist"
        mkdir -p "$(dirname "$dest")" "$LOG_DIR"
    fi
    if [ -n "$DATA_ROOT" ]; then
        python3 "$SCRIPT_DIR/launchd/render_plist.py" "$template" "$dest" "$REPO_ROOT" "$HOME" "$DATA_ROOT"
        where="repo $REPO_ROOT, data $DATA_ROOT"
    else
        python3 "$SCRIPT_DIR/launchd/render_plist.py" "$template" "$dest" "$REPO_ROOT" "$HOME"
        where="repo $REPO_ROOT"
    fi
    if [ -n "$RENDER_ONLY" ]; then
        echo "wrote $dest ($where); loaded nothing"
        return 0
    fi
    # Boot out any stale instance first (ignore failure when none is loaded),
    # then load the fresh plist.
    launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
    # launchd can still hold the old instance for a moment: bootstrap then
    # fails with error 5, so try a few times before giving up.
    tries=0
    until launchctl bootstrap "gui/$UID_NUM" "$dest" 2>/dev/null; do
        tries=$((tries + 1))
        if [ "$tries" -ge 10 ]; then
            echo "could not load $label; run: launchctl bootstrap gui/$UID_NUM $dest" >&2
            exit 1
        fi
        sleep 1
    done
    echo "installed $label ($where)"
}

uninstall_job() {
    label="$1"
    if [ -n "$RENDER_ONLY" ]; then
        rm -f "$RENDER_ONLY/$label.plist"
        echo "removed $RENDER_ONLY/$label.plist; unloaded nothing"
        return 0
    fi
    launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$label.plist"
    echo "uninstalled $label"
}

case "$cmd" in
  install)
    install_job "$SERVE_LABEL" "$SERVE_TEMPLATE"
    install_job "$HOUSEKEEPER_LABEL" "$HOUSEKEEPER_TEMPLATE"
    ;;
  buildhost)
    # Opt-in: polls the build host over ssh every 120 s (docs/operations.md).
    install_job "$BUILDHOST_LABEL" "$BUILDHOST_TEMPLATE"
    ;;
  uninstall)
    uninstall_job "$SERVE_LABEL"
    uninstall_job "$HOUSEKEEPER_LABEL"
    uninstall_job "$BUILDHOST_LABEL"
    ;;
  *)
    echo "usage: $0 [install|buildhost|uninstall] [--data-root DIR]" >&2
    exit 2
    ;;
esac
