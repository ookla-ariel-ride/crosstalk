# Server scenarios (end to end)

The page is tested against a real `serve` process over a temp log. `e2e.py` seeds
a temp repo (roster + a few messages, one from a quarantined sender), starts the
server on an ephemeral port, and prints the URL and the one-time login PIN.

The rendering scenarios (`unknownSenderNeutral`, `rosterGroups`, `rosterFilter`,
`dotThresholds`, …) run against the stub harness (`tests/page/`) instead, where the
`__store`/`__emit` injection is available. The page code is identical in both, so
those scenarios cover the render logic; the server scenarios here cover the fetch
data layer end to end.

1. Start the server (it keeps running until stopped):

   ```sh
   python3 tests/server/e2e.py
   ```

   It prints `ROOT=…`, `URL=http://127.0.0.1:PORT/`, `PIN=NNNNNN`, `READY`.

2. Open `URL` in the browser. The page uses the fetch layer (`window.__crosstalkLayer
   === "fetch"`), the strip reads "Live", and the seeded messages render.

3. Run the viewer scenarios (no login needed):

   ```js
   await new Promise((ok, fail) => { const s = document.createElement("script"); s.src = "scenarios.js"; s.onload = ok; s.onerror = fail; document.head.appendChild(s); });
   await runAll(["serverLiveStrip", "serverQuarantineCarried"]);
   ```

   (Serve `tests/server/scenarios.js` next to the page, or inject it from disk.)

4. Log in as the owner with the PIN, then reload so `/api/me` picks up the cookie:

   ```js
   await fetch("/api/login", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({pin: "NNNNNN"}), credentials: "same-origin" });
   ```

   Reload the page, re-inject `scenarios.js`, then:

   ```js
   await runAll(["serverAppendRoundTrip", "serverRetryIdempotent"]);
   ```

   Every entry should have `pass: true`.

5. Offline: stop `e2e.py`; within one poll the strip reads "Offline" and the reason
   is "the server is unreachable" (or a `/api/health` fatal when one is present).

6. Export smoke check: the static export still renders offline after the seam change.

   ```sh
   python3 scripts/relay.py export-page /tmp/crosstalk-export.html
   ```

   Open the file from disk: the feed renders the full history, the strip reads
   "Offline", and no composer is shown (read-only snapshot).

## Scenarios

- `serverLiveStrip`: the strip reads "Live" once the first poll succeeds and the
  server clock is fresh.
- `serverQuarantineCarried`: a quarantined sender's card is dimmed with a
  "not approved" pill.
- `serverAppendRoundTrip`: owner login → post a note → it appears in the feed on
  the next poll.
- `serverRetryIdempotent`: a post whose response is lost and re-sent yields one doc
  after replay (review focus 7).
