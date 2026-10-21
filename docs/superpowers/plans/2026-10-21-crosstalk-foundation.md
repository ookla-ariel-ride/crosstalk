# Crosstalk Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make per-participant JSONL logs in this repo the conversation's
source of truth, with a courier helper that keeps the claude.ai page in step,
and a page that shows any number of participants.

**Architecture:** A small Python package (`scripts/crosstalk/`) reads,
validates, appends and replays logs, and turns them into `ArtifactData`
batch files. A Claude session (the courier) makes the actual `ArtifactData`
calls. `index.html` stops hard-coding two projects and reads a
`participants` collection.

**Tech Stack:** Python 3 standard library only (json, re, fcntl, argparse,
unittest, dataclasses, pathlib); plain HTML/CSS/JS for the page; the
existing stub-db harness with Playwright MCP for page checks.

**Spec:** `docs/superpowers/specs/2026-10-21-crosstalk-foundation-design.md`

## Global Constraints

- Python: standard library only, no `pip install`; must run with the
  macOS system `python3` (3.9+), so no `match` statements or `X | Y`
  type unions.
- Log lines: one JSON object per line, UTF-8, `\n`-terminated, keys `seq`,
  `t`, `from`, `op`, `collection`, `id`, `data`, optional `via`.
- `op` is `set` or `update`; in an `update`, `{"__delete__": true}`
  removes a field.
- Participant id: `^[a-z0-9-]+(\.[a-z0-9-]+)*$`. A new doc id must start
  with `<from>-`, except rows that existed before this design.
- `kind` ∈ `project`, `agent`, `reviewer`, `researcher`, `tool`, `person`.
  `agent` needs `parent`.
- `trust` ∈ `approved`, `quarantined`; the default is `quarantined`.
- Collections: `messages`, `agreements`, `waiting`, `locks`, `lessons`,
  `decisions`, `progress`, `incidents`, plus `participants` (owner lines
  only).
- Replay order: `t`, then `from`, then `seq`. The last `set` wins, and
  `update`s merge on top.
- Batches: at most 50 writes, and each doc at most once per batch.
  `if_version` is required on every write to a doc that already exists.
- Check-in dot: amber at `checkInMin + 5` minutes, red at
  `checkInMin + 15`.
- The page writes, and the courier carries quarantined participants'
  lines, but sessions never act on them.
- Never write a row to the page that isn't in a log file first.

## Review Focus

1. **Real rows carry fields and values PROTOCOL.md doesn't list**, e.g. an
   agreement with `status: "ready-to-adopt"`. Import, replay and the page
   must pass them through unchanged. Tested in Task 3 and Task 4.
2. **The owner and an agent change the same doc in one trip.** Both changes
   survive: pull records only the fields the owner actually changed. Tested
   in Task 5.
3. **An agent's direct page write failed, so the page holds an old value.**
   Pull must not record that old value as an owner change and overwrite
   the agent's line. Tested in Task 5.
4. **Bodies with newlines, quotes, emoji or accents** stay on one log line
   and come back byte-identical. Tested in Task 2.
5. **Two processes of the same participant append at once** (a session
   and its sub-agent sharing an id by mistake, or a hook firing twice).
   Their `seq`s must not collide. Tested in Task 2.

---

## File structure

```
participants.json            created in Task 1
scripts/crosstalk/__init__.py
scripts/crosstalk/participants.py   load and validate the roster      (Task 1)
scripts/crosstalk/logs.py           Line, read, check, append          (Tasks 1–2)
scripts/crosstalk/replay.py         replay lines into docs             (Task 3)
scripts/crosstalk/importer.py       snapshots -> log lines             (Task 4)
scripts/crosstalk/pull.py           page export -> owner lines, versions (Task 5)
scripts/crosstalk/courier.py        state, pending, sent, restore      (Task 6)
scripts/relay.py                    argparse CLI over the package      (Task 7)
tests/helpers.py                    temp-repo fixture                  (Task 1)
tests/test_*.py                     one per module
tests/page/                         stub harness + scenarios           (Task 8)
log/                                created by the import in Task 11
```

Run all Python tests with `python3 -m unittest discover -s tests -v` from
the repo root. `tests/helpers.py` puts `scripts/` on `sys.path`.

---

### Task 1: Participants and log checking

**Files:**
- Create: `participants.json` (exactly the spec's example, all four
  participants `approved`)
- Create: `scripts/crosstalk/__init__.py`, `scripts/crosstalk/participants.py`, `scripts/crosstalk/logs.py`
- Create: `tests/helpers.py`, `tests/test_participants.py`, `tests/test_check.py`

**Interfaces:**
- Produces:
  - `Participant` (dataclass): `id, name, kind, color=None, repo=None, parent=None, checkInMin=None, trust="quarantined"`
  - `Roster` (dataclass): `page: str, relay: str, participants: dict[str, Participant]`, with `repo_for(pid) -> Optional[str]` (falls back to the parent's)
  - `load_roster(path: Path) -> Roster`, which raises `ValueError` naming the bad entry
  - `Line` (dataclass): `seq:int, t:str, from_:str, op:str, collection:str, id:str, data:dict, via:Optional[str]=None`, with `to_json() -> str`
  - `Problem` (dataclass): `file:str, line:int, message:str, fatal:bool`
  - `read_log(path: Path) -> tuple[list[Line], list[Problem]]`: stops at the first fatal problem and returns the good lines before it
  - `check_all(root: Path, roster: Roster) -> list[Problem]`
  - `LEGACY_PREFIXES = ("m-", "vc-", "own-", "a-", "d-", "w-", "l-", "p-", "i-", "lesson-")`
  - `tests/helpers.py`: `TempRepo` context manager, with `.root`, `.write_roster(dict)` and `.write_log(pid, list[dict])`

- [ ] **Step 1: Write failing tests**

`test_participants.py`:
- `test_loads_spec_example`: the four ids, `page` and `relay` match.
- `test_defaults_trust_to_quarantined`: an entry without `trust` gives
  `"quarantined"`.
- `test_agent_without_parent_rejected`: raises `ValueError` mentioning the
  id.
- `test_bad_id_rejected`: `"Codex"` and `"a b"` both raise.
- `test_repo_falls_back_to_parent`: `bewareilive.verifier` gives
  `"ookla-ariel-ride/bewareilive"`.

`test_check.py`, one test per spec Errors row. Each asserts the
`Problem.file`, the `Problem.line` (1-based) and `fatal`:
- `test_malformed_json` → fatal at that line.
- `test_seq_gap` (1, 3) → fatal at line 2, message contains `"seq"`.
- `test_from_mismatch` (codex line in `owner.jsonl`) → fatal.
- `test_new_id_without_prefix` (`codex` sets `messages/x-1`) → fatal.
- `test_update_may_name_any_id` (`owner` updates `agreements/a-fast-verify`) → no problem.
- `test_legacy_ids_allowed` (`bewareilive` sets `messages/m-0001`) → no problem.
- `test_unknown_participant_warns` (`log/gemini.jsonl`, not in the roster) → one problem, `fatal=False`.
- `test_lines_before_bad_line_returned`: `read_log` returns line 1 when line 2 is bad.

- [ ] **Step 2: Run, expect failures**

Run: `python3 -m unittest discover -s tests -v`
Expected: errors importing `crosstalk.participants` / `crosstalk.logs`.

- [ ] **Step 3: Implement** `participants.py` and the read/check half of `logs.py` to the interfaces above. Validate `kind`, `trust`, the id pattern and the `parent` rule from Global Constraints.

- [ ] **Step 4: Run, expect all pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add participants.json scripts/crosstalk tests
git commit -m "Add the participants roster and log checking"
```

### Task 2: Appending lines

**Files:**
- Modify: `scripts/crosstalk/logs.py`
- Create: `tests/test_append.py`

**Interfaces:**
- Consumes: `Line`, `read_log` (Task 1).
- Produces: `append_line(root: Path, from_: str, op: str, collection: str, id: str, data: dict, t: Optional[str]=None, via: Optional[str]=None) -> Line`. It takes an exclusive `fcntl.flock` on `log/<from_>.jsonl`, reads the last `seq`, writes `seq+1`, and defaults `t` to now in UTC (`%Y-%m-%dT%H:%M:%SZ`). It writes with `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`.

- [ ] **Step 1: Write failing tests**
- `test_first_line_is_seq_1`, and `test_seq_increments`.
- `test_round_trips_awkward_text`: a body of
  `"line1\nline2 \"quoted\" café 🎮"`. The file has exactly one line, and
  `read_log` returns the same `data`.
- `test_concurrent_appends_unique_seq`: 8 `multiprocessing` processes ×
  25 appends each to `codex`. `read_log` gives seqs 1..200 with no
  problems.
- `test_rejects_unknown_op` (`"delete"` raises `ValueError`).

- [ ] **Step 2: Run, expect failures** (`append_line` missing).
- [ ] **Step 3: Implement** `append_line`.
- [ ] **Step 4: Run, expect all pass.**
- [ ] **Step 5: Commit**: `git commit -m "Append log lines with a file lock"`

### Task 3: Replay

**Files:**
- Create: `scripts/crosstalk/replay.py`, `tests/test_replay.py`

**Interfaces:**
- Consumes: `Line`.
- Produces:
  - `DocKey = tuple[str, str]` (collection, id)
  - `order_key(line: Line) -> tuple` = `(t, from_, seq)`
  - `replay(lines: Iterable[Line]) -> dict[DocKey, dict]`, applying sorted lines: `set` replaces, `update` merges top-level fields and drops a field whose value is `{"__delete__": True}`
  - `load_all_lines(root: Path) -> list[Line]`: every `log/*.jsonl` via `read_log`, keeping the good lines

- [ ] **Step 1: Write failing tests**
- `test_last_set_wins`; `test_update_merges`; `test_delete_marker_removes_field`.
- `test_order_by_t_then_from_then_seq`: two lines with the same `t` from
  `codex` and `bewareilive` → bewareilive applies first.
- `test_update_before_any_set_creates_doc`.
- `test_unknown_fields_and_values_pass_through`:
  `{"status": "ready-to-adopt", "extra": [1, {"x": None}]}` survives
  unchanged (Review Focus 1).

- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Replay log lines into documents"`

### Task 4: One-time import

**Files:**
- Create: `scripts/crosstalk/importer.py`, `tests/test_importer.py`

**Interfaces:**
- Consumes: `append_line`, `load_all_lines`.
- Produces: `import_snapshots(root: Path, snapshot_dirs: list[Path]) -> dict[str, int]` (lines written per participant). Each dir holds `<collection>/<doc_id>.json` with only the doc's fields. It refuses (`RuntimeError`) if `log/` already has any line.
  - Writer by id prefix: `m-` → `bewareilive`, `vc-` → `vectorcraft`, `own-` → `owner`. Otherwise use the row's `project`, else `by`, else `builder`, if that is a known project id, else `bewareilive`. `both` counts as unknown.
  - Time: the first of `t`, `since`, `start` present, else `1970-01-01T00:00:00Z`.
  - Writes `set` lines in global `(time, writer, doc id)` order.
  - Raises `RuntimeError` if two snapshot dirs hold the same `(collection, id)`.

- [ ] **Step 1: Write failing tests**, using small fixture dirs built in
  the temp repo:
- `test_prefix_mapping`: `m-0001` → bewareilive, `vc-x` → vectorcraft, `own-x` → owner, `a-y` with `builder: vectorcraft` → vectorcraft, `lesson-z` with `project: both` → bewareilive.
- `test_time_fields`: `w-` uses `since`, `i-` uses `start`.
- `test_refuses_when_log_not_empty`.
- `test_duplicate_across_snapshots_raises`.
- `test_real_snapshots_import_cleanly`: imports both
  `import/2uM4ZVpmoYLQYkyXVgvckH-2026-10-21` and
  `import/ThPWGePNJjieLDTUqJbfmc-2026-10-21` into a temp copy. `check_all`
  gives no fatal problems, and `replay` yields 215 docs. Includes
  `agreements/a-review-broker-cleanup` with `status == "ready-to-adopt"`.

- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Import page snapshots into the log"`

### Task 5: Pull (the page's changes down to the log)

**Files:**
- Create: `scripts/crosstalk/pull.py`, `tests/test_pull.py`

**Interfaces:**
- Consumes: `load_all_lines`, `replay`, `append_line`, `load_roster`, and `load_state` / `save_state` (Task 6 defines them; implement them here first if Task 6 isn't done, in `courier.py`).
- Produces:
  - `parse_listing(text: str) -> dict[str, int]`: reads the `ArtifactData list` result text the courier saves as `<dir>/<collection>.listing.txt`. It accepts both shapes the tool returns: an `out_dir` summary line, matched by `^- "(?P<id>[^"]+)"\s+\d+ bytes\s+version (?P<v>\d+)`, and a plain-list JSON line `{"id": …, "data": …, "version": N, "updatedAt": …}`. Other lines are ignored.
  - `pull(root: Path, export_dir: Path) -> PullResult`, where `PullResult` (dataclass) is `owner_lines: int, approvals: list[str], versions: dict[str, int]`. The keys of `versions` are `"<collection>/<id>"`. They are saved into `log/relay-state.json` under the roster's current `page`.
  - Raises `RuntimeError("log is empty; run import first")` when no log lines exist.

The owner-change rule (this is the part the tests pin): let `sent` be the
replay of lines up to each participant's bookmark for this page, `all` the
replay of every line, and `page` the exported doc. Then:

- A doc absent from `all` → one `set` line with the whole `page` doc.
- Otherwise, for each field in either: the owner changed it when
  `page[f] != all[f]` **and** `page[f] != sent[f]` (missing counts as a
  value). The changed fields go into one `update` line. A field the owner
  removed becomes `{"__delete__": true}`.
- Owner lines have `from: "owner"`, `via: "page"` and `t` = now.
- For `participants/<pid>` docs, a `trust` change also rewrites that
  entry in `participants.json` and is listed in `approvals`.

- [ ] **Step 1: Write failing tests**
- `test_parse_listing_summary`: lines like
  `- "a-fast-verify"  612 bytes  version 1  "…"` give the expected dict.
- `test_parse_listing_json_lines`: a line
  `{"id":"l-vc-githooks-branch-reviews","data":{…},"version":2,"updatedAt":"…"}`
  gives `{"l-vc-githooks-branch-reviews": 2}`; a header line is ignored.
- `test_no_changes_no_lines`: export == replay → 0 owner lines.
- `test_owner_created_doc`: `messages/own-x` only on the page → one `set`.
- `test_owner_and_agent_same_doc` (Review Focus 2): the agent's unsent
  line sets `status: building`, and the page shows `hold: true` with the
  old `status`. → One owner `update` with only `hold`. After replay, the
  doc has both `status: building` and `hold: true`.
- `test_failed_write_through_not_owner` (Review Focus 3): the agent's
  unsent line sets `released`, and the page lacks it. → 0 owner lines.
- `test_owner_removed_field` → `__delete__` marker.
- `test_approval_updates_roster`: page `participants/gemini` with `trust: approved` → `participants.json` updated, and `approvals == ["gemini"]`.
- `test_refuses_on_empty_log`.

- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Pull the owner's page changes into the log"`

### Task 6: Pending, sent and restore

**Files:**
- Create: `scripts/crosstalk/courier.py`, `tests/test_courier.py`

**Interfaces:**
- Consumes: `load_all_lines`, `replay`, `order_key`, `check_all`, `load_roster`.
- Produces:
  - `load_state(root) -> dict` and `save_state(root, state)`. The file is `log/relay-state.json`: `{"<page url>": {"bookmarks": {pid: seq}, "versions": {"col/id": int}, "participantsHash": str}}`.
  - `pending(root: Path, out: Path) -> list[Path]`. It writes `out/batch-NNN.json` (and `out/docs/NNN-<col>-<id>.json`) and returns the batch paths in order. A batch file is `{"page": url, "advance": {pid: seq}, "writes": [{"op": "set", "collection", "doc_id", "file_path": "<abs path>", "if_version"?}]}`.
    - It collapses all unsent lines per doc into one `set` of that doc's `all` replay.
    - `if_version` comes from the state's `versions` when the key is present.
    - Lines go in `order_key` order of each doc's last unsent line, 50 writes per batch.
    - `advance` holds the highest seq per participant whose lines are all in this batch or earlier ones.
    - When the sha256 of `participants.json` differs from `participantsHash`, the first batch also includes one `participants/<pid>` doc per entry (all fields).
    - It carries nothing from a file past its first fatal `check` problem.
  - `sent(root: Path, batch: Path) -> None`: applies `advance`, and also `participantsHash` when that batch carried participants.
  - `restore(root: Path, out: Path) -> list[Path]`: `pending` for the current `page` with empty bookmarks and versions, so it writes no `if_version`.

- [ ] **Step 1: Write failing tests**
- `test_batches_at_most_50_and_doc_once`: 120 lines over 60 docs → 2 batches (50 + 10 writes), no doc twice in one batch.
- `test_pending_then_sent_then_nothing`.
- `test_bookmarks_move_only_on_sent`: `pending` twice without `sent` → the same output.
- `test_if_version_from_state`.
- `test_participants_mirrored_once`.
- `test_bad_file_stops_at_bad_line_others_go`.
- `test_restore_carries_everything_without_versions`.
- `test_round_trip_real_history`: import both real snapshots (as in Task 4), then `restore`. Apply the batches to a dict fake db (the `set`s; check that no batch has a doc twice). Write the fake db out as an export dir with a matching listing, then `pull` → 0 owner lines, and the fake db has 215 docs plus the 4 `participants` docs.

- [ ] **Step 2–4:** run (fail), implement, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Build courier batches, bookmarks and restore"`

### Task 7: The `relay.py` command line

**Files:**
- Create: `scripts/relay.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces the subcommands below, run from anywhere (the script finds the
  repo root from its own path). They exit 0 on success and 1 on a fatal
  problem, which is printed as `file:line: message`.
  - `check`
  - `append --from ID --op set|update --collection C --id ID (--data JSON | --data-file PATH)`: prints the new seq. This is the one-line entry point for non-Claude agents.
  - `import DIR...`
  - `pull EXPORT_DIR`: prints `owner lines: N, approvals: …`.
  - `pending OUT_DIR` and `restore OUT_DIR`: print the batch paths, one per line.
  - `sent BATCH`

- [ ] **Step 1: Write failing tests** with `subprocess.run([sys.executable, "scripts/relay.py", …])` against a temp repo copy:
- `test_check_clean_exit_0`; `test_check_bad_exit_1_prints_location`.
- `test_append_prints_seq`.
- `test_pending_prints_paths`.

- [ ] **Step 2–4:** run (fail), implement with argparse, run (pass).
- [ ] **Step 5: Commit**: `git commit -m "Add the relay command line"`

### Task 8: The page reads participants

**Files:**
- Modify: `index.html` (the `REPOS`/`NAMES`/`SIDE` constants and `who()`, `refLink()`, `msgCard()`, `positionsEl()`, the `.msg.bil/.vc/.own` CSS)
- Create: `tests/page/stub.js` (from this session's harness; add a `participants` collection and `__store` seeding), `tests/page/run.md` (how to serve and which scenarios to run), `tests/page/scenarios.js`

**Interfaces:**
- Consumes: the `participants` collection docs (fields as in `participants.json`, doc id = participant id).
- Produces, in page code:
  - `people` (an object keyed by id, filled from the snapshot)
  - `person(id) -> {id, name, kind, color, repo, parent, checkInMin, trust}`: unknown ids give `{name: id, color: "var(--muted)", trust: "quarantined"}`
  - `displayName(id)`: `"Parent › Name"` for an `agent`
  - colors set as a `--who` CSS custom property per card, not as classes

Behavior:
- `COLS` gains `participants`.
- Refs: `<id>#N` and `<id>@hash` (case-insensitive hash) resolve through `person(id).repo`, falling back to the parent's.
- `positionsEl` renders one column per key in `positions`.
- The two-lane CSS stays until Task 9 replaces it.

- [ ] **Step 1: Write scenarios** in `tests/page/scenarios.js`, each a function returning `{name, pass, detail}`:
- `unknownSenderNeutral`: a message from `gemini` (not in participants) shows the name `gemini` and the muted color.
- `subAgentTag`: `bewareilive.verifier` shows `BewareILive › verifier`.
- `refViaRoster`: `codex@ABC1234` links to codex's repo when it has one, and is plain text when it doesn't.
- `threeWayPositions`: positions for three ids give three columns, each with a "Go with" button.
- All of this session's earlier checks still pass: composer kind kept, no duplicate posts, Partly live, search spaces, hold release, focus kept.

- [ ] **Step 2: Run them against the current page** (serve `tests/page/` per `run.md`, then run `runAll()` with Playwright `browser_evaluate`). Expected: the new scenarios fail and the old ones pass.
- [ ] **Step 3: Implement** to the behavior above.
- [ ] **Step 4: Run all scenarios.** Expected: all pass, and the console has no errors besides the favicon.
- [ ] **Step 5: Commit**: `git commit -m "Page: participants come from data"`

### Task 9: Feed, roster and quarantine

**Files:**
- Modify: `index.html` (timeline render, strip check-in tile, CSS; remove `.lanes` and two-lane `.log::before`)
- Modify: `tests/page/scenarios.js`

**Interfaces:**
- Consumes: `person()`, `displayName()`, `people` (Task 8).
- Produces:
  - `rosterEl()`
  - a `who` filter (`whoFilter`: participant id or `null`), alongside `kindFilter`
  - `dotState(p, now) -> "ok"|"warn"|"bad"|null`, by last `check-in` message from that id against `checkInMin + 5` / `+ 15` minutes

Behavior:
- Layout is mockup C, kept at `.superpowers/brainstorm/*/content/timeline-layout.html`: a roster panel (Projects with sub-agents nested under their parent, Reviewers and research, Tools and people) next to one feed, newest first.
- Each card has the sender tag in its color, and `→ names` when `to` is set.
- Clicking a roster name sets `whoFilter`; clicking it again clears it.
- Below 640px, the roster becomes a `<details>` with the summary "People".
- A quarantined sender's cards get `.quarantined` (opacity .55) and a "not approved" pill.
- For editors, the roster shows an "Approve" button on quarantined entries. It runs `db.collection("participants").doc(id).update({trust: "approved"})`.
- The strip tile "Last check-in" becomes "Check-ins": "all on time", or the worst one, e.g. "VectorCraft 38 min late".

- [ ] **Step 1: Write scenarios**: `rosterGroups`; `rosterFilter`; `quarantineDimmedAndApprove` (click → the stub store shows `trust: approved`, and the dimming is gone after the snapshot); `dotThresholds` (34 min → ok, 36 → warn, 46 → bad for `checkInMin: 30`); `toArrow`; `mobileRosterFolds` (Playwright `browser_resize` to 390 wide).
- [ ] **Step 2: Run**, expect the new scenarios to fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all scenarios, expect all pass.** Also take one screenshot each at 1100px and 390px, light and dark (`browser_emulate_media`), and look at them.
- [ ] **Step 5: Commit**: `git commit -m "Page: one feed with a roster and quarantine"`

### Task 10: Docs

**Files:**
- Modify: `PROTOCOL.md`, `README.md`, `projectbrief.md`

- [ ] **Step 1: PROTOCOL.md.**
  - Replace "Who writes" with the spec's Writing section.
  - Add sections for participants and trust, log lines (with the
    `relay.py append` example), the courier's trip (the exact command
    sequence: list each collection with `out_dir`, save each list
    result's text as `<dir>/<collection>.listing.txt`, run `relay.py pull`,
    then `relay.py pending`, then `ArtifactData batch` on each file with
    its `writes`, then `relay.py sent` after each), the trip's failure handling from the spec's Errors table (a bad log file → post an `alert` message naming it; a version conflict → stop, retry next trip; a quota failure → `alert` plus a `waiting` row for the owner), and moving to a new
    page.
  - Check-in step 1 skips quarantined senders.
  - Replace the hard-coded page URL with "the `page` in `participants.json`".
- [ ] **Step 2: README.md.**
  - Add `participants.json`, `log/`, `scripts/relay.py`, `scripts/crosstalk/` and `tests/` to "What's here".
  - Add `{"path": "participants", "read": "view", "write": "admin"}` to the capabilities block.
  - Add "Adding a participant" (one roster entry, starts quarantined) and "Running the tests".
- [ ] **Step 3: projectbrief.md.**
  - Move "local copy" and "more agents" from Open questions and Known gaps into Scope.
  - Note the five-sub-project sequence.
- [ ] **Step 4: Verify.** Every command in the three files runs as written against the repo (`check`, `append` into a temp copy, the unittest line). Every file path named exists.
- [ ] **Step 5: Commit**: `git commit -m "Document the log, the roster and the courier"`

### Task 11: Go live (needs the owner's go-ahead at each marked step)

**Files:**
- Create: `log/*.jsonl`, `log/relay-state.json` (by the import and the first trip)

- [ ] **Step 1: Fresh export of the current page.** Run `ArtifactData list` (limit 1000) for all 8 collections, with `out_dir` set to `import/ThPWGePNJjieLDTUqJbfmc-<today>-live/`. Save each result's text as `<collection>.listing.txt` in that folder.
- [ ] **Step 2: Import.** Run `python3 scripts/relay.py import import/2uM4ZVpmoYLQYkyXVgvckH-2026-10-21 import/ThPWGePNJjieLDTUqJbfmc-<today>-live`, then `python3 scripts/relay.py check`. Expected: exit 0. The replay count equals 132 plus the live export's row count.
- [ ] **Step 3: Record the current versions.** Run `python3 scripts/relay.py pull import/ThPWGePNJjieLDTUqJbfmc-<today>-live`. Expected: `owner lines: 0`, because the page rows were imported as their writers. Bookmarks stay empty, so the first trip carries every line: the old page's rows are new docs, and the current page's rows are rewritten with identical content using the versions just recorded (safe, per the spec's "a trip is safe to repeat").
- [ ] **Step 4 (owner go-ahead): Publish the page with the `participants` rule.** Commit, run `scripts/stamp.sh`, then publish `build/index.html` with `url` set to the current page and `capabilities` set to the full README block, including `participants`. Note that this is a full-set declaration.
- [ ] **Step 5 (owner go-ahead): First trip.** Run `relay.py pending` and send each batch, running `relay.py sent` after each. This adds the 132 old-page rows and the 4 participants docs, and rewrites the current rows unchanged. Old and current ids don't overlap.
- [ ] **Step 6: Verify live.** `ArtifactData list` row counts per collection equal the replay counts. Open the page: `m-0001` is visible, the roster shows 4 participants, and the footer hash equals `git rev-parse --short HEAD`.
- [ ] **Step 7 (owner go-ahead): Tell the sessions.** Post an owner message (`relay.py append --from owner …`, then send it) saying that from now on each session appends to `log/<id>.jsonl` first and BewareILive runs the courier trip at each check-in, per PROTOCOL.md. Commit `log/` and push.
