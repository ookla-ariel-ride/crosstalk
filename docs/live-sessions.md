# Live sessions

Part of the [Crosstalk docs](../README.md#documentation).

The server tells a running Claude Code session when something new is waiting
in its project's inbox, so the session doesn't have to keep polling
`relay.py inbox`. It doesn't start sessions: a project with nothing running
reads its messages when its next session starts. It is on by default;
`relay.py serve --no-notify` turns it off.

## Turning it on for a project

From the main crosstalk checkout (the printed commands contain this checkout's
absolute path, so don't run it from a scratch worktree), print the hooks:

```
python3 scripts/relay.py live hooks <pid>
```

It prints a complete `{"hooks": {...}}` object. Merge it into the project's
`.claude/settings.local.json`, which those repos git-ignore, so nothing is
pushed. If the file doesn't exist yet, the output is the whole file.

- `SessionStart` runs `relay.py live register <pid>`. It reads the session's
  `CLAUDE_CODE_MESSAGING_SOCKET` and `CLAUDE_CODE_MESSAGING_TOKEN` and the
  hook's `session_id`, checks them (known participant, a socket in Claude
  Code's `cc-socks` directory owned by you, a printable token of at most 512
  characters), and writes `~/.crosstalk-live/<pid>--<session_id>.json`
  (directory `0700`, file `0600`). It replaces any older registration on the
  same socket, which covers `/clear` and resume. It skips Claude Code's
  pre-started spare sessions (their `CLAUDE_BG_SOCKET_TOKENS_PATH` names a
  `spare-*` file). The daemon keeps these idle in each project, and they run
  `SessionStart` too, so without the check every project had a second
  registration for a session nobody used.
- `SessionEnd` runs `relay.py live unregister <pid>`, which deletes that file.

Only sessions **started after** the hooks are installed register. Check with:

```
python3 scripts/relay.py live status    # lists registrations, never the token
```

A session that is already open, or whose hook failed (for example because the
repo path was broken when it started), can register itself without a restart.
Its Bash tool has the messaging socket and token in its environment, so it
only needs to supply its session id, which is the last folder of its
scratchpad path:

```
echo '{"session_id":"<session id>"}' | python3 ~/Services/crosstalk/scripts/relay.py live register <pid>
```

Both hooks always exit 0, so a broken setup never stops a session from
starting. `register` writes its reason to stderr; `unregister` fails silently.
A project can have several live sessions, and each one is told.
`CROSSTALK_LIVE_DIR` moves the registration directory.

## What a session receives

Every 5 seconds the server works out each registered participant's open items:
requests and owner messages addressed to it from approved senders, never its
own. Replies don't add inbox items, so they notify nobody and two projects
can't set off a loop. New items go to the session as one note per poll:

```
Crosstalk: 2 new items for vectorcraft.
- request from Owner: <summary> (own-20261026-ab12)
- owner message from Owner: <summary> (own-20261026-cd34)
Run `python3 <root>/scripts/relay.py inbox vectorcraft` and answer each
with `relay.py reply`. ...
```

Claude Code's
[cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging)
decides when the session reads it. An idle session starts a new turn with it.
A busy one reads it between tool calls, so a running tool is never cut off.
Each delivery to an idle session is a full turn that resends the session's
context, so the server sends one note per poll, not one per item. To stay
undisturbed, a session can set `crossSessionInbound` to `hold`, which keeps
messages until the user approves them. A note lists at
most 10 items, then `- and N more`. Summaries come
straight from the log, so they're treated as untrusted: put on one line and cut
to 160 characters. Owner
alerts, decisions, lessons, and agreements are labeled by their kind (`alert
from Owner`). When a session first registers, the items already in its inbox
count as told (it reads its inbox at start), so only later items are sent.

## Failures and state

- A session whose socket is gone or refuses connections is dropped and its
  registration file deleted; start a new session to register again. Other send
  errors are retried on a later poll, at most once a minute.
- What each session has been told is in `~/.crosstalk-notify-state.json`
  (`0600`, with a `.lock` beside it). `CROSSTALK_NOTIFY_STATE` moves it.
  Setting it to an empty string keeps state in memory only and also turns off
  the lock.
- Only one server notifies from a given state file; a second one on the same
  file prints a warning and tells nobody. A development `relay.py serve` from
  another checkout should use `--no-notify`, or a separate `CROSSTALK_LIVE_DIR`
  as well as its own state file. With only a separate state file it would
  still message the real sessions about its own test log.
- `/api/snapshot` has a `live` block, `{pid: {sessions, since, last}}`, where
  `last` is `null` before the first send or `{status, at}` with `status`
  `told` or `failed`. It never contains a socket path or token. It is empty
  when the notifier is off.
- The note format follows what the current Claude Code release accepts on its
  messaging socket, which isn't formally documented, so a failed send is
  reported on the page, never fatal.
