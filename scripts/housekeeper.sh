#!/bin/sh
# Housekeeper: check the logs and, when this install turned it on, push. It
# never commits. The push is off by default; turn it on with
#   git config crosstalk.autopush true
# The serve process never runs git, so no two jobs fight over the working
# tree the server serves from.
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
LOG_FILE="$HOME/Library/Logs/crosstalk-check.log"

# housekeeper.sh [--dry-run] [--data-root DIR]: with a data root, each
# relay.py command reads that folder; git still runs in this checkout.
dry_run=0
data_args=0
while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) dry_run=1; shift ;;
      --data-root) [ $# -ge 2 ] || { echo "housekeeper: --data-root needs a folder" >&2; exit 2; }
                   DATA_ROOT="$2"; data_args=1; shift 2 ;;
      *) shift ;;  # as before: any other argument is ignored
    esac
done

relay() {
    if [ "$data_args" -eq 1 ]; then
        python3 "$REPO_ROOT/scripts/relay.py" --data-root "$DATA_ROOT" "$@"
    else
        python3 "$REPO_ROOT/scripts/relay.py" "$@"
    fi
}

cd "$REPO_ROOT"

# Run the check, capturing its output so a failure can be logged.
if check_output=$(relay check 2>&1); then
    check_status=0
else
    check_status=$?
fi

if [ "$check_status" -ne 0 ]; then
    if [ "$dry_run" -eq 1 ]; then
        echo "housekeeper: check failed (exit $check_status); would log to $LOG_FILE"
    else
        {
            echo "housekeeper: relay.py check failed (exit $check_status) at $(date)"
            echo "$check_output"
        } >> "$LOG_FILE"
    fi
    echo "$check_output" >&2
    exit 0
fi

printf '%s\n' "$check_output"

# App data (log/, participants.json) is git-ignored, so there is nothing to
# commit. The push sends the commits a person or an agent made.
# A deployment (relay.py deploy) has no .git: there is nothing to push.
if [ -e "$REPO_ROOT/.git" ]; then
    autopush=$(git config --bool crosstalk.autopush 2>/dev/null || true)
else
    autopush=deployment
fi

if [ "$dry_run" -eq 1 ]; then
    if [ "$autopush" = "true" ]; then
        echo "housekeeper: would run: git push origin HEAD"
    elif [ "$autopush" = "deployment" ]; then
        echo "housekeeper: would not push (a deployment has no checkout)"
    else
        echo "housekeeper: would not push (crosstalk.autopush is not true)"
    fi
    exit 0
fi

# The run is reported as the coordinator (the roster's relay). With none set
# there is nobody to report as, and the step is skipped.
coordinator=$(relay coordinator 2>/dev/null || true)
if [ -n "$coordinator" ]; then
    relay schedule ran --from "$coordinator" --name housekeeper-launchd --result ok \
        >/dev/null 2>&1 || echo "housekeeper: could not report the run" >&2
fi

if [ "$autopush" = "true" ]; then
    git push origin HEAD
elif [ "$autopush" != "deployment" ]; then
    echo "housekeeper: no push (crosstalk.autopush is not true)"
fi
