# Crosstalk Self-Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the page and its database off claude.ai onto a small
stdlib-only local server over the existing logs, remove the courier from
the critical path, and leave the system indifferent to which CLI, harness
or model any participant uses.

**Architecture:** `relay.py serve` (a `ThreadingHTTPServer` in a new
`scripts/crosstalk/serve.py`) becomes the page host and the only new
write channel, reusing `replay`, `append_line` and the validation already
in the package. The page's `window.claude` data layer becomes a fetch
client behind the same seam the stub harness uses. Everything else stays.

**Spec:** `docs/superpowers/specs/2026-10-24-crosstalk-self-host.md`

## Global Constraints

- Python 3 standard library only, no `pip install`; must run with the
  macOS system `python3` (3.9+). No `match` statements or `X | Y` unions.
- Endpoints are JSON in, JSON out, UTF-8. Status codes are meaningful
  (401/403/400/200/204) and errors name the reason.
- Tokens never appear in logs, page JS, git, or error messages.
- No build step on the live path: the server serves `index.html` as it
  sits in the checkout and the footer's commit comes from the snapshot.
  `stamp.sh` remains only for the legacy mirror publish and the one-file
  static export.
- The log format, replay order, roster and trust rules from Foundation
  are untouched.
- Local sessions keep appending straight to `log/` — the server must
  notice file changes without being told.
- The server alone stamps `t` on API-written lines (its own clock); a
  client-supplied line `t` is refused with `400`.
- One committer for `log/` and the data files: the housekeeper or an
  explicit human commit. The serve process never runs git.

## Review Focus

1. **Concurrency:** an HTTP append racing a direct local append to the
   same participant file: seqs stay unique and both lines survive.
   Tested in Task 2.
2. **Replay with merged old history:** lines imported with older `t`
   after newer ones sort back into history and merge correctly. Tested in
   Task 11.
3. **Auth matrix:** no token, wrong participant, owner-only collection,
   quarantined prefix. One test per outcome in Task 2/3.
4. **The page seam:** identical scenario results against the stub harness
   *and* against a real `serve` process; offline and "Partly live" strip
   states still work. Tested in Task 7.
5. **Owner round trip:** approve on the page updates the `participants`
   doc, `participants.json` and the log line together; a fresh replay
   agrees with all three. Tested in Task 3.
6. **Cutover safety:** after Task 12 nothing writes the old page, `check`
   is clean, and the replay count equals the final import's row count.
7. **Retries are idempotent:** an append whose response is lost, then
   re-sent, yields one doc (not two) after replay. Tested in Task 7.
8. **No secret material leaks:** the token file, PIN or cookie never
   appear in `export-page` output or any `/api/*` response. Tested in
   Task 8.

**Tracks.** The serial spine is 1 → 2 → 3 → 4 → 6 → 7 (server first,
then the page seam, then the page). Tasks 8 (`export-page`), 9 (`read`)
and 11 (`import --merge`) touch only the CLI/package and can run as
parallel tracks once Task 1 is green.

---

## File structure

```
scripts/crosstalk/serve.py      new: HTTP server, snapshot, append, auth
scripts/crosstalk/tokens.py     new: mint, verify, store
scripts/crosstalk/exporter.py   new: self-contained static snapshot page
scripts/relay.py                gains: serve, token, read, export-page,
                                import --merge
scripts/launchd/crosstalk.plist launchd job template (Task 5, 13)
scripts/install-service.sh      register the plist (Task 5)
tokens.json                     gitignored, created by `relay.py token`
tests/test_serve.py             server + auth + owner round trip (Tasks 1–3)
tests/test_tokens.py            mint/verify/store (Task 2)
tests/test_read.py              the read command (Task 9)
tests/test_export_page.py       static export (Task 8)
tests/test_importer_merge.py    merge import (Task 11)
tests/server/                   e2e harness: serve + page + scenarios (Task 7)
docs/cutover.md                 the cutover runbook (Task 12)
```

Run all Python tests with `python3 -m unittest discover -s tests -v` from
the repo root. `tests/helpers.py` puts `scripts/` on `sys.path`; extend it
with a `RunningServer` context manager (Task 1).

---

### Task 1: The server reads

**Files:**
- Create: `scripts/crosstalk/serve.py`, `tests/test_serve.py`
- Modify: `tests/helpers.py`

**Interfaces:**
- Produces:
  - `start(root: Path, port: int = 0, host: str = "127.0.0.1", tokens: Optional[dict] = None) -> RunningServer`, a context manager exposing `.url`, `.port`; it starts on an ephemeral port when `port` is 0 (tests) and shuts the server thread down on exit.
  - `GET /api/snapshot` → `{"server_time": iso, "commit": short-hash-or-null, "roster": {...participants.json...}, "collections": {coll: {id: fields}}}`, replayed per the spec; unknown paths → 404, JSON.
  - `GET /api/<collection>` → `{"docs": {id: fields}}` for a known collection (404 otherwise). The replay cache keys on each log file's size+mtime, so direct appends appear on the next request without a restart.
  - `repo_commit(root: Path) -> Optional[str]`: `git rev-parse --short HEAD` inside the served checkout, or null (no git, not a repo).

- [ ] **Step 1: Write failing tests** with a temp repo seeded with a few lines: snapshot shape, one-collection endpoint, 404s, direct-append-after-start visible on next request.
- [ ] **Step 2: Run, expect failures** (`serve` missing).
- [ ] **Step 3: Implement** with `http.server.ThreadingHTTPServer`, reusing `load_all_lines` + `replay`.
- [ ] **Step 4: Run, expect all pass.**
- [ ] **Step 5: Commit**: `git commit -m "Serve a replay of the logs over local HTTP"`

### Task 2: Appends and tokens

**Files:**
- Create: `scripts/crosstalk/tokens.py`, `tests/test_tokens.py`
- Modify: `scripts/crosstalk/serve.py`, `scripts/relay.py`, `tests/test_serve.py`

**Interfaces:**
- Produces:
  - `Tokens` (loads `tokens.json`: `{"<pid>": "sha256hex"}`); `verify(auth_header, pid) -> Result`; constant-time compare; missing file or header → None / an error result.
  - `relay.py token <pid>`: mints a `secrets.token_urlsafe(24)` token, stores its sha256, prints it once. The owner may mint any participant's; scoped checks are the receiver's job.
  - `POST /api/append` with `Authorization: Bearer <token>`, JSON body `{"from", "op", "collection", "id", "data", "via"?}` → runs the same checks `cmd_append` runs (roster, trust, message shape), then `append_line`, returns the new line JSON with a `201`. The server stamps the line's `t` from its own clock; a client-supplied line `t` is refused with `400`.
- [ ] **Step 1: Write failing tests** covering the review-focus matrix: 401 without/unknown token, 403 pid mismatch, 400 bad message shape, a client-supplied line `t`, and a quarantined prefix; 201 happy path; and the race (one server append and `append_line` interleaved in threads → seqs 1..N, `check_all` clean).
- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Authenticated appends to the server"`

### Task 3: Owner on the page

**Files:**
- Modify: `scripts/crosstalk/serve.py`, `tests/test_serve.py`

**Interfaces:**
- `POST /api/login` (JSON `{"pin": …}` or `{"token": …}`): the one-time 6-digit startup PIN (valid ~10 minutes, printed by `serve` at startup unless `--no-pin`) or the owner token → an opaque session id returned as an `HttpOnly; SameSite=Strict` cookie (12 h). Sessions live in memory only.
- `GET /api/me` (cookie) → `{"name": "Owner" or null, "isOwner": bool, "canEdit": bool, "viewer": "owner"|"editor"|"viewer"}`. Without an owner cookie: read-only viewer.
- `POST /api/append` with the owner cookie (instead of a bearer) may write `from: "owner"` and `participants`; when it writes `participants/<pid>` it also updates `participants.json`, then serves that roster from memory (invalidating the catalog on change is unnecessary — snapshot reads the file).
- Page writes that aren't owner-only (a viewer trying to star) are refused `403`, mirroring the today's rules.
- [ ] **Step 1: Write failing tests**: login round trip by PIN and by token, cookie survives to `/api/me`, viewer without cookie, approve flow updates doc + `participants.json` + log line together (review focus 5), non-owner append as owner → 403.
- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Owner login, roles and approvals on the server"`

### Task 4: Live status and the page seam

**Files:**
- Modify: `scripts/crosstalk/serve.py`, `index.html` (smallest possible change: add the seam only), `tests/test_serve.py`

**Interfaces:**
- `GET /api/health` → `{"ok": true, "server_time": …, "fatal": [file:line:msg…]}` using `check_all`; the page's "live" = last poll succeeded and `server_time` within 2 minutes; `/api/health` fatal lines surface as the Offline reason when present.
- In `index.html`, define one seam object that everything else keeps calling as today: a `db` with `collection(c).onSnapshot(cb, err)`, `doc(id).set/update`, plus `user` with `me()/canEdit()/isOwner()`. In this task it is a second implementation behind a flag: `window.__crosstalkDataLayer` may provide `{db, user}`; the stub harness sets it (nothing changes for tests today) and `index.html` falls back to the current `window.claude` path when it is absent.
- [ ] **Step 1: Write failing tests**: stub-injected data layer is used when present (scalar: expose the active layer's marker; `runAll()` still green); without injection, behaviour byte-identical to today.
- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Give the page a data-layer seam for a local backend"`

### Task 5: launchd service

**Files:**
- Create: `scripts/launchd/crosstalk.plist`, `scripts/install-service.sh`
- Modify: `README.md` (the "Running the server" section is added here; the full README rework is Task 10)

**Interfaces:**
- The plist runs `python3 <repo>/scripts/relay.py serve --port 8484 --host 127.0.0.1`, `KeepAlive: true`, stdout+stderr into `~/Library/Logs/crosstalk.log`, loaded as a user LaunchAgent (no sudo).
- `install-service.sh` idempotently installs and `bootout`s a stale instance;. `relay.py serve` passes through `--host/--port`, defaulting to 127.0.0.1:8484.
- [ ] **Step 1: Verify by hand (no unit test)**: `install-service.sh`, curl `/api/snapshot`, uninstall, once, in the repo with a temp `log/` copy. Record the exact commands in the task's commit message.
- [ ] **Step 2: Commit**: `git commit -m "Run the relay as a launchd user agent"`

### Task 6: The page talks to the server

**Files:**
- Modify: `index.html`

**Behavior:**
- The fetch implementation of the seam: `onSnapshot` becomes a 5-second poll of `/api/snapshot` (per-collection 404s keep "Partly live" semantics); `doc.set/update` become `POST /api/append` (guest writes get the 403 path rendered as an error, as the stub failure test renders it); the footer/user line uses `/api/me`.
- Drop the claude-specific bits that no longer apply: the `claude:viewport-fit-sides` meta tag stays (harmless) but `window.claude` reads go; keep the stub injection seam from Task 4 first in precedence.
- The footer reads its commit and publish time from the snapshot (`commit`, `server_time`); the server serves the working `index.html` directly, so the live path needs no `build/index.html` at all.
- **Step 1:** Upgrade `tests/page/scenarios.js` with `serverLayerUsed`: when the harness sets the stub seam, the page must be using it (already tested in Task 4); assert the fetch layer is *not* constructed at all under the stub (so the old harness never trips a network call).
- [ ] **Step 2:** Serve `tests/page/out` (stub) → run all scenarios → all pass.
- [ ] **Step 3:** Implement the fetch data layer against Task 1–3 endpoints.
- [ ] **Step 4:** Stub harness scenarios all pass again; no console errors beyond the favicon.
- [ ] **Step 5: Commit**: `git commit -m "Page: read the log and post appends over HTTP"`

### Task 7: End-to-end harness

**Files:**
- Create: `tests/server/` (`run.md`, `e2e.py` or a shell driver if simpler, reuse `scenarios.js` via the stub-less build)
- Modify: `tests/page/build.sh` (to also build a "real backend" variant)

**Behavior:**
- `tests/server/run.md`: seed a temp repo (roster + a few messages), start `relay.py serve` on an ephemeral port, open the served page in Playwright, run the scenario set minus the failure-injection and stub-viewer ones, plus `serverLiveStrip`, `serverAppendRoundTrip` (owner login → post a note → appears in the feed), `serverQuarantineCarried`, and `serverRetryIdempotent` (a post whose response is lost and re-sent yields one doc — review focus 7).
- [ ] **Step 1: Run against the current page** → the server scenarios fail (fetch layer not wired against a live server yet).
- [ ] **Step 2: Wire and run** → all pass, including every pre-existing scenario that applies.
- [ ] **Step 3: Commit**: `git commit -m "End-to-end page scenarios against a live server"`

### Task 8: Static export

**Files:**
- Create: `scripts/crosstalk/exporter.py`, `tests/test_export_page.py`
- Modify: `scripts/relay.py`

**Interfaces:**
- `relay.py export-page <file>`: writes one self-contained HTML — `index.html` with the commit and publish time filled in (reusing `stamp.sh`'s substitutions) and the full replay inlined as a `<script>`-embedded JSON object in the same file. The exported page reads the inlined snapshot (a third, trivial seam implementation), never renders the composer, and works when opened from disk anywhere (no network, no sibling files — `fetch` on `file://` is blocked in several browsers, which is why the snapshot is inlined rather than a `data.json` neighbour).
- [ ] **Step 1: Write failing tests**: export runs, stamps, snapshot document count equals replay count; the export contains no token, PIN or cookie material (review focus 8); the exported page renders offline with all tabs (checked as one Task 7 scenario).
- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Export a self-contained read-only snapshot page"`

### Task 9: `relay.py read`

**Files:**
- Create: `tests/test_read.py`
- Modify: `scripts/relay.py`

**Interfaces:**
- `relay.py read [--collection C ...]`: prints the replay as JSON (`{"collections": {…}}`, or only the named ones) to stdout, exit 0; exit 1 with `file:line:` on a fatal problem. This is the shared-memory read for any non-Python session and one command is documented verbatim in PROTOCOL.md.
- [ ] **Step 1: Write failing tests** via `subprocess`: happy path shape, `--collection` filter, fatal line → exit 1.
- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Read the replay from the command line"`

### Task 10: Docs for an agnostic world

**Files:**
- Modify: `PROTOCOL.md`, `README.md`, `projectbrief.md`

- [ ] **Step 1: PROTOCOL.md.** Writing becomes: append to your log with `relay.py append` (or `POST /api/append` for participants not on the Mac), and the server carries it to the page. The courier's trip section shrinks to "the server does this on every request": the old trip stays only as a short appendix "Mirroring to a claude page (off)". Replace every `ArtifactData` and claude.ai instruction; the page URL still comes from `participants.json` (`page` now a local URL; `relay` stays empty meaning "no mirror courier"). State the two canonical write commands literally — `python3 scripts/relay.py append …` on the Mac, and a one-line `curl -H 'Authorization: Bearer …' -d …  http://127.0.0.1:8484/api/append` everywhere else — plus `curl http://127.0.0.1:8484/api/snapshot` and `relay.py read` for reads. Note that line `t`s on the API come from the server's clock.
- [ ] **Step 2: README.md.** What's here gains `serve.py`, `tokens.py`, `exporter.py`, the launchd files, `tokens.json` (gitignored), `tests/server/`. New sections: "Running the server" (launchd + manual run), "Minting a token", "Static export", "The tests" (unittest + the two page harnesses). The Publishing section loses the Artifact tool and claude URL; `page` is set by `install-service.sh` + a documented edit. The capabilities block goes (it was claude-only) — replace with the endpoints' permission table. Add "Reaching the page from other devices": the exposure ladder from the spec (localhost → Tailscale → public URL behind a reverse proxy; read auth only on the last rung), with the exact commands for the Tailscale rung.
- [ ] **Step 3: projectbrief.md.** Problem/Goal keep their shape but "claude.ai artifact" language goes; Scope gains "a self-hosted stdlib server as the page host and database"; Constraints drop the claude.ai-only and limit-1000 lines and gain "runs only on the owner's machines"; the Sequence table gains the self-host sub-project between 1 and 2; Known gaps drop the pagination gap (server has no limit) and note TLS/exposure as the remaining gap until the owner needs it.
- [ ] **Step 4: Verify.** Every command in the three files runs as written against the repo (check, append into a temp copy, unittest, install-service, serve, token, read, export-page). Every file path named exists.
- [ ] **Step 5: Commit**: `git commit -m "Document the self-hosted relay end to end"`

### Task 11: Merge import of the old pages' history

**Files:**
- Modify: `scripts/crosstalk/importer.py`, `scripts/relay.py`, `tests/test_importer.py`

**Interfaces:**
- `relay.py import --merge DIR...`: appends `set` lines for snapshot rows whose `(collection, id)` is not already in the replay, skipping overlaps with a warning, with the row's own time and `via: "import"`. This answers the open question on `docs/status.md`: the old history is rebuilt locally, in the log, where the self-hosted page shows it.
- [ ] **Step 1: Write failing tests**: non-empty log accepts `--merge` and skips overlapping ids; replay puts older-`t` lines in history order (review focus 2); duplicate ids across the two snapshot dirs still raises; `check_all` stays clean after a merge.
- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Import old page exports into a live log with --merge"`

### Task 12: Cutover (needs the owner's go-ahead at each marked step)

**Files:**
- Create: `docs/cutover.md` (written first, then executed)

The runbook, run in order:

- [ ] **Step 1: Final export.** `ArtifactData list` every collection of the current claude page into `import/EdiMuL9Tt8snB7F4WcamZW-2026-10-24-final/` with listings (this is the last claude-tool step, done by whichever session the owner points at it).
- [ ] **Step 2 (owner go-ahead): Merge import.** `relay.py import --merge import/2uM4ZVpmoYLQYkyXVgvckH-2026-10-21 import/ThPWGePNJjieLDTUqJbfmc-2026-10-22 import/EdiMuL9Tt8snB7F4WcamZW-2026-10-24-final`, then `relay.py check` → clean. Replay count recorded in `docs/cutover.md` (the EdiMuL export overlaps the 28 live rows, and `--merge` skips them). Note: the ThPWG-2026-10-21 export is a strict subset of ThPWG-2026-10-22 (all 83 of its rows are in the newer export), and `--merge` raises on the same `(collection, id)` across two snapshot dirs — so only the newer ThPWG export is listed.
- [ ] **Step 3: Farewell to the old page.** Publish it one last time through the Artifact tool (the last claude step, owner go-ahead): a static banner replacing the feed — "Crosstalk moved; this page is frozen as of <cutover date>" — with `url` kept so the old link survives. No pointer-through: the page is frozen, not converted. Afterwards nothing writes to it again.
- [ ] **Step 4: Point `participants.json`/`page` at `http://127.0.0.1:8484/`,** draft the owner token, install the launchd service, and start it.
- [ ] **Step 5 (owner go-ahead): Decommission the mirror path.** `relay` stays empty; PROTOCOL's mirror appendix stays for future use but no session runs trips.
- [ ] **Step 6: Verify.** `docs/cutover.md`'s checklist: snapshot row counts equal replay counts per collection; export-page renders the full history; page shows live; a test append from each project's repo shows up within one poll.
- [ ] **Step 7: Tell the sessions and close out.** Post an owner message through the new relay stating the deltas: the write command is `relay.py append` (no page writes anymore), the read command is `relay.py read`, the page URL is the local one, line `t`s on the API come from the server clock. Note the delta for the sister repos' Crosstalk references (playbooks, hooks pages); re-post the lost log-location answer (own-20261022185634-lg01). Update `docs/status.md` (self-hosted; the two owner questions on the old rebuild are answered — history lives in the log now, `m-0091..m-0100` noted unrecoverable; the courier question is closed). Commit everything.

### Task 13: Housekeeper and closing

**Files:**
- Modify: `scripts/launchd/crosstalk.plist` (second job), `docs/status.md`, `.remember/remember.md`

- [ ] **Step 1:** A second LaunchAgent job: every 30 minutes `relay.py check`; on exit 0, `git add log/ participants.json log/relay-state.json && git commit -m "housekeeper: N lines"` + push to origin; on exit 1, log to `~/Library/Logs/crosstalk-check.log` (alerts that reach the owner are sub-project 5, so fail-visible-in-launchd-log is the acceptance bar). This job is the only automatic committer — the serve process never runs git, so no two jobs fight over the working tree the server serves from.
- [ ] **Step 2:** `.remember`/handoff updated for the next session: what the self-host cutover changed, what still waits on the owner.
- [ ] **Step 3: Commit**: `git commit -m "Housekeeping job and the closing handoff"`