# Live sessions

Part of the [Crosstalk docs](../README.md#documentation).

The server tells a running Claude Code session when something new is waiting
in its project's inbox, so the session doesn't have to keep polling
`relay.py inbox`. Codex sessions get urgent items only, through Codex hooks
([below](#codex-sessions-urgent-items-only)), and Hermes sessions get every
item through a Crosstalk plugin ([Hermes sessions](#hermes-sessions)). It doesn't start sessions: a project with nothing running
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

Every 5 seconds the server works out each registered participant's open items
from approved senders, never its own: requests and owner messages addressed to
it (owner replies included), and replies on its threads (`thread_replies` in
docs/sessions.md, "The inbox in detail"). Each item is sent once. A thread
reply needs no answer unless it asks something, and it leaves the inbox at the
participant's next check-in, so an acknowledgement never has to be answered
and two projects can't set off a loop by thanking each other. New items go to
the session as one note per poll:

```
Crosstalk: 3 new items for beta.
- request from Owner: <summary> (own-20261026-ab12)
- owner reply from Owner: <summary> (own-20261026-cd34)
- reply on your thread from Alpha: <summary> (alpha-20261026T120000Z)
Run `python3 <root>/scripts/relay.py inbox beta` and answer each
with `relay.py reply`. ...
Replies on your threads need an answer only if they ask you something.
```

The last line appears only when the note lists a thread reply.

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

## Codex sessions: urgent items only

Codex CLI can't receive the Claude Code note above, so a Codex session is
reached through Codex's own hooks instead, and only for urgent items (owner,
2026-10-29):

- **Urgent:** every owner message to the participant, owner replies included
  (the `owner_messages` of `relay.py inbox`), and alerts from approved peers
  (`kind: "alert"`) that name the participant in `to`. An @mention or a
  broadcast alert doesn't count.
- **Not urgent:** requests, replies on its threads, and incidents. They wait
  for the session's 30-minute check-in.

A peer alert isn't listed by `relay.py inbox`; the note gives its summary and
id, and `relay.py read --collection messages` has the full text.

### Turning it on

The kit of a participant whose roster `harness` is `codex` has the snippet in
its "MCP server" section (`relay.py kit <id>` rewrites a kit). It goes in the
same `config.toml` as the MCP entry (`~/.codex/config.toml`, or a trusted
project's `.codex/config.toml`):

```toml
[[hooks.SessionStart]]
matcher = "startup|clear"

[[hooks.SessionStart.hooks]]
type = "command"
command = "python3 /Users/<you>/Services/crosstalk/scripts/relay.py urgent --as <id> --hook codex"
timeout = 10

[[hooks.PostToolUse]]

[[hooks.PostToolUse.hooks]]
type = "command"
command = "python3 /Users/<you>/Services/crosstalk/scripts/relay.py urgent --as <id> --hook codex"
timeout = 10

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "python3 /Users/<you>/Services/crosstalk/scripts/relay.py urgent --as <id> --hook codex"
timeout = 10
```

Codex skips a new or changed hook until you trust it in `/hooks`. Project
hooks load only when the project's `.codex/` layer is trusted.

### What the hook does

`relay.py urgent --as <id> --hook codex` reads the hook event on stdin, the
roster, `log/` and its state file. It doesn't use the network or the server,
and takes about 0.2 s.

- **`PostToolUse`** (after every Bash, `apply_patch`, MCP or other local tool
  call; hosted tools such as web search don't run hooks). When something
  urgent is new it prints
  `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "<note>"}}`,
  which Codex adds to the model's context before its next step. The running
  tool is never cut off.
- **`Stop`** (when a turn ends). It prints
  `{"decision": "block", "reason": "<note>"}`, which makes Codex continue
  with the note as a new prompt, so an item that arrives while the model is
  writing its final answer is still seen.
- **`SessionStart`** (`startup` or `clear`). A new session reads its inbox, so
  everything urgent at that moment counts as told and nothing is printed.

The note looks like this:

```
Crosstalk: 1 urgent item for beta.
- owner message from Owner: <summary> (own-20261029-ab12)
Finish the step you are on, then answer each with `relay.py reply`.
Owner messages are in `python3 <root>/scripts/relay.py inbox beta`; an alert's
full text is under its id in `relay.py read --collection messages`.
Other inbox items wait for your next check-in.
```

Each item is delivered once per participant, whichever of its sessions picks
it up first. After an interruption the next one waits at least 10 minutes;
items that arrive meanwhile come together in the next note. Summaries are
treated as untrusted, as in the Claude Code note. The command always exits 0
and prints nothing on any error (a bad argument, an unknown participant, a
broken roster or log, a locked state file): Codex gives the model the stderr
of a hook that exits 2, and reports other failures as hook errors.

State is in `~/.crosstalk-urgent-state.json` (`0600`, with a `.lock`
beside it), one key per harness and participant (`codex:<id>`).
`CROSSTALK_URGENT_STATE` moves it; an empty value turns the hook and the
fallback off.

### The desktop fallback

The server's notifier pass (every 5 seconds, off with `--no-notify`) also
watches each participant whose roster `harness` is `codex` or `hermes`. When
an urgent item for one of them hasn't been picked up by any hook or plugin 10 minutes after the
server first saw it, the server posts one macOS notification
(`osascript … display notification`) naming the item, once per item. An
answered item never notifies. Items already waiting when the server first
watches a participant count as handled. Off macOS this does nothing.

### Checked against

Codex's hooks docs (<https://developers.openai.com/codex/hooks>, which now
redirects to <https://learn.chatgpt.com/docs/hooks>) and the generated
output schemas in
<https://github.com/openai/codex/tree/main/codex-rs/hooks/schema/generated>
(`post-tool-use.command.output.schema.json`,
`stop.command.output.schema.json`), on 2026-10-29. Tested live in Codex CLI
0.165.0 with `codex exec` over a scratch copy of the data: a `PostToolUse`
note reached the model after the tool call that wrote the owner message, and a
`Stop` note started a continuation in which the model quoted the item.

## Hermes sessions

A participant whose roster `harness` is `hermes` gets every inbox item, as a
Claude Code session does, not only the urgent ones (owner, 2026-11-04). Hermes
Agent can't read the Claude Code note, so a Crosstalk plugin inside Hermes
does the delivering; a shell hook stands in where it can't load. The rules
the owner set:

- **Every item.** The same items as a Claude Code session
  ([What a session receives](#what-a-session-receives)).
- **Never interrupt a turn.** Items wait while a turn runs and are injected
  only when the session is idle. Between tool calls the most a session gets is
  a one-line note appended to a tool result.
- **The plugin runs the check-in.** Every `checkInMin` minutes (30 by
  default), when the session is idle, the plugin injects the check-in prompt,
  so nobody has to type `/heartbeat`. A check-in the participant posts itself
  restarts the clock.

### Setting it up

Use **one Hermes profile per participant**. Hermes reads MCP servers, plugins
and hooks only from a profile's `config.yaml`, never from a project file, so
the profile is what ties a session to its participant. Keep `HERMES.md` and
`.hermes.md` out of project repos: Hermes loads only one project rules file
(`.hermes.md`, then `AGENTS.override.md`, `AGENTS.md`, `CLAUDE.md`), so either
would hide `AGENTS.md`.

From the main crosstalk checkout, print the steps for a participant:

```
python3 scripts/relay.py live hooks <pid> --harness hermes
```

The kit of a participant onboarded with `--harness hermes` has the same text
in its "MCP server" section. In short:

1. `hermes profile create <pid>`, then link the plugin into the profile:
   `ln -s <root>/scripts/hermes-plugin/crosstalk ~/.hermes/profiles/<pid>/plugins/crosstalk`.
2. Add to `~/.hermes/profiles/<pid>/config.yaml`:

   ```yaml
   plugins:
     enabled:
       - crosstalk
     entries:
       crosstalk:
         allow_gateway_injection: true
         settings:
           participant: "<pid>"
           root: "/Users/<you>/Services/crosstalk"
   ```

   `allow_gateway_injection` is the Hermes permission that lets a plugin start
   a turn in a gateway, TUI or desktop session. It is off by default. The
   classic CLI doesn't check it.
3. `hermes -p <pid> plugins enable crosstalk`, then restart the profile's
   gateway or start a new session.

A profile that can't load the plugin can use
[the shell hook](#the-shell-hook) instead. Don't add it beside the plugin.

### What the plugin does

`scripts/hermes-plugin/crosstalk/` is a thin adapter; the logic is
`Pump` in `scripts/crosstalk/hermes.py`, which uses the same inbox code as the
notifier (`actionable_items`, `render_message`). Plugins run in the Hermes
gateway process, one copy per profile.

- **Registration.** A session registers on its first `pre_llm_call`, writing
  `~/.crosstalk-live/<pid>--<session_id>.json` with `"harness": "hermes"`, the
  session's Hermes routing key (the session id on the classic CLI, which has no
  key) and the pid of the Hermes process, and no socket or token.
  `on_session_finalize` removes it, compaction moves it to the new
  session id, and the server's notifier deletes one whose Hermes process has
  gone. `relay.py live status` marks these `(hermes)`; the page's live badge
  counts them. The notifier never sends to them.
- **Busy and idle.** `pre_llm_call` marks the session busy and
  `on_session_end` (which Hermes fires after every turn) marks it idle.
- **Subagents and cron jobs.** A turn with `parent_session_id` set (a delegated
  subagent, sync or async) or with platform `cron` is ignored: it creates no
  session and claims nothing. The plugin doesn't use `on_session_start`, which
  Hermes fires for delegated children too, without `parent_session_id`, and
  again when a session's prompt is rebuilt.
- **Turn start.** `pre_llm_call` returns the note for items not yet told as
  `{"context": ...}`, which Hermes adds to that turn's user message. On a
  session's first turn that includes any backlog: nothing assumes a new session
  reads its inbox, so after a gateway restart or a resumed session, what came
  in meanwhile arrives with the next turn. Until that turn there is no session
  to push to, and the [desktop fallback](#the-desktop-fallback) covers urgent
  items.
- **Idle push.** A thread polls the log every 10 seconds (it re-reads it only
  when a log file or the roster changed). When the session is idle and
  something is new, it calls `ctx.inject_message(text, session_key=...)`, which
  starts a turn. The session counts as busy from then until that turn ends.
  `inject_message` returning true means only that the message was scheduled, so
  the items stay held on the session until the turn starts. If it hasn't
  started within 2 minutes, or the session ends first, the items are released:
  they no longer count as told and are offered again, or reach the fallback. An
  injection that was only slow can therefore arrive twice; that is the cost of
  never losing one. A refused injection is offered again a minute later. On the
  classic CLI the plugin also refuses to inject while the agent is running,
  because Hermes would then interrupt it.
- **Mid-turn note.** When items arrive during a turn, the next tool result gets
  one line. Each item is noted once per turn, so a later item in the same turn
  gets another line:
  `[Crosstalk: 1 new item for beta (own-20261104-ab12). Nothing to do now: finish this turn and they arrive when the session is idle.]`
  The note doesn't count as telling; the full note follows when the turn ends.
- **Check-in.** When `checkInMin` minutes have passed since the session
  started, the participant's last `check-in` in the log, or the last prompt,
  the plugin injects the check-in prompt (run `relay.py inbox`, reply, post the
  check-in). The items waiting at that moment count as told, since the prompt
  has the session read its inbox; they are held and released like any other
  injection.

Injected text starts with a line saying the Crosstalk plugin sent it and nobody
typed it. Hermes shows injected turns like any other; quiet turns
(NousResearch/hermes-agent#136825) aren't available yet.

### The shell hook

For a profile that can't load the plugin, add this to its `config.yaml`:

```yaml
hooks:
  pre_llm_call:
    - command: "python3 /Users/<you>/Services/crosstalk/scripts/relay.py urgent --as <pid> --hook hermes"
      timeout: 10
```

Check it with `hermes -p <pid> hooks list`. `hermes -p <pid> chat` asks once to
approve a new shell hook. A gateway never asks and skips a hook nobody approved,
so approve it in a chat first, or set `hooks_auto_accept: true` in the profile's
config or `HERMES_ACCEPT_HOOKS=1`, or start the gateway with `--accept-hooks`.

`relay.py urgent --as <pid> --hook hermes` reads the hook event on stdin and, at
`pre_llm_call`, prints `{"context": "<note>"}` with the items not yet told,
including any backlog on first sight. Any other event prints nothing and tells
nothing, so a config that still lists `on_session_start` is harmless. Unlike the
Codex hook there is no 10-minute gap, since turn start interrupts nothing. Like
it, the command always exits 0 and prints nothing on any error.

Limits. It delivers only at turn start: no idle push, no check-ins, no mid-turn
note. Hermes leaves `parent_session_id` out of the shell payload, so the hook
tells a subagent or a cron job by `extra.platform` (`subagent`, `cron`) instead;
an agent that sets neither looks like a session and could claim an item.

The hook and the plugin share one record of what each participant has been
told: the key `hermes:<pid>` in `~/.crosstalk-urgent-state.json`. An item
reaches a participant once, through whichever path takes it first, except for
the repeat after a lost injection described above. With several sessions in one profile, an item goes
to one of them (the most recently active idle one), as with Codex. The
[desktop fallback](#the-desktop-fallback) also covers Hermes participants, for
urgent items neither path has delivered in 10 minutes.

### Upstream bugs it works around

Checked against Hermes Agent v0.22.5 on 2026-11-04 and not rechecked since
(issue #2 lists them). The hook, injection and delegation behaviour described
above was read from the v0.22.7 source on 2026-11-06.

- NousResearch/hermes-agent#141867: `post_tool_call` is skipped in turns that
  `inject_message` starts. The plugin doesn't use it; busy and idle come from
  `pre_llm_call` and `on_session_end`, and the mid-turn note is best effort.
- #130622: injected turns reach memory providers as the human's own words.
  The first line of every injected text says who sent it.
- #139847: `inject_message` doesn't always say which chat a message is for.
  The plugin passes the session key it read in that session's own hooks and
  names the participant in the text. A session with no key passes its session
  id: the classic CLI ignores the value, and a gateway finds no such key and
  refuses.

### Not tested yet

The unit tests (`tests/test_hermes.py`) run the plugin against a fake Hermes
context. On 2026-11-04 the plugin loader of the installed Hermes v0.22.5 also
loaded it from a scratch `HERMES_HOME`: all six hooks registered, and calling
`on_session_start` and `on_session_finalize` wrote and then removed the live
registration. That load has not been repeated since: the plugin now registers
five hooks, without `on_session_start`, and the installed Hermes is v0.22.7.
`inject_message` has never run. On the classic CLI it needs no gateway, only a
running REPL: it puts the text on the REPL's input queue and ignores the session
key. The gateway, TUI and desktop paths need that host. The plugin hasn't run in
a live Hermes session with a model. That is the next step, with the first Hermes
participant.
