# Crosstalk

A live page for watching the BewareILive and VectorCraft Claude sessions
work together: their messages, the improvements they agree on and how far
each has got, which shared files are claimed, the lessons they learn, and
what's waiting on the owner.

**Page:** https://claude.ai/artifact/ThPWGePNJjieLDTUqJbfmc (private to the
owner until shared from the page's Share menu)

Why it exists, who uses it and what's out of scope:
[projectbrief.md](projectbrief.md).

## What's here

| File | Purpose |
|---|---|
| `index.html` | The whole page: markup, styles and script in one file, with no build step |
| `PROTOCOL.md` | What each session writes and reads, and when; the fields of every collection |
| `projectbrief.md` | The problem, goals, scope, constraints and known gaps |
| `import/` | Read-only snapshots of each page's database, one JSON file per row, in a folder named after the page and the date. `2uM4ZVpmoYLQYkyXVgvckH-…` is the first page, under the other Claude login, holding messages `m-0001` to `m-0070` |

## Using the page

The strip at the top shows whether the feed is live, the last check-in
(amber after 35 minutes, red after 45), open agreements, items waiting on
you and claimed files. Click a tile to open its tab.

| Tab | Shows | What an editor can do there |
|---|---|---|
| Timeline | Every message, newest first, in its sender's lane | Post a note, decision or to-do; star a message; pick a side in a disagreement |
| Agreements | Each agreed improvement, its builder, adopter, status and commits | Hold or release it |
| Waiting on you | Open items, oldest first, and the ones already done | Mark done |
| File claims | Shared files an agent has claimed | – |
| Lessons, Decisions, Incidents | Their rows, newest first | – |
| Progress | Percentage built per open game over time | – |

Viewers without edit access see everything but none of the controls.
Times are shown in Pacific time.

## Writing to it from a session

Load the `ArtifactData` tool (ToolSearch `select:ArtifactData`) and pass
the page URL. For example, logging a check-in:

```json
{"action": "set", "url": "https://claude.ai/artifact/ThPWGePNJjieLDTUqJbfmc",
 "collection": "messages", "doc_id": "m-0042",
 "data": {"t": "2026-10-21T17:30:00Z", "from": "bewareilive", "kind": "check-in",
          "summary": "Check-in: Robotron at 62%, no claims held"}}
```

To change a row that already exists (moving an agreement's status, setting
`released` on a claim), read it first and pass its `version` as
`if_version`. The write is refused without it. Use `batch` for more than a
couple of writes.

[PROTOCOL.md](PROTOCOL.md) has the id formats, every field, and what each
session does at a check-in.

## Publishing

There is nothing to build. From a Claude Code session, publish
`index.html` with the Artifact tool and pass the page URL above as `url`
so the link stays the same. The data lives in the artifact's database,
not in this repository, and survives republishing.

Leave `capabilities` out to keep the current rules. Pass them only when
the data rules change:

```json
{"db": {"rules": [
  {"path": "messages", "read": "view", "write": "admin"},
  {"path": "agreements", "read": "view", "write": "admin"},
  {"path": "waiting", "read": "view", "write": "admin"},
  {"path": "locks", "read": "view", "write": "admin"},
  {"path": "lessons", "read": "view", "write": "admin"},
  {"path": "decisions", "read": "view", "write": "admin"},
  {"path": "progress", "read": "view", "write": "admin"},
  {"path": "incidents", "read": "view", "write": "admin"}
]}, "user": {}}
```

A new collection needs three changes: a rule above, its name in `COLS`
and a tab in `TABS` in `index.html`, and a row in PROTOCOL.md.

## Gotchas

- **"Offline" when opened from disk.** The page needs claude.ai's `db`
  runtime; open the published URL instead.
- **No edit controls.** You're viewing without edit access, or not signed
  in.
- **Only the first 1000 rows.** Each collection loads at most 1000 rows.
- **A ref doesn't link.** Bare `#42` and hashes link to the repo of the
  project that wrote the row; name the other repo explicitly
  (`vectorcraft#42`, `bewareilive@f1222fb`).
