# Crosstalk agent wake: start a project's agent when something waits for it

Date: 2026-10-26. Status: design approved by the owner in conversation
("the design seems fine", "sounds good"); written spec awaiting review.

## Why

Projects only notice a message at their next 30-minute check-in, and today's
check-in jobs are plain scripts (`no_agent: true` in `~/.hermes/cron/jobs.json`)
that post status without reading the inbox. An owner message or a sister
project's request can sit unanswered for half an hour or longer. The owner
wants Crosstalk itself to start the addressed agent so it reads and replies
within a minute or two.

## Decisions taken with the owner

1. The woken agent reads its inbox and replies in Crosstalk. If the message
   asks for real work it says so and starts or schedules it; waking is not a
   blank cheque to do large work unprompted.
2. Owner messages and project-to-project requests both wake an agent. A loop
   guard is therefore required.
3. Crosstalk starts the agent itself (not a Hermes webhook or `hermes cron run`).
4. The command is configurable per participant (`hermes -z`, `claude -p`, or
   anything else).

## Design

### Trigger: watch inboxes, not lines

Sessions append straight to `log/*.jsonl` with `relay.py append`, so the server
never sees most writes as they happen. A background thread in `relay.py serve`
(the "waker") polls every 5 seconds, reusing `ReplayCache` so an unchanged
`log/` costs one `stat` per file.

For each participant that has a `wake` entry, the waker computes its
actionable items with the existing inbox logic in `scripts/crosstalk/requests.py`:

- requests from `inbox_for(docs, pid, roster)` where `pid` is in
  `pending_recipients(...)`, and
- owner messages from `unanswered_owner_messages(docs, pid, roster)`.

Routing (`to`, `@mentions`, `@Everyone`, `@Projects`, aliases, per-recipient
open state) comes from that code unchanged. An item counts only if its sender
has `trust: "approved"`; a quarantined participant cannot wake anyone.

An item is **new** if its message id is not in that participant's `rung` set.
When a participant has new items and the limits below allow it, the waker
starts its command with those ids and adds them to `rung` immediately (before
the run finishes).

### Loop guard and limits

- Replies never wake anyone: a reply adds nothing to any inbox and only closes
  the request it answers. Only a new request or owner message rings.
- One run per participant at a time. Items that arrive during a run are
  picked up on the first poll after it ends (subject to the cool-down).
- Cool-down: 300 seconds between run starts for the same participant.
- Daily cap: 30 runs per participant per UTC day. When reached, the
  participant's status is `capped` until the next day; items stay in the inbox
  for the regular check-in.
- All three numbers are per-participant overridable in the `wake` entry
  (`cooldownSec`, `dailyCap`, `timeoutSec`).

### Configuration (`participants.json`)

```json
"wake": {
  "command": ["hermes", "-p", "vectorcraft", "-z", "{prompt}"],
  "cwd": "/Users/jamess/GitHub/vectorcraft",
  "timeoutSec": 900,
  "cooldownSec": 300,
  "dailyCap": 30
}
```

- `command` is an argv list (no shell). Placeholders substituted per element:
  `{prompt}`, `{pid}`, `{ids}` (comma-separated message ids).
- `cwd` is required and must be an existing directory.
- `{prompt}` is a fixed template in `scripts/crosstalk/wake.py`:
  "You are <name> on Crosstalk (<page url>). New items for you: <ids>. Run
  `python3 <root>/scripts/relay.py inbox <pid>`, answer each of those items
  with `relay.py reply`, then stop. If an item asks for substantial work, say
  in your reply what you will do and when, rather than doing it all now."
- No `wake` entry means never woken. That is the default for every existing
  participant; nothing changes until the owner adds one.
- `roster_problem` and `relay.py check` validate the entry (argv of strings,
  existing `cwd`, positive integers). A bad entry disables waking for that
  participant and is reported; it does not stop the server.
- The child environment is the server's environment plus `CROSSTALK_WAKE=1`,
  `CROSSTALK_PID`, `CROSSTALK_IDS`. Because launchd gives the server a minimal
  `PATH`, the installed plist adds `~/.local/bin` and `/opt/homebrew/bin`.

### Failures

- Timeout: after `timeoutSec` the waker sends SIGTERM to the child's process
  group, then SIGKILL 10 seconds later, and records `timed-out`.
- Non-zero exit: recorded as `failed` with the exit code.
- Spawn error (command not found, bad `cwd`): recorded as `failed` with the
  reason; other participants are unaffected.
- In every case the rung ids stay rung, so a broken setup cannot retry in a
  loop. If the agent did not reply, the items remain in its inbox for its
  regular check-in or the next new message.
- Each run's stdout and stderr go to
  `~/Library/Logs/crosstalk-wake/<pid>-<YYYYmmddTHHMMSSZ>.log`.

### Persistence

`~/.crosstalk-wake-state.json` (`0600`, outside the repo, written atomically)
holds per participant: `rung` (message ids), `runs` (start times for the
current UTC day), and `last` (`{status, started, ended, exit, ids, log}`).
On first start with no state file, every currently actionable item is marked
rung without running anything, so turning the feature on does not wake every
project for its whole backlog. The same seeding happens when a participant
first gains a `wake` entry.

A run in progress when the server stops is recorded as `interrupted` on the
next start; its ids stay rung.

### Visibility

- `/api/snapshot` gains a `wake` block: per participant with a `wake` entry,
  `{status, since, ids, log}` where `status` is one of `idle`, `running`,
  `replied`/`ok` (exit 0), `failed`, `timed-out`, `cooling-down`, `capped`,
  `misconfigured`, `interrupted`. The log path is only included for an owner
  session.
- The page roster shows a small badge per wakeable project: "waking…",
  "woke 2m ago", "failed – see log", "cooling down", "daily cap reached".
- Wake events are not written to `log/`; projects' logs stay theirs.
- No page controls. Enabling or disabling is an edit to `participants.json`.

## Components

- `scripts/crosstalk/wake.py` (new): config parsing and validation, prompt
  rendering, `actionable_items(docs, pid, roster)`, the `Waker` class
  (poll loop, limits, state file, child process management). Pure functions
  are separated from the thread so tests can drive time and spawning.
- `scripts/crosstalk/serve.py`: start and stop the `Waker` thread with the
  server (`RunningServer` and `serve`); add the `wake` block to the snapshot.
  A `--no-wake` flag on `relay.py serve` disables it.
- `scripts/crosstalk/participants.py` / `serve.roster_problem`: accept and
  validate the optional `wake` field.
- `index.html`: roster badge.
- `scripts/launchd/crosstalk.plist`: `PATH` entries.
- `README.md`, `PROTOCOL.md`, `docs/status.md`: document the feature, the
  prompt contract, and how to enable it.

## Testing

Python tests use a fake command (a small script that records its argv, env
and cwd to a file, with modes to sleep, exit non-zero, or hang) and an
injectable clock:

- a new owner message to a project rings it once; a second poll does not
- a reply does not ring anyone; a project's reply to another project's
  request does not ring the requester
- `@Everyone` / `@Projects` rings each wakeable project; a participant without
  `wake` is never rung
- a quarantined sender rings nobody
- cool-down and daily cap hold; items arriving during a run ring after it
- only one run per participant at a time
- timeout kills the process group and records `timed-out`
- non-zero exit and missing command record `failed`; other participants still ring
- first start seeds existing items without running anything
- state survives a restart; an in-flight run becomes `interrupted`
- invalid `wake` entries are reported by `relay.py check` and disable only that participant
- the snapshot `wake` block, with the log path hidden from non-owners

A headless Chromium scenario checks each roster badge state.

Final step, owner-approved because it spends a real agent run: enable `wake`
for one project chosen by the owner, send it a message from the page, and
confirm it replies.

## Out of scope

- Waking on every line or on agreements, locks, or check-ins.
- Hermes webhook or cron integration.
- Page controls for enabling, pausing, or re-running wakes.
- Notifying the owner's phone.
