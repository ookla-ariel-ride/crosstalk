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
   before running them again.

5. Check `browser_console_messages` at level `error`. The only expected error is the
   missing `favicon.ico`.

6. Stop the http server. Delete any `.playwright-mcp/` folder Playwright left in the repo.

## Scenarios

- `unknownSenderNeutral`: a sender not in `participants` shows its raw id in the muted color.
- `subAgentTag`: an `agent` participant shows as `Parent › name`.
- `refViaRoster`: `codex@ABC1234` and `codex#12` link through codex's `repo`; plain text without one.
- `threeWayPositions`: three ids in `positions` give three columns, each with "Go with".
- `forColumnUsesName`: the decisions "For" column shows the participant's name; `both` shows "Both projects".
- `ownerColorThemed`: the owner keeps the themed `--own` color and the centered lane.
- `composerKindKept`: a snapshot re-render keeps the composer's kind.
- `noDuplicatePosts`: retrying a half-failed post rewrites the same rows.
- `searchSpaces`: a trailing space stays in the search box and still matches.
- `focusKept`: a snapshot re-render keeps focus in the search box.
- `holdRelease`: releasing an old-style `status: "held"` agreement sets `agreed` and `hold: false`.
- `partlyLive`: one stopped collection shows "Partly live" and names its tab.
- `participantsStopShown`: a stopped `participants` subscription is named too.
