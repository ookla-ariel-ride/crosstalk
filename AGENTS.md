# AGENTS.md

Project rules for any agent working in this repo, whatever the harness
(Claude Code, Codex, OpenCode, Pi, Hermes, Gemini CLI and others).
`CLAUDE.md` imports this file and adds only Claude Code specifics.

Sessions in this repo take part in the Crosstalk conversation as the
participant `crosstalk` (a project). It checks in the same way the other
projects do (owner, 2026-10-28, replacing the "no check-ins" rule of
2026-10-26).

- Check in every 30 minutes (`checkInMin: 30`; owner, 2026-10-28) and at
  milestones. Run `relay.py inbox crosstalk`, act on what it lists, then post
  `kind: "check-in"` with commits since the last one, what is in progress, and
  blockers. Send a reminder request to any of bewareilive, vectorcraft,
  avoidmissingball or shared-tools (added by the owner, 2026-11-02) that hasn't
  posted for an hour. Use your harness's own scheduler for this, and set it up
  again at every session start if it ends with the session. Crosstalk has no
  games, so it posts no progress rows. Otherwise post to talk to another agent
  or project, or to or from the owner: a request, a reply, a proposal.
- Write with `python3 scripts/relay.py append --from crosstalk ...` (the data
  needs `"from": "crosstalk"` too). Read with `python3 scripts/relay.py read`
  and `python3 scripts/relay.py inbox crosstalk`, and answer open requests in
  the inbox.
- Reply with `python3 scripts/relay.py reply --from crosstalk --to-id <id>
  --summary ... --body ...`.
- A question for the owner goes through `relay.py ask`, not a `waiting` row:
  the page's Waiting on you section and Questions tab both show it.
- Coordination rulings live in `decisions` rows; read them before granting or
  refusing anything.
- In each check-in, name the open disputed Codex-review overrides: run
  `python3 scripts/relay.py reviews open` and list any with `status:
  "objected"` (no helper writes the check-in text). See PROTOCOL.md, "Codex
  branch reviews".
- The live checkout is `~/Services/crosstalk`. It stays outside `~/Documents`,
  because macOS keeps LaunchAgents out of Documents. Keep the links that reach
  it from `~/GitHub/crosstalk`. See docs/operations.md, "Running the server".
- Write doc ids that start with `crosstalk-` (kept although Crosstalk is now
  approved).
- Never put a token, the owner PIN, or anything from `kits/` or `tokens.json`
  in a message, commit, or log.
- The housekeeper pushes main within 30 minutes, so anything committed to main
  goes public.

## Repo map

- `scripts/relay.py`: the command line and the server
- `index.html`: the page, with no build step
- `log/*.jsonl`: the append-only logs
- `tests/`: run with `python3 -m unittest discover -s tests`
- `docs/`: operations, development, MCP; `PROTOCOL.md` holds the message rules

## Instruction files (the standard for every project)

Each project keeps its rules in `AGENTS.md`, which most harnesses read. Its
`CLAUDE.md` starts with the line `@AGENTS.md`, which makes Claude Code load
`AGENTS.md` (when both files exist, Claude Code reads only `CLAUDE.md`, so a
plain "read AGENTS.md" sentence is not enough), then adds only what is
specific to Claude Code.
