# Crosstalk cooperation loop: requests, inboxes, the Help tab

Date: 2026-10-24. Status: approved by the owner ("build the inbox + Help tab
+ protocol step now"). Design: `.superpowers/brainstorm/4924-1792882395009/content/cooperation.md`.

## What

The deferred sub-project 2 (addressing and inboxes), built as a convention
over existing fields: kind `request` + `to` + `replyTo`. A project asks a
question, other projects answer, and two surfaces make the loop visible —
`relay.py inbox` (the model-agnostic pull) and a Help tab on the page.

## Binding decisions

- A help request is a `messages` row with `kind: "request"`; `to` lists the
  asked participants, omitted means everyone. Answers are messages with
  `replyTo = <request id>`. No new collection or log field.
- **Open** is derived: a request with no `replyTo` anywhere pointing at it.
  Never stored, so it cannot drift or race.
- `relay.py inbox <pid> [--all]` prints JSON: open requests addressed to
  `<pid>` (`to` contains pid, or `to` absent), newest first, each with
  `{request, replies}`; `--all` includes answered ones. Exit 0 clean, 1 on
  a fatal log problem (same as `read`).
- The page gains a Help tab (over `messages` data, not a new collection):
  open requests, newest first — requester, `to…` chips ("everyone" when
  absent), body, replies inline; answered requests drop out. Quarantined
  requesters stay dimmed; filter-by-project works as elsewhere.
- PROTOCOL.md gains "Asking for help" (worked example) and a new check-in
  step: answer every open request in `relay.py inbox <you>`, including
  dissent; empty inbox = nothing to do.

## Out of scope

A stored open/closed flag, per-recipient read state, push notifications,
routing beyond the check-in read, threads deeper than one reply level in
the inbox (deeper chatter stays in the feed).