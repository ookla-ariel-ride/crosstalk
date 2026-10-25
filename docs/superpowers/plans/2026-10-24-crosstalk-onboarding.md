# Crosstalk Onboarding Implementation Plan

**Goal:** one-command onboarding with a generated kit, per the spec (docs/superpowers/specs/2026-10-24-crosstalk-onboarding.md).

**Architecture:** new `scripts/crosstalk/onboard.py` (roster entry, palette
colors, kit template, key minting) behind three relay.py subcommands
(`onboard`, `kit`, `token --new`), then `POST /api/onboard` + the page's
Add-participant form on top of the same module.

## Global Constraints

- Python stdlib only, macOS python3 3.9+ (no `match`, no `X | Y` unions).
- kits/ is gitignored; keys never reach git, error messages, or the log.
- `load_roster`'s color validation stays the single source of truth: the
  palette code calls it, it never reimplements the rules.
- The kit template interpolates ONLY validated values (id by ID_RE, name
  by the roster name rule, kind from the known set, repo/checkInMin/parent
  checked), and every example data block carries `"from": "<pid>"`.
- Strict TDD, commits at every green checkpoint, exact closing messages
  below.

## Review Focus

1. **Selected colors always pass** `load_roster` (auto AND overrides),
   including the dark companion.
2. **The kit is a drop-in prompt**: paste it verbatim into a scratch
   session → first post lands attributed and quarantined (Task 3).
3. **A rotated key stops working** for API appends immediately; nothing
   about the old key is recoverable from disk.
4. **`/api/onboard` refuses everyone but the owner session** (viewer 401,
   token-holder editor 403) and never echoes the minted key in a way a
   non-owner could script against beyond the single response.
5. **No new secrets in stderr/logs**: grep runs and tests assert the old
   and new keys appear nowhere in particles (relay-state, log dir,
   kit dir is exempt by design but must be gitignored).

---

### Task 1: The CLI: onboard, kit, key rotation

**Files:** create `scripts/crosstalk/onboard.py`, `tests/test_onboard.py`; modify `scripts/relay.py`, `.gitignore`.

**Interfaces:**
- `Palette`: deterministic candidate colors (+ derived dark companions); `next_free_and_valid(roster, colors, colors_dark) -> (color, colorDark)` returning the first pair `load_roster` accepts.
- `onboard(root, id, name, kind, repo=None, check_in_min=None, parent=None, color=None, color_dark=None) -> dict` with `{"kit_path", "token"}`. Refuses (ValueError) on duplicate id/name, bad id/kind, unvalidated colors.
- `kit_text(root, pid, token, entry) -> str` from a template in the module.
- `relay.py onboard …`, `relay.py kit <pid>` (regenerate, no roster/token touch), `relay.py token <pid> --new [--kit]` (rotate; minted key prints once; `--kit` regenerates the kit with the new key).
- [ ] Step 1 failing tests: dup refusal; auto color passes `load_roster` with several existing participants; override rejected when invalid; kit text assertions (pid, key, `"from":"<pid>"` in examples, quarantine note, warning line, no `__REPO__`-ish placeholders left); rotation: old token no longer verifies, new one does; kits/ listed in .gitignore.
- [ ] Step 2–4: red, implement minimal, green (full suite must stay 219+).
- [ ] Step 5: `git commit -m "Onboard a participant with one command and a generated kit"`

### Task 2: The page button

**Files:** modify `scripts/crosstalk/serve.py`, `index.html`, `tests/test_serve.py`, `tests/page/stub.js`, `tests/page/scenarios.js`.

**Interfaces:**
- `POST /api/onboard` (owner session) `{id, name, kind, repo?, checkInMin?, parent?, color?, colorDark?}` → `{token, kit}`; 401 unauthenticated, 403 non-owner, 400 invalid input.
- Page: an owner-only "Add participant" button beside the roster (visible iff `me.isOwner`, hidden for everyone else), a small form (id, name, kind select, optional repo), on success shows the token once and the kit in a readonly textarea with a copy button.
- [ ] Step 1 failing tests: server auth matrix for /api/onboard; roster gains the entry (trust quarantined) and a subsequent snapshot shows the participant dimmed until approved.
- [ ] Step 2: stub-harness scenario `ownerAddParticipant` (stub seam fakes the onboard call; form visible only for owner).
- [ ] Step 3–4: implement both layers; all page scenarios green; suite green.
- [ ] Step 5: `git commit -m "Add participants from the page"`

### Task 3: The paste smoke test

- [ ] In a temp repo copy: `relay.py onboard smoke-agent …`, open `kits/smoke-agent.md`, paste its append example verbatim as that participant, run `relay.py check` (clean) and `relay.py read` (the post present, `from` correct, roster entry quarantined). Record the transcript in the commit message.
- [ ] `git commit -m "Smoke: a pasted kit lands an attributed, quarantined first post"`

### Task 4: Adversarial pass on the new surface

- [ ] Independent reviewer (scratch copy, exploit runs): injection through every free-text field (name/kind/repo/parent/checkInMin/overrides), key recovery attempts, /api/onboard privilege from editor token and viewer, kit template hole (can a value break out into markdown the owner would misread as instructions — e.g. name with newlines), rotation residue.
- [ ] Fixes land failing-first + re-review until clean.