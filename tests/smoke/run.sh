#!/bin/sh
# Smoke suite: a real `relay.py serve` (live notices and the auto-granter on)
# over a fixture data root in a temp folder, checked by tests/smoke/smoke.py.
# Usage: sh tests/smoke/run.sh   (exit 0 when every check passes)
# The server, the temp folder and the socket folder go away on every exit.
set -u

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
py=${PYTHON:-python3}
start=$(date +%s)

tmp=$(mktemp -d "${TMPDIR:-/tmp}/crosstalk-smoke.XXXXXX") || exit 1
socks=""
server=""

cleanup() {
    if [ -n "$server" ]; then
        kill "$server" 2>/dev/null
        wait "$server" 2>/dev/null
    fi
    rm -rf "$tmp"
    [ -n "$socks" ] && rm -rf "$socks"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# live.py takes session sockets only in /tmp/cc-socks-<digits>.
n=$$
while ! mkdir -m 700 "/tmp/cc-socks-$n" 2>/dev/null; do
    n=$((n + 1))
    [ "$n" -gt $(($$ + 100)) ] && { echo "FAIL cannot make /tmp/cc-socks-<n>" >&2; exit 1; }
done
socks=/tmp/cc-socks-$n

# Every path the code reads from the environment points into the temp folder,
# so nothing here reaches a real install, a real session or the home folder.
mkdir "$tmp/home"
# `run` execs, so `run ... &` gives $! the pid of Python itself.
run() {
    exec env -i PATH="$PATH" HOME="$tmp/home" LANG=C.UTF-8 \
        CROSSTALK_LIVE_DIR="$tmp/live" CROSSTALK_SESSIONS="" \
        CROSSTALK_NOTIFY_STATE="$tmp/notify-state.json" \
        CROSSTALK_URGENT_STATE="" CROSSTALK_REVIEWS_STATE="" \
        CROSSTALK_BUILDHOST_STATE="$tmp/buildhost-state.json" \
        "$py" "$@"
}

(run "$here/smoke.py" fixture "$tmp") || exit 1

run "$repo/scripts/relay.py" --data-root "$tmp/data" serve --port 0 --no-pin \
    >"$tmp/serve.out" 2>"$tmp/serve.err" &
server=$!

if (run "$here/smoke.py" check "$tmp" "$socks"); then
    status=0
else
    status=1
    echo "--- server stderr (last 20 lines)" >&2
    tail -n 20 "$tmp/serve.err" >&2
fi

kill "$server" 2>/dev/null
wait "$server" 2>/dev/null
server=""
echo "smoke: $([ "$status" -eq 0 ] && echo ok || echo FAILED) in $(($(date +%s) - start)) s"
exit "$status"
