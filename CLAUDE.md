@AGENTS.md

## Claude Code

- An install's git-ignored `CLAUDE.local.md` starts with `@AGENTS.local.md`,
  then adds what is specific to Claude Code in that install.
- A `CronCreate` job ends with its session. Create each scheduled job again at
  every session start, and report it with `relay.py schedule set`.
- `relay.py live hooks <pid>` prints the hooks that register a session for
  live messages. Check a registration with `relay.py live status`.
