#!/bin/sh
# Housekeeper: check the logs, commit them, and push. The only automatic
# committer — the serve process never runs git, so no two jobs fight over
# the working tree the server serves from.
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
LOG_FILE="$HOME/Library/Logs/crosstalk-check.log"

dry_run=0
if [ "${1:-}" = "--dry-run" ]; then
    dry_run=1
fi

cd "$REPO_ROOT"

# Run the check, capturing its output so a failure can be logged.
if check_output=$(python3 "$REPO_ROOT/scripts/relay.py" check 2>&1); then
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

# The line count for the commit message comes from the check's own tally.
n=$(printf '%s\n' "$check_output" | sed -n 's/^ok: \([0-9][0-9]*\) lines in .*/\1/p')
[ -n "$n" ] || n=0

if [ "$dry_run" -eq 1 ]; then
    if git status --porcelain log/ participants.json log/relay-state.json | grep -q .; then
        echo "housekeeper: would run: git add log/ participants.json log/relay-state.json"
        echo "housekeeper: would commit \"housekeeper: $n lines\""
    else
        echo "housekeeper: nothing to commit"
    fi
    echo "housekeeper: would run: git push origin HEAD"
    exit 0
fi

git add log/ participants.json log/relay-state.json

if git diff --cached --quiet; then
    echo "housekeeper: nothing to commit"
else
    git commit -m "housekeeper: $n lines"
fi

git push origin HEAD
