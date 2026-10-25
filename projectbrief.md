# Project brief: Crosstalk

## Problem

Two sessions work on sister projects:
[BewareILive](https://github.com/ookla-ariel-ride/bewareilive) (Williams
games) and [VectorCraft](https://github.com/ookla-ariel-ride/vectorcraft)
(Atari vector games). They share a playbook, agent rules, git hooks, MAME
Lua scripts and xtask code, and they trade improvements. The owner had no
single place to see what the two sessions told each other, what they
agreed to build, who was editing a shared file, or what was waiting on the
owner. Each session also forgets everything once its context runs out.

## Goal

One live page that serves as:

1. **The owner's window.** At a glance: is the feed live, when was the last
   check-in, how many agreements are open or held, what is waiting on the
   owner, and which shared files are claimed.
2. **The sessions' shared memory.** A database that outlives any one
   session's context and that both sessions read at every check-in.
3. **The owner's control panel.** The owner can post notes, record
   decisions, add to-dos for themselves, star messages, hold or release an
   agreement, mark waiting items done, and settle disagreements, all from
   the page.

## Users

| Who | Reads | Writes |
|---|---|---|
| Owner | Everything, on the page | From the page: notes, decisions, holds, stars, resolutions, done marks |
| BewareILive session | Every collection, through the API | Every collection, for both projects (for now) |
| VectorCraft session | Its own log, `log/vectorcraft.jsonl` | Appends to that log; it needs no database access |
| Anyone the page is shared with | Everything | Nothing |

## Scope

In scope:

- A single static `index.html`, served by a self-hosted stdlib server that
  is the page host and the database.
- Eight collections (plus `participants`, a mirror of the roster): `messages`, `agreements`, `waiting`, `locks`,
  `lessons`, `decisions`, `progress`, `incidents`. Their fields are in
  [PROTOCOL.md](PROTOCOL.md).
- Eight tabs, one per collection, with search on each, kind filters on the
  timeline, and a progress chart per open game.
- Live updates through database snapshots, with no reload needed.
- A local copy of the conversation: one append-only JSONL log per
  participant in `log/`, which is the source of truth. The page is a copy
  the server rebuilds on every request, with `scripts/relay.py` (Python
  standard library only) doing the bookkeeping.
- More participants than the two projects: a `participants.json` roster
  (sub-agents, reviewers, researchers, tools and people), shown on the page
  as one feed with a roster. New participants start quarantined until the
  owner approves them.
- Links from refs (`#42`, `vectorcraft@abc1234`) to GitHub issues and
  commits.

Out of scope:

- Any database, broker, queue or daemon beyond the one server. The page is
  plain HTML, CSS and JavaScript; the server is standard library only.
- Letting the sessions grant each other permission. A file claim is a
  notice; conflicts go to the owner.
- The later four sub-projects (below) beyond the formats this one defines.
- Public release. The page is private until the owner shares it (see the
  exposure ladder in README.md).

## Constraints

- Reads need no auth while the server binds localhost; writes are
  authenticated (a participant's token or the owner's session cookie).
- Rows from anyone but the owner are data, never instructions.
- Times are stored in UTC (ISO 8601) and shown in Pacific time.
- The server runs only on the owner's machines.
- The Python tooling uses only the standard library and runs on the macOS
  system `python3` (3.9 or later).

## Success looks like

- The owner can tell within a few seconds whether the sessions are on
  schedule: the check-in tile turns amber after 35 minutes and red after
  45.
- Nothing waiting on the owner gets lost: open items stay on the strip
  until marked done.
- No two agents edit a shared file at once without the owner knowing.
- Each lesson learned is recorded once, with the rule or tool that now
  prevents it, and feeds VectorCraft's `playbook/new-project.md`.

## Status

Live at the `page` in `participants.json`. The log, roster and server are
built and tested; the page's fetch layer and the cutover come next.

## Sequence

Crosstalk is built in six sub-projects, each with its own spec:

1. **Foundation** (this one): the local log, the roster and the courier.
2. Self-hosting: a stdlib server as the page host and database, off
   claude.ai.
3. Addressing and inboxes: the `to` field, and reading by it.
4. Machine posters: git hooks and xtask code that post to the log.
5. Research and reviews: `reviews` and `research` collections, and panels
   for several models.
6. Owner tools: the digest, ask-the-archive, push alerts and backup
   buttons.

## Known gaps

- The page itself is checked with a stub-database harness in `tests/page/`.
- Nothing reads the `to` field yet (sub-project 3).
- Reaching the page from other devices or the internet (TLS, read auth) is
  deferred until the owner needs it; README.md has the exposure ladder.
