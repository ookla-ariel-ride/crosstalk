# Where Crosstalk is (2026-10-28)

Background and scope are in [projectbrief.md](../projectbrief.md). How to use
everything starts at [README.md](../README.md).

## Running

- Location: `~/Services/crosstalk`, outside `~/Documents` because macOS
  keeps LaunchAgents out of it. `~/GitHub/crosstalk` reaches it through links
  (keep them). iCloud doesn't back it up; GitHub (every 30 minutes) and Time
  Machine do. The owner chose this on 2026-10-28. Details are in docs/operations.md,
  "Running the server".
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

## Live sessions and check-ins

- Hooks are on for all four Claude Code projects (crosstalk, bewareilive,
  vectorcraft, avoidmissingball), in each repo's git-ignored
  `.claude/settings.local.json`. `SessionStart` registers the session and
  prints the 30-minute check-in rule; `SessionEnd` unregisters it. The hooks
  call `~/Services/crosstalk` directly, not through the `~/GitHub` links.
- On 2026-10-28 the owner asked every session to read its inbox every 30
  minutes, through an in-session cron that is set up again at each session
  start. Request `crosstalk-req-checkin-20261028T1822Z` asked the three
  projects to do so and to register their running sessions. The crosstalk
  session's cron also flags any project that has been quiet for an hour.
- `relay.py live status` lists registrations. A running session can register
  without a restart (docs/live-sessions.md).

## Recent changes

- 2026-10-28 (afternoon): Crosstalk checks in every 30 minutes like the other
  projects; rules moved to `AGENTS.md`, which `CLAUDE.md` imports
  (`@AGENTS.md`), the standard for all projects; gitleaks `pre-commit` and
  `pre-push` hooks (history scanned clean); Claude Code's pre-started spare
  sessions no longer register for live messages; multi-harness research in
  issue #2.
- 2026-10-28: about 09:38, `~/GitHub` was replaced by a real folder holding
  only a BewareILive review folder, which broke every `~/GitHub/...` path. The
  server served 404s and the four sessions that started then never registered
  for live messages. The link was restored and the stray review files merged
  into the real `bil-reviews`. A backup and the renamed stray folder are kept
  at `~/.cache/crosstalk-backup-20261028-094507/` and
  `~/GitHub.stray-20261028-094507`. At 10:33 the repo moved to
  `~/Services/crosstalk` and the services were reinstalled (README note
  `f464b01`).

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

406 Python tests (`python3 -m unittest discover -s tests`) pass as of
2026-10-29; `tests/page/scenarios.js` has 95 page scenarios (`tests/page/run.md`).

## History

- Old claude.ai page snapshots are merged into `log/` from `import/`.
  Messages `m-0091` to `m-0100` were never exported and are lost. The page no
  longer lives on claude.ai, and the courier that synced it is no longer
  used (`relay` in `participants.json` is empty).

## Open items

- None waiting on the owner for Crosstalk itself.
- Not started: read login, then Tailscale access; `reviews` and `research`
  collections; owner tools (ask-the-archive, push alerts, backup buttons).
