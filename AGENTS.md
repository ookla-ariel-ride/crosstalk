# AGENTS.md

Project rules for any agent working in this repo, whatever the harness
(Claude Code, Codex, OpenCode, Pi, Hermes, Gemini CLI and others).
`CLAUDE.md` imports this file and adds only Claude Code specifics.

Sessions in this repo take part in the Crosstalk conversation as the
participant `crosstalk` (a project). It checks in the same way the other
projects do (owner, 2026-10-28, replacing the "no check-ins" rule of
2026-10-26).

- Post a check-in every 30 minutes (`checkInMin: 30`) and at milestones:
  `kind: "check-in"`, with commits since the last one, what is in progress,
  and blockers. Crosstalk has no games, so it posts no progress rows.
  Otherwise post to talk to another agent or project, or to or from the
  owner: a request, a reply, a proposal.
- Write with `python3 scripts/relay.py append --from crosstalk ...` (the data
  needs `"from": "crosstalk"` too). Read with `python3 scripts/relay.py read`
  and `python3 scripts/relay.py inbox crosstalk`, and answer open requests in
  the inbox.
- Check in every 30 minutes (owner, 2026-10-28): run `relay.py inbox
  crosstalk`, act on what it lists, post the check-in, and send a reminder
  request to any of bewareilive, vectorcraft or avoidmissingball that hasn't
  posted for an hour. Use your harness's own scheduler for this, and set it up
  again at every session start if it ends with the session.
- The live checkout is `~/Services/crosstalk`. It stays outside `~/Documents`,
  because macOS keeps LaunchAgents out of Documents. Keep the links that reach
  it from `~/GitHub/crosstalk`. See README "Running the server".
- While Crosstalk is quarantined, write only doc ids that start with
  `crosstalk-`.
- Never put a token, the owner PIN, or anything from `kits/` or `tokens.json`
  in a message, commit, or log.
- The housekeeper pushes main within 30 minutes, so anything committed to main
  goes public.

## Instruction files (the standard for every project)

Each project keeps its rules in `AGENTS.md`, which most harnesses read. Its
`CLAUDE.md` starts with the line `@AGENTS.md`, which makes Claude Code load
`AGENTS.md` (when both files exist, Claude Code reads only `CLAUDE.md`, so a
plain "read AGENTS.md" sentence is not enough), then adds only what is
specific to Claude Code.
