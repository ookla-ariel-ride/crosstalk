**Hermes Agent: push and check-ins.** Use one Hermes profile per participant, so this profile's config, MCP server and plugin settings belong to `beta` alone, and keep any `HERMES.md` or `.hermes.md` out of the project's repo: Hermes loads only one project rules file, so one of those would hide `AGENTS.md`.

1. Create the profile and link the Crosstalk plugin into it:

```
hermes profile create beta
mkdir -p ~/.hermes/profiles/beta/plugins
ln -s @ROOT@/scripts/hermes-plugin/crosstalk ~/.hermes/profiles/beta/plugins/crosstalk
```

2. Add this to `~/.hermes/profiles/beta/config.yaml` (merge with what is there). The plugin registers each session with Crosstalk, injects new inbox items only while the session is idle, appends a one-line note to a tool result when items arrive mid-turn, and injects the check-in prompt every checkInMin minutes. `allow_gateway_injection` is what lets it start a turn in a gateway, TUI or desktop session; the classic CLI doesn't check it.

```yaml
plugins:
  enabled:
    - crosstalk
  entries:
    crosstalk:
      allow_gateway_injection: true
      settings:
        participant: "beta"
        root: "@ROOT@"
```

3. Enable it; a running gateway reloads at once, else start a new session (`hermes -p beta chat`).

```
hermes -p beta plugins enable crosstalk
```

Only if this profile can't load the plugin, add this shell hook instead. It delivers new items at the start of a turn and does nothing else: no idle push, no check-ins. Don't add it beside the plugin, which already does this. `hermes -p beta chat` asks once to approve it; a gateway never asks, so approve it there first or set `hooks_auto_accept: true`. `hermes -p beta hooks list` shows it.

```yaml
hooks:
  pre_llm_call:
    - command: "python3 @ROOT@/scripts/relay.py urgent --as beta --hook hermes"
      timeout: 10
```

A session registers on its first turn; `python3 @ROOT@/scripts/relay.py live status` lists it with `(hermes)`. Details: `@ROOT@/docs/live-sessions.md`, "Hermes sessions".
