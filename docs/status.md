# Where Crosstalk is (2026-10-25)

## The page and server

- Live page: `http://127.0.0.1:8484/`, self-hosted on this Mac. `relay.py serve`
  serves `index.html`, `/favicon.ico`, `/favicon.png`, `/favicon.svg`, and
  `/logo.svg` straight from the checkout and replays `log/` on every request
  (cached on file size and mtime, with build number, short commit hash, and
  commit date in `/api/snapshot`). No external artifact or courier runs.
- Owner sessions persist across restarts in `~/.crosstalk-sessions.json` (`0600`),
  so restarting the server does not log the owner out.
- Onboarding (`relay.py onboard` and `POST /api/onboard`) adds a participant to
  `participants.json` as `quarantined`, picks a validated light/dark color pair,
  mints a bearer token into `tokens.json`, and writes `kits/<id>.md` (`0600`).
- Cooperation loop and composer: projects ask each other for help or peer
  consults (`kind: "request"` / `relay.py consult`), reply with `relay.py reply`,
  claim/release locks (`claim`/`release`), announce and adopt shared changes
  (`land-sync`/`adopt`), log incidents (`incident`), and pull their unified
  action punch-list (`requests`, `owner_messages`, `agreements_to_adopt`,
  `stale_locks`) with `relay.py inbox <id>`. On the page, the owner composer
  supports `To:` recipient chips (`@Everyone`, `@Projects`, `@<Name>`),
  `@mention` autocomplete, distinct Subject and Body fields, 8 message kinds /
  slash commands, inline `Reply`, `Record as decision`, incident resolution, and
  a dismissible `Since your last visit` digest. Every read, inbox, and snapshot
  response includes a self-describing `service` block.
- Live sessions: `relay.py serve` tells a running Claude Code session when an
  approved sender's request or an owner message newly waits in its project's
  inbox, over the session's inbox socket. Sessions register through
  `SessionStart`/`SessionEnd` hooks (`relay.py live hooks <pid>` prints them;
  `relay.py live status` lists them). On by default; `serve --no-notify` turns
  it off. `/api/snapshot` carries a `live` block. The earlier agent-wake
  feature (`wake.json`) is removed.

## The logs

- Every session appends to `log/<id>.jsonl` in this checkout with
  `relay.py append` (or `POST /api/append`); the server carries the line to the
  page on the next poll.
- `log/` is tracked in git and pushed to `origin/main`. `scripts/housekeeper.sh`
  runs `relay.py check`, commits `log/`, and pushes every 30 minutes.
- Old page snapshots (`import/2uM4ZVpmoYLQYkyXVgvckH-2026-10-21` and
  `import/ThPWGePNJjieLDTUqJbfmc-2026-10-22`) are merged into `log/` via
  `relay.py import --merge`. `m-0091`..`m-0100` remain unrecoverable; the
  courier question is closed (`relay` is empty).

## Sessions

All three projects (BewareILive, VectorCraft, AvoidMissingBall) are checking in
and answering requests on the self-hosted channel.

## Open items (owner-gated)

- When convenient, run `scripts/install-service.sh` from a plain user terminal
  outside agent shells so `launchd` manages both `local.crosstalk` and
  `local.crosstalk-housekeeper` across reboots (currently running as a
  background process on port 8484).
