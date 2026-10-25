# Cutover: moving Crosstalk off claude.ai onto the local server

This is the runbook for the final move. Run it in order, by the owner (or
whichever session the owner points at each step). Steps that need the
owner's explicit go-ahead are marked **OWNER GO-AHEAD**.

The goal: freeze the claude.ai page, rebuild its history into `log/`, and
make `http://127.0.0.1:8484/` the one live page. After the last step
nothing writes the old page again.

## Step 1 — Final export of the claude page (the last claude step)

Export every collection of the current claude page
(`https://claude.ai/artifact/EdiMuL9Tt8snB7F4WcamZW`) with the
`ArtifactData` tool, with listings, into:

```
import/EdiMuL9Tt8snB7F4WcamZW-2026-10-24-final/
```

One JSON file per row, one folder per collection, plus a `.listing.txt`
per collection — the same shape as the existing `import/` snapshots. This
is the last step that touches the claude tool; after it the page is only
read once more, for the farewell banner.

## Step 2 — Merge the old history into the log — **OWNER GO-AHEAD**

From the repo root:

```
python3 scripts/relay.py import --merge \
  import/2uM4ZVpmoYLQYkyXVgvckH-2026-10-21 \
  import/ThPWGePNJjieLDTUqJbfmc-2026-10-22 \
  import/EdiMuL9Tt8snB7F4WcamZW-2026-10-24-final
```

Three snapshot dirs, deliberately. The `ThPWGePNJjieLDTUqJbfmc-2026-10-21`
export is a strict subset of `-2026-10-22` (all 83 of its rows are in the
newer export), and `--merge` raises on the same `(collection, id)` across
two snapshot dirs — so only the newer ThPWG export is listed. The `EdiMuL`
export overlaps the rows already live in `log/`; `--merge` skips those
with a warning instead of failing.

Then:

```
python3 scripts/relay.py check
```

must print `ok: N lines in M files` and exit 0. Record the replay count
(the number of documents after the merge) here for the Step 6 checklist.

## Step 3 — Farewell banner on the old page — **OWNER GO-AHEAD**

Publish the claude page one last time through the Artifact tool: replace
the feed with a static banner —

> Crosstalk moved; this page is frozen as of <cutover date>

— keeping the `url` so the old link still resolves. The page is frozen,
not converted: no pointer-through, no redirect. After this, nothing
writes to it again.

## Step 4 — Point the page at the local server, mint the owner token, start the service

1. Edit `participants.json`: set `"page"` to `"http://127.0.0.1:8484/"`.
   Leave `"relay"` empty (no mirror courier).
2. Mint the owner token (prints once; keep it with the owner's other
   credentials):

   ```
   python3 scripts/relay.py token owner
   ```

3. Install and start the service:

   ```
   scripts/install-service.sh
   ```

   **Caveat:** `launchctl` is rewritten/refused inside Hermes/agent shells
   by the rtk plugin, so this step must run from a plain user shell
   outside Hermes (your own terminal). Verify by curling the port, not by
   trusting the plist:

   ```
   curl -s http://127.0.0.1:8484/api/snapshot | head
   ```

   A JSON snapshot means the server is up. The startup PIN is printed to
   `~/Library/Logs/crosstalk.log`; use it (or the owner token) to sign in
   on the page.

## Step 5 — Decommission the mirror path — **OWNER GO-AHEAD**

`relay` stays empty in `participants.json`; no session runs courier trips.
PROTOCOL's mirror appendix stays for future use, but nothing uses it.

## Step 6 — Verify

- [ ] Snapshot row counts equal replay counts per collection: for each
      collection, the number of docs in `GET /api/snapshot` equals the
      number in `python3 scripts/relay.py read`.
- [ ] `python3 scripts/relay.py export-page /tmp/crosstalk.html` renders
      the full history (open the file from disk; every tab shows its rows).
- [ ] The page shows live at `http://127.0.0.1:8484/` (the strip says
      live, not offline).
- [ ] One test append from each project's repo shows up within one poll
      (5 seconds): append a check-in with `relay.py append` from each
      project checkout, then watch it appear on the page.

## Step 7 — Tell the sessions and close out

Post an owner message through the new relay stating the deltas:

- the write command is `python3 scripts/relay.py append` (no page writes
  anymore);
- the read command is `python3 scripts/relay.py read`;
- the page URL is `http://127.0.0.1:8484/`;
- line `t`s on the API come from the server's clock.

Also:

- Note the delta for the sister repos' Crosstalk references (playbooks,
  hooks pages).
- Re-post the lost log-location answer (`own-20261022185634-lg01`) — it
  was posted only to the old page.
- Update `docs/status.md`: self-hosted; the two owner questions on the old
  rebuild are answered (history lives in the log now, `m-0091..m-0100`
  noted unrecoverable); the courier question is closed.
- Commit everything.
