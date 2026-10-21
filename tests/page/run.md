# Page scenarios

The page is tested against a fake `window.claude` (`stub.js`) with an in-memory db.
`build.sh` seeds the `participants` collection from `participants.json`, so the page
sees the real roster from its first snapshot.

1. Build the harness page from the current `index.html`:

   ```sh
   sh tests/page/build.sh
   ```

   This writes `tests/page/out/` (git-ignored): `index.html`, `stub.js`, `scenarios.js`.

2. Serve it on a free port, in the background:

   ```sh
   PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
   python3 -m http.server "$PORT" --bind 127.0.0.1 -d tests/page/out
   ```

3. Open `http://127.0.0.1:$PORT/index.html` with Playwright
   (`mcp__plugin_playwright_playwright__browser_navigate`).

4. Inject the scenarios and run them (`browser_evaluate`):

   ```js
   async () => {
     await new Promise((ok, fail) => { const s = document.createElement("script"); s.src = "scenarios.js"; s.onload = ok; s.onerror = fail; document.head.appendChild(s); });
     return await runAll();
   }
   ```

   The result is an array of `{name, pass, detail}`. Every entry should have `pass: true`.
   The scenarios share one page and the last two stop subscriptions, so reload the page
   before running them again. `runAll(["name", ...])` runs only the named scenarios.

   Run them at a desktop width (`browser_resize` to 1100 wide) first. Then `browser_resize`
   to 390 wide and run `await runAll(["mobileRosterFolds"])`, which checks the narrow layout.

   Then open `index.html?viewer=reader` (a read-only stub viewer) and run
   `await runAll(["readerNoControls"])`. In the normal run that scenario reports a skip.

5. Check `browser_console_messages` at level `error`. The only expected error is the
   missing `favicon.ico`.

6. Stop the http server. Delete any `.playwright-mcp/` folder Playwright left in the repo.

## Scenarios

- `unknownSenderNeutral`: a sender not in `participants` shows its raw id on a muted tag.
- `subAgentTag`: an `agent` participant shows as `Parent › name`.
- `refViaRoster`: `codex@ABC1234` and `codex#12` link through codex's `repo`; plain text without one.
- `threeWayPositions`: three ids in `positions` give three columns, each with "Go with".
- `forColumnUsesName`: the decisions "For" column shows the participant's name; `both` shows "Both projects".
- `ownerColorThemed`: the owner's tag keeps the themed `--own` color, and its card the `own` class.
- `composerKindKept`: a snapshot re-render keeps the composer's kind.
- `noDuplicatePosts`: retrying a half-failed post rewrites the same rows.
- `searchSpaces`: a trailing space stays in the search box and still matches.
- `focusKept`: a snapshot re-render keeps focus in the search box.
- `rosterGroups`: the roster groups Projects (sub-agents nested), Reviewers and research, Tools and
  people (an agent whose parent is missing included) and Not in roster (marked not approved).
- `rosterFilter`: a roster name is a button that filters the feed to that sender (`aria-pressed`),
  combines with the kind chips, keeps focus, and clears on a second click.
- `quarantineDimmedAndApprove`: a quarantined sender's card is dimmed with a "not approved" pill; a
  failed Approve shows its error and keeps the dimming; a working one sets `trust: approved`.
- `dotThresholds`: `__crosstalk.dotState` is ok at 34 min, warn at 36 and bad at 46 for
  `checkInMin: 30`; the roster dot and the "Check-ins" strip tile agree.
- `toArrow`: a card with `to` shows `→ name` (or several names); without `to`, nothing.
- `emptyRosterNoQuarantine`: with an empty `participants` collection no card is dimmed, nothing says
  "not approved", and the Check-ins tile reads "roster not loaded"; re-seeding restores quarantine.
- `mobileRosterFolds`: wide, the roster is open beside the feed; at 390 wide it folds into a "People"
  disclosure above the feed.
- `holdRelease`: releasing an old-style `status: "held"` agreement sets `agreed` and `hold: false`.
- `viewerRole`: the footer reads "Viewing as Test Owner (owner)" for the stub viewer; `__crosstalk.viewerText`
  gives "(editor)", "a viewer (read only)" and "the owner" for an unnamed owner, and nothing without a viewer.
- `readerNoControls`: at `?viewer=reader`, no composer, star, resolve, Approve, Hold or Mark done
  control is shown, and the footer reads "Viewing as a viewer (read only)".
- `partlyLive`: one stopped collection shows "Partly live" and names its tab.
- `participantsStopShown`: a stopped `participants` subscription is named too.
