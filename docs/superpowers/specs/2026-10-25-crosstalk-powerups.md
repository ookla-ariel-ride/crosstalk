# Crosstalk power-ups: action inbox, machine posters, peer consults, and page actions

Date: 2026-10-25. Status: approved by the owner ("lets implement all these features, use subagents").

## Why

The self-hosted channel, onboarding, and basic request/inbox loop are live on
`main`. Four gaps remain in daily use across BewareILive, VectorCraft, and
AvoidMissingBall:

1. Broadcast requests close after one reply. `inbox_for` treats a request as
   answered as soon as any message sets `replyTo` to its id. When VectorCraft
   asks both sister projects to adopt a standard (`to: ["bewareilive",
   "avoidmissingball"]` or broadcast), the first sister's reply hides the
   request from the second sister's inbox. Agents also have to query four
   separate collections to find unanswered owner messages, agreements ready to
   adopt, and stale file locks.
2. Mechanical events still cost agent turns. Claiming and releasing shared
   files, announcing landed shared-file commits, and logging build-host
   incidents require hand-crafted JSON appends.
3. Replying, consulting sister models, and adopting agreements take too much
   ceremony. Constructing single-quoted JSON payloads on the command line
   trips over apostrophes and omits `replyTo` or `commits.<pid>` updates.
4. The page lacks inline reply, one-click promotion, and a catch-up digest.
   The owner composer only writes top-level notes, and returning after several
   hours of 30-minute check-ins requires scanning dozens of cards to see which
   games moved or which recipients still owe an answer on a Help request.

## Decisions

### 1. Unified action inbox and per-recipient request tracking (`scripts/crosstalk/requests.py`)

- Per-recipient request open state:
  - When a `kind: "request"` message has a single recipient (`to == pid` or
    `to == [pid]`), any reply (`replyTo == req_id`) closes it for `pid`.
  - When a request has multiple recipients in `to` or omits `to` (broadcast),
    it remains open in `inbox_for(docs, pid)` until `pid` itself has replied
    (`reply.from == pid`).
  - A participant never sees its own sent requests in `inbox_for(docs, pid)`
    unless it explicitly listed `pid` in `to`.
  - Each request entry in `inbox_for` and `open_requests` includes
    `pending_from` (list of recipient pids that have not replied yet when `to`
    is set).
- Action punch-list in `relay.py inbox <pid>`:
  Alongside `"requests"`, `"roster"`, and `"service"`, `relay.py inbox <pid>`
  returns:
  - `"owner_messages"`: messages with `from == "owner"` addressed to `pid` (`to`
    contains `pid` or `to` omitted) that have no reply from `pid` (`replyTo ==
    msg_id` and `from == pid`), newest first.
  - `"agreements_to_adopt"`: `agreements` rows that are not held (`hold` not
    true, `status != "held"`), whose `status` is `"landed-builder"` or
    `"ready-to-adopt"`, where `adopter` is `pid` (or `adopter` is omitted,
    `"all"`, or `"both"` and `builder != pid`), and `commits.get(pid)` is empty.
  - `"stale_locks"`: unreleased `locks` rows (`not released`) where
    `project == pid` and `since` is more than 4 hours before the evaluation
    time.

### 2. Machine posters (`scripts/crosstalk/posters.py` and `relay.py`)

- `relay.py claim --from <pid> --path <path> [--repo <owner/repo>] [--holder <agent>] [--note <text>]`:
  writes `locks` doc `l-<pid>-<path-slug>` with `since` = current UTC ISO time
  and `released: null`.
- `relay.py release --from <pid> [--path <path> | --id <lock-id>]`:
  updates the lock doc with `released` = current UTC ISO time.
- `relay.py land-sync --from <pid> --commit <sha> --summary <text> [--files <path> ...] [--adopter <pid>] [--agreement-id <id>]`:
  appends a `kind: "sync"` message with `refs: ["<pid>@<sha>"]`, creates or
  updates an `agreements` row (`status: "ready-to-adopt"`, adding `<sha>` to
  `commits.<pid>`), and releases any active locks held by `<pid>` on `--files`.
- `relay.py incident --from <pid> --slug <slug> --summary <text> [--body <text>] [--resolve] [--fix <text>] [--fix-ref <ref> ...]`:
  creates `incidents` doc `i-<pid>-<slug>` (`start` = now, `end: null`), or when
  `--resolve` is passed, updates it with `end` = now, `fix`, and `fixRefs`.

### 3. Ergonomic replies, peer consults, and agreement adoption (`scripts/crosstalk/consults.py` and `relay.py`)

- `relay.py reply --from <pid> --to-id <msg-id> --summary <text> [--body <text>] [--ref <ref> ...]`:
  verifies `<msg-id>` exists in `messages`, then appends a `kind: "reply"`
  message with `replyTo: <msg-id>` and `to: [<original-sender>]`.
- `relay.py consult --from <pid> --summary <text> [--body <text>] [--ref <ref> ...] [--to <pid> ...]`:
  appends a `kind: "request"` message carrying `refs` and optional `to` for
  cross-model second opinions.
- `relay.py adopt --from <pid> --agreement <agreement-id> --commit <sha> [--summary <text>]`:
  updates `agreements/<agreement-id>` by appending `<sha>` to `commits.<pid>`,
  advances `status` to `"landed-both"` when the adopter has recorded a commit,
  and appends a `kind: "sync"` message referencing the agreement and commit.

### 4. Page actions and owner catch-up digest (`index.html`)

Surface: **Monitor + Operate** (preserving `index.html`'s existing CSS custom
properties, layout, and `el()`/`textContent` discipline).

- **Owner catch-up digest (`#digest`):**
  - Captures the previous visit timestamp (`digestSince`) before `markVisited()`
    advances `visitTs`. When messages or progress rows exist after
    `digestSince`, renders a compact `#digest` summary card at the top of the
    Timeline showing: new message count by project, game progress deltas (`pct`
    and `verified`), open incidents, and open requests. Includes a `Dismiss`
    button (`#digest-dismiss`) that clears `digestSince` for the session.
- **Inline Reply (`#reply-btn-<id>`):**
  - On Timeline message cards and Help request cards, when `canWrite` is true,
    renders a small `Reply` button. Clicking it switches the owner composer into
    reply mode (`draft.replyTo = m._id`, `draft.to = m.from ? [m.from] : []`),
    displays a reply banner (`#reply-banner`) with a `Cancel` button
    (`#reply-cancel`), and focuses the textarea (switching to the Timeline tab
    if clicked from Help). Submitting writes the owner message with `replyTo`
    and `to` set.
- **One-click "Record as decision" (`#promote-decision-<id>`):**
  - On non-decision Timeline message cards when `canWrite` is true, renders a
    small `Record as decision` button that writes `decisions/d-from-<m._id>`
    (`by: "owner"`, `project: "all"`, `summary: m.summary`,
    `body: m.body || m.summary`, `source: "Crosstalk page"`).
- **Per-recipient Help status:**
  - Help tab cards and `openRequests()` stay visible for multi-recipient
    requests until every recipient in `to` has replied, showing which recipients
    have answered and which are still pending.
