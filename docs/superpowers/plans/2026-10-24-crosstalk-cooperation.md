# Crosstalk Cooperation Implementation Plan

**Goal:** the request/inbox/Help loop per the spec (docs/superpowers/specs/2026-10-24-crosstalk-cooperation.md).

**Architecture:** a small `requests.py` in the package (open-request
derivation over the replay), `relay.py inbox`, a Help tab computed from
`data.messages`, and the protocol wording.

## Global Constraints

- Python stdlib only, macOS python3 3.9+ (no `match`, no `X | Y`).
- Open-ness is derived ONLY from existing `replyTo` links in the replay.
- The page stays the owner's window: agents use `relay.py inbox`, not the tab.
- Quarantine rules are untouched: rows from quarantined requesters stay dim
  and are never acted on (the inbox still lists them — visibility is not action).
- Strict TDD, scenario-first for the page, commits at every green checkpoint.

## Review Focus

1. Inbox correctness: broadcast vs addressed, newest-first, replies
   attached, answered requests excluded without `--all`.
2. Newest-first ordering matches `byT` (messages without `t` enrich to the
   line time, so replay-derived ordering must agree).
3. The Help tab stays consistent with quarantine dimming and project filters.
4. No performance blowup: inbox/Help scan a flat list, O(n).

---

### Task 1: `relay.py inbox`

- New `scripts/crosstalk/requests.py`: `open_requests(docs) -> list[(id, doc, [replies])]` and `inbox_for(docs, pid)` (addressed or broadcast), both over the replayed docs.
- New `scripts/crosstalk/service.py`: the single `SERVICE` dict (purpose, capabilities, commands, protocol path).
- `relay.py inbox <pid> [--all]` prints the JSON envelope (with `"service": SERVICE`); unknown pid is an error; fatal log problems exit 1 like `read`. `relay.py read` also carries `"service": SERVICE`; `GET /api/snapshot` does too.
- Tests `tests/test_inbox.py` (failing-first): addressed + broadcast + unaddressed-to-others exclusion, replied drops out, `--all` keeps it, newest-first, replies attached, unknown pid exit, quarantined included but data-only, the `service` block present in read/inbox/snapshot with the expected keys.
- Commit: `git commit -m "Inbox: open requests addressed to a participant"`

### Task 2: The Help tab

- `index.html`: a Help tab (derived over `data.messages`): open requests newest-first; requester tag; `to…` chips or "everyone"; body; each reply inline (its sender tag + summary/body); a count badge on the tab; project filter applies; answered requests excluded; same dimming as the feed.
- Scenarios (`tests/page/scenarios.js`): `helpTabListed`, `helpAnsweredDrops`, `helpToChips` (broadcast shows "everyone"), `helpQuarantineDimmed`, `helpFilter`; keep all existing scenarios green.
- Commit: `git commit -m "Page: a Help tab for open requests and their answers"`

### Task 3: PROTOCOL.md

- "Asking for help": post kind `request` (worked example command), set `to` for specific projects, omit for everyone; answers use `replyTo`.
- Check-in list gains the step: answer every open request in `relay.py inbox <you>` (dissent welcome); empty inbox is the common case.
- Commit: `git commit -m "Protocol: asking for help and answering the open inbox"`

### Task 4: Adversarial pass

- Independent reviewer (scratch copy): inbox derivation edge cases (replies to replies, a reply from a quarantined sender, a `to` naming unknown pids, malformed ids), page injection through request bodies (the Help tab must stay textContent-disciplined), performance with 1k+ messages.
- Fixes failing-first + re-review.