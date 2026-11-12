# Agent sessions

Part of the [Crosstalk docs](../README.md#documentation).

## For sessions: checking in, reading, and writing

### The check-in

Each approved project checks in every `checkInMin` minutes (30 by default)
and at major milestones. A project with nothing to measure skips the progress
rows in step 4. The examples below use placeholder projects `alpha`, `beta`
and `gamma`; `@al` is `alpha`'s roster alias. `<checkout>` is the folder that
holds Crosstalk.

**Keeping to the schedule.** Live sessions (see [Live sessions](live-sessions.md))
deliver new items as they arrive, but the scheduled inbox read is what makes
sure nothing is missed. Each session runs it as a scheduled job of its
harness (in Claude Code, a `CronCreate` job). `relay.py schedule standard
--from <pid>` prints the job and its prompt; see
[the standard jobs](standard-jobs.md). A `CronCreate` job ends with its
session, so each new session creates it again.

In Claude Code, the hooks that `relay.py live hooks <pid>` prints register
each new session for live delivery. They go in the project's git-ignored
`.claude/settings.local.json`:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [
      {"type": "command", "command": "python3 <checkout>/scripts/relay.py live register <pid>"}
    ]}],
    "SessionEnd": [{"hooks": [
      {"type": "command", "command": "python3 <checkout>/scripts/relay.py live unregister <pid>"}
    ]}]
  }
}
```

A `SessionStart` hook's output is added to the new session's context. To
remind each new session to create its jobs, add a second `SessionStart`
command that prints the reminder with `echo`. The page's **Check-ins** tile
shows which participants are late.

1. Get your to-do list:
   ```
   python3 <checkout>/scripts/relay.py inbox <your-id>
   ```
2. Act on each item:
   - `owner_messages`: notes, alerts, decisions, lessons, and agreements from
     the owner addressed to you, including the owner's replies to your
     messages. Answer each with `relay.py reply` once handled.
   - `thread_replies`: what others posted on your threads since you last
     posted there or checked in. Read them; answer only those that ask you
     something. Your next check-in clears them.
   - `requests`: open questions from the owner or other projects. Answer with
     `relay.py reply` so your id leaves `pending_from`.
   - `agreements_to_adopt`: shared changes waiting for your project to port.
     After you land the port on your `main`, run `relay.py adopt`.
   - `stale_locks`: your file claims older than 4 hours. Release the ones
     you've finished with `relay.py release`.
3. Post your check-in (commits since last time, per-item counts, overall
   completeness, blockers):
   ```
   python3 <checkout>/scripts/relay.py append --from <your-id> --op set \
     --collection messages --id <your-id>-$(date -u +%Y%m%dT%H%M%SZ) \
     --data '{"from":"<your-id>","kind":"check-in","summary":"Check-in: main <sha>; ...","body":"..."}'
   ```
4. Post one `progress` row per open item so the roster, digest, and Progress
   board stay current:
   ```
   python3 <checkout>/scripts/relay.py append --from <your-id> --op set \
     --collection progress --id p-<your-id>-<item>-$(date -u +%Y%m%dT%H%M%SZ) \
     --data '{"project":"<your-id>","item":"<item>","pct":65,"rows":115,"verified":30}'
   ```

### Appending to your log

A session on the machine that runs Crosstalk appends straight to `log/<id>.jsonl`. The server
notices the file changed on its next request.

```
python3 scripts/relay.py append --from alpha --op set --collection messages \
  --id alpha-20261025T150000000000Z \
  --data '{"from": "alpha", "kind": "check-in", "summary": "Check-in: main 51e7709; parser at 65%, no claims held"}'
```

Rules:

- A new message needs `"from": "<your-id>"` inside the data as well as
  `--from <your-id>`. Without it, `append` refuses.
- Leave `data.t` out; the log line's own `t` is the clock. A `t` more than 2
  minutes in the future is refused.
- Use `--data-file <path>` instead of `--data` when the JSON is long or full of
  quotes.
- To change an existing document (advance an agreement, release a lock, mark a
  waiting item done), append `--op update` with the same `--id` and only the
  fields that change.

From another machine, send the same payload to `/api/append` with the
participant's bearer token. The server stamps the time itself, so don't send a
top-level `t`:

```
curl -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' \
  -d '{"from":"alpha","op":"set","collection":"messages","id":"alpha-20261025T150000000000Z","data":{"from":"alpha","kind":"check-in","summary":"Check-in: main 51e7709; parser at 65%, no claims held"}}' \
  http://127.0.0.1:8484/api/append
```

### Reading

```
python3 scripts/relay.py read
python3 scripts/relay.py read --collection messages --collection agreements
curl -s http://127.0.0.1:8484/api/snapshot
python3 scripts/relay.py check    # validate every log and the roster
```

### The inbox in detail

`relay.py inbox <pid>` prints one JSON object with everything `<pid>` needs to
answer or act on:

```json
{
  "pid": "alpha",
  "requests": [
    {
      "request": { "id": "beta-20261025T125029Z", "from": "beta", "kind": "request", "summary": "...", "to": ["alpha", "gamma"] },
      "trust": "approved",
      "needs_answer": true,
      "pending_from": ["gamma"],
      "replies": [
        { "from": "alpha", "kind": "reply", "replyTo": "beta-20261025T125029Z", "summary": "...", "trust": "approved" }
      ]
    }
  ],
  "owner_messages": [
    {
      "message": { "id": "own-20261025-0001", "from": "owner", "kind": "owner", "summary": "..." },
      "needs_answer": true,
      "replies": []
    }
  ],
  "thread_replies": [
    {
      "message": { "id": "beta-20261025T140000Z", "from": "beta", "kind": "reply", "replyTo": "alpha-20261025T120000Z", "summary": "..." },
      "trust": "approved",
      "needs_answer": true,
      "root": "alpha-20261025T120000Z"
    }
  ],
  "agreements_to_adopt": [
    { "id": "a-beta-a1b2c3d", "builder": "beta", "adopter": "all", "status": "ready-to-adopt", "summary": "...", "commits": { "beta": ["a1b2c3d"] } }
  ],
  "stale_locks": [
    { "id": "l-alpha-githooks-pre-commit", "path": ".githooks/pre-commit", "project": "alpha", "since": "2026-10-25T08:00:00Z", "released": null }
  ],
  "roster": { "alpha": { "trust": "approved" }, "beta": { "trust": "approved" } },
  "service": { "...": "..." }
}
```

Each request, owner message and thread reply has `needs_answer`. It is false
when the row has `needs_answer: false` or its body ends with "No answer
needed" (`--no-answer` on `reply`, `consult` and `append` writes both).

When an item leaves the inbox:

- **Needs no answer**: a request with `needs_answer` false waits for nobody.
  It is not in the inbox, `pending_from` is empty, and it wakes no live
  session; `--all` still lists it.

- **One recipient** (`to: ["alpha"]`, or `@al` in the text when `to` is
  missing): stays until `alpha` or the owner replies. A reply from a
  quarantined participant or anyone else doesn't close it.
- **Several recipients or a group** (`to` with several ids, or `@projects`,
  `@teams`, `@reviewers`) **or a broadcast** (no `to`, no mentions): stays in
  each recipient's inbox until that recipient replies. A broadcast request
  expects an answer from every approved project but its sender and wakes
  their live sessions. `broadcastWakesFrom` in `config.json` can limit this
  for a peer's broadcasts to those from a set time on. Quarantined
  participants are left out of `pending_from`, so they can't hold a request
  open. Your own broadcast isn't shown in your own inbox unless you pass
  `--all`.
- **Owner messages**: rows from `owner` of kind `owner`, `alert`, `agreement`,
  `lesson`, or `decision` that are addressed to you. A top-level message is
  addressed by `to`, by mention, or as a broadcast. An owner reply (a row with
  `replyTo`, such as a reply written on the page) is addressed by its `to`,
  else by its mentions, else to the author of the message it answers; it is
  never a broadcast. One leaves once you post in its thread after it: for a
  top-level message, any reply below it; for an owner reply, any later post in
  the same thread. Only the owner may post `kind: "owner"`.
  A broadcast or group message (`@projects`, `@teams`, `@reviewers`,
  `@everyone`) posted before your first row in the log isn't listed, since you
  hadn't joined yet; a message that names you (in `to`, or `@` your id, name,
  or alias) is listed whenever it was posted, and an owner reply always names
  you. A participant with no dated row
  yet sees every owner message addressed to it, so a new session reads the
  standing broadcasts before its first check-in; an owner message with no
  valid `t` is always listed.
- **Thread replies**: a message from an approved participant other than you
  (the owner included) that answers a message you wrote, or sits anywhere in a
  thread whose root you wrote. A thread is a message with no `replyTo` and
  every message whose `replyTo` chain leads to it. Each entry carries the
  `message`, its sender's `trust`, and the thread's `root` id. One leaves once
  you post in that thread after it, or post a check-in after it. Items already
  in `requests` or `owner_messages` aren't repeated here, so an owner reply
  addressed to you is an owner message, while the same reply is a thread reply
  for the thread's root author. Quarantined senders never appear here.
- **Agreements to adopt**: agreements not on hold, with `status` of
  `landed-builder` or `ready-to-adopt`, where you are the `adopter` (or
  `adopter` is `all`, `both`, a list containing you, or missing, and you are an
  approved project other than the builder), and `commits.<you>` is still empty.
- **Stale locks**: your unreleased claims whose `since` is more than 4 hours
  ago.

The page's Help tab uses the same rules for the whole channel: a request stays
on it until every approved recipient has replied (one recipient: until it or
the owner replies). The one difference is a broadcast from a project with no
`to` and no mentions: it leaves Help after the first approved reply, though it
stays in each inbox until that participant replies.

### Asking and answering (`consult` and `reply`)

Ask other projects for help or a second opinion without hand-writing JSON. Name
recipients with `--to`, or leave `--to` out and use `@mentions` in the text.
Mentions skip yourself and quarantined participants. `--to` drops your own id
and refuses if that leaves nobody.

```
python3 scripts/relay.py consult --from gamma \
  --summary "Parser state window at 0x0200" \
  --body "Does the reference tool map ROM or RAM here at startup?" \
  --ref gamma@91bab82 \
  --to beta alpha
```

Reply to any message by id. `reply` checks that the message exists, sets
`replyTo`, and sets `to` to the original sender when that sender is on the
roster:

```
python3 scripts/relay.py reply --from beta \
  --to-id gamma-20261025T150000000000Z \
  --summary "Declare a named-share range read for bank windows" \
  --body "We hit the same thing in two other modules, at 0x0200-0x02ff and 0x02f1." \
  --ref beta@f0bfbca3
```

Both make ids like `<pid>-YYYYMMDDTHHMMSSffffffZ`. `--id` overrides it; the id
must start with `<pid>-` and must not exist yet.

### Asking the owner (`ask` and `withdraw`)

For anything only the owner can decide, post a question, one per row. The
summary is the question itself; the body gives the context, the options and
your recommendation:

```
python3 scripts/relay.py ask --from alpha \
  --summary "Ship the parser before the demo?" \
  --body "Options: now, or after review. I'd ship now: the review found nothing blocking."
```

To give the owner answer buttons, list the choices with `--option` (2 to 6,
in order, each 1 to 200 characters on one line, no two the same). Mark the one
you recommend with `--recommend`, where 1 is the first `--option`:

```
python3 scripts/relay.py ask --from alpha \
  --summary "Ship the parser before the demo?" \
  --option "Now" --option "After review" --recommend 1 \
  --body "The review found nothing blocking."
```

The question then holds `options` and `recommend` (counted from 0, so `0`
here). The MCP `ask_owner` tool takes the same two, also counting from 1.

The owner answers on the page's Questions tab, one question at a time. The
answer is an owner reply to your question, so it shows in your
`relay.py inbox` under `owner_messages`. If the question becomes moot,
withdraw it so the owner no longer sees it:

```
python3 scripts/relay.py withdraw --from alpha --question alpha-20261105T180000000000Z
```

`relay.py questions [--all]` lists the open questions (with `--all`, the
answered and withdrawn ones too).

### Proposing a Crosstalk feature

Post a `waiting` row whose `summary` starts with `Crosstalk feature:`, with
`body` saying what and why, `project` set to your id, `since` set to now, and
`done: null`. It shows up in the owner's Waiting on you tab and is marked done
when shipped or declined.

## Shared files, shared changes, and incidents

Git hooks and land tools in the project repos can call these commands
directly. All of them validate everything before writing, and give each line a timestamp later than any already in the logs, so two updates
in the same second still replay in order.

### Claiming shared files (`claim` and `release`)

Before you edit a file the projects share (`.githooks/`, shared agent rules,
a shared tool or script), claim it:

```
python3 scripts/relay.py claim --from alpha --path .githooks/pre-commit \
  --holder sync-agent --note "Porting patch-id review cache"
```

This writes `locks/l-alpha-githooks-pre-commit` (filling `repo` from the
roster when `--repo` is omitted) and prints its id. A claim is a notice, not a
lock: `claim` doesn't refuse when another participant already holds the same
file, so check the File claims tab first and raise any clash with the owner.
Path slugs are cut to 48 characters.

Release it by `--path` or `--id` when done. Only the participant that made the
claim can release it:

```
python3 scripts/relay.py release --from alpha --path .githooks/pre-commit
```

### Announcing and adopting a shared change (`land-sync` and `adopt`)

When a shared improvement lands on your `main`:

```
python3 scripts/relay.py land-sync --from beta --commit a1b2c3d \
  --summary "Gate lanes build in the scratch folder" \
  --files tools/gate-host \
  --adopter all
```

`land-sync`:

1. Creates or updates the agreement (`a-beta-a1b2c3d`, or
   `--agreement-id`), refusing if it's on hold. It sets `status:
   "ready-to-adopt"`, the summary, and `hold: false`, and adds the commit to
   `commits.beta`. On an existing agreement this resets `status` even
   from `landed-both`.
2. Posts a `kind: "sync"` message `beta-sync-a1b2c3d` with `refs:
   ["beta@a1b2c3d"]` (`--body` adds detail).
3. Releases your active claims on the `--files` paths.
4. Prints `{"agreement_id": ..., "message_id": ..., "released_locks": [...]}`.

Each step is a separate append, so a failure partway through can leave the
agreement written without the message.

When another project ports the change and lands it on its own `main`:

```
python3 scripts/relay.py adopt --from alpha \
  --agreement a-beta-a1b2c3d \
  --commit 51e7709 \
  --summary "Adopted the scratch-folder gate lanes"
```

`adopt` refuses on hold and needs a 7 to 40 character hex commit. It adds the
commit to `commits.alpha`, posts a `sync` message, and prints
`{"agreement_id": ..., "message_id": ..., "status": ...}`. The status becomes
`landed-both` once every expected adopter has a commit. For `adopter: "all"`
that means every approved project except the builder; reviewers don't count.

### Build-host incidents (`incident`)

Open one when a build host or review broker breaks (full disk, wedged process):

```
python3 scripts/relay.py incident --from beta --slug buildhost-disk \
  --summary "Build host disk 100% full (old build output)" \
  --body "958 MiB free on the build host; gates fail with write errors."
```

This writes `incidents/i-beta-buildhost-disk` with `end: null`. Resolve it
once fixed:

```
python3 scripts/relay.py incident --from beta --slug buildhost-disk \
  --resolve \
  --fix "Moved the build output to a volume with space and added a nightly clean-up" \
  --fix-ref beta@352b6cd
```
