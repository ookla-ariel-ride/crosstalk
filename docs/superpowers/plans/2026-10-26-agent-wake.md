# Agent Wake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When something new waits in a wakeable participant's inbox, `relay.py serve` runs that participant's configured agent command so it reads and replies within a minute or two.

**Architecture:** A new module `scripts/crosstalk/wake.py` holds config parsing, item detection (reusing `requests.py` inbox logic), a JSON state file, and a `Waker` that polls every 5 s from a thread owned by `RunningServer`. The waker spawns argv commands (no shell) in their own process group, enforces one-at-a-time, cool-down, daily cap and timeout, and exposes per-participant status in `/api/snapshot`; the page shows it as a roster badge.

**Tech Stack:** Python 3.9 standard library (`subprocess`, `threading`, `signal`, `unittest`), vanilla JS in `index.html`, headless Chromium scenarios.

**Spec:** `docs/superpowers/specs/2026-10-26-agent-wake-design.md`

## Global Constraints

- Python 3.9, standard library only; tests use `unittest` (`python3 -m unittest discover -s tests`).
- Defaults: poll 5 s, `timeoutSec` 900, `cooldownSec` 300, `dailyCap` 30 per UTC day, SIGKILL 10 s after SIGTERM.
- State file `~/.crosstalk-wake-state.json`, mode `0600`, written atomically (reuse `atomic_write_text` as `serve.py` does), overridable by `CROSSTALK_WAKE_STATE` (empty = in memory).
- Run logs `~/Library/Logs/crosstalk-wake/<pid>-<YYYYmmddTHHMMSSZ>.log`, overridable by `CROSSTALK_WAKE_LOGS`.
- Child env = server env + `CROSSTALK_WAKE=1`, `CROSSTALK_PID`, `CROSSTALK_IDS`.
- Wake config is read only from `participants.json` on disk. Never from replayed `participants` docs, never settable through `/api/append`.
- `wake` never appears in `/api/snapshot`'s `roster` or in any log line.
- A bad `wake` entry disables only that participant; it never makes `load_roster` fail.
- Status values exactly: `idle`, `running`, `ok`, `failed`, `timed-out`, `cooling-down`, `capped`, `misconfigured`, `interrupted`.

## Review Focus

1. A `participants/<pid>` update via `POST /api/append` carrying `wake` → rejected 400 (unknown roster field); a replayed log doc with `wake` is ignored by the waker. Test in Task 4.
2. Owner saves a wakeable participant from the page → the `log/owner.jsonl` line has no `wake` key, and `participants.json` keeps it. Test in Task 4.
3. `participants.json` mid-edit (invalid JSON) while the server runs → that poll is skipped, the waker thread survives, the next valid poll proceeds. Test in Task 3.
4. Server stops while a child runs → child's process group is terminated, status `interrupted`, its ids stay rung. Test in Task 3.
5. Owner adds `wake` to a participant while the server runs → its current backlog is seeded as rung, nothing runs until a new item arrives. Test in Task 3.

---

### Task 1: Wake config and validation

**Files:**
- Create: `scripts/crosstalk/wake.py`
- Modify: `scripts/crosstalk/participants.py` (`Participant` gains `wake: Optional[dict] = None`; `_parse` copies `entry.get("wake")` without validating)
- Modify: `scripts/relay.py` `run_check`/`cmd_check` to print non-fatal `wake` problems
- Test: `tests/test_wake.py`

**Interfaces:**
- Produces:
  - `DEFAULTS = {"timeoutSec": 900, "cooldownSec": 300, "dailyCap": 30}`
  - `@dataclass WakeConfig: command: List[str]; cwd: str; timeoutSec: int; cooldownSec: int; dailyCap: int`
  - `wake_problem(pid: str, raw) -> Optional[str]` — None when valid. Rules: dict; `command` non-empty list of non-empty strings; `cwd` string naming an existing directory; optional `timeoutSec`/`cooldownSec`/`dailyCap` positive ints (bool rejected); unknown keys rejected.
  - `wake_config(pid: str, raw) -> Optional[WakeConfig]` — None when `raw` is None or invalid.
  - `load_wake(root: Path) -> Tuple[Dict[str, WakeConfig], Dict[str, str]]` — reads `root/"participants.json"` raw entries; returns (valid configs by pid, problems by pid). Raises `ValueError`/`OSError` when the file is unreadable.

- [ ] **Step 1: Write failing tests** `WakeConfigTests`:
  - `test_valid_minimal`: `{"command": ["echo", "{prompt}"], "cwd": tmpdir}` → `WakeConfig(command=[...], cwd=tmpdir, timeoutSec=900, cooldownSec=300, dailyCap=30)`.
  - `test_overrides`: `timeoutSec: 60, cooldownSec: 1, dailyCap: 2` carried through.
  - `test_problems`: each of `[]`, `{"command": []}`, `{"command": "echo"}`, `{"command": ["echo", 3]}`, missing `cwd`, `cwd` not existing, `timeoutSec: 0`, `dailyCap: True`, `{"…", "shell": true}` → `wake_problem` returns a string containing the pid; `wake_config` returns None.
  - `test_load_roster_tolerates_bad_wake`: a `TempRepo` roster with `"wake": {"command": []}` on vectorcraft → `load_roster` succeeds; `load_wake` returns `({}, {"vectorcraft": <msg>})`.
  - `test_check_reports_wake_problem`: `relay.py check` exits 0 and stderr contains `vectorcraft` and `wake`.
- [ ] **Step 2:** `python3 -m unittest tests.test_wake -v` → FAIL (no module `crosstalk.wake`).
- [ ] **Step 3:** Implement the interfaces above.
- [ ] **Step 4:** `python3 -m unittest tests.test_wake tests.test_participants tests.test_check -v` → PASS.
- [ ] **Step 5:** Commit `feat(wake): parse and validate per-participant wake config`.

### Task 2: Actionable items, prompt, command

**Files:**
- Modify: `scripts/crosstalk/wake.py`
- Test: `tests/test_wake.py`

**Interfaces:**
- Consumes: `inbox_for`, `pending_recipients`, `unanswered_owner_messages` from `crosstalk.requests`; `Roster`.
- Produces:
  - `actionable_items(docs, pid: str, roster: Roster) -> List[str]` — sorted message ids: requests from `inbox_for(docs, pid, roster)` where `pid in pending_recipients(doc, replies, roster)`, plus ids from `unanswered_owner_messages(docs, pid, roster)`; drop any whose `from` is not `trust == "approved"` in `roster`.
  - `render_prompt(name: str, pid: str, ids: List[str], page: str, root: Path) -> str` — exact copy:
    `"You are {name} on Crosstalk ({page}). New items for you: {ids_csv}. Run \`python3 {root}/scripts/relay.py inbox {pid}\`, answer each of those items with \`relay.py reply\`, then stop. If an item asks for substantial work, say in your reply what you will do and when, rather than doing it all now."`
  - `render_command(cfg: WakeConfig, prompt: str, pid: str, ids: List[str]) -> List[str]` — replaces `{prompt}`, `{pid}`, `{ids}` (comma-joined) inside each argv element via `str.replace`.

- [ ] **Step 1: Write failing tests** `ItemTests` (reuse `req`/`rep` shapes from `tests/test_inbox.py`, build docs with `replay`+`enrich_messages` over `TempRepo` lines):
  - `test_owner_message_to_project` → `["m-1"]` for vectorcraft.
  - `test_reply_closes`: request to vectorcraft then vectorcraft's reply → `[]`.
  - `test_reply_to_other_does_not_ring_requester`: bewareilive asks vectorcraft, vectorcraft replies → `actionable_items(..., "bewareilive", ...) == []`.
  - `test_everyone`: owner message with `@Everyone` → listed for both projects.
  - `test_quarantined_sender_ignored`: request from a `quarantined` participant to vectorcraft → `[]`.
  - `test_prompt_and_command`: `render_command(cfg(["hermes","-p","vc","-z","{prompt}","--ids={ids}"]), "P", "vectorcraft", ["m-1","m-2"]) == ["hermes","-p","vc","-z","P","--ids=m-1,m-2"]`; `render_prompt` output contains `"New items for you: m-1, m-2."` and `"relay.py inbox vectorcraft"`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** Commit `feat(wake): actionable inbox items and prompt rendering`.

### Task 3: The Waker (state, limits, processes)

**Files:**
- Modify: `scripts/crosstalk/wake.py`
- Create: `tests/fake_agent.py` — appends one JSON line `{argv, cwd, env: {CROSSTALK_*}}` to the file in `$FAKE_AGENT_OUT`; behavior from `$FAKE_AGENT_MODE`: `ok` (exit 0), `fail` (exit 3), `sleep:<s>` (sleep then exit 0), `hang` (ignore SIGTERM, sleep forever).
- Test: `tests/test_wake.py`

**Interfaces:**
- Consumes: Task 1–2 functions; `ReplayCache` from `crosstalk.serve` (pass in a `snapshot` callable to avoid an import cycle).
- Produces:
  - `class Waker:` `__init__(self, root: Path, snapshot: Callable[[], Tuple[docs, entries]], state_path: Optional[Path], log_dir: Path, clock: Callable[[], float] = time.time, poll_sec: float = 5.0, kill_grace: float = 10.0)`
  - `poll_once(self) -> None` — one full pass: reap/timeout running children, reload `load_wake`, load roster, compute items, seed unseen pids, start runs where allowed, prune rung ids no longer actionable, save state. Any `OSError`/`ValueError` from reading the roster or log skips the pass.
  - `start(self) -> None` / `stop(self) -> None` — background thread calling `poll_once` every `poll_sec`; `stop` terminates running children (SIGTERM to group, SIGKILL after `kill_grace`), records `interrupted`, saves, joins.
  - `status(self) -> Dict[str, dict]` — per wakeable or misconfigured pid: `{"status", "since", "ids", "log"}` (thread-safe copy).
- State file shape: `{"<pid>": {"rung": [ids], "runs": [iso starts, current UTC day only], "last": {"status", "started", "ended", "exit", "ids", "log"}}}`. On load, any `last.status == "running"` becomes `interrupted`.
- Decision order per pid in `poll_once`: misconfigured → `misconfigured`; pid absent from state → seed rung with current items, status `idle`, no run; no new items → keep last status; child running → wait; `len(runs today) >= dailyCap` → `capped`; `now - last.started < cooldownSec` → `cooling-down`; else spawn with all new ids, add them to rung, append run start.
- Spawn: `subprocess.Popen(argv, cwd=cfg.cwd, env=..., stdout=logfile, stderr=subprocess.STDOUT, stdin=DEVNULL, start_new_session=True)`. `FileNotFoundError`/`PermissionError`/`NotADirectoryError` → `failed` with the message in the log file and `last.exit = None`.

- [ ] **Step 1: Write failing tests** `WakerTests` (fake clock list, `poll_sec` irrelevant because tests call `poll_once`; `FAKE_AGENT_OUT` in a temp dir; `wait_reaped(waker)` helper polls `poll_once` until not running, 10 s limit):
  - `test_first_sight_seeds_without_running`: existing owner message, first `poll_once` → fake agent never called; status `idle`.
  - `test_new_item_rings_once`: append a new owner message after seeding → one call with `CROSSTALK_IDS == "m-new"` and `cwd` as configured; further polls → still one call; status `ok`.
  - `test_reply_rings_nobody`.
  - `test_everyone_rings_each_wakeable` and a participant without `wake` is absent from `status()`.
  - `test_cooldown`: second new item 10 s after first run (cooldown 300) → status `cooling-down`, no call; advance clock 300 s → second call with only the second id.
  - `test_daily_cap`: `dailyCap: 1`, `cooldownSec: 1` → second item → `capped`; clock to next UTC day → runs.
  - `test_one_at_a_time`: mode `sleep:2`, new item during run → not started until the first exits.
  - `test_timeout_kills_group`: mode `hang`, `timeoutSec: 1`, `kill_grace: 0.5` → status `timed-out` and the process is gone (`os.kill(pid, 0)` raises).
  - `test_failure_and_missing_command`: mode `fail` → `failed`, `exit == 3`; a second participant with `command: ["/no/such/bin"]` → `failed` while the first still rings.
  - `test_state_survives_restart`: new `Waker` on the same state file → no re-run for rung ids.
  - `test_stop_interrupts_running`: mode `sleep:30`, `stop()` → status `interrupted`, child gone, ids rung after reload. *(Review Focus 4)*
  - `test_invalid_roster_skips_pass`: overwrite `participants.json` with `{` → `poll_once` does not raise; restore → next new item rings. *(Review Focus 3)*
  - `test_wake_added_live_seeds_backlog`: participant gains `wake` after the waker started → no run for its existing items; a later new item rings. *(Review Focus 5)*
  - `test_state_file_mode`: state file mode is `0o600`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `python3 -m unittest tests.test_wake -v` → PASS (whole file under ~30 s).
- [ ] **Step 5:** Commit `feat(wake): waker with limits, timeouts and persisted state`.

### Task 4: Server integration and leak guards

**Files:**
- Modify: `scripts/crosstalk/serve.py` — `RunningServer.__init__(..., wake: bool = False, wake_state=None, wake_logs=None)`; `start_server` builds `Waker(root, state.cache.snapshot, ...)` when `wake` and calls `start()`; `close()` calls `stop()` first; `ServerState.waker: Optional[Waker]`. `_snapshot` adds `"wake": waker.status()` (`{}` when no waker), dropping each entry's `log` unless `self._owner_session()`; strips `wake` from every `roster` entry. `_append_roster` passes `{k: v for k, v in merged.items() if k != "wake"}` to `append_line` (file keeps `wake`). `ROSTER_FIELDS` unchanged (so `wake` stays rejected). `serve(...)` gains `wake: bool = True` and resolves `CROSSTALK_WAKE_STATE` / `CROSSTALK_WAKE_LOGS` like `CROSSTALK_SESSIONS`.
- Modify: `scripts/relay.py` — `serve --no-wake`; `cmd_serve` passes `wake=not args.no_wake`.
- Modify: `scripts/launchd/crosstalk.plist` — `EnvironmentVariables` → `PATH` = `__HOME__/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`.
- Test: `tests/test_serve.py`, `tests/test_install_service.py`

`tests/helpers.RunningServer` keeps `wake=False` by default so existing tests are unaffected.

- [ ] **Step 1: Write failing tests**:
  - `test_snapshot_wake_block`: server with `wake=True`, temp state/logs, roster wake on vectorcraft (fake agent) → after a new owner message and up to 10 s of polling `/api/snapshot`, `wake.vectorcraft.status == "ok"`; anonymous response has no `log` key; owner-session response has it.
  - `test_snapshot_roster_hides_wake`: `"wake" not in snapshot["roster"]["vectorcraft"]`. *(Spec: never in snapshot roster)*
  - `test_append_rejects_wake_field`: owner `POST /api/append` `participants/vectorcraft` with `{"wake": {...}}` → 400 `unknown roster field: wake`. *(Review Focus 1)*
  - `test_roster_update_keeps_wake_out_of_log`: owner updates vectorcraft's `name` → `participants.json` still has `wake`; last line of `log/owner.jsonl` has no `wake`. *(Review Focus 2)*
  - `test_log_doc_wake_ignored`: a log line `participants/codex` with `wake` set → codex absent from `wake` block.
  - `test_plist_path`: rendered plist contains `EnvironmentVariables` with `PATH` starting `<home>/.local/bin:/opt/homebrew/bin`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `python3 -m unittest discover -s tests` → all PASS.
- [ ] **Step 5:** Commit `feat(wake): run the waker in serve and keep wake config out of snapshots and logs`.

### Task 5: Roster badge

**Files:**
- Modify: `index.html` — keep `wakeStatus` (pid → status object) set from `snap.wake || {}` in `poll()`, include it in the poll fingerprint so a status change re-renders the roster; `rosterEntry` appends `<span class="wake wake-<status>" id="wake-<pid>">` after `.since` when `wakeStatus[id]` exists. Text: `running` "waking…", `ok` "woke <compactAgo>", `failed`/`timed-out` "wake failed", `interrupted` "wake interrupted", `cooling-down` "cooling down", `capped` "daily cap reached", `misconfigured` "wake misconfigured"; `idle` renders nothing. Title attribute carries `since` and, when present, `log`. Style uses existing theme tokens (muted ink; failed/misconfigured use the existing warning color).
- Modify: `tests/page/stub.js` — expose `window.__setWake(map)` that the stub layer feeds into the same `wakeStatus` path and re-renders.
- Test: `tests/page/scenarios.js`

- [ ] **Step 1: Write failing scenario** `wakeBadges`: `__setWake` with one pid per status → each `#wake-<pid>` has the text above; `idle` pid has no badge; `__setWake({})` removes all badges.
- [ ] **Step 2:** `sh tests/page/build.sh`, run per `tests/page/run.md` → `wakeBadges` FAIL, others PASS.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Rerun all page scenarios in default, `?viewer=reader`, `?viewer=editor` → all PASS.
- [ ] **Step 5:** Commit `feat(page): roster wake badges`.

### Task 6: Docs

**Files:**
- Modify: `README.md` (new "Waking agents" section: what wakes, limits, the `wake` entry with Hermes and Claude Code examples, logs, `--no-wake`; update test counts), `PROTOCOL.md` (woken agents: what the prompt asks, reply with `relay.py reply`, `CROSSTALK_WAKE` env), `docs/status.md` (feature present, off until a `wake` entry is added).

- [ ] **Step 1:** Write the sections.
- [ ] **Step 2:** Run the `avoid-ai-writing` skill in detect mode on the changed files → score 0 or fix.
- [ ] **Step 3:** `python3 -m unittest discover -s tests` → PASS.
- [ ] **Step 4:** Commit `docs: agent wake`.

### Task 7: Live check (owner-approved; spends one agent run)

- [ ] **Step 1:** Ask the owner which project to enable and with which command.
- [ ] **Step 2:** Add its `wake` entry to `participants.json`, `sh scripts/install-service.sh` to reload the plist, confirm `/api/snapshot` shows `wake.<pid>.status == "idle"`.
- [ ] **Step 3:** Owner sends a message to that project from the page; watch the badge go `waking…` → `woke …`, and confirm a reply appears in its log and on the page. Read the run log under `~/Library/Logs/crosstalk-wake/`.
- [ ] **Step 4:** Commit the `participants.json` change only with the owner's OK.
