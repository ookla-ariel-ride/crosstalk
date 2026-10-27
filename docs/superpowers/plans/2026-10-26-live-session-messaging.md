# Live-Session Messaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a project's Claude Code session is running, Crosstalk posts that project's new inbox items into the session through its documented inbox socket, and removes the agent-wake feature.

**Architecture:** Sessions register themselves through `SessionStart`/`SessionEnd` hooks that call `relay.py live register|unregister`, which write per-session files in `~/.crosstalk-live/`. A `Notifier` thread in `relay.py serve` polls the log, works out each live participant's new actionable items, and posts one message per session over its Unix socket (auth line, then a user line). The snapshot gains a `live` block and the page shows a "live" badge in place of the wake badges.

**Tech Stack:** Python 3.9 standard library only (`socket`, `json`, `fcntl`, `threading`), unittest, single-file vanilla JS page with the Playwright page-scenario harness in `tests/page/`.

**Spec:** `docs/superpowers/specs/2026-10-26-live-session-messaging-design.md`

## Global Constraints

- Python 3.9 stdlib only; no new dependencies. Tests: `python3 -m unittest discover -s tests`.
- Registration dir `~/.crosstalk-live/` mode `0700`; each file `0600`, written atomically; override with `CROSSTALK_LIVE_DIR`.
- Registration file name `<pid>--<session_id>.json`, content `{pid, session_id, socket, token, registered}`.
- Notifier state `~/.crosstalk-notify-state.json` (`0600`, written only on change, flock on `<state>.lock`); override with `CROSSTALK_NOTIFY_STATE`.
- Poll every 5 seconds; failed-send retry at most once per 60 seconds per registration.
- Wire format, in this order on one connection: `{"type":"auth","token":"<token>"}\n` then `{"type":"user","from":"crosstalk","message":{"content":"<text>"}}\n`.
- Item ids must match `^[A-Za-z0-9._-]{1,80}$`; summaries truncated to 160 characters with newlines collapsed to spaces.
- `token` is never logged, printed by `live status`, or included in any API response.
- `register`/`unregister` always exit 0 and print nothing to stdout; problems go to stderr.
- `relay.py serve --no-notify` disables the notifier; `--no-wake`, `wake.json`, `wake.py` are removed.

## Review Focus

1. A session that hits `/clear` or resumes fires `SessionStart` again with a new `session_id` but the same socket: it must be told once, not twice. `register` deletes any other registration file with the same `socket` (test in Task 1).
2. A session that crashes never runs `SessionEnd`: its socket file is gone (ENOENT) or refuses (ECONNREFUSED) and the registration must be deleted on the next send attempt, not retried forever (test in Task 2).
3. Several items arriving in one poll for a session must arrive as one message, and items arriving while a send is failing must all be delivered once the session is reachable again (test in Task 2).
4. A server restart must not re-send items already told (state persists) and must not send a backlog for a session first seen after restart (seed on first sight) (test in Task 2).
5. A summary containing newlines, very long text, or text that looks like markup must arrive as one truncated line per item (test in Task 2).

---

### Task 1: Session registration (`live` commands)

**Files:**
- Create: `scripts/crosstalk/live.py`
- Modify: `scripts/relay.py` (add `live` subcommand with `register`, `unregister`, `status`, `hooks`)
- Test: `tests/test_live.py`

**Interfaces:**
- Produces:
  - `live_dir() -> Path` (env `CROSSTALK_LIVE_DIR`, else `~/.crosstalk-live`)
  - `register(root: Path, pid: str, env: Mapping[str,str], stdin_text: str, now: float) -> Optional[Path]` — returns the written file or None (problem reported on stderr).
  - `unregister(pid: str, stdin_text: str) -> bool`
  - `Registration` (dataclass/NamedTuple): `pid, session_id, socket, token, registered, path`
  - `load_registrations(root: Path) -> Tuple[List[Registration], List[str]]` — valid ones, plus problem strings for invalid files (ignored).
  - `hooks_json(root: Path, pid: str) -> str`

- [ ] **Step 1: Write failing tests** in `tests/test_live.py` using a temp dir for `CROSSTALK_LIVE_DIR`, a `TempRepo` roster, and a real listening Unix socket created under a temp `cc-socks` directory (patch the allowed-directory check through a module-level `SOCKET_DIR_RES` list so tests can add the temp dir):
  - `test_register_writes_private_file`: env `CLAUDE_CODE_MESSAGING_SOCKET`/`CLAUDE_CODE_MESSAGING_TOKEN`, stdin `{"session_id":"s1"}` → file `vectorcraft--s1.json` exists, mode `0o600`, dir mode `0o700`, JSON has `pid, session_id, socket, token, registered`.
  - `test_register_replaces_same_socket`: register `s1` then `s2` with the same socket → only `vectorcraft--s2.json` remains.
  - `test_register_silently_ignores_problems`: missing env, unknown pid, bad session_id (`../x`), non-JSON stdin → returns None, no file, no exception.
  - `test_unregister_removes_file`: returns True and file gone; second call False.
  - `test_load_registrations_validates`: files with socket outside allowed dirs, socket path not a socket, empty token, token over 512 chars, unknown pid, malformed JSON → excluded and each yields one problem string; a valid one is returned.
  - `test_cli_register_exits_zero_and_prints_nothing`: run `relay.py live register vectorcraft` via subprocess with no env → exit 0, empty stdout.
  - `test_status_never_prints_token`: `relay.py live status` output contains `vectorcraft` and the session id and not the token.
  - `test_hooks_json_has_absolute_paths`: parsed JSON has `hooks.SessionStart[0].hooks[0].command` ending in `relay.py live register vectorcraft` and starting with `python3 /`, and a matching `SessionEnd` unregister.

- [ ] **Step 2: Run** `python3 -m unittest tests.test_live -v` — expect import failure.

- [ ] **Step 3: Implement `scripts/crosstalk/live.py`** with the interfaces above. Allowed socket directories (module list `SOCKET_DIR_RES`): `^/(private/)?tmp/cc-socks(-\d+)?$`, `^/(private/)?var/folders/.+/cc-socks$`, `^/run/user/\d+/cc-socks$`; the socket must be an absolute path, `stat.S_ISSOCK`, owned by `os.getuid()`. `session_id` must match `^[A-Za-z0-9._-]{1,128}$`. `pid` must be in `participants.json` (via `load_roster`). Atomic write: temp file in the same dir opened with `0o600`, then `os.replace`.

- [ ] **Step 4: Wire `relay.py live …`** subcommands. `hooks` prints `hooks_json`; `status` prints one line per registration: `<pid>  <session_id>  registered <iso>`.

- [ ] **Step 5: Run** the new tests and the full suite — all pass.

- [ ] **Step 6: Commit** `feat(live): session registration via relay.py live`.

---

### Task 2: The notifier

**Files:**
- Create: `scripts/crosstalk/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `load_registrations`, `Registration` (Task 1); `actionable_items(docs, pid, roster) -> List[str]` (copy it from `scripts/crosstalk/wake.py` unchanged, with `SAFE_ID_RE`).
- Produces:
  - `render_message(name: str, pid: str, items: List[dict], root: Path) -> str` — `items` are message docs (`_id`, `kind`, `from`, `summary`).
  - `send_to_session(socket_path: str, token: str, text: str, timeout: float = 5.0) -> None` — raises `SessionGone` on ENOENT/ECONNREFUSED, `OSError` otherwise.
  - `class Notifier(root, snapshot, state_path: Optional[Path], clock=time.time, poll_sec=5.0, send=send_to_session)` with `poll_once()`, `start()`, `stop()`, `status() -> Dict[str, dict]`, attribute `disabled`.

- [ ] **Step 1: Write failing tests** in `tests/test_notify.py`. A `FakeSession` helper binds a Unix socket in a temp `cc-socks` dir, accepts connections on a thread and records received lines; modes `refuse` (close listener → ECONNREFUSED) and `vanish` (unlink socket). Drive `Notifier` with an injected clock and `poll_once()`; snapshot from a `TempRepo` replay.
  - `test_new_owner_message_is_told_once`: register live session, first poll seeds (no lines), append owner message to vectorcraft, poll → exactly two lines received: first `{"type":"auth","token":T}`, second `type=="user"`, `from=="crosstalk"`, content contains the message id and `relay.py inbox vectorcraft`; poll again → nothing new.
  - `test_items_in_one_poll_arrive_as_one_message`: two new items → one user line naming both ids.
  - `test_first_sight_seeds_backlog`: items exist before the registration appears → nothing sent.
  - `test_replies_quarantined_and_own_messages_tell_nobody`.
  - `test_two_sessions_both_told`.
  - `test_vanished_or_refusing_session_is_unregistered`: modes `vanish` and `refuse` → registration file deleted, status shows no session for that pid.
  - `test_other_errors_retry_after_60s`: `send` stub raises `OSError(EIO)` → told set unchanged; poll at +30 s does not call send; poll at +61 s calls send again; once it succeeds, all pending items (including ones that arrived meanwhile) go in one message.
  - `test_told_survives_restart`: new Notifier on same state file sends nothing for already-told ids.
  - `test_summary_is_one_truncated_line`: summary `"a\nb" + "x"*300` → the item line has no newline inside and is cut at 160 characters.
  - `test_second_notifier_on_same_state_is_disabled`.
  - `test_status_has_no_token`: `status()` → `{"vectorcraft": {"sessions": 1, "since": iso, "last": {"status": "told", "at": iso}}}` and the token string appears nowhere in `json.dumps(status())`.

- [ ] **Step 2: Run** `python3 -m unittest tests.test_notify -v` — expect failures.

- [ ] **Step 3: Implement `notify.py`.** `render_message` text exactly per spec section 2 (header line `Crosstalk: N new item(s) for <pid>.`, one `- <kind> from <Name>: <summary> (<id>)` line per item, then the `Run \`python3 <root>/scripts/relay.py inbox <pid>\` …` paragraph). State per registration key `<pid>--<session_id>`: `told` (list), `last` `{status, at}`, `retry_at`. Reuse the flock pattern of `wake.Waker._take_lock`. Drop state for keys with no registration file. Stale sockets: delete the registration file via `Registration.path`.

- [ ] **Step 4: Run** the new tests and the full suite — all pass.

- [ ] **Step 5: Commit** `feat(notify): tell live sessions about new inbox items`.

---

### Task 3: Server integration and removing agent wake

**Files:**
- Modify: `scripts/crosstalk/serve.py`, `scripts/relay.py`, `README.md`, `PROTOCOL.md`, `docs/status.md`, `.gitignore`
- Delete: `scripts/crosstalk/wake.py`, `tests/test_wake.py`
- Test: `tests/test_serve.py` (replace `WakeServeTests` with `NotifyServeTests`)

**Interfaces:**
- Consumes: `Notifier` (Task 2).
- Produces: snapshot key `live` (`Notifier.status()`, `{}` when disabled or off); `start(..., notify=False, notify_state=None, notify_poll_sec=5.0)` and `serve(..., notify=True)`; CLI `--no-notify`.

- [ ] **Step 1: Write failing tests** in `NotifyServeTests`: snapshot has `live` and no `wake` key; with a fake session registered, an owner message appended through `/api/append` reaches the fake session within 2 poll intervals (`notify_poll_sec=0.1`); `start(..., notify=False)` leaves `live == {}` and starts no thread; the token never appears in the snapshot body; SIGTERM shutdown still exits 143 (keep the existing signal test, minus wake specifics).
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement.** Replace `Waker` with `Notifier` in `ServerState`/`RunningServer`/`serve` (env `CROSSTALK_NOTIFY_STATE`, default `~/.crosstalk-notify-state.json`); snapshot `live` block; remove `wake` stripping that only existed for wake config only if nothing else needs it (roster `wake` keys are no longer special — leave the existing "strip `wake` from roster" lines in place so stale keys never leak); remove `load_wake`/`roster_wake_keys` use from `relay.py check`; `--no-wake` → `--no-notify`; delete `wake.py`, `test_wake.py`, the `wake.json` line in `.gitignore` stays (harmless). Docs: replace the "Waking agents" README section with "Live sessions" (hooks from `relay.py live hooks <pid>`, what a session receives, `live status`), update PROTOCOL.md and docs/status.md.
- [ ] **Step 4: Run** the full suite — all pass; `grep -rn "wake" scripts/ tests/*.py` shows only the roster-stripping lines and their comment.
- [ ] **Step 5: Commit** `feat(serve): run the notifier; remove agent wake`.

---

### Task 4: Page live badge

**Files:**
- Modify: `index.html` (replace `wakeStatus`/`wakeBadge`/`setWake`/`.wake*` CSS and the snapshot `wake` handling around lines 128-130, 291, 1294-1330, 1979-1980)
- Modify: `tests/page/stub.js`, `tests/page/scenarios.js` (replace the `wakeBadges` scenario)

**Interfaces:**
- Consumes: snapshot `live` block (Task 3): `{pid: {sessions, since, last: {status, at}}}`.

- [ ] **Step 1: Write failing scenario `liveBadges`**: feed `live = {vectorcraft: {sessions: 1, last: {status: "told"}}, bewareilive: {sessions: 1, last: {status: "failed"}}}` through the stub's snapshot path → roster row for VectorCraft has `#live-vectorcraft` with text `live`; BewareILive shows `couldn't reach session` with a warning class; AvoidMissingBall has no badge; no element with id starting `wake-` exists.
- [ ] **Step 2: Run** the page scenarios per `tests/page/run.md` — `liveBadges` fails.
- [ ] **Step 3: Implement** `liveStatus`, `liveBadge(id)`, `setLive(map)` replacing the wake equivalents; CSS `.live`, `.live-failed`.
- [ ] **Step 4: Run** all page scenarios in every mode `run.md` lists, plus the Python suite — all pass.
- [ ] **Step 5: Commit** `feat(page): live badge replaces wake badges`.

---

### Owner-gated final check (not a task for subagents)

With owner approval: `relay.py live hooks vectorcraft` → add to `~/GitHub/vectorcraft/.claude/settings.local.json`, restart the service, start a VectorCraft session, post a message from the page, confirm the session replies. Then delete `~/.crosstalk-wake-state.json` and `~/Library/Logs/crosstalk-wake/`.
