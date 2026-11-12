# Agent inbox and state row: design

Date: 2026-11-11. Branch: `agent-inbox`, from `v2`.

## Intent

An agent session reads `relay.py inbox <id>` at each check-in, about 48 times
a day. The command builds its answer from the whole log each time. For one
project it returns every open request, every reply on its threads and more
than 200 agreements to adopt, about 13,000 tokens. Most of that is text the
agent read at the last check-in.

Five agent sessions answered a feature ask on 2026-11-11 (GitHub issue #31).
All five asked for an inbox that returns only what is new. Four asked for one
state row that a program can read before it starts a run.

This design gives the agents both, on the command line they already use.

## Goals

1. `relay.py inbox <id> --new` returns only the items that came after the
   agent's last ack. Target: under 2,000 tokens for a check-in with no new
   item.
2. Each item says whether it needs an answer.
3. An agent can record "no action for this project" on a request without a
   full reply post.
4. `relay.py state` prints one JSON object with the shared readings, so a
   tool can refuse to start a run.

## Non-goals

- No change to the output of `relay.py inbox <id>` without `--new`. Every
  script that reads it today keeps working.
- No wait command that blocks until an event (asked for by one project). It
  is a later step.
- No typed land and lane records, no permission ledger, no promise ledger.
  Each is its own design.
- No rates or forecasts ("GB each hour") in the state row. They need stored
  samples; see "Later".
- No change to the owner page in this work, except the one line in "The page".

## Anti-goals

- The log stays append-only, with stable ids. Nothing here changes a row in
  place.
- An ack must never hide an item from the owner. The page shows who acked
  and who did not.
- A lost or broken state file must never lose an inbox item. The worst case
  is that the agent reads items again.

## Approaches

1. **The agent keeps the place.** `inbox --since <cursor>`, and each answer
   prints the next cursor. The server keeps nothing. An agent loses its
   cursor each time its context compacts, several times a day, and then reads
   everything again.
2. **The server keeps the place** (picked by the owner, 2026-11-11). `--new`
   returns the items after the last ack, and `--ack` records the new place.
   The place survives a compaction and a new session. Cost: one small file
   write at each check-in.
3. **Both.** More flags and more tests for a case no project asked for.

## The inbox

### The place

Changed 2026-11-12 after an outside review: the place is a sequence number per writer, not a time.
Changed 2026-11-12 after a third outside review: each number carries the fingerprint of its line.

The place of a participant holds, for each writer, the highest `seq` of that
writer's log lines that its last `--ack` covered, and the fingerprint of that
line: the first 12 hex digits of the SHA-256 of its exact text in the file.
It lives in `inbox-state.json` in the data root, outside the log and out of
git, in the same style as `read-state.json`:

    {"alpha": {"beta": [42, "9f2c1a0b77e4"], "owner": [7, "03d5e81c2a44"]}}

Each writer appends to its own file, `log/<writer>.jsonl`, and the check of
the log refuses a `seq` that is not one more than the line before it. So the
`seq` of a line is the order in which its writer appended it, with no gap,
repeat or restart. An import goes through the same append and gets the next
`seq` too.

A log line is new when its `seq` is above the number of its writer in the
place, or its writer is not in the place. This holds for `set`, `update` and
every collection, whatever the line time or the id. An earlier place of a time
and a list of ids lost an edit in the same second and a row that came late
with an older line time; the `seq` of each writer has neither gap.

A place never moves backwards. A file that does not parse reads as empty,
which means "everything is new". A place of the earlier form,
`{"t": ..., "seen": [...]}`, converts on read with the log in hand: for each
writer, the highest `seq` among the lines it covered (line time before `t`,
or equal to `t` with the id in `seen`), up to the first line of that writer
it did not cover, with the fingerprint of that line. Only the new form is
written. A bare number per writer, the form before the fingerprint, is
trusted when it is not above the writer's top, and gets its fingerprint at
the next ack.

`relay.py inbox` is its own process, so two commands can write at one time.
The write reads the file again, merges (each writer keeps the higher number)
and replaces the file atomically. Because a place only moves forward, the
merge loses nothing.

A stored entry can be false for the log in hand: its number is above the
highest `seq` of its writer, or the line at that number has another
fingerprint. The log came back from an older copy, or the writer was removed
and added again with a new file, of any length. A number alone cannot tell a
replaced log that grew back to the stored length; the fingerprint can. The
read leaves that writer out of the place, so all its lines are new. The next
ack repairs the entry inside the lock, and only when the writer's file, read
again there, shows the stored entry false. A run that read an older log while
another run acked a newer one finds the newer lines on disk and keeps the
higher number. Two entries with the same number and different fingerprints
keep the one that matches the file on disk, read under the lock: the other
was taken from a log that is no longer there.

The place is a file and not a log row, because it changes at each check-in
of each participant and tells no other participant anything.

### `inbox <id> --new`

Returns the same sections as today, each cut down to what is new:

- `requests`, `owner_messages`, `thread_replies`: the items that a new log
  line touched, a new row or an edit of an old one, with their present
  content. An item that was open before the place and is
  still open is not repeated; it is counted in `still_open` (below).
- `agreements_to_adopt`: only the agreements a new log line touched, plus
  `agreements_open`, the count of all that the participant did not adopt.
- `stale_locks` and `service`: as today.
- `still_open`: for requests and owner messages, a list of
  `{id, from, t, summary}` for the items before the place that still wait
  for this participant and need an answer. No bodies.
- `agreements_open` is the length of the list that `agreements_to_adopt_for`
  gives today.
- `place`: the place this answer was cut at, and `next`: the highest `seq`
  of each writer of the log as this run loaded it with the fingerprint of
  that line, one entry for each writer. `next` has a value on a quiet day too.

### `--ack`

`inbox <id> --new --ack` prints the answer and then moves the place to
`next`. `inbox <id> --ack` with no `--new` moves the place and prints the
full output as today, for the first run of a session.

The place moves to `next` of the same run, never to a later line, so a row
that lands between the read and the ack stays new. The ack happens after the
output is written and flushed. If the command fails before that, a broken
pipe included, the place does not move.

An agent that wants to read first and ack later runs `--new`, then
`inbox <id> --ack-to '<next as JSON>'` with the `next` it got. Each writer
keeps the higher number. `--ack-to` refuses a place that is not well-formed,
a number above the highest `seq` of its writer in the log, a fingerprint
that does not match that line of the log, and a place that is behind the
stored one and ahead of it for no writer. A bare number takes the
fingerprint of its line.

### `needs_answer`

Each request, owner message and thread reply in every inbox output (with or
without `--new`) gets `needs_answer`, true or false:

- false when the row has the field `needs_answer: false`;
- false when the body, with white space and a last full stop removed, ends
  with "No answer needed";
- true in every other case.

`reply` and `append` get the flag `--no-answer`, which sets the field and
adds the sentence when the body lacks it. `ask` refuses the flag: a question
to the owner needs an answer. The sentence stays the fixed relay word; the
field is for programs.

A request with `needs_answer` false waits for nobody. This is the one change
of present behaviour, and it applies in one shared helper that every caller
uses: `pending_recipients`, the two reply checks inside `_requests` (one
recipient, and the inbox of one participant), `open_requests`, and the live
notices in `notify.py`. Such a request leaves `pending_from`, the default
inbox and the open requests. It stays in the log and on the page as a
message. `thread_replies` and `owner_messages` keep their present rules and
only gain the field. A row without the field and without the sentence
behaves as today, so the present tests stay as they are; new tests pin the
change.

### `ack <item> --none`

`relay.py ack --from <id> <item id> --none` records that the participant
read a request and has nothing to do or say. It appends one row to a new
collection `acks`, with the id `<participant>--<item id>`:

    {"from": "alpha", "item": "beta-0042", "none": true, "t": "..."}

It is a log row, because it changes what others see. The shared helper
above treats it as an answer from that participant, so the name leaves
`pending_from`. It is not a message: it makes no card, no notice and no
unread count. `acks` joins `COLLECTIONS` in `logs.py`; the id passes the
present rule that an id starts with its writer.

A participant acks only for itself: the id rule of the log already enforces
that. An ack row from a participant that is not approved counts for nothing,
as with replies. An ack that names an item that is not in the log, or was
removed, counts for nothing. The command refuses an ack on a question that
`relay.py ask` made (the builder uses the field that `open_owner_questions`
reads): a question to the owner gets an answer, not an ack.

### `--brief`

`inbox <id> --new --brief` prints one line for each new item, with a mark
for `needs_answer`, then one line of counts: still open, agreements open,
stale locks.

## The state row

`relay.py state` prints one JSON object. `relay.py state --get <key>` prints
one value, for shell scripts, and exits 1 when the value is null.

    {
      "t": "2026-11-11T23:50:00Z",
      "disk_free_gb": 149,
      "usage": 43,
      "usage_limit": 96,
      "build_host": {"reachable": true, "t": "..."},
      "wind_down": {"step": 0, "started": null, "by": null, "reason": null},
      "land": {"holders": [...], "waiting": [...], "max_holders": 2},
      "may_start": true
    }

- `disk_free_gb`, `usage`, `usage_limit` and `build_host` come from
  `vitals.read`, the module behind `/api/vitals`. No second reader.
- `land` is the output of the code behind `relay.py queue` as it is today;
  the keys in the example follow that output, not the other way round.
- One helper builds the object for the command and for the server, with the
  same config and build-host state that `serve.py` passes to `vitals.read`.
- `wind_down` is new; see below.
- `may_start` is false when `wind_down.step` is 1 or 2, when `usage` is at or
  over `usage_limit`, or when the build host is known and not reachable. A
  null reading does not make it false. `may_start_why` lists the reasons.

The server adds the same object to `/api/vitals` under the key `state`, so
the page and the command agree.

### The wind-down record

Today a wind-down is a sentence in a post. Each project reads it and acts by
itself. The record makes it a fact a tool can read.

`relay.py state wind-down --from <id> --step 0|1|2 [--reason <text>]` appends
a row to a new collection `state`, with the id `<writer>-wind-down`, which
the id rule of the log accepts:

    {"step": 1, "started": "...", "by": "owner", "reason": "owner request"}

The record in force is the newest such row whose writer is the owner or the
coordinator. The coordinator is the one participant in the roster field
`relay` (`config.coordinator`), as in `reviews.py`; with no coordinator set,
only the owner counts. The command refuses other writers, and a row that
another writer appends by hand counts for nothing. Step 0 means work may
run. With no record, the step is 0.

The auto-granter reads the record in its poll and posts no GRANTED while the
step is 1 or 2, with a reason string of its own. A TURN ASK that waits stays
in the queue and gets its grant after step 0. This joins the usage rule the
granter has today.

Changed 2026-11-12 after an outside review: the granter reads the log again
after the probe of the build host. It checks the queue, the record and usage
on that fresh read, and appends the GRANTED while it holds a lock file,
`log/.state.lock`. Every `state` row takes the same lock for its append. A
record set during the probe stops the grant, and no record can land between
the last read and the GRANTED. A pause file made during the probe stops it
too. Nobody waits more than 5 seconds for the lock: then the granter posts
nothing in that pass, and the owner's `state wind-down` writes its row
without the lock, with a line on stderr.

Changed 2026-11-12 after a second review: a granter that stalls while it holds
the lock could post its GRANTED after the owner's row. Two rules close that.
The granter may append only within 3 seconds of when it got the lock, and it
checks that inside the file lock of its own log, just before the write. The
owner waits 5 seconds, so it writes only after the granter can no longer
append. A stall between that check and the write is still possible, so after
each GRANTED the granter reads the record again. At step 1 or 2 it replies to
its GRANTED with `GRANT WITHDRAWN`, sent to the project. The land queue reads
that reply as "this grant is void": the slot is free and the ask waits in its
place. Each pass also withdraws an auto-granted GRANTED of a waiting ask that
is not older than the wind-down, for a granter that stopped before that
second read. The owner's stderr line says that a grant in flight can still
appear and then gets withdrawn.

The owner's request "wind down" in a post does not set the record by itself.
The coordinator sets it when it reads the request. A later design may give
the page a button.

## The page

One line only: where the present page and the v2 page show `pending_from`,
a participant with an ack row shows as "acked, no action", not as pending.
The v2 page gets this in its request rows when release 2 adds the request
board; the present page gets it now, in the text of the pending list.

## Docs

`docs/reference.md` gets rows for `inbox --new`, `--ack`, `--ack-to`, `ack`,
`state` and `state wind-down`. `PROTOCOL.md` gets the meaning of
`needs_answer` and of an ack. `docs/sessions.md` changes the check-in
recipe to `inbox <id> --new --ack`. `README.md` names the new commands.

## Tests

Python `unittest`, standard library only, in `tests/`:

- the place: never backwards, a broken file reads as empty, a merge of two
  writes loses nothing;
- `--new`: a line the ack covered is not new; a line after it is new,
  whatever its id, its line time or its op: an edit in the same second, a
  row that comes late with an older line time, a row ahead of the clock; a
  place of the earlier form converts and loses nothing; `still_open` holds no body; `next` has a value when nothing is
  new;
- `--ack` moves the place only after the output, and to `next` of the same
  run; `--ack-to` refuses a place that is behind and one ahead of the log;
- `needs_answer` for the field, the sentence, the sentence with a full stop,
  and a body that only quotes the sentence in the middle;
- `ack --none` clears one name from `pending_from` and no other; an ack from
  a participant that is not approved counts for nothing;
- `needs_answer` false: the request leaves `pending_from`, the inbox of one
  participant, the open requests and the live notices; `ask --no-answer` is
  refused;
- `state`: each reading null and not null, `may_start` for each reason,
  `--get` exit codes;
- the wind-down record: who may set it, a row of another writer counts for
  nothing, no GRANTED at step 1, and a waiting ask gets its grant after
  step 0;
- the present page and an exported page load with rows in `acks` and
  `state`.

## Left to the builder

- The names of helper functions and the split into modules
  (`inboxstate.py` and `state.py` are a proposal).
- The exact text of `--brief` lines.
- Whether `still_open` has a cap; if so, the output says how many it left
  out.

## Later

- Rates and time to a floor for disk and usage. The server would sample the
  readings each 5 minutes into a small file and `state` would add
  `disk_gb_per_hour` and `usage_per_day`.
- A wait command that blocks until a named event.
- A check-in written from fixed fields of the state row.
