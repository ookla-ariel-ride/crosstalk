@AGENTS.md

## Claude Code

- The 30-minute check-in runs as a `CronCreate` job, which ends with the
  session, so set it up again at every session start. The `SessionStart` hook
  prints this rule and registers the session for live messages; check with
  `relay.py live status`.
- Agent traffic (subagent prompts, SendMessage, long reports) is mirrored to
  Crosstalk by the global hook `~/.claude/hooks/crosstalk-mirror.py`; nothing
  in an agent prompt needs to change.
