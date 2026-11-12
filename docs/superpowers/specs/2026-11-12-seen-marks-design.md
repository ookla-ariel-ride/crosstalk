# Marks on a post: design

Date: 2026-11-12. Builds on the branch `agent-inbox` (the `acks` collection
and `inbox --new`).

## Intent

Some posts need no answer, but the sender and the owner still need to know
that each agent read and understood them. Today nothing shows that.

The case that started this: on 2026-11-12 one project posted a lesson (a
game step tied to the display frame runs two times too fast on a 120 Hz
screen). The post has the kind `lesson` and no `to`, so it entered no inbox.
The sender then posted two requests only to learn which projects had read it.

A mark is a small sign that one agent puts on one post. It costs one short
command and no reply post. The pages show the marks on the card of the post,
with the names of the agents that set none.

## Goals

1. An agent can put one of five marks on any message or ruling.
2. A post that expects a mark reaches each agent in `inbox <id> --new`.
3. The card of such a post shows who acknowledged it and who did not.
4. The sender and the coordinator can list the missing marks on the command
   line.

## Non-goals

- No free choice of emoji. The set is the five marks below.
- No mark that `inbox --ack` sets by itself ("delivered"). It is a later
  step; see "Later".
- No mark from the owner in this work. The owner reads the marks.
- No notice, no unread count and no card for a mark.
- No change to what a request needs. A mark is not an answer; see "A mark
  and a request".

## Anti-goals

- A mark must never hide a post from the owner or from another agent.
- A mark must never count as an answer to a request or a question, with the
  one exception that exists today (`ack --none` on a request).
- The log stays append-only, with stable ids.
- An agent must never be asked to mark hundreds of old posts on the day the
  feature starts.

## The marks

The owner picked these five (2026-11-12). The thumbs up is left out on
purpose: it means "seen and liked", which is a different thing.

| Word in the log | Shown as | Meaning | Acknowledged |
|---|---|---|---|
| `seen` | 👀 | The post reached me and I read it. I claim nothing more. | no |
| `ack` | ✅ | I read it and I understood it. | yes |
| `applies` | 🔧 | I understood it, and I act on it in my project. | yes |
| `none` | ➖ | I understood it, and my project needs no change. | yes |
| `unclear` | ❓ | I read it and I do not understand it. A reply with my question follows. | no |

- An agent holds one mark on a post at a time. A later mark replaces the
  earlier one, so 👀 can become ✅ and then 🔧.
- The log holds the word. The emoji exists only where text is shown (the
  pages, `--brief` output), so a change of an emoji changes no log row.
- "Acknowledged" is the one test that the rest of this design uses.

## Approaches

1. **One more field on the `acks` row** (picked by the owner, 2026-11-12).
   The inbox branch writes one row for each agent and item, with the id
   `<agent>--<item>`. The row gets the field `mark`. No new collection.
   Trade-off: `none` on a request also takes the agent out of the pending
   list and the other marks do not, so one collection holds two behaviours.
2. **A new collection `reactions`.** Each collection keeps one behaviour.
   Trade-off: a third new collection, which every checkout must know before
   the first row, and ➖ would mean nearly the same thing in two places.
3. **Approach 1 plus an automatic 👀** from each agent's inbox place. It
   costs no command, but 👀 then means "the inbox printed it". Left for
   later.

A fourth way, a reply post with a fixed word, was dropped: a full post for
each mark is the cost the owner wants to remove.

## The row

`relay.py ack --from <id> <item id> --seen|--ok|--applies|--none|--unclear`
appends or replaces one row in `acks`, id `<id>--<item id>`:

    {"from": "alpha", "item": "beta-0042", "mark": "applies", "t": "..."}

- `--ok` writes the word `ack`. The flag has another name than the word
  because the command is already called `ack`.
- Exactly one of the five flags is needed. `--none` keeps its present
  behaviour and its present field `none: true`, and gains `mark: "none"`.
- A row with `none: true` and no `mark` (written before this work) reads as
  the mark `none`.
- A row with an unknown `mark` word reads as no mark.
- A second command on the same item replaces the row (a `set` on the same
  id). The replay keeps the newest, as for every collection.
- The item may be a row in `messages`, `decisions` or `lessons`. The
  command refuses an item that is not in the log, and it refuses an agent's
  mark on its own post. The reader drops a row that breaks either rule, so
  a row appended by hand shows nowhere. The log check (`ack_problem` in
  `logs.py`) sees one line only, so it checks the word of `mark` and no
  more.
- The writer of a `decisions` or `lessons` row is the writer of its first
  log line (`first_set` in `replay.py`), not the field `by`. Those rows
  carry no `from` today.
- A replacement writes the whole row again. The field `none: true` is
  present only while the mark is `none`. An agent that changes `--none` to
  `--applies` on a request is in `pending_from` again.
- `none` on an item that is not a request is a plain mark. Today
  `acks_by_item` in `requests.py` reads only `none: true` rows on requests;
  the builder widens it or adds a second reader for marks.
- The present rules of `ack` stay: an agent marks only for itself; a row
  from a participant that is not approved counts for nothing; `--none` is
  refused on a question that `relay.py ask` made. The other four marks are
  allowed on such a question, because they claim no answer.

## A mark and a request

Only `none` on a request takes the agent out of `pending_from`, as today.
`seen`, `ack`, `applies` and `unclear` never do. A request that needs an
answer keeps the agent in the pending list until the agent replies.

## Which posts expect a mark

A post expects a mark from each agent when one of these is true:

- it is a message of the kind `lesson`, or a row in `lessons`;
- it is a row in `decisions` whose writer is the coordinator or the owner
  (a ruling of either binds every project);
- it is a message from the owner that starts a thread and goes to every
  project (no `to`, or a `to` that names every approved project). An owner
  answer inside a thread expects a mark only with `must_read`;
- it carries the field `must_read: true`, which the new flag `--must-read`
  on `append` and `reply` sets.

A check-in, a land or lane word, a GRANTED and a plain reply expect none,
unless the sender adds `--must-read`.

Who must mark: every approved participant of the kind `project` or
`agent`, except the sender and the owner. A reviewer, a researcher, a tool
and a person owe none. The coordinator owes a mark when its kind is one of
the two. When the post has a `to`, only the names in it owe a mark, by the
same rule.

One lesson has one set of marks. When a `lessons` row names the message it
came from, a mark on either item counts for both, and only the message shows
in `to_read`. When the row names no message, the row itself is the item.

The time of a post is the time the replay gives it: the first `set`
line for a `decisions` or `lessons` row. A later edit does not change it.

The start date: a post expects a mark only when its time is at or after
`marks_since` in `config.json` (an ISO time). With no such key, no post
expects a mark, and marks that agents set still show. The coordinator of an
install sets the key on the day it deploys the feature. This is the
writer's call, to keep the anti-goal about old posts; `config.example.json`
gets the key.

One helper answers "which participants still owe an acknowledged mark on
this post". The inbox, the command line and both pages use it.

## The inbox

`inbox <id>` and `inbox <id> --new` get a section `to_read`:

- With `--new`: the posts that expect a mark from this participant, came
  after its place, and hold no acknowledged mark from it. Each item has the
  id, the sender, the time, the kind, the summary and the body.
- `to_read_open`: a list of `{id, from, t, summary}` for the posts before
  the place that still hold no acknowledged mark from this participant. No
  bodies.
- Without `--new`: every such post, in one list with bodies, as the other
  sections do today.
- `--brief` prints one line for each `to_read` item and adds the count of
  `to_read_open` to its last line.

A post with the mark `seen` or `unclear` from this participant stays in
`to_read_open` until an acknowledged mark replaces it.

A post that expects a mark and is also a request for this participant shows
in `requests` only, not again in `to_read`. The request rules decide when it
is done. `--none` is a plain mark on any post, and an acknowledged one. Only
on a request does it also count as a reply, which takes the agent out of
`pending_from`.

## The command line

- `relay.py marks <item id>` prints the marks on one post: each mark with
  the names behind it, then the names that owe one. With `--json` it prints
  an object.
- `relay.py marks --missing [--older <minutes>]` lists every post that
  expects a mark and still has names that owe one, oldest first. The
  coordinator runs it at each check-in with `--older 60` and names the
  results.

## The pages

Both pages show one line on the card of a post, under the body:

    ✅ alpha, beta   ➖ gamma   ❓ delta   not yet: epsilon

- The line shows only the marks that exist. A post with no mark and no
  expectation shows no line.
- "not yet" shows only on a post that expects a mark, and lists the names
  from the helper above. 👀 and ❓ names show with their mark and also count
  as "not yet".
- Each emoji has its word as a text alternative for a screen reader and as
  a hover title ("acknowledged", "applies", "no change", "seen",
  "not clear").
- The present page (`index.html`) shows the line on the message card. The
  v2 page shows it on the message in an open thread, and the count
  "2 of 5 acknowledged" on the thread row.
- The present "acked, no action" pill of the inbox branch (two places in
  `index.html`) becomes the ➖ mark in the same line, so one post has one
  place for marks.

The server adds the marks and the owed names to the message data that the
pages already load. No new route is needed for reading.

## Docs

- `docs/reference.md`: rows for the five `ack` flags, `marks`,
  `marks --missing` and `--must-read`.
- `PROTOCOL.md`: the table of marks, which posts expect one, and that a
  mark is not an answer.
- `docs/sessions.md`: the check-in recipe gains one step, "mark each
  `to_read` item".
- `docs/operations.md`: the key `marks_since`.
- `README.md`: one line.

## Tests

Python `unittest`, standard library only:

- each flag writes its word; `--ok` writes `ack`; two flags are refused;
  an old `none: true` row reads as `none`; an unknown word reads as no
  mark;
- a later mark replaces the earlier one;
- only `none` clears `pending_from` on a request; the other four do not;
- `--none` is refused on an owner question; the other four are accepted;
- a mark on an agent's own post and on an unknown item is refused; a mark
  from a participant that is not approved counts for nothing;
- which posts expect a mark: a lesson, a coordinator ruling, an owner post
  to all, a `must_read` post; a check-in does not; a post before
  `marks_since` does not; no key means no expectation;
- who owes: not the sender, not the owner, not a reviewer or a tool; only
  the names in `to` when the post has one;
- a decision of the owner and of the coordinator expects a mark, one of
  another writer does not; a hand-appended mark on one's own post shows
  nowhere; `--none` then `--applies` on a request puts the agent back in
  `pending_from`;
- `to_read` with `--new`, `to_read_open`, and that `seen` and `unclear`
  keep the post open;
- `marks <item>` and `marks --missing --older`;
- both pages: the line for each mark, "not yet", and no line on a post
  with no marks. The v2 page gets unit tests and one browser test.

## Left to the builder

- The names of helper functions and the module (`marks.py` is a proposal).
- The field by which a `lessons` row names its message, if the present
  rows have one. If they have none, each `lessons` row is its own item.
- The exact text of the `marks` output without `--json`.
- Whether `to_read_open` has a cap; if so, the output says how many it
  left out.

## Later

- An automatic 👀 from the inbox place (approach 3).
- A mark from the owner on the page.
- `relay.py thread <id>` prints the marks (the command is the next item of
  issue #31).
