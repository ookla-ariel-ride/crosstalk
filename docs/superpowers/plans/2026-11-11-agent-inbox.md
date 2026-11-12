# Agent inbox, ack and state row: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `relay.py inbox <id> --new --ack` returns only what came after the agent's last ack, each item says whether it needs an answer, `relay.py ack` records "no action" on a request, and `relay.py state` prints one JSON row that a tool reads before it starts a run.

**Architecture:** The place of each participant lives in `inbox-state.json` in the data root (new module `inboxstate.py`). `--new` cuts the present `inbox_report` output down by the time of the last log line of each row. Two new collections join the log: `acks` (one row for each "no action") and `state` (the wind-down record). A new module `state.py` builds the state row from `vitals.read`, `land_queue` and the wind-down record, for the command and for `/api/vitals`. Two reader fixes ride along: the land queue and the review position.

**Tech Stack:** Python 3.9 standard library (`json`, `fcntl`, `argparse`, `unittest`). The present page `index.html` (plain script, no build).

**Spec:** `docs/superpowers/specs/2026-11-11-agent-inbox-design.md`. Read it before any task.

## Global Constraints

- Python 3.9 standard library only. No new dependency.
- The log stays append-only with stable ids. Nothing changes a row in place.
- `relay.py inbox <id>` with no new flag prints what it prints today, except the added `needs_answer` field and the one change the design names: a request with `needs_answer` false waits for nobody.
- No name, host, folder layout or history of an install in a tracked file. Examples use `alpha`, `beta`, `gamma`, `relay-bot`. No token or PIN anywhere. No em-dash in any doc. `tests/test_clean_install.py` checks the names.
- Docs upkeep: a commit that adds or changes a `relay.py` command updates `README.md`, `docs/reference.md` and the nearest doc in the same commit. A new module gets a row in `docs/development.md`. `tests/test_docs.py` must pass.
- `python3 -m unittest discover -s tests` passes before each commit. Keep this work out of `web/`, so the `web/dist` rule (`tests/test_web_dist.py`) does not apply.
- Each task is one GitHub issue and one pull request into branch `agent-inbox`. The task title is the issue title; the "Done when" lines are the issue's success criteria. Issue and PR text follow the name rule above; run gitleaks on it before you post.
- Run the prose check (`node ~/.claude/skills/avoid-ai-writing/bin/avoid-ai-writing.js <file>`) on each doc you change; the score must be 0.

## Review Focus

1. `--ack-to` with a place later than the newest row of the log (a typed time in the future) would hide every row until then. It is refused (Task 4, test `test_ack_to_refuses_a_place_ahead_of_the_log`).
2. Two participants run `inbox --ack` at the same moment. Both places are stored; neither write drops the other (Task 3, test `test_two_processes_keep_both_places`).
3. A row whose own `t` is behind the clock (the log only warns about it) is written after the place. It is new under `--new` (Task 4, test `test_row_with_an_old_own_t_written_after_the_place_is_new`).
4. An agreement that turns `ready-to-adopt` by an `update` after the place keeps its old `t`. It is new under `--new` (Task 4, test `test_agreement_updated_after_the_place_is_new`).
5. `inbox --new --ack | head -1` ends in a broken pipe. The place does not move (Task 4, test `test_broken_pipe_does_not_move_the_place`).

---

## File structure

| File | Responsibility |
|---|---|
| `scripts/crosstalk/requests.py` (modify) | `needs_answer`, `no_answer`, `acks_by_item`; the shared reply rule in `pending_recipients` and `_requests` |
| `scripts/crosstalk/inboxstate.py` (new) | Read, compare and move the place of each participant in `inbox-state.json` |
| `scripts/crosstalk/reports.py` (modify) | `needs_answer` and `acked` on inbox items; the row times; `new_report` for `--new` |
| `scripts/crosstalk/state.py` (new) | The wind-down record and the state row |
| `scripts/crosstalk/logs.py` (modify) | `acks` and `state` in `COLLECTIONS`, and their id rules |
| `scripts/crosstalk/consults.py` (modify) | `no_answer` on `post_reply` and `post_consult`; `ack_item` |
| `scripts/relay.py` (modify) | `--no-answer`, `ack`, `inbox --new/--ack/--ack-to`, `state` |
| `scripts/crosstalk/autogrant.py`, `serve.py`, `notify.py` (modify) | Read the wind-down record; `state` in `/api/vitals`; acks in the live notices |
| `scripts/crosstalk/landqueue.py`, `reviews.py` (modify) | The two reader fixes |
| `index.html` (modify) | Load `acks` and `state`; "acked, no action" in the pending list |

## Order and parallel work

- Wave A, side by side (no shared file): Tasks 1, 3, 7, 8. Task 7 changes only the land queue section of `PROTOCOL.md`; Task 1 changes other sections of it.
- Wave B: Task 2 (it changes the helpers that Task 1 adds).
- Wave C, side by side: Task 4 (needs Tasks 1 and 3) and Task 5 (needs Task 2: both add to the same `COLLECTIONS` and `COLS` lines). Both touch `relay.py`, `README.md` and `docs/reference.md` in different rows; the second to merge rebases.
- Wave D: Task 6 (needs Task 5).

---

### Task 1: Inbox items say whether they need an answer (`needs_answer`, `--no-answer`)

**Files:** Modify `scripts/crosstalk/requests.py` (`pending_recipients`, `_requests`, new helpers), `scripts/crosstalk/reports.py` (`inbox_report`), `scripts/crosstalk/consults.py` (`post_reply`, `post_consult`), `scripts/relay.py` (`cmd_reply`, `cmd_consult`, `cmd_append`, `cmd_ask`, their parsers). Test: create `tests/test_needs_answer.py`. Docs: `PROTOCOL.md` ("Replies that ask nothing", "Asking for help"), `docs/reference.md` (rows `reply`, `consult`, `append`, `ask`, `inbox`), `docs/sessions.md` ("The inbox in detail"), `docs/mcp.md` (the `inbox` tool), `README.md`.

**Interfaces:**
- Produces `requests.NO_ANSWER = "No answer needed"`.
- Produces `requests.needs_answer(doc: dict) -> bool`: false when `doc["needs_answer"] is False`; false when the `body`, with white space and one last `.` removed, ends with `NO_ANSWER` (exact case); else true.
- Produces `requests.no_answer(data: dict) -> dict`: sets `data["needs_answer"] = False`; when `needs_answer(data)` was true for the body, sets `body` to `NO_ANSWER + "."` (no body) or `body.rstrip() + "\n\n" + NO_ANSWER + "."`. Returns `data`.
- Changes `pending_recipients(doc, replies, roster=None) -> List[str]`: returns `[]` when `needs_answer(doc)` is false.
- Changes `_requests`: with `open_only`, a request with `needs_answer(doc)` false is left out, for `pid` None (`open_requests`) and for one `pid` (`inbox_for`). `all_requests_for` keeps it.
- `inbox_report` items: each item of `requests`, `owner_messages` and `thread_replies` gets the key `needs_answer` next to `trust` (on the item, not inside the doc).
- `post_reply(..., no_answer: bool = False)`, `post_consult(..., no_answer: bool = False)`: call `no_answer(data)` before the append.
- CLI: `--no-answer` on `reply`, `consult` and `append`. On `append` it applies only to `--collection messages --op set`; elsewhere exit 1, "--no-answer is for a new message". `ask` takes the flag and exits 1 with "a question to the owner needs an answer; ask takes no --no-answer".

- [ ] Step 1: Write failing tests in `tests/test_needs_answer.py`:
  - `test_field_false_needs_no_answer`: `needs_answer({"needs_answer": False, "body": "x"})` is False.
  - `test_sentence_at_the_end`: bodies `"Done. No answer needed"`, `"Done. No answer needed."`, `"Done.\nNo answer needed.  \n"` give False.
  - `test_sentence_in_the_middle_needs_an_answer`: `'He wrote "No answer needed" but I ask: why?'` gives True; `{}` gives True.
  - `test_no_answer_request_waits_for_nobody`: a request from `beta` to `["alpha", "gamma"]` with `needs_answer: false`: `pending_recipients` is `[]`; it is not in `inbox_for(docs, "alpha")` nor in `open_requests(docs)`; it is in `all_requests_for(docs, "alpha")`; `relay.py inbox alpha` does not list it and `inbox alpha --all` does.
  - `test_sentence_request_waits_for_nobody`: the same with only the sentence in the body.
  - `test_live_notice_skips_a_no_answer_request`: `notify.actionable_items(docs, "alpha", roster)` leaves it out.
  - `test_inbox_items_carry_needs_answer`: in `relay.py inbox alpha`, an open request, an owner message and a thread reply each have `needs_answer` True; a thread reply whose body ends with the sentence has False.
  - `test_reply_no_answer_sets_field_and_sentence`: `relay.py reply --no-answer` with body `"ok"` writes `needs_answer: false` and body `"ok\n\nNo answer needed."`; with body `"ok. No answer needed."` the body stays as it is; with no body the body is `"No answer needed."`.
  - `test_consult_no_answer`, `test_append_no_answer_on_a_new_message`, `test_append_no_answer_elsewhere_is_refused` (exit 1, nothing appended), `test_ask_refuses_no_answer` (exit 1, nothing appended).
- [ ] Step 2: Run `python3 -m unittest tests.test_needs_answer -v`. Expected: failures (no `needs_answer` in `requests`).
- [ ] Step 3: Implement the interfaces above.
- [ ] Step 4: Run `python3 -m unittest discover -s tests`. Expected: OK, with every test of `tests/test_inbox.py` unchanged.
- [ ] Step 5: Docs. `PROTOCOL.md`: the field `needs_answer`, the sentence rule, and that such a request waits for nobody but stays in the log and on the page. The other docs: the flag and the field. Commit: `inbox: needs_answer on each item, --no-answer on reply, consult and append`.

**Done when:** each inbox item has `needs_answer`; a request with the field false or the sentence at the end leaves `pending_from`, the default inbox, `open_requests` and the live notices; `ask --no-answer` is refused; the present inbox tests pass unchanged.

### Task 2: Record "no action" on a request (`relay.py ack --none`, the `acks` collection)

**Files:** Modify `scripts/crosstalk/logs.py` (`COLLECTIONS`, `_validate`), `scripts/crosstalk/requests.py`, `scripts/crosstalk/reports.py`, `scripts/crosstalk/notify.py` (`actionable_items`), `scripts/crosstalk/consults.py`, `scripts/relay.py`, `index.html` (`COLS`, `openRequests`, `helpCard`), `tests/page/scenarios.js`. Test: create `tests/test_acks.py`; extend `tests/test_export_page.py`. Docs: `PROTOCOL.md` (Collections table, "Asking for help"), `docs/reference.md`, `docs/sessions.md`, `README.md` (the collection count), `docs/development.md`.

**Interfaces:**
- Consumes `needs_answer`, `pending_recipients`, `_requests` (Task 1).
- `logs.COLLECTIONS` gains `"acks"`. `_validate` rule, for every op in `acks`: the id starts with `<writer>--`, and `data["from"]`, when present, is the writer. A `set` needs `data["from"]` and a string `data["item"]` with id `<writer>--<item>`. The rule is in `_validate` (not append-time only like `schedule_problems`) because an approved participant may `update` any id: the reader must be able to trust the id.
- `requests.ACK_SEP = "--"`.
- `requests.acks_by_item(docs: Dict, roster=None) -> Dict[str, Set[str]]`: item id to the ids of the participants that acked it. A row counts when `none` is True, its id is `from + ACK_SEP + item`, `from` is approved (`_is_approved`), and `item` is a `kind: "request"` message in `docs`.
- `pending_recipients(doc, replies, roster=None, acked: Iterable[str] = ()) -> List[str]`: an acked id counts as a reply from that id.
- `_requests` computes `acks_by_item` once and treats each acked id as a reply in both checks (one recipient, and the inbox of one `pid`).
- `inbox_report`: `pending_from` passes `acked`; each request item gets `acked: [ids]`, sorted.
- `notify.actionable_items` passes `acked` to `pending_recipients`.
- `consults.ack_item(root, pid, item_id, roster=None) -> Line`: refuses an unknown participant, an item that is not a message, and a message that is not `kind: "request"` (a question from `relay.py ask` gets "a question to the owner gets an answer, not an ack"). Writes `set` to `acks`, id `<pid>--<item>`, data `{"from", "item", "none": true, "t": now}`.
- CLI: `relay.py ack --from <pid> <item> --none` (`--none` required); prints the row id.
- `threads.py` stays as it is: it passes a messages-only dict to the helpers, so acks do not reach the v2 thread flags. The v2 request board of release 2 takes this up.
- Page: `COLS` gains `"acks"`. In `openRequests`, a valid ack (the same rule as `acks_by_item`) counts as a reply from its writer. `helpCard` shows the pill "acked, no action: " and the names, next to "waiting on".

- [ ] Step 1: Failing tests in `tests/test_acks.py`:
  - `test_ack_clears_one_name_from_pending_from`: request from `beta` to `["alpha", "gamma"]`; `relay.py ack --from alpha <id> --none`; `inbox beta --all` shows `pending_from == ["gamma"]` and `acked == ["alpha"]`; `inbox alpha` no longer lists it; `inbox gamma` still does.
  - `test_ack_closes_a_one_recipient_request`: it leaves `open_requests`.
  - `test_ack_from_a_quarantined_participant_counts_for_nothing`.
  - `test_ack_of_an_item_not_in_the_log_counts_for_nothing` (an `acks` row appended by hand whose `item` names no message).
  - `test_ack_refuses_a_question`, `test_ack_refuses_a_reply` (exit 1, nothing appended).
  - `test_ack_id_rule`: `append --collection acks` as `beta` with id `alpha--x` is refused; as `alpha` with `data.from` `beta` is refused; `update` by `beta` of `alpha--x` is refused.
  - `test_ack_is_no_message`: `relay.py read --collection messages` holds no new row; `notify.actionable_items` for the requester holds no new id.
  - `test_live_notice_skips_an_acked_request`.
  - In `tests/test_export_page.py`: `test_page_cols_name_every_collection` (each name in `logs.COLLECTIONS` is in the `var COLS = [...]` line of `index.html`) and `test_export_carries_acks_rows`.
- [ ] Step 2: Run `python3 -m unittest tests.test_acks tests.test_export_page -v`. Expected: failures.
- [ ] Step 3: Implement the interfaces above.
- [ ] Step 4: Add a scenario to `tests/page/scenarios.js`: a request to two projects, one ack row, a check that the card shows "acked, no action" with the one name and "waiting on" with the other. Run it as `tests/page/run.md` says. Expected: pass, and no console error.
- [ ] Step 5: Run `python3 -m unittest discover -s tests`. Expected: OK.
- [ ] Step 6: Docs: the `acks` row of the Collections table, the meaning of an ack, the `ack` command row, and "twelve collections" in `README.md` (Task 5 makes it thirteen). Commit: `ack: record no action on a request; the acks collection`.

**Done when:** `relay.py ack --from <id> <item> --none` removes one name from `pending_from` and no other; an ack from a participant that is not approved, or of an item that is not a request in the log, counts for nothing; the present page and an exported page load with `acks` rows and show "acked, no action".

### Task 3: Store each participant's inbox place (`inbox-state.json`)

**Files:** Create `scripts/crosstalk/inboxstate.py`, `tests/test_inboxstate.py`. Modify `.gitignore` (add `inbox-state.json`, `inbox-state.json.lock`), `scripts/crosstalk/deploy.py` (`DATA_PATHS` gains both names), `docs/development.md` (a row for the module).

**Interfaces:**
- File `<data root>/inbox-state.json`: `{"alpha": {"t": "2026-11-11T22:17:00Z", "seen": ["beta-0042"]}}`.
- `FILE = "inbox-state.json"`, `LOCK = "inbox-state.json.lock"`.
- `clean_place(raw) -> Optional[dict]`: `{"t", "seen"}` when `t` is a log time (`readstate.is_time`) and `seen` a list of non-empty strings (sorted, no repeats); else None.
- `load(root: Path) -> Dict[str, dict]`: the clean places; `{}` for a missing file or one that does not parse. A broken file stays on disk until the next write.
- `later(a: Optional[dict], b: Optional[dict]) -> Optional[dict]`: the later time wins; at equal times the `seen` lists join.
- `is_new(t: str, did: str, place: Optional[dict]) -> bool`: True when `place` is None, `t > place["t"]`, or `t == place["t"]` and `did` not in `place["seen"]`.
- `newest(times: Dict[Tuple[str, str], str]) -> Optional[dict]`: the place of the newest row: the largest time, and every id with that time. None for no rows.
- `move(root: Path, pid: str, place: dict) -> dict`: takes `fcntl.flock(LOCK_EX)` on `<root>/inbox-state.json.lock`, reads the file again, stores `later(stored, place)`, writes with `files.atomic_write_text`, and returns the stored place. The lock is needed: two processes that read, merge and replace with no lock can both read the old file, and the second write then drops the first participant's place.

- [ ] Step 1: Failing tests: `test_missing_file_is_empty`, `test_broken_file_reads_as_empty_and_stays_until_a_write`, `test_move_never_goes_back` (a move to an earlier `t` returns and keeps the later place), `test_equal_times_join_seen`, `test_is_new_rules` (an id in `seen` is not new; an id of the same second not in `seen` is new, also when it sorts before every id in `seen`; an earlier time is not new), `test_newest_takes_every_id_of_the_last_second`, `test_two_processes_keep_both_places` (two subprocesses each run 50 moves for `alpha` and `beta` with rising times; at the end both places hold their last time).
- [ ] Step 2: Run `python3 -m unittest tests.test_inboxstate -v`. Expected: import error.
- [ ] Step 3: Implement the module.
- [ ] Step 4: Run `python3 -m unittest discover -s tests`. Expected: OK (`tests/test_clean_install.py` and the deploy tests see the new data names).
- [ ] Step 5: Commit: `inbox: store each participant's place in inbox-state.json`.

**Done when:** the place never moves backwards, a broken file reads as empty, two processes that write at once keep both places, and `deploy` and git leave the file alone.

### Task 4: Return only new inbox items (`inbox --new`, `--ack`, `--ack-to`)

**Files:** Modify `scripts/crosstalk/reports.py`, `scripts/relay.py` (`cmd_inbox`, the `inbox` parser). Test: create `tests/test_inbox_new.py`. Docs: `docs/sessions.md` (the check-in recipe and "The inbox in detail"), `docs/reference.md` (the `inbox` row; `inbox-state.json` in the `--data-root` row), `README.md` (the commands; `inbox-state.json` under "App data and backups"), `PROTOCOL.md` ("What each session does", step 2).

**Interfaces:**
- Consumes `needs_answer` (Task 1); `inboxstate.load`, `later`, `is_new`, `newest`, `move` (Task 3).
- `reports.load_docs_and_times(root, roster) -> Tuple[Optional[Dict], Dict[Tuple[str, str], str], List[Problem]]`: the docs as `load_docs` gives them, and for each `(collection, id)` the `t` of the last log line that touched it (`replay.last_line_map`). `load_docs` calls it.
- Row time: the time of the last log line of the row. The design names the replay time (the row's own `t`, else its line time). The code allows an own `t` behind the clock (`logs.message_problems` only warns), and an agreement keeps its first `t` when an `update` makes it `ready-to-adopt`. With the replay time, both rows would land behind the place and never show. The line time equals the replay time for a message without its own `t`. An edit of an old row makes it new again, which the design accepts as the worst case.
- `reports.STILL_OPEN_MAX = 20`.
- `reports.new_report(full: dict, times: Dict[Tuple[str, str], str], place: Optional[dict], nxt: Optional[dict]) -> dict`, from the output of `inbox_report`:
  - `requests`, `owner_messages`, `thread_replies`: the items with `is_new(times[("messages", id)], id, place)`.
  - `still_open`: the other items of `requests` and `owner_messages` with `needs_answer` True, as `{id, from, t, summary}`, newest first, at most `STILL_OPEN_MAX`; `still_open_more`: how many it left out.
  - `agreements_to_adopt`: the new ones by `("agreements", id)`; `agreements_open`: the length of the full list.
  - `pid`, `roster`, `stale_locks`, `service`, and for the owner `owner_questions`: as in `full`.
  - `place`: `place`; `next`: `nxt`.
- CLI flags on `inbox`: `--new`; `--ack` (with or without `--new`); `--ack-to PLACE`, a JSON object, alone. `nxt` is `inboxstate.newest(times)`.
- `--ack`: print, then `sys.stdout.flush()`, then `inboxstate.move(ROOT, pid, nxt)`. A `BrokenPipeError` or any error before the move: no move, exit 1.
- `--ack-to`: exit 1 with a message for JSON that `clean_place` refuses, a `t` before the stored place's `t`, or a `t` after the newest row of the log. Else move and print the stored place.
- `--new --brief`: one line for each new item, `!` before it when `needs_answer`, a space when not, then the present `_brief_line`; then one line `still open N, agreements open N, stale locks N`. `--brief` without `--new` prints what it prints today.
- The MCP `inbox` tool gets `needs_answer` through `inbox_report` (Task 1) and no `new` or `ack` argument in this work: the design scopes the command line, and none of the five asks named the MCP server. A later issue can add both on top of `new_report`.

- [ ] Step 1: Failing tests in `tests/test_inbox_new.py` (each runs `relay.py` as `tests/test_inbox.py` does, with log lines at set times):
  - `test_first_new_returns_everything_and_a_next`.
  - `test_after_ack_a_quiet_inbox_is_empty_and_next_still_has_a_value`.
  - `test_row_in_seen_is_not_new`; `test_row_of_the_same_second_not_in_seen_is_new` (its id sorts before the seen id).
  - `test_row_without_own_t_uses_its_line_time`.
  - `test_row_with_an_old_own_t_written_after_the_place_is_new`.
  - `test_agreement_updated_after_the_place_is_new`; `test_agreements_open_counts_all`.
  - `test_still_open_has_no_body`; `test_still_open_leaves_out_needs_answer_false`; `test_still_open_cap_says_how_many_it_left_out` (25 open requests: 20 listed, `still_open_more` 5).
  - `test_ack_moves_to_next_of_the_same_run`: after `inbox alpha --new --ack`, the stored place equals the printed `next`.
  - `test_row_after_the_read_stays_new`: run `inbox alpha --new` and keep its `next`; append a row with the same second as `next` and another id; run `inbox alpha --ack-to '<next>'`; `inbox alpha --new` lists the row.
  - `test_ack_without_new_prints_the_full_output_and_moves`.
  - `test_broken_pipe_does_not_move_the_place` (run `inbox alpha --new --ack` with stdout to a pipe that the test closes at once; the file holds no place for `alpha`).
  - `test_ack_to_moves_and_refuses_a_place_behind`; `test_ack_to_refuses_a_place_ahead_of_the_log`; `test_ack_to_refuses_bad_json`.
  - `test_broken_state_file_means_everything_is_new`.
  - `test_new_brief_marks_needs_answer_and_prints_counts`.
  - `test_quiet_check_in_is_small`: 30 participants, 250 agreements to adopt, 25 old open requests, after an ack: `len(stdout) < 8000` (about 2,000 tokens).
  - `test_plain_inbox_is_unchanged`: `inbox alpha` gives the same JSON with and without a stored place.
- [ ] Step 2: Run `python3 -m unittest tests.test_inbox_new -v`. Expected: failures (unknown flag `--new`).
- [ ] Step 3: Implement `load_docs_and_times` and `new_report`, then the flags in `cmd_inbox`.
- [ ] Step 4: Run `python3 -m unittest discover -s tests`. Expected: OK.
- [ ] Step 5: Docs. `docs/sessions.md`: the check-in runs `relay.py inbox <your-id> --new --ack`; a new session runs `inbox <your-id> --ack` once for the full view; read-first-ack-later with `--ack-to`. Commit: `inbox: --new, --ack and --ack-to return only what came after the last ack`.

**Done when:** a check-in with nothing new prints under 8,000 characters; a row written after the read stays new; the place moves only after the output is flushed; `--ack-to` refuses a place behind the stored one or ahead of the log; `inbox <id>` with no new flag prints what it printed before this task.

### Task 5: Record a wind-down that the auto-granter obeys (`relay.py state wind-down`)

**Files:** Create `scripts/crosstalk/state.py`, `tests/test_state.py`. Modify `scripts/crosstalk/logs.py` (`COLLECTIONS`, `_validate`), `scripts/crosstalk/autogrant.py` (`Granter.poll_once`), `scripts/relay.py`, `index.html` (`COLS`). Tests: extend `tests/test_autogrant.py`, `tests/test_export_page.py`. Docs: `PROTOCOL.md` (Collections table; the land queue: no grant during a wind-down), `docs/operations.md` ("The coordinator"), `docs/reference.md`, `README.md` (commands; "thirteen collections"), `docs/development.md`.

**Interfaces:**
- Consumes `config.coordinator(roster)`, `logs.append_line`.
- `logs.COLLECTIONS` gains `"state"`. `_validate` rule, for every op in `state`: the id is exactly `<writer>-wind-down`. An exact id closes two gaps of the present id rule: an approved participant may `update` any id, and a participant `relay` may `set` an id that starts `relay-bot-`.
- `state.WIND_DOWN_STEPS = (0, 1, 2)`.
- `state.wind_down(docs: Dict, roster) -> dict`: `{"step", "started", "by", "reason"}`. It reads only the rows `("state", "owner-wind-down")` and `("state", "<coordinator>-wind-down")` (the owner's alone with no coordinator), takes the one with the later `t` (a log time; a row without one counts for nothing), and sets `by` from the id, not from the data. A `step` outside `WIND_DOWN_STEPS` counts for nothing. With no record: `{"step": 0, "started": null, "by": null, "reason": null}`.
- `state.set_wind_down(root, pid, step: int, reason: Optional[str] = None, roster=None) -> Line`: refuses a `pid` that is not the owner or the coordinator, and a step outside `WIND_DOWN_STEPS`. Writes `set` id `<pid>-wind-down`, data `{"from", "step", "started", "by", "reason", "t"}`. `started` is now for step 1 or 2, kept from the record in force when that one is already at 1 or 2, and null for step 0.
- CLI: `relay.py state wind-down --from <pid> --step 0|1|2 [--reason TEXT]`; prints the row id. In this task the `state` parser has only this subcommand.
- `Granter.poll_once`: after `may_start`, when `state.wind_down(docs, roster)["step"]` is 1 or 2, `self._say("not granting %s: wind-down step %d" % (ask id, step))` and return None. The ask stays in the queue.
- Page: `COLS` gains `"state"`.

- [ ] Step 1: Failing tests:
  - `tests/test_state.py`: `test_no_record_is_step_0`; `test_owner_sets_and_coordinator_sets`; `test_newest_of_owner_and_coordinator_wins`; `test_other_writer_is_refused_by_the_command`; `test_other_writer_row_counts_for_nothing` (`beta` appends `beta-wind-down` with step 2 through `relay.py append`; `wind_down` stays 0); `test_prefix_id_is_refused` (a roster with `relay` and coordinator `relay-bot`: `relay` cannot `set` `relay-bot-wind-down`); `test_update_by_another_writer_is_refused`; `test_no_coordinator_only_owner_counts`; `test_started_carries_from_step_1_to_step_2`.
  - `tests/test_autogrant.py`: `test_no_grant_during_wind_down` (step 1, then step 2: `poll_once` returns None and posts nothing); `test_waiting_ask_gets_its_grant_after_step_0`.
  - `tests/test_export_page.py`: `test_export_carries_state_rows`; `test_page_cols_name_every_collection` (Task 2) now needs `"state"`.
- [ ] Step 2: Run `python3 -m unittest tests.test_state tests.test_autogrant tests.test_export_page -v`. Expected: failures.
- [ ] Step 3: Implement the interfaces above.
- [ ] Step 4: Run `python3 -m unittest discover -s tests`. Expected: OK.
- [ ] Step 5: Docs. `docs/operations.md`: the coordinator sets the record when it reads an owner's wind-down request; the owner can set it too. Commit: `state: the wind-down record, and no GRANTED during a wind-down`.

**Done when:** only the owner and the coordinator can set the record; a row of another writer counts for nothing; the auto-granter posts no `GRANTED` at step 1 or 2 and grants a waiting ask after step 0; the present page and an exported page load with `state` rows.

### Task 6: Print one state row for tools (`relay.py state`, `state --get`, `/api/vitals`)

**Files:** Modify `scripts/crosstalk/state.py`, `scripts/relay.py` (the `state` parser), `scripts/crosstalk/serve.py` (`_vitals`). Tests: extend `tests/test_state.py`, `tests/test_vitals.py`. Docs: `docs/reference.md` (rows `state`, `/api/vitals`), `README.md`, `docs/operations.md` ("Build host status").

**Interfaces:**
- Consumes `state.wind_down` (Task 5), `vitals.read(root, cfg, buildhost_state=None, now=None)`, `landqueue.land_queue(docs, projects=..., now=..., coordinator=...)`.
- `state.state_row(vitals: dict, land: dict, wind: dict, now_iso: str) -> dict`: `{"t", "disk_free_gb", "usage", "usage_limit", "build_host", "wind_down", "land", "may_start", "may_start_why"}`. `land` is the queue dict as it is. `may_start` is false for each of: `wind["step"]` 1 or 2 (`"wind-down step N"`), `usage` and `usage_limit` both not null and `usage >= usage_limit` (`"usage U at or over the limit L"`), `build_host` not null and `reachable` false (`"build host not reachable"`). A null reading adds no reason. `may_start_why` lists the reasons in that order.
- `state.read(root, docs, roster, projects, cfg, buildhost_state=None, now=None) -> dict`: calls `vitals.read` once, `land_queue` with `now` and `config.coordinator(roster)`, `wind_down`, then `state_row`.
- `state.get(row: dict, key: str)`: a dotted key (`wind_down.step`, `build_host.reachable`); raises `KeyError` for a key that is not there.
- CLI: `relay.py state` prints the row as JSON (`cfg = config.of(roster)`, `buildhost.state_path_from_env()`, `projects=roster.participants`). `--get KEY` prints one value: a string as it is, any other value as JSON; exit 1 and no output for null; exit 2 with a message for an unknown key. The `state` subparsers become optional (`required=False`), so `wind-down` still works.
- `serve._vitals`: the present dict plus `"state"`, from the same `vitals` dict (no second `vitals.read`), the cache snapshot's docs, `entries` and roster, and `self.state.buildhost_state`.

- [ ] Step 1: Failing tests: `test_state_row_readings_null_and_not_null` (usage null, build host null, each not null); `test_may_start_false_for_each_reason` (one test for each reason, and for all three: `may_start_why` order); `test_null_reading_does_not_block`; `test_land_is_the_queue_output`; `test_get_prints_a_value_exit_0`, `test_get_null_exits_1`, `test_get_unknown_key_exits_2`; in `tests/test_vitals.py`, `test_vitals_carries_the_state_row` (`/api/vitals` has `state` with the same `disk_free_gb` as the top level).
- [ ] Step 2: Run `python3 -m unittest tests.test_state tests.test_vitals -v`. Expected: failures.
- [ ] Step 3: Implement the interfaces above.
- [ ] Step 4: Run `python3 -m unittest discover -s tests`. Expected: OK.
- [ ] Step 5: Docs. Commit: `state: one row of shared readings for tools, in relay.py state and /api/vitals`.

**Done when:** `relay.py state` and `/api/vitals` give the same row; `may_start` is false for each named reason and never for a null reading; `state --get` exits 0, 1 or 2 as above.

### Task 7: Land queue: free a stopped turn, close a landed grant

Cause, from `scripts/crosstalk/landqueue.py`:
- `land_word` knows the five fixed words and the land tool's "turn released" line. A summary "Turn free" is no land word, so the holder stays.
- In `land_queue`, `start()` returns at once with the note "already holding" when the sender holds a slot. A project whose run stopped with no `LAND END` asks again, gets its `GRANTED`, and posts a new `LAND START` for the same branch: the old holder stays (with the old `since`), and the start does not take the ask. The ask stays in `waiting` with its grant, and after 30 minutes `grant_lapsed` is set. After the `LAND END` the holder leaves, but the ask still waits with a lapsed grant.
- A `LAND END` from a sender that holds nothing only adds a note, so a granted ask whose branch landed (its start missed or misspelt) also stays waiting.

**Files:** Modify `scripts/crosstalk/landqueue.py` (`land_word`, `land_queue`), `tests/test_landqueue.py`, `PROTOCOL.md` ("The land queue").

**Interfaces:**
- `land_word(summary)`: also returns `"LAND END"` for a summary that starts with `turn free` in any case (`re.compile(r"^turn free\b", re.I)` on the stripped summary).
- `land_queue`: a `LAND START` or `LANE START` from a holder whose branches (its named branches, else those of its waiting ask) share a branch with the held ones replaces the holder: remove the old holder, add the note `"<id>: <word> from <sender> replaces <old id>, which had no LAND END"`, then run the start as usual (it takes the ask). A start whose branches share none keeps today's "already holding" note.
- `land_queue`: a `LAND END` from a sender that holds nothing, when the sender's waiting ask has a grant and the end names one of the ask's branches, removes the ask and adds the note `"<id>: LAND END from <sender> closes granted ask <ask id>"`. With no grant, today's "not holding" note stays and the ask waits.

- [ ] Step 1: Failing tests: `test_turn_free_frees_the_holder` (`"Turn free: v-1108 stopped"` and `"TURN FREE gamma"`); `test_a_new_land_start_for_the_same_branch_replaces_a_stopped_turn` (LAND START gamma v-1108; TURN ASK gamma v-1108; GRANTED from `relay-bot` with `replyTo` the ask; LAND START gamma v-1108: one holder with the last start's id and `since`, `waiting` empty, the note names "replaces"); `test_land_end_after_a_restart_leaves_no_holder_and_no_ask`; `test_a_granted_ask_closes_when_its_branch_lands_without_a_start`. The present `test_two_holders_at_most_and_only_distinct_projects` and `test_land_end_without_holding_is_noted_and_ignored` stay as they are.
- [ ] Step 2: Run `python3 -m unittest tests.test_landqueue -v`. Expected: the new tests fail.
- [ ] Step 3: Implement.
- [ ] Step 4: Run `python3 -m unittest discover -s tests`. Expected: OK.
- [ ] Step 5: `PROTOCOL.md`: "Turn free" frees the slot like `LAND END`; a new start of the same branch replaces a run with no `LAND END`. Commit: `land queue: free a stopped turn, and close a granted ask whose branch landed`.

**Done when:** "Turn free" and a repeated `LAND START` for the same branch each end the stopped turn; a granted ask leaves `waiting` when its branch lands; the present land queue tests pass unchanged.

### Task 8: Review scanner: read a position after the replier's own name

The reader `reviews.position_of` takes the `position` field or the first word of the summary. The scanner's ask already says to start the summary with "Agree:" or "Object:", but the ask's own summary starts with the reviewer's label (`label(roster, reviewer)`, such as `AL: agree or object ...`), and a reply that copies it ("AL agrees ...") stays `awaiting`. The code knows the replier (`from`) and its handles, so the reader can accept the replier's own name before the word without the risk of reading another name.

**Files:** Modify `scripts/crosstalk/reviews.py` (`position_of`, `override_status`, the ask body in `_override`), `tests/test_reviews.py`, `PROTOCOL.md` ("Codex branch reviews").

**Interfaces:**
- `position_of(doc: dict, roster=None) -> Optional[str]`: as today, and when the summary does not match `POSITION_RE`, strip one leading handle of the replier (`doc["from"]`: its id, name, each alias, and `label(roster, pid)`; any case; followed by `:`, `,` or white space) and try `POSITION_RE` on the rest. With `roster` None, only the id counts as a handle.
- `override_status` passes `roster`.
- The ask body says: "a summary whose first word is Agree or Object, before your name or anything else (or pass a `position` field ...)".

- [ ] Step 1: Failing tests: `test_position_after_the_repliers_own_label` (`from` `beta` with alias `be`: `"BE agrees: the reason holds"` gives `agree`; `"beta: object, see body"` gives `object`); `test_another_name_before_the_word_is_no_position` (`from` `beta`: `"AL objects, I do not"` gives None); `test_ask_says_first_word`; `test_override_counts_a_reply_after_the_name` (an override whose reply is `"BE agrees"` is `agreed`). The present `test_position` stays as it is.
- [ ] Step 2: Run `python3 -m unittest tests.test_reviews -v`. Expected: the new tests fail.
- [ ] Step 3: Implement.
- [ ] Step 4: Run `python3 -m unittest discover -s tests`. Expected: OK.
- [ ] Step 5: `PROTOCOL.md`: the position word comes first; the replier's own name before it is accepted. Commit: `reviews: read the position word after the replier's own name`.

**Done when:** a reply "AL agrees" from the participant with that label counts as agree; a reply that names another participant first counts for nothing; the ask text names the first word.
