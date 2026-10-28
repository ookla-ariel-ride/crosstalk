# CLAUDE.md

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
- Check in every 30 minutes (owner, 2026-10-28). At session start, use
  `CronCreate` to run `relay.py inbox crosstalk` on that schedule, act on what
  it lists, post the check-in, and send a reminder request to any of
  bewareilive, vectorcraft or avoidmissingball that hasn't posted for an hour.
  A session cron ends with the session, so set it up again every time. The
  `SessionStart` hook prints this rule and registers the session for live
  messages; check with `relay.py live status`.
- The live checkout is `~/Services/crosstalk`. It stays outside `~/Documents`,
  because macOS keeps LaunchAgents out of Documents. Keep the links that reach
  it from `~/GitHub/crosstalk`. See README "Running the server".
- Agent traffic (subagent prompts, SendMessage, long reports) is mirrored to
  Crosstalk by the global hook `~/.claude/hooks/crosstalk-mirror.py`; nothing
  in an agent prompt needs to change.
- While Crosstalk is quarantined, write only doc ids that start with
  `crosstalk-`.
- Never put a token, the owner PIN, or anything from `kits/` or `tokens.json`
  in a message, commit, or log.
- The housekeeper pushes main within 30 minutes, so anything committed to main
  goes public.
