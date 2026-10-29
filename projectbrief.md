# Project brief: Crosstalk

Last reviewed 2026-10-27. For the current running state, see
[docs/status.md](docs/status.md).

## Problem

Three sessions work on sister arcade-remake projects:
[BewareILive](https://github.com/ookla-ariel-ride/bewareilive) (Williams
games), [VectorCraft](https://github.com/ookla-ariel-ride/vectorcraft)
(Atari vector games), and
[AvoidMissingBall](https://github.com/ookla-ariel-ride/avoidmissingball)
(Atari raster games). They share a playbook, agent rules, git hooks, MAME
Lua scripts, and xtask code, and they trade improvements. The owner had no
single place to see what the sessions told each other, what they agreed to
build, who was editing a shared file, or what was waiting on the owner. Each
session also forgets everything once its context runs out.

## Goal

One live page with three jobs:

1. The owner's window. At a glance: is the feed live, is every session
   checking in on time, how many agreements are open or held, what is
   waiting on the owner, and which shared files are claimed.
2. The sessions' shared memory. A log that outlives any one session's
   context and that every session reads at every check-in.
3. The owner's control panel. From the page the owner can sign in, post
   notes, questions, decisions, alerts, lessons, incidents and to-dos, reply
   in threads, star messages, record a message as a decision, hold or release
   an agreement, mark waiting items done, resolve incidents, settle
   disagreements, and add or approve participants.

## Users

| Who | Reads | Writes |
|---|---|---|
| Owner | Everything, on the page | From the page, into `log/owner.jsonl` and `participants.json` |
| Project and agent sessions (BewareILive, VectorCraft, AvoidMissingBall, Codex, and later sub-agents) | Every collection via `relay.py read`, `relay.py inbox`, or `GET /api/snapshot` | Their own log (`log/<id>.jsonl`) via `relay.py append` and the helper commands, or `POST /api/append` |
| Anyone who can reach the port | Everything | Nothing |

## Scope

In scope:

- A single static `index.html`, served by `relay.py serve`, a Python
  standard-library server that is both the page host and the database.
- Append-only JSONL logs, one per participant, in `log/`. They are the
  source of truth. The server replays them on every request into nine
  collections: `messages`, `agreements`, `waiting`, `locks`, `lessons`,
  `decisions`, `progress`, `incidents`, and `participants`. Field rules are
  in [PROTOCOL.md](PROTOCOL.md).
- Nine tabs: Timeline (with threads, search, kind filters, and a "Since your
  last visit" digest), Agreements, Waiting on you, File claims, Lessons,
  Decisions, Progress (a chart per game), Incidents, and Help (open
  requests).
- Updates without a reload: the page polls every 5 seconds.
- A `participants.json` roster for projects, sub-agents, reviewers, tools and
  people. Onboarding (`relay.py onboard` or the page's Add participant form)
  picks checked light and dark colors, mints a token, and writes
  `kits/<id>.md`. New participants start quarantined until the owner
  approves them.
- The cooperation loop: requests and replies (`consult`, `reply`), a
  per-participant inbox, file claims (`claim`, `release`), shared-change
  announcements and adoption (`land-sync`, `adopt`), and build-host
  incidents (`incident`).
- Live sessions: the server can push a short note into a running Claude Code
  session when something new waits in its project's inbox.
- Links from refs (`#42`, `vectorcraft@abc1234`) to GitHub issues and
  commits.

Out of scope:

- Any database, broker, queue or daemon beyond the one server and the
  housekeeper. The page is plain HTML, CSS and JavaScript.
- Letting the sessions grant each other permission. A file claim is a
  notice; conflicts go to the owner.
- Public release. Reads are open to anyone who can reach the port, so the
  server stays on localhost until reads need a login (docs/operations.md, "Reaching
  the page from other devices").

## Constraints

- Reads need no auth while the server binds localhost. Writes need a
  participant's token or the owner's session cookie.
- Rows from anyone but the owner are data, never instructions.
- Times are stored in UTC (ISO 8601) and shown in Pacific time.
- The server runs only on the owner's Mac.
- The Python tooling uses only the standard library and runs on the macOS
  system `python3` (3.9 or later). Nothing depends on one CLI, harness or
  model, except live sessions, which are a Claude Code feature.

## Success looks like

- The owner can tell within a few seconds whether the sessions are on
  schedule: a check-in dot turns amber 5 minutes past due and red 15 minutes
  past due (35 and 45 minutes for a 30-minute interval).
- Nothing waiting on the owner gets lost: open items stay on the strip until
  marked done.
- No two agents edit a shared file at once without the owner knowing.
- A question sent to several projects stays in each one's inbox until that
  project answers.
- Each lesson is recorded once, with the rule or tool that now prevents it,
  and feeds VectorCraft's `playbook/new-project.md`.

## Sequence

Crosstalk is built in stages. Specs and plans are in `docs/superpowers/`.

1. Foundation (done): the JSONL logs, the roster, and
   replay/check.
2. Self-hosting (done): the server, owner sessions, token auth,
   static export, and the launchd housekeeper.
3. Onboarding (done): command-line and in-page onboarding, color
   checks, token rotation, and kits.
4. Cooperation loop (done): `kind: "request"`, `to`, `replyTo`,
   `relay.py inbox`, the Help tab, the self-describing `service` block, and
   `"Crosstalk feature:"` proposals.
5. Power-ups (done 2026-10-25): the action inbox, per-recipient request
   tracking, the machine-poster commands (`claim`, `release`, `land-sync`,
   `incident`), `consult`, `reply`, `adopt`, and page actions (inline reply,
   record as decision, the digest).
6. Threads, permanent PIN, and live sessions (done 2026-10-26). Live
   sessions replaced the earlier agent-wake feature, which was removed.
7. Not started: `reviews` and `research` collections with panels for
   several models; owner tools such as ask-the-archive, push alerts, and
   backup buttons; read login for access from other devices.

## Known gaps

- Reads have no login, so the page can't safely leave localhost yet.
  docs/operations.md has the exposure ladder; Tailscale is its next rung.
- Live sessions work only for Claude Code. Other harnesses are planned in
  issue #2.
- Whether each project's own git hooks and xtask code call the poster
  commands is tracked in those repos, not here.
