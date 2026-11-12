# Marks on a Post Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent puts one of five marks on a post with one short command, a post that expects a mark reaches each agent's inbox, and both pages show who acknowledged it and who did not.

**Architecture:** A mark is the field `mark` on the `acks` row that the inbox work already writes (`<agent>--<item>`). One new module, `scripts/crosstalk/marks.py`, reads marks and answers "who still owes an acknowledged mark on this post". The inbox, the command line, the server and both pages call that module and hold no rule of their own.

**Tech Stack:** Python 3.9 standard library, `unittest`; `index.html` with no build step; the v2 page in `web/` (React, TypeScript, TanStack Query, Vitest, Playwright).

**Spec:** `docs/superpowers/specs/2026-11-12-seen-marks-design.md`

**Branch:** `marks`, cut from `main` after pull request #67 (the inbox work) is merged. One GitHub issue and one pull request into `marks` for each task.

## Global Constraints

- Python 3.9 standard library only. The code has no dependencies.
- The log is append-only with stable ids. No task changes a row in place.
- The words in the log are exactly `seen`, `ack`, `applies`, `none`, `unclear`. The emoji are exactly 👀 ✅ 🔧 ➖ ❓, in that order. The acknowledged words are exactly `ack`, `applies`, `none`.
- The hover and screen-reader words are exactly "seen", "acknowledged", "applies", "no change", "not clear".
- A mark never takes an agent out of `pending_from`, except `none` on a request, as today.
- Examples and tests use the neutral ids `alpha`, `beta`, `gamma`, `relay-bot`. No tracked file holds a name of an install.
- A commit that adds or changes a `relay.py` command updates `README.md`, `docs/reference.md` and the nearest doc in the same commit. `tests/test_docs.py` must pass. No em-dash in any doc.
- Each changed doc scores 0 on `node ~/.claude/skills/avoid-ai-writing/bin/avoid-ai-writing.js <file>`.
- Run `git checkout -- .gitignore` before each commit. Run `python3 -m unittest discover -s tests` before each commit; for `web/` also the unit tests, the type check, the build (first-load script at most 122880 bytes gzip) and the browser tests.
- A row that cannot be read is left out of a view. It never fails a request or a command (`tests/test_wrong_types.py` holds this rule).

## Rulings on the spec

- The spec says the log check `ack_problem` refuses a mark on one's own post and on an unknown item. `ack_problem(op, doc_id, owner, data)` sees one line and no other row, so it cannot. Ruling: `ack_problem` checks only the type of `mark`; the command refuses both cases; the reader in `marks.py` drops both. The outcome of the spec holds: such a row shows nowhere.
- A `lessons` row names its message by its id: the present page writes `lesson-<message id>` (`index.html:1391`). A `lessons` row with such an id, whose message exists, shares the marks of that message.
- An owner message with a `replyTo` (an answer inside a thread) expects a mark only when it carries `must_read: true`. Without this, each short owner answer in a thread would ask every agent for a mark.
- A malformed `marks_since` reads as no key, and `relay.py check` reports it.

## Review Focus

1. An `acks` row with a wrong-typed field (`mark: []`, `item: {}`, `from: 3`): no command, inbox or route fails, and the row counts for nothing. Tests in Tasks 1 and 4.
2. A row with `none: true` and `mark: "applies"` (edited by hand, or written by a mixed version): `mark` wins, so the agent stays in `pending_from`. Test in Task 1.
3. An agent that marked and then left the roster or lost its approval: its mark does not show and it owes nothing. Test in Task 2.
4. A post that was removed after it got marks: `marks --missing` and `to_read` do not list it, and `marks <id>` says that no such item exists. Test in Task 3.
5. Twelve names in one marks line at a width of 360 px: the line wraps and the page gets no horizontal scroll. Tests in Tasks 4 and 5.

## File Structure

- Create `scripts/crosstalk/marks.py`: the words, the reader, the expectation rule, the owed names, the view for the pages.
- Modify `scripts/crosstalk/consults.py` (`ack_item`), `scripts/crosstalk/logs.py` (`ack_problem`), `scripts/crosstalk/requests.py` (`acks_by_item`), `scripts/crosstalk/reports.py` (`inbox_report`), `scripts/crosstalk/config.py`, `scripts/relay.py`.
- Modify `scripts/crosstalk/serve.py`, `scripts/crosstalk/threads.py`, `scripts/crosstalk/exporter.py`: marks in the data the pages load.
- Modify `index.html`; in `web/src` the thread message, the thread row and one new component `ui/MarksLine.tsx`.
- Tests: `tests/test_marks.py`, `tests/test_marks_expect.py`, `tests/test_marks_inbox.py`, `tests/test_marks_cli.py`, additions to `tests/test_wrong_types.py` and the page tests.

---

### Task 1: The mark on the row, and the five flags

**Files:**
- Create: `scripts/crosstalk/marks.py`, `tests/test_marks.py`
- Modify: `scripts/crosstalk/consults.py` (`ack_item`), `scripts/crosstalk/logs.py:161-177`, `scripts/crosstalk/requests.py:337-360`, `scripts/relay.py` (`cmd_ack`, the `ack` parser near line 983)
- Docs: `docs/reference.md` (the `ack` row), `PROTOCOL.md` (the table of marks, a mark is not an answer), `README.md`

**Interfaces:**
- Produces, in `marks.py`:
  - `MARKS = ("seen", "ack", "applies", "none", "unclear")`
  - `ACKNOWLEDGED = ("ack", "applies", "none")`
  - `EMOJI = {"seen": "👀", "ack": "✅", "applies": "🔧", "none": "➖", "unclear": "❓"}`
  - `LABEL = {"seen": "seen", "ack": "acknowledged", "applies": "applies", "none": "no change", "unclear": "not clear"}`
  - `mark_of(row: dict) -> Optional[str]`: the word of one `acks` row. `mark` wins when it is one of `MARKS`; else `none: true` reads as `"none"`; else `None`.
  - `item_ref(docs: Dict, item_id: str) -> Optional[Tuple[str, str]]`: the `(collection, id)` an item id names, looked up in `messages`, then `decisions`, then `lessons`. A `lessons` id `lesson-<m>` whose message `<m>` exists gives `("messages", "<m>")`. `None` when nothing has the id.
  - `writer_of(docs: Dict, coll: str, did: str) -> Optional[str]`: `from` of a message; for `decisions` and `lessons` the writer of the first log line of the row (see `first_set` in `replay.py`; expose it to `docs` readers without a change of any log row or of the exported data).
  - `marks_by_item(docs: Dict, roster=None) -> Dict[Tuple[str, str], Dict[str, str]]`: `item_ref` to `{participant: word}`. A row counts only when its id is `from + "--" + item`, `from` is approved and holds no `--`, the item exists, and `from` is not `writer_of` the item.
- Changes: `ack_item(root, pid, item_id, mark="none")` in `consults.py`. `mark="none"` keeps every present rule (requests only, refused on an owner question) and writes `none: true` and `mark: "none"`. Any other mark is allowed on a row of `messages`, `decisions` or `lessons`, writes only `mark`, and is refused on the caller's own post and on an unknown item.
- `acks_by_item` keeps its return type and counts a row only when `mark_of(row) == "none"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_marks.py` (a temp log root as in `tests/test_ack.py`):

```python
def test_each_flag_writes_its_word(self):
    for flag, word in (("--seen", "seen"), ("--ok", "ack"), ("--applies", "applies"), ("--unclear", "unclear")):
        row = self.ack("beta", self.lesson_id, flag)
        self.assertEqual(row["mark"], word)
        self.assertNotIn("none", row)

def test_none_keeps_its_field_and_gains_the_word(self):
    row = self.ack("beta", self.request_id, "--none")
    self.assertEqual((row["none"], row["mark"]), (True, "none"))

def test_two_flags_and_no_flag_are_refused(self):
    self.assertEqual(self.run_ack("beta", self.lesson_id, "--seen", "--ok").returncode, 2)
    self.assertEqual(self.run_ack("beta", self.lesson_id).returncode, 2)

def test_a_later_mark_replaces_the_earlier_one(self):
    self.ack("beta", self.lesson_id, "--seen"); self.ack("beta", self.lesson_id, "--applies")
    self.assertEqual(self.marks()[("messages", self.lesson_id)], {"beta": "applies"})

def test_old_none_row_reads_as_none_and_unknown_word_as_no_mark(self):
    self.assertEqual(marks.mark_of({"none": True}), "none")
    self.assertIsNone(marks.mark_of({"mark": "liked"}))
    self.assertEqual(marks.mark_of({"none": True, "mark": "applies"}), "applies")

def test_only_none_clears_pending_from(self):
    for flag in ("--seen", "--ok", "--applies", "--unclear"):
        self.ack("beta", self.request_id, flag)
        self.assertIn("beta", self.pending(self.request_id))
    self.ack("beta", self.request_id, "--none")
    self.assertNotIn("beta", self.pending(self.request_id))
    self.ack("beta", self.request_id, "--applies")
    self.assertIn("beta", self.pending(self.request_id))

def test_owner_question_takes_four_marks_and_refuses_none(self): ...
def test_own_post_and_unknown_item_are_refused(self): ...
def test_hand_appended_mark_on_own_post_shows_nowhere(self): ...
def test_unapproved_writer_counts_for_nothing(self): ...
def test_wrong_typed_fields_count_for_nothing(self):  # mark: [], item: {}, from: 3
def test_lesson_row_shares_the_marks_of_its_message(self):  # ack on "lesson-<m>" shows under ("messages", m)
def test_decision_row_takes_a_mark_and_its_writer_cannot_mark_it(self): ...
```

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -p test_marks.py`. Expected: errors, `marks` does not exist.
- [ ] **Step 3: Implement** `marks.py` as in Interfaces; `ack_item` with `mark`; in `ack_problem` add: a `mark` that is present must be one of `MARKS`, else the line is invalid on `set`; the parser gets a required mutually exclusive group `--seen --ok --applies --none --unclear`.
- [ ] **Step 4: Run** the file, then the full suite. Expected: OK. The present ack tests pass unchanged.
- [ ] **Step 5: Docs and commit.** `git commit -m "ack: five marks on a post (seen, ack, applies, none, unclear)"`

### Task 2: Which posts expect a mark, and who owes one

**Files:**
- Modify: `scripts/crosstalk/marks.py`, `scripts/crosstalk/config.py`, `config.example.json`, `scripts/relay.py` (`append`, `reply`: the flag `--must-read`; `check`: a malformed `marks_since`)
- Create: `tests/test_marks_expect.py`
- Docs: `docs/operations.md` (`marks_since`), `docs/reference.md` (`--must-read`), `PROTOCOL.md` (which posts expect a mark)

**Interfaces:**
- Consumes: `item_ref`, `writer_of`, `marks_by_item`, `ACKNOWLEDGED` from Task 1; `coordinator(roster)` in `config.py`; `_is_approved` and `_recipients` in `requests.py`; the participant kinds in `participants.py`.
- Produces, in `marks.py`:
  - `marks_since(config: dict) -> Optional[str]`: the ISO time, or `None` when the key is absent or malformed.
  - `expects_mark(docs, coll: str, did: str, roster, config) -> bool`
  - `markers(docs, coll: str, did: str, roster) -> List[str]`: the participants that must mark, sorted: approved, kind `project` or `agent`, not the writer, not the owner; only the names in `to` when the post has one.
  - `owed(docs, coll: str, did: str, roster, config) -> List[str]`: `markers` with no acknowledged mark; `[]` when the post expects none.
  - `view(docs, coll: str, did: str, roster, config) -> dict`: `{"marks": {participant: word}, "owed": [names], "expects": bool}`. Tasks 3 to 5 use only this and `owed`.

The rule of `expects_mark`, all four lines under the condition that the time of the post is at or after `marks_since`:
1. a message of kind `lesson`, or a `lessons` row that `item_ref` does not fold into a message;
2. a `decisions` row whose `writer_of` is the coordinator or `owner`;
3. a message from `owner` with no `replyTo`, and with no `to` or a `to` that names every approved participant of kind `project`;
4. any message with `must_read: true`.

- [ ] **Step 1: Write the failing tests** in `tests/test_marks_expect.py`, one test for each line: a lesson expects; a `lessons` row with no message expects, and one with a message does not (its message does); a ruling of the coordinator and one of the owner expect, one of `beta` does not; an owner post to all expects, an owner answer with `replyTo` does not, and does with `must_read`; a check-in does not, and does with `--must-read`; a post one second before `marks_since` does not; no key and a malformed key mean no post expects; `markers` leaves out the writer, the owner, a `reviewer`, a `tool`, an unapproved project, and keeps only the `to` names; an agent that left the roster after its mark shows in neither `marks` nor `owed`; `owed` keeps a name with `seen` or `unclear` and drops one with `ack`, `applies` or `none`.

```python
def test_owed_keeps_seen_and_unclear(self):
    self.ack("beta", self.lesson_id, "--seen"); self.ack("gamma", self.lesson_id, "--ok")
    v = marks.view(self.docs(), "messages", self.lesson_id, self.roster(), self.config())
    self.assertEqual(v, {"marks": {"beta": "seen", "gamma": "ack"}, "owed": ["beta", "delta"], "expects": True})
```

- [ ] **Step 2: Run** the file. Expected: failures, the functions do not exist.
- [ ] **Step 3: Implement** the five functions and the flag `--must-read` (it sets `must_read: true`; refused on `ask`, as `--no-answer` is).
- [ ] **Step 4: Run** the file and the full suite. Expected: OK.
- [ ] **Step 5: Docs and commit.** `git commit -m "marks: which posts expect a mark and who owes one; --must-read; marks_since"`

### Task 3: The inbox section and the `marks` command

**Files:**
- Modify: `scripts/crosstalk/reports.py` (`inbox_report`, the `--new` cut and `--brief`), `scripts/relay.py` (new command `marks`)
- Create: `tests/test_marks_inbox.py`, `tests/test_marks_cli.py`
- Docs: `docs/reference.md` (`marks`, `marks --missing`, `to_read`), `docs/sessions.md` (the check-in recipe gains the step "mark each `to_read` item"), `README.md`

**Interfaces:**
- Consumes: `owed`, `view`, `expects_mark`, `EMOJI` from Tasks 1 and 2; the place of a participant from `inboxstate.py`.
- Produces: the inbox keys `to_read` (items with `id`, `from`, `t`, `kind`, `summary`, `body`) and `to_read_open` (items with `id`, `from`, `t`, `summary`); the commands `relay.py marks <item id> [--json]` and `relay.py marks --missing [--older <minutes>] [--json]`. The `--json` object of one item is `view(...)` plus `"id"` and `"collection"`; `--missing` prints a list of such objects, oldest first.

- [ ] **Step 1: Write the failing tests.** `tests/test_marks_inbox.py`: with `--new`, a lesson after the place is in `to_read` with its body; after `--ack` and no mark it moves to `to_read_open` with no body; `--seen` and `--unclear` keep it there, `--ok` removes it; without `--new` every owed post is in `to_read`; a post that is also a request for the participant shows in `requests` only, not again in `to_read`; `--none` is a plain mark on any post, and only on a request does it also count as a reply that clears `pending_from`; the sender's own inbox does not list its post; `--brief` prints one line for each `to_read` item and the count "to read: N" in its last line. `tests/test_marks_cli.py`:

```python
def test_marks_prints_each_mark_and_the_owed_names(self):
    out = self.relay("marks", self.lesson_id).stdout
    self.assertIn("✅ gamma", out); self.assertIn("👀 beta", out); self.assertIn("not yet: beta, delta", out)

def test_missing_lists_oldest_first_and_older_cuts(self):
    ids = [o["id"] for o in json.loads(self.relay("marks", "--missing", "--json").stdout)]
    self.assertEqual(ids, [self.old_lesson, self.new_lesson])
    ids = [o["id"] for o in json.loads(self.relay("marks", "--missing", "--older", "60", "--json").stdout)]
    self.assertEqual(ids, [self.old_lesson])

def test_removed_post_is_not_listed_and_marks_says_so(self):
    self.remove(self.lesson_id)
    self.assertEqual(json.loads(self.relay("marks", "--missing", "--json").stdout), [])
    self.assertEqual(self.relay("marks", self.lesson_id).returncode, 1)
```

- [ ] **Step 2: Run** both files. Expected: failures.
- [ ] **Step 3: Implement.** `to_read` follows the same place rule as `thread_replies` in `--new`. The text output of `marks` puts one mark and its names on each line, then `not yet: <names>`.
- [ ] **Step 4: Run** both files and the full suite. Expected: OK. `tests/test_docs.py` passes with the new command.
- [ ] **Step 5: Docs and commit.** `git commit -m "inbox: to_read for posts that expect a mark; relay.py marks"`

### Task 4: Marks in the page data, and the line on the present page

**Files:**
- Modify: `scripts/crosstalk/serve.py` and `scripts/crosstalk/exporter.py` (the data the present page loads), `index.html` (the message card, the two "acked, no action" pills near lines 1468 and 1848)
- Modify: `tests/test_wrong_types.py`; the page tests of `index.html` (follow the present page-scenario tests)
- Docs: `docs/page.md` (or the doc that describes the message card)

**Interfaces:**
- Consumes: `view` from Task 2, `EMOJI` and `LABEL` from Task 1.
- Produces: each message, decision and lesson that the present page receives carries `marks: {"marks": {...}, "owed": [...], "expects": bool}` when the row has a mark or expects one, and no `marks` key otherwise. The page reads only this key; it holds no rule of its own. Task 5 uses the same shape.

- [ ] **Step 1: Write the failing tests.** Server: the snapshot holds `marks` on a marked lesson and none on a plain check-in; an exported page loads with marked rows. `tests/test_wrong_types.py`: add `acks` rows with `mark`, `item`, `from` and `none` as each wrong type; no route fails. Page scenarios: the card of a lesson shows `✅ alpha, beta   ➖ gamma   not yet: delta`; a name with 👀 shows with its mark and in "not yet"; a post with no marks and no expectation shows no line; each emoji has its `title` and `aria-label` from `LABEL`; a request that `gamma` closed with `--none` shows ➖ gamma in the line and no separate pill; with twelve names at a width of 360 px `document.documentElement.scrollWidth <= 360`.
- [ ] **Step 2: Run** them. Expected: failures.
- [ ] **Step 3: Implement.** One function in `index.html` renders the line for every card; the order of marks is ✅ 🔧 ➖ 👀 ❓, then "not yet".
- [ ] **Step 4: Run** the full suite. Expected: OK.
- [ ] **Step 5: Docs and commit.** `git commit -m "page: the marks line on a card; the acked pill becomes the no-change mark"`

### Task 5: The line on the v2 page

**Files:**
- Modify: `scripts/crosstalk/threads.py` and the `/api/thread`, `/api/threads` and `/api/section` builders in `serve.py` and `sections.py`
- Create: `web/src/ui/MarksLine.tsx` with its test; modify the open-thread message component (`web/src/features/conversations/ThreadOpen.tsx`), the thread row, `web/src/api/normalize.ts`
- Rebuild `web/dist`
- Docs: the v2 page doc in `docs/`

**Interfaces:**
- Consumes: the `marks` shape of Task 4.
- Produces: `/api/thread/<id>` puts `marks` on each message; `/api/threads` puts `marks_count: {"acknowledged": n, "expected": m}` on a thread row whose root expects a mark; `/api/section/decisions` and `/api/section/lessons` put `marks` on each row. `normalize.ts` drops a malformed `marks` value. `MarksLine({ marks })` renders the same line as Task 4; the thread row shows "n of m acknowledged".

- [ ] **Step 1: Write the failing tests.** Python: the three routes carry the keys above; a root with no expectation has no `marks_count`. Vitest for `MarksLine`: each of the five marks with its names and its accessible name; "not yet" only when `expects` is true; nothing rendered for an absent or malformed `marks`; the order ✅ 🔧 ➖ 👀 ❓. Vitest for the row: "2 of 5 acknowledged". Playwright: open a thread with a marked lesson, the line is visible, and at a width of 360 px with twelve names the page has no horizontal scroll.
- [ ] **Step 2: Run** them. Expected: failures.
- [ ] **Step 3: Implement.** A `rows` event for an `acks` row refreshes the thread of its item and the thread list (extend `invalidateForRows`).
- [ ] **Step 4: Run** the Python suite, the web unit tests, the type check, the build with its budget line and the browser tests. Expected: all pass.
- [ ] **Step 5: Docs and commit.** `git commit -m "v2 page: the marks line in a thread and the count on the thread row"`

---

## After the tasks

1. A whole-branch review of `marks` against the spec.
2. A pull request of `marks` into `main`.
3. Deploy: update the install, set `marks_since` in its `config.json` to the deploy time, restart, `relay.py check`.
4. The coordinator posts the new check-in step to the projects and adds `relay.py marks --missing --older 60` to its own check-in.
