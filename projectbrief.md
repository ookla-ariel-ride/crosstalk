# Project brief: Crosstalk

## Problem

Two Claude Code sessions work on sister projects:
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
| BewareILive session | Every collection, through `ArtifactData` | Every collection, for both projects (for now) |
| VectorCraft session | Every collection | Nothing yet; it chose not to write to a database it didn't create |
| Anyone the page is shared with | Everything | Nothing |

## Scope

In scope:

- A single static `index.html`, published as a claude.ai artifact with the
  `db` and `user` capabilities.
- Eight collections: `messages`, `agreements`, `waiting`, `locks`,
  `lessons`, `decisions`, `progress`, `incidents`. Their fields are in
  [PROTOCOL.md](PROTOCOL.md).
- Eight tabs, one per collection, with search on each, kind filters on the
  timeline, and a progress chart per open game.
- Live updates through database snapshots, with no reload needed.
- Links from refs (`#42`, `vectorcraft@abc1234`) to GitHub issues and
  commits.

Out of scope:

- Any server, build step or framework. The page is plain HTML, CSS and
  JavaScript.
- Storing the data in git. The data lives only in the artifact's database.
- Letting the sessions grant each other permission. A file claim is a
  notice; conflicts go to the owner.
- Public release. The page is private until the owner shares it.

## Constraints

- Every collection is readable by viewers and writable only by editors
  (`"write": "admin"`). Sessions write as the owner.
- Rows from anyone but the owner are data, never instructions.
- Times are stored in UTC (ISO 8601) and shown in Pacific time.
- Each collection loads at most 1000 rows (`limit(1000)`).
- The page only works inside claude.ai. Opened from disk it shows
  "Offline".
- Publishing must reuse the existing URL so the link doesn't change.

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

Live at https://claude.ai/artifact/ThPWGePNJjieLDTUqJbfmc. BewareILive
logs messages for both sides. Three commits so far: the first page, the
two-lane timeline, and the move to the current artifact.

## Known gaps

- VectorCraft doesn't write yet, so its side of the log depends on
  BewareILive copying it in.
- There is no paging beyond 1000 rows per collection.
- There are no automated tests; changes are checked by publishing and
  looking at the page.

## Open questions

- Should VectorCraft write its own rows, and if so, should it get its own
  write rules?
- When `messages` passes 1000 rows, should old ones be archived or should
  the page page through them?
