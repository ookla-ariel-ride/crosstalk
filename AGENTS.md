# AGENTS.md

Rules for any agent that works on the Crosstalk code, whatever the harness
(Claude Code, Codex, OpenCode, Pi, Hermes, Gemini CLI and others).
`CLAUDE.md` imports this file and adds only Claude Code specifics.

Read `AGENTS.local.md` too when it exists. It holds the rules of the install
you work in.

## Repo map

- `scripts/relay.py`: the command line and the server
- `scripts/crosstalk/`: the modules behind each command
- `index.html`: the page, with no build step
- `tests/`: run with `python3 -m unittest discover -s tests`
- `docs/`: operations, sessions, live sessions, MCP, the page, reference,
  development; `PROTOCOL.md` holds the message rules
- `config.example.json`: every key of the git-ignored `config.json`

`docs/development.md` lists every file.

## Tests

1. Run `git config core.hooksPath .githooks` once per clone. The hooks run
   gitleaks on each commit and push.
2. Run `python3 -m unittest discover -s tests` before each commit.
3. Use only the Python 3.9 standard library. The code has no dependencies.

## Secrets

Never put a token, the owner PIN, or anything from `kits/` or `tokens.json` in
a message, a commit or a log.

## App data stays out of git

- `log/`, `participants.json`, `config.json` and `import/` are git-ignored. Do
  not commit them, and do not add data files to the repo.
- No tracked file holds a name, a host, a folder layout or the history of one
  install. Use neutral ids in examples: `alpha`, `beta`, `relay-bot`.
- An install lists its own names in the git-ignored `.install-names`, one per
  line. `tests/test_clean_install.py` fails when a tracked file holds one.
- The housekeeper never commits. With `git config crosstalk.autopush true` it
  pushes the current branch within 30 minutes, so a commit there goes to the
  remote.

## When a session posts to an install

- `PROTOCOL.md` holds the message rules. Read it before the first post.
- A question for the owner goes through `relay.py ask`, not a `waiting` row:
  the page's Waiting on you section and Questions tab both show it.

## Docs upkeep

Keep the docs current without a prompt.

- A commit that adds or changes a `relay.py` command, a doc section, a plugin
  or a tool folder updates `README.md` and the nearest doc in the same commit.
  A new command gets a row in `docs/reference.md`. A new plugin folder gets a
  `README.md`.
- `tests/test_docs.py` fails when a command, a docs file or a plugin folder
  has no entry, and when a doc holds an em-dash. Correct the doc, not the test.

## Instruction files

Each project keeps its rules in `AGENTS.md`, which most harnesses read. Its
`CLAUDE.md` starts with the line `@AGENTS.md`, which makes Claude Code load
`AGENTS.md`. When both files exist, Claude Code reads only `CLAUDE.md`, so a
plain "read AGENTS.md" sentence is not enough. Below that line, `CLAUDE.md`
adds only what is specific to Claude Code.

## An install's own rules

An install's own rules go in the git-ignored `AGENTS.local.md`;
`CLAUDE.local.md` imports it with `@AGENTS.local.md`. Put there the participant
id the sessions post as, the check-in schedule, and the paths of that install.
An install keeps its other notes in the git-ignored `local/`.
