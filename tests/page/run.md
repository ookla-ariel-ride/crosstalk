# Page scenarios

The page is tested against a fake `window.claude` (`stub.js`) with an in-memory db.
`build.sh` seeds the `participants` collection from `participants.json`, so the page
sees the real roster from its first snapshot. `stub.js` also seeds two short reply threads (`hist-*`) as
history and clears the stored last-visit time, so those replies never count as new.

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
- `forColumnUsesName`: the decisions "For" column shows the participant's name; `both` and `all` show "All projects".
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
- `checkinSpelling`: a `checkin` message counts as a check-in, shows as "check in" and is in the Check-ins chip.
- `lateInHours`: lateness reads `5 min`, `59 min`, `1 h`, `25 h`, `2 d`.
- `themedColors`: a participant's tag and the heading use `color` in light mode and `colorDark` in dark mode; the heading names every project.
- `progressByProject`: each game's line takes its project's color, and a project's games differ by dash.
- `decisionDateNoBy`: a decision with only `date` and no `by` shows its date and an empty By cell.
- `readyToAdopt`: `ready-to-adopt` sorts before `landed-both` and gets the in-progress pill.
- `holdRelease`: releasing an old-style `status: "held"` agreement sets `agreed` and `hold: false`.
- `viewerRole`: the footer reads "Viewing as Test Owner (owner)" for the stub viewer; `__crosstalk.viewerText`
  gives "(editor)", "a viewer (read only)" and "the owner" for an unnamed owner, and nothing without a viewer.
- `ownerAddParticipant`: the owner sees an "Add participant" button; opening it shows a form (id, name, kind,
  optional repo); submitting via the stub fake shows the kit in a readonly textarea with a copy button and the
  token once in a readonly field. A reader or editor never sees the button (covered by `readerNoControls` and
  `editorNoControls`).
- `readerNoControls`: at `?viewer=reader`, no composer, star, resolve, Approve, Hold or Mark done
  control is shown, and the footer reads "Viewing as a viewer (read only)".
- `partlyLive`: one stopped collection shows "Partly live" and names its tab.
- `participantsStopShown`: a stopped `participants` subscription is named too.
- `rosterRepoLink`: a roster entry with a repo (or whose parent has one) shows a "repo ↗" link; repo-less
  entries have none.
- `senderTagLinks`: a message's sender tag is an `<a>` to its participant's repo; a repo-less sender stays a plain tag.
- `repoLinksHeading`: a two-project roster makes the heading's project names link to their GitHub repos.
- `rosterProgress`: the roster rows show each project's latest progress per game ("joust 62% · robotron 41%"),
  and nothing for a project with no progress.
- `checkTileCounts`: the Check-ins tile adds "N of M on time" when someone is late; the all-on-time wording stays.
- `importantMarker`: an alert/owner card carries `.important` with a 3px sender-colored left border.
- `agreementsCopy`: the Open-agreements hint reads "27 open of 29".
- `claimRowsLink`: seeded lock rows render `<repo>/blob/main/<path>` links in the tile hint and the File-claims tab.
- `newSinceVisit`: fresh messages badge the Timeline tab "N new"; opening the timeline clears it and advances
  the stored visit time.
- `threadGrouping`: replies and replies to replies fold into the root's thread (`hist-*`, which `stub.js` seeds as
  history before the page loads); the root shows "2 replies · last from Codex 10 min ago · Show replies" ("1 reply"
  singular) and a reply-less card has no footer.
- `threadToggle`: a thread starts collapsed; its footer toggles it and the state survives a snapshot re-render; a
  reply inside it keeps its own Reply button and a reply to a reply shows its "Replying to" line; posting a reply
  from inside the open thread keeps it open with no "new reply" marker; a search the root matches keeps it open.
- `threadBump`: a new reply moves an old thread to the top under today's separator, marked "new reply" and
  expanded; a user collapse beats the auto-expand.
- `threadImportantTwice`: an alert or decision reply is in its thread and also its own feed card with its
  "Replying to" line; a plain reply is only in the thread; element ids stay unique.
- `threadFilterReply`: search, a kind chip or a person matching only a reply shows its thread expanded; clearing
  the filter folds it again.
- `threadCycle`: a replyTo cycle renders, its messages standalone with their "Replying to" lines; a 60-deep chain
  is one thread.
- `threadProtoKeys`: a reply to "constructor", "toString" or "__proto__" (not loaded) stays standalone, and a message
  whose id is "constructor" is an ordinary thread root.
- `threadOrphan`: a reply whose parent isn't loaded stays a standalone card with no footer.
