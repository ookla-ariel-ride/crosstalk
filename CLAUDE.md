# CLAUDE.md

Sessions in this repo take part in the Crosstalk conversation as the
participant `crosstalk` (a project that checks in every 30 minutes). Follow
PROTOCOL.md, "What each session does".

- Write with `python3 scripts/relay.py append --from crosstalk ...`. Read with
  `python3 scripts/relay.py read` and `python3 scripts/relay.py inbox crosstalk`.
- Check in at session start if you have been away more than a couple of hours,
  after a change lands on main, and at session end. Crosstalk has no games, so
  a check-in has no `progress` rows. Its body gives the main hash and whether it
  is pushed, the commits since the last check-in, the test results (the Python
  suite and the page scenarios), what is next, and the owner items ("none" when
  there are none).
- While Crosstalk is quarantined, write only doc ids that start with
  `crosstalk-`.
- Never put a token, the owner PIN, or anything from `kits/` or `tokens.json`
  in a message, commit, or log.
- The housekeeper pushes main within 30 minutes, so anything committed to main
  goes public.
