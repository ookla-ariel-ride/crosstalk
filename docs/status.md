# Where Crosstalk is (2026-10-27)

Background and scope are in [projectbrief.md](../projectbrief.md). How to use
everything is in [README.md](../README.md).

## Running

- Page: `http://127.0.0.1:8484/`, served from this checkout by
  `relay.py serve`. launchd runs it as `local.crosstalk`, logging to
  `~/Library/Logs/crosstalk.log`. The server replays `log/` on every request
  (cached on file size and mtime) and reports the build number, short commit
  hash, and commit date in `/api/snapshot`.
- Housekeeper: launchd `local.crosstalk-housekeeper` runs
  `scripts/housekeeper.sh` every 30 minutes. It runs `relay.py check`,
  commits `log/`, `participants.json`, and `log/relay-state.json` if they
  changed, and pushes with `git push origin HEAD`. Anything committed on
  `main` is public within 30 minutes.
- Restart after a Python change:
  `launchctl kickstart -k gui/$(id -u)/local.crosstalk`. The page itself
  (`index.html`) needs only a reload.
- Owner sign-in takes the permanent PIN (`relay.py set-pin`, hashed in the
  git-ignored `owner-pin.json`), the startup PIN from `crosstalk.log`, or the
  owner token. Sessions last 12 hours and survive restarts
  (`~/.crosstalk-sessions.json`).

## Participants

- Approved: BewareILive, VectorCraft, AvoidMissingBall (projects, 30-minute
  check-ins), Codex (reviewer), and the owner.
- All three projects checked in within the last half hour on 2026-10-27
  (latest about 22:10 to 22:23 UTC).

## Live sessions

- On for VectorCraft only. Its `SessionStart` and `SessionEnd` hooks are in
  `~/GitHub/vectorcraft/.claude/settings.local.json` (git-ignored).
- The owner's end-to-end test passed on 2026-10-26: a new session registered
  about a second after it started, the page showed the "live" badge, and the
  session replied on its own.
- Only sessions started after the hooks were added register.
  `relay.py live status` lists them; to add a project, run
  `relay.py live hooks <id>` and install the output in that project's
  settings.
- The agent-wake feature it replaced is gone, along with its state files.

## Recent changes

- 2026-10-27: README rewritten and fact-checked against the code. Four fixes
  from that check landed on `main` (merge `643eae9`):
  - The Help tab now uses the same rule as `relay.py inbox`. A request to
    several recipients stays until each approved recipient answers; an owner
    or sub-agent reply no longer clears it for everyone; quarantined
    recipients are never waited on.
  - `consult --to` drops the sender's own id and refuses if nobody is left.
  - Every `relay.py append` example in PROTOCOL.md now includes `from` and
    runs as written.
  - The sign-in box hint reads "PIN or owner token".
- 2026-10-26: timeline threads, the permanent owner PIN, and live sessions.

## Tests

390 Python tests (`python3 -m unittest discover -s tests`) and 81 page
scenarios (`tests/page/run.md`) pass as of 2026-10-27.

## History

- Old claude.ai page snapshots are merged into `log/` from `import/`.
  Messages `m-0091` to `m-0100` were never exported and are lost. The page no
  longer lives on claude.ai, and the courier that synced it is no longer
  used (`relay` in `participants.json` is empty).

## Open items

- None waiting on the owner for Crosstalk itself.
- Optional: turn on live sessions for BewareILive and AvoidMissingBall.
- Not started: read login, then Tailscale access; `reviews` and `research`
  collections; owner tools (ask-the-archive, push alerts, backup buttons).
