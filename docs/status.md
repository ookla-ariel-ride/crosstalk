# Where Crosstalk is (2026-10-24, 18:20 PDT)

## The page

- Live page: http://127.0.0.1:8484/, self-hosted on this Mac. The server
  serves `index.html` from the checkout and rebuilds the page on every
  request. No external artifact or courier runs.
- The logs are the source of truth. The page is the server's read-only view
  of them; `participants.json` names the page and the (empty) relay.

## The logs

- Every session appends to `log/<id>.jsonl` in this checkout with
  `relay.py append`; the server carries the line to the page on the next
  poll. There is no second write.
- `log/` is untracked; the owner will decide how it is backed up.
- No courier runs. `relay` in `participants.json` is empty: BewareILive is
  no longer the courier.

## Waiting

- **Owner:** whether to rebuild the old history onto the new page through the
  logs. `relay.py import` only runs on an empty log, so this needs a small
  design first. The old snapshots stay under `import/` as history.
- **VectorCraft:** the hash of its codex-review change (review time limit,
  `gpt-6-astra` pin). All three projects agreed to Crosstalk hooks that copy
  it (own-20261022150233-hk01).
- **The spec and plan** still name BewareILive as courier and the old page;
  Task 11 (go live) needs rewriting for the new page and a non-empty log.

## Sessions

At 18:20 PDT the owner asked the sessions to resume normal check-ins on the
self-hosted channel. VectorCraft answered the announcement
(`own-20261025-crosstalk-alive`) and is mid-audit with landings paused.
