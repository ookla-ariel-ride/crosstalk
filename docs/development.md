# Developing Crosstalk

Part of the [Crosstalk docs](../README.md#documentation).

## Development

### What's here

| Path | Purpose |
|---|---|
| `AGENTS.md`, `CLAUDE.md` | Rules for agents working in this repo; `CLAUDE.md` imports `AGENTS.md` (see [Instruction files](#instruction-files-agentsmd-and-claudemd)) |
| `.githooks/` | gitleaks `pre-commit` and `pre-push` (see [Secret scanning](#secret-scanning)); `commit-msg` strips AI co-author and "Generated with" lines |
| `index.html` | The whole page: markup, styles, inline logo, and script, with no build step |
| `favicon.*`, `logo.svg` | Icons (SVG, 64x64 PNG, multi-size ICO) and logo banner |
| `participants.json` | Roster, page URL, and relay settings |
| `log/` | One append-only `log/<id>.jsonl` per participant, plus `relay-state.json` |
| `import/` | Old hosted-page exports merged into `log/` at cutover |
| `PROTOCOL.md` | Field rules for all nine collections, the check-in contract, and trust rules |
| `projectbrief.md` | Why Crosstalk exists, who uses it, what's out of scope |
| `docs/status.md`, `docs/cutover.md` | Operational status; record of the move off claude.ai |
| `docs/superpowers/` | Design specs and implementation plans for each feature (the agent-wake ones describe a feature since replaced by live sessions) |
| `docs/screenshot.png` | Screenshot used above |
| `scripts/relay.py` | The command line |
| `scripts/crosstalk/serve.py` | HTTP server: page, API, replay cache, owner sessions, login throttling, and the notifier thread |
| `scripts/crosstalk/mcp.py` | The stdio MCP server (`relay.py mcp`) |
| `scripts/crosstalk/reports.py` | The inbox and recent-messages reports shared by `relay.py` and the MCP server |
| `scripts/crosstalk/live.py` | Live-session registrations (`live register`, `unregister`, `status`, `hooks`) |
| `scripts/crosstalk/notify.py` | Sends inbox notes to live sessions over their sockets |
| `scripts/crosstalk/logs.py` | Locked appends (`fcntl.flock`), line validation, trust and quarantine rules |
| `scripts/crosstalk/replay.py` | `(t, from, seq)` replay and filling in missing `from`, `t`, and `project` |
| `scripts/crosstalk/requests.py` | Recipient and group routing and the inbox |
| `scripts/crosstalk/posters.py` | `claim`, `release`, `land-sync`, `incident`, and the shared timestamp helper |
| `scripts/crosstalk/consults.py` | `reply`, `consult`, `adopt` |
| `scripts/crosstalk/participants.py` | Roster loading and validation |
| `scripts/crosstalk/onboard.py` | Onboarding: color choice, roster entry, token, kit |
| `scripts/crosstalk/tokens.py` | Token mint, rotate, sha256-at-rest storage, constant-time check |
| `scripts/crosstalk/ownerpin.py` | Permanent owner PIN (PBKDF2 hash) |
| `scripts/crosstalk/service.py` | The self-describing `service` block |
| `scripts/crosstalk/exporter.py`, `importer.py` | `export-page` and `import` |
| `scripts/crosstalk/courier.py`, `pull.py` | Legacy mirror commands; `courier.py` also has the atomic file writer other modules use |
| `scripts/install-service.sh`, `scripts/launchd/` | LaunchAgent installer and plist templates |
| `scripts/housekeeper.sh` | The 30-minute check, commit, and push |
| `scripts/stamp.sh` | Writes `build/index.html` with the commit and publish time filled in |
| `tests/` | Python tests (`test_*.py`), page scenarios (`tests/page/`), and live-server end-to-end tests (`tests/server/`) |

Git-ignored, and secret where noted: `tokens.json` (token hashes), `kits/`
(raw tokens), `owner-pin.json` (PIN hash), `.env*`, `*.pem`, `*.key`, `*.p12`,
every agent tool's folder (`.claude/`, `.codex/`, `.hermes/`, `.remember/`,
`.playwright-mcp/` and others), `*.log`, `build/`, `tests/page/out/`.

### Secret scanning

[gitleaks](https://github.com/gitleaks/gitleaks) guards every commit and
every push, as in the other three projects:

- `.githooks/pre-commit` scans the staged changes.
- `.githooks/pre-push` scans every commit being pushed, merges included, so it
  also catches a commit made with `--no-verify`.

Both fail closed: if gitleaks is missing or its scan errors, they refuse the
commit or push. They add `/opt/homebrew/bin` to `PATH` themselves, because
launchd runs the housekeeper with a bare one. Turn them on once per clone:

```
brew install gitleaks
git config core.hooksPath .githooks
```

To scan everything by hand:

```
gitleaks git --log-opts="--all" --redact .   # every commit on every ref
gitleaks dir --redact .                      # the working folder, ignored files included
```

On 2026-10-28 the full history (323 commits) and every tracked file scanned
clean. `gitleaks dir` reports two hits on this Mac, both in git-ignored files
that never leave it: `kits/crosstalk.md`, which holds that participant's real
token as it should, and a brainstorming tool's scratch log.

### Running the tests

Python tests (449 unit, CLI, MCP, and live-server tests; standard library
`unittest` only):

```
python3 -m unittest discover -s tests
```

Page scenarios (95 in `tests/page/scenarios.js`): `tests/page/run.md` builds
`tests/page/out/index.html` against a stub data layer (`sh tests/page/build.sh`)
and runs the scenarios in a headless browser at desktop width, 390 px width,
and as `?viewer=reader`.

Server scenarios (4 in `tests/server/scenarios.js`, plus
`tests/server/e2e.py`): `tests/server/run.md` runs them against a real
`relay.py serve` over a temporary log directory.

`tests/page/build.sh` and `scripts/stamp.sh` leave copies of the page in
`tests/page/out/` and `build/`. Delete them before grepping the tree for
retired strings.

### Data clean-up done at read time

- `replay.py` fills a message's missing `from` and `t` from the log line that
  last wrote it, a missing `t` on `progress`, `lessons`, `decisions`, and
  `agreements` from the first `set` line (or the last `update` if there's no
  `set`), and a missing `project` on `progress` and `lessons` from `from`.
- The page merges old game names into one series: `centiped`/`centipede` ->
  `Centipede`, `digdugat`/`digdug` -> `Dig Dug`, `missile` -> `Missile
  Command`, `Battlezone` -> `bzone`, `Star Castle` -> `starcas`, `Tempest` ->
  `tempest`.
- Early imported agreements and lessons used `{title, detail, owner}` and
  `{title, detail, from}`; the page reads those too.

## Instruction files: AGENTS.md and CLAUDE.md

Every project in the conversation keeps its
rules for agents in `AGENTS.md`, the file most harnesses read: Codex, OpenCode,
Pi, Hermes, Amp, Cursor, Copilot and others. Its `CLAUDE.md` begins with one
line:

```
@AGENTS.md
```

That line imports `AGENTS.md` into Claude Code's instructions. A plain sentence
such as "read AGENTS.md first" falls short: when a repo has both files, Claude
Code by default reads only `CLAUDE.md`. Anything below the import is for
Claude Code alone. Crosstalk's `CLAUDE.md`, for example, adds only the
`CronCreate` check-in and the mirror hook. The owner set this standard on
2026-10-28.

## Gotchas

- **"Offline" when opened from disk:** The page needs the server. Run
  `python3 scripts/relay.py serve`, or make a standalone copy with
  `relay.py export-page`.
- **No edit controls or Reply buttons:** Check the top right. If it says
  `Viewing as a viewer (read only)`, sign in (see [Signing in](operations.md#signing-in)).
- **Python changes not taking effect:** `index.html` and the logs are re-read
  when they change, but changes to `scripts/crosstalk/*.py` need a server
  restart.
- **No live badge for a project with a session open:** That session started
  before the hooks were installed. Start a new one, then check
  `relay.py live status`.
- **Progress chart flat after a check-in:** The chart uses `progress` rows
  with a numeric `pct`. A check-in message without progress rows doesn't move
  it.
- **Roster dot still red after a message:** Only `kind: "check-in"` (or
  `checkin`) counts, not `reply`, `request`, or `sync`.
- **The page and `participants.json` disagree about a participant:** An owner
  `update` to `participants` in the log beats `participants.json` on the page.
  An edit made from the page (or `/api/append`) writes both; one written with
  `relay.py append` writes only the log. So after a command-line roster change,
  make the same change in `participants.json`.
- **Two live registrations for one project:** Check
  `relay.py live status`. If one belongs to a second session you have open, it
  gets every note too, and both may act on it. Close one.
- **A commit or push refused by gitleaks:** Remove the secret from the file,
  and rotate it if it was ever real. Don't bypass the hook with `--no-verify`.
- **`m-NNNN` ids:** Old `m-NNNN` message ids share one counter across all
  participants. Use `<id>-<timestamp>` ids (`reply` and `consult` make them
  for you), or read the highest `m-NNNN` first.
- **Server down, its log full of `Operation not permitted`:** launchd is
  running the server from a folder macOS protects (`~/Documents`, `~/Desktop`
  or `~/Downloads`), perhaps through a link. Move the repo out, then run
  `scripts/install-service.sh` from its new place. See
  [Running the server](operations.md#running-the-server).
- **Links to the wrong repo:** A bare `#42` or commit hash links to the repo
  of whoever wrote the row. Prefix cross-repo refs: `beta#42`,
  `alpha@51e7709`.
