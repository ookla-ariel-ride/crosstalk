# Crosstalk Power-Ups Implementation Plan

Goal: implement the four Crosstalk expansion tracks (unified action inbox with
per-recipient request tracking, machine posters, peer consults and ergonomic
replies/adoptions, and page inline replies + decision promotion + owner digest)
using parallel git worktrees, strict TDD, and adversarial review before merging.

Spec: `docs/superpowers/specs/2026-10-25-crosstalk-powerups.md`

## Global Constraints

- Python 3 standard library only, macOS system `python3` (3.9+). No `match`
  statements or `X | Y` unions.
- Strict TDD: write a failing test first, run it to confirm it fails, write
  minimal implementation, run full suite green, and commit at every green
  checkpoint.
- Never `git add -A`; stage explicit file paths.
- Never touch or restart the live server running from the main checkout; subagent
  tests use `TempRepo` and ephemeral ports (`port=0`).
- All DOM rendering in `index.html` must use `el()` and `textContent` (never
  `innerHTML`), preserving XSS safety.

---

### Track 1 (`feat/action-inbox`): Per-recipient requests & unified action inbox

Files: `scripts/crosstalk/requests.py`, `scripts/relay.py`, `tests/test_inbox.py`

- [ ] Step 1 (RED): Add failing tests in `tests/test_inbox.py`:
  - Multi-recipient `to: ["bewareilive", "codex"]`: when `bewareilive` replies,
    `inbox("bewareilive")` drops it, while `inbox("codex")` still lists it with
    `pending_from == ["codex"]` and `bewareilive`'s reply attached.
  - Broadcast request (`to` omitted): when `bewareilive` replies,
    `inbox("bewareilive")` drops it, while `inbox("codex")` still lists it; the
    sender (`vectorcraft`) does not see its own broadcast request in
    `inbox("vectorcraft")`.
  - Single-recipient `to: ["bewareilive"]`: any reply closes it (existing
    behavior preserved).
  - `owner_messages`: unanswered `from: "owner"` messages addressed to `pid` (or
    broadcast) appear in `out["owner_messages"]`, and drop out once `pid` posts
    a reply with `replyTo == msg_id`.
  - `agreements_to_adopt`: unheld agreements with `status` in
    `("landed-builder", "ready-to-adopt")` where `adopter` matches `pid` (or
    omitted/`"all"`/`"both"` and `builder != pid`) and `commits[pid]` is empty
    appear in `out["agreements_to_adopt"]`.
  - `stale_locks`: unreleased `locks` where `project == pid` and `since` is > 4h
    ago appear in `out["stale_locks"]`.
- [ ] Step 2 (GREEN): Implement in `scripts/crosstalk/requests.py` and wire
  `owner_messages`, `agreements_to_adopt`, `stale_locks`, and `pending_from` in
  `scripts/relay.py` `cmd_inbox`.
- [ ] Step 3: Verify `python3 -m unittest discover -s tests` passes and commit.

---

### Track 2 (`feat/machine-posters`): Machine posters for locks, land-sync, and incidents

Files: `scripts/crosstalk/posters.py`, `scripts/relay.py`, `tests/test_posters.py`

- [ ] Step 1 (RED): Create `tests/test_posters.py` with failing tests for:
  - `relay.py claim --from bewareilive --path .githooks/pre-commit --repo ookla-ariel-ride/bewareilive --holder sync-agent --note "updating hook"`
  - `relay.py release --from bewareilive --path .githooks/pre-commit`
  - `relay.py land-sync --from vectorcraft --commit a1b2c3d --summary "Gate host lanes in Scratch" --files tools/gate-host --adopter bewareilive` (verifies `messages` sync row, `agreements` row in `ready-to-adopt` with `commits.vectorcraft == ["a1b2c3d"]`, and automatic release of any open lock held by `vectorcraft` on `tools/gate-host`).
  - `relay.py incident --from vectorcraft --slug studio-disk --summary "Studio disk 100% full"` and `--resolve --fix "Excluded ~/Scratch from Time Machine" --fix-ref vectorcraft@352b6cd`.
- [ ] Step 2 (GREEN): Implement `scripts/crosstalk/posters.py` and add
  subparsers `claim`, `release`, `land-sync`, and `incident` in `scripts/relay.py`.
- [ ] Step 3: Verify `python3 -m unittest discover -s tests` passes and commit.

---

### Track 3 (`feat/peer-consults`): Ergonomic replies, cross-model consults, and agreement adoption

Files: `scripts/crosstalk/consults.py`, `scripts/crosstalk/service.py`,
`scripts/relay.py`, `PROTOCOL.md`, `tests/test_consults.py`

- [ ] Step 1 (RED): Create `tests/test_consults.py` with failing tests for:
  - `relay.py reply --from bewareilive --to-id vc-r1 --summary "Confirmed on Williams hardware" --body "Details..." --ref bewareilive@51e7709` (auto-fills `replyTo: "vc-r1"` and `to: ["vectorcraft"]`; refuses unknown `--to-id`).
  - `relay.py consult --from avoidmissingball --summary "Centipede RNG bank window" --body "Question..." --ref avoidmissingball@91bab82 --to vectorcraft bewareilive` (creates `kind: "request"` with `refs` and `to`).
  - `relay.py adopt --from bewareilive --agreement a-fast-verify --commit 51e7709 --summary "Adopted fast verify"` (appends `51e7709` to `commits.bewareilive`, moves `status` to `"landed-both"` when adopters have commits, and logs a `sync` message).
- [ ] Step 2 (GREEN): Implement `scripts/crosstalk/consults.py`, wire `reply`,
  `consult`, and `adopt` into `scripts/relay.py`, and update
  `scripts/crosstalk/service.py` and `PROTOCOL.md`.
- [ ] Step 3: Verify `python3 -m unittest discover -s tests` passes and commit.

---

### Track 4 (`feat/page-actions-digest`): Inline owner reply, decision promotion, Help per-recipient status, and catch-up digest

Files: `index.html`, `tests/page/scenarios.js`

- [ ] Step 1 (RED): Add failing page scenarios in `tests/page/scenarios.js`:
  - `ownerInlineReply`: clicking `#reply-btn-<id>` on a message card sets the
    composer's `#reply-banner`, and submitting posts an owner message with
    `replyTo == <id>` and `to == [<sender>]`.
  - `ownerPromoteDecision`: clicking `#promote-decision-<id>` on a message card
    creates `decisions/d-from-<id>` with `by: "owner"`, `project: "all"`, and
    the message's summary/body.
  - `helpPerRecipient`: a Help request addressed to `["bewareilive",
    "vectorcraft"]` stays in the Help tab after `bewareilive` replies (showing
    `bewareilive` answered and `vectorcraft` pending), and drops out once
    `vectorcraft` also replies.
  - `ownerDigest`: when messages and progress rows arrive after `visitTs`,
    switching to Timeline renders `#digest` summarizing the new messages and
    progress deltas, and `#digest-dismiss` hides it.
- [ ] Step 2 (GREEN): Implement in `index.html` using `el()` and `textContent`
  only, rebuild `tests/page/build.sh`, and verify scenarios pass.
- [ ] Step 3: Verify `python3 -m unittest discover -s tests` passes and commit.

---

### Track 5: Merge, adversarial review, regression tests, and server restart

- [ ] Merge Tracks 1–4 cleanly, combining `scripts/relay.py` imports and
  subparsers.
- [ ] Run independent adversarial reviewer subagent over the merged tree.
- [ ] Land failing-first regression tests for any findings, run full Python test
  suite + browser page scenarios, run `avoid-ai-writing` on docs, restart the
  background server, and push `main` to `origin`.
