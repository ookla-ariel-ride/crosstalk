# Crosstalk live-session messaging: tell running sessions about new messages

Date: 2026-10-26. Status: design approved by the owner in conversation
("b, with a as a backup", "sounds good"); written spec awaiting review.
Replaces the agent wake feature (`2026-10-26-agent-wake-design.md`).

## Why

The owner wants Crosstalk and the project agents to message back and forth
while those agents are running. Starting a fresh agent per message is not
wanted. A project with no running session reads its queued messages when its
next session starts, which already happens today (that is the backup).

## How Claude Code makes this possible

Claude Code documents a per-session inbox socket
(https://code.claude.com/docs/en/cross-session-messaging.md, "The session's
inbox socket"). Each session exports `CLAUDE_CODE_MESSAGING_SOCKET` and
`CLAUDE_CODE_MESSAGING_TOKEN` to hooks (including `SessionStart`) and Bash
commands. A process may connect, send `{"type":"auth","token":"<token>"}` as
the first line, then post a message; an idle session starts a new turn with
it, a busy one reads it at its next tool step. A message carrying the
session's token is verified as the session's own and is delivered without an
approval dialog, even in bypass-permissions sessions. A spike on 2026-10-26
confirmed this on Claude Code 2.1.311 with this message line:

```json
{"type":"user","from":"crosstalk","message":{"content":"<text>"}}
```

The auth line is documented; the message line shape is not, and was taken from
the 2.1.311 client. Delivery failures are therefore reported, never fatal.

## Design

### 1. A session checks in (registration)

Each project gets two hooks in its own `.claude/settings.local.json`
(git-ignored in those repos, so nothing is pushed):

- `SessionStart`: `python3 <crosstalk>/scripts/relay.py live register <pid>`
- `SessionEnd`: `python3 <crosstalk>/scripts/relay.py live unregister <pid>`

`register` reads the two environment variables and the hook's JSON on stdin
(`session_id`), and writes `~/.crosstalk-live/<pid>--<session_id>.json`
(directory `0700`, file `0600`, written atomically):
`{pid, session_id, socket, token, registered}`. `unregister` deletes that
file. Both exit 0 without output on any problem (missing variables, unknown
pid), so a broken setup never blocks a session from starting; problems go to
stderr. `relay.py live status` lists the live registrations (never the token).

A project may have several live sessions; each gets its own file and each is
notified.

`relay.py live hooks <pid>` prints the exact hook JSON to paste, with the
absolute path filled in. Installing the hooks in the three project repos is a
separate owner-approved step.

### 2. Crosstalk forwards new items (the notifier)

`scripts/crosstalk/notify.py` replaces `wake.py`. A `Notifier` thread in
`relay.py serve` polls every 5 seconds (reusing `ReplayCache`):

- For each registration, compute that participant's actionable items with the
  existing `actionable_items(docs, pid, roster)` (moved from `wake.py`
  unchanged: inbox requests where the pid is a pending recipient, plus
  unanswered owner messages; approved senders only; never the participant's
  own messages; ids must match `^[A-Za-z0-9._-]{1,80}$`).
- An item is new for a registration if its id is not in that registration's
  `told` set. On first sight of a registration, every current item is added to
  `told` without sending (the session reads its inbox at start).
- New items are sent as one message per registration per poll:

  ```
  Crosstalk: 2 new items for vectorcraft.
  - request from Owner: <summary, max 160 chars> (own-20261026-ab12)
  - owner message from Owner: <summary> (own-20261026-cd34)
  Run `python3 <root>/scripts/relay.py inbox vectorcraft` and answer each
  with `relay.py reply`. If one asks for substantial work, say in your reply
  what you will do and when.
  ```

  Summaries come from the log and are untrusted text; they are already in the
  inbox the session reads, are truncated, and have newlines collapsed.
- On a successful send the ids join `told`. If the connect fails because the
  socket is gone or refuses (`ENOENT`, `ECONNREFUSED`), the registration is
  stale: its file is deleted. Any other error is recorded on the registration
  and retried on the next poll, at most once per 60 seconds.
- Replies never notify anyone (a reply adds nothing to an inbox), so two
  projects cannot loop. Claude Code also throttles repeated messages per
  sender.

Validation before connecting: `socket` must be an absolute path to a Unix
socket owned by the current user, inside a directory matching Claude Code's
socket directories (`/tmp/cc-socks`, `/private/tmp/cc-socks`,
`/tmp/cc-socks-<uid>`, `/private/tmp/cc-socks-<uid>`, `$TMPDIR/cc-socks`,
`/private/var/folders/.../cc-socks`); `token` must be a non-empty string of at
most 512 printable characters; `pid` must be in `participants.json`. An
invalid file is ignored and reported once on stderr.

Persistence: `told` sets live in `~/.crosstalk-notify-state.json` (`0600`,
written only on change, keyed by `<pid>--<session_id>`; entries for vanished
registrations are dropped). `CROSSTALK_NOTIFY_STATE` and `CROSSTALK_LIVE_DIR`
override the paths (tests use them). A second server instance on the same
state file disables its notifier (same flock pattern as before).

### 3. Visibility

- `/api/snapshot` gains `live`: `{pid: {sessions: N, since, last: {status,
  at}}}` where `status` is `told` or `failed`. No socket path or token is ever
  included.
- The page roster shows a small "live" badge on each project with a session,
  and "couldn't reach session" when the last send failed. The wake badges go.

### 4. Removing agent wake

`wake.py`, `wake.json` support, the `wake` snapshot block and badges,
`--no-wake`, the wake state and log paths, and their tests and docs are
removed. `--no-notify` on `relay.py serve` turns the notifier off. The
launchd plist keeps its `PATH` line (harmless). `~/.crosstalk-wake-state.json`
is deleted by hand after the merge.

## Components

- `scripts/crosstalk/notify.py` (new): registration read/validate, message
  rendering, socket send, `Notifier` thread; `actionable_items` moves here.
- `scripts/crosstalk/live.py` (new) or a section of `relay.py`: `live
  register|unregister|status|hooks`.
- `scripts/crosstalk/serve.py`: swap Waker for Notifier; `live` snapshot block.
- `index.html`: live badge; remove wake badges.
- `README.md`, `PROTOCOL.md`, `docs/status.md`: replace the waking section.

## Testing

Python tests use a fake session: a Unix socket server in a temp dir that
records each line, with modes to refuse, vanish, or hang.

- register writes a 0600 file from env + stdin; unregister removes it;
  both exit 0 silently on missing env or bad pid
- a new owner message to a live project sends one message with auth line
  first, then the user line; a second poll sends nothing
- several new items in one poll arrive as one message
- first sight of a registration sends nothing for existing items
- replies, quarantined senders, and a participant's own messages send nothing
- two sessions for one project are both told
- a vanished socket deletes the registration; another error retries after 60 s
- invalid socket path, foreign-owned socket, bad token, unknown pid: ignored
- `told` survives a restart; snapshot `live` block never contains a token
- `--no-notify` starts no thread

Page scenario: the live and failed badges render; wake badges are gone.

Final check, owner-approved: install the hooks in VectorCraft, start a
session, post a message from the page, and see the session reply.

## Out of scope

- Starting agents that are not running.
- Messages from sessions to Crosstalk over the socket (sessions keep using
  `relay.py reply`/`append`).
- Sessions on other machines or in the cloud.
