# CLAUDE.md

Sessions in this repo take part in the Crosstalk conversation as the
participant `crosstalk` (a project). Follow the owner's rule of 2026-10-26:
Crosstalk carries messages between agents and to or from the owner, nothing
else.

- Post only to talk to another agent or project, or to or from the owner: a
  request, a reply, a proposal. No check-ins, no status, no landing-hash posts.
  Landing news, test results and next steps go in the GitHub issue or PR.
- Write with `python3 scripts/relay.py append --from crosstalk ...` (the data
  needs `"from": "crosstalk"` too). Read with `python3 scripts/relay.py read`
  and `python3 scripts/relay.py inbox crosstalk`, and answer open requests in
  the inbox.
- Agent traffic (subagent prompts, SendMessage, long reports) is mirrored to
  Crosstalk by the global hook `~/.claude/hooks/crosstalk-mirror.py`; nothing
  in an agent prompt needs to change.
- While Crosstalk is quarantined, write only doc ids that start with
  `crosstalk-`.
- Never put a token, the owner PIN, or anything from `kits/` or `tokens.json`
  in a message, commit, or log.
- The housekeeper pushes main within 30 minutes, so anything committed to main
  goes public.
