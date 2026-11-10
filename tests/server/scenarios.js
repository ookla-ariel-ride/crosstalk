// Server scenarios for the live harness. Inject into the served page, then `await runAll()`.
// These exercise the fetch data layer end to end: the live strip, the owner append round
// trip, quarantine carried from the roster, and retry idempotency. The rendering scenarios
// (unknownSenderNeutral, rosterGroups, ...) run against the stub harness instead, where the
// __store/__emit injection is available; the page code is identical in both.
(function () {
  "use strict";
  function res(name, pass, detail) { return { name: name, pass: !!pass, detail: detail || "" }; }
  function $(id) { return document.getElementById(id); }
  function tick(ms) { return new Promise(function (r) { setTimeout(r, ms == null ? 40 : ms); }); }
  // Poll until fn() is truthy, or timeout.
  async function waitFor(fn, ms) {
    var deadline = Date.now() + (ms || 10000);
    while (Date.now() < deadline) {
      var v = fn();
      if (v) return v;
      await tick(100);
    }
    return null;
  }

  var S = [];

  // The live strip reads "Live" once the first poll succeeds and the server clock is fresh.
  S.push(async function serverLiveStrip() {
    var live = await waitFor(function () { return $("liveText").textContent === "Live"; }, 10000);
    var latest = $("latest").textContent;
    return res("serverLiveStrip", live === true && /latest message/.test(latest),
      "live=" + JSON.stringify(live) + " latest=" + JSON.stringify(latest));
  });

  // A quarantined sender's card is dimmed with a "not approved" pill.
  S.push(async function serverQuarantineCarried() {
    var card = await waitFor(function () {
      var cards = [].slice.call(document.querySelectorAll("#log article.msg"));
      return cards.filter(function (c) { return c.dataset.from === "qbot"; })[0] || null;
    }, 10000);
    if (!card) return res("serverQuarantineCarried", false, "no qbot card");
    var dim = card.classList.contains("quarantined");
    var pill = [].slice.call(card.querySelectorAll(".pill")).some(function (p) { return /not approved/i.test(p.textContent); });
    return res("serverQuarantineCarried", dim && pill, "dim=" + dim + " pill=" + pill);
  });

  // Owner login -> post a note -> it appears in the feed on the next poll.
  S.push(async function serverAppendRoundTrip() {
    if (!$("composer")) return res("serverAppendRoundTrip", false, "no composer (not logged in as owner)");
    var ta = $("note");
    ta.value = "E2E round-trip note";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click();
    var posted = await waitFor(function () { return $("noteStatus").textContent === "Posted."; }, 5000);
    var inFeed = await waitFor(function () {
      var summaries = [].slice.call(document.querySelectorAll("#log .msg .summary")).map(function (s) { return s.textContent; });
      return summaries.indexOf("E2E round-trip note") !== -1;
    }, 10000);
    return res("serverAppendRoundTrip", posted === true && inFeed === true,
      "status=" + JSON.stringify(posted) + " inFeed=" + JSON.stringify(inFeed));
  });

  // A post whose response is lost and re-sent yields ONE doc after replay.
  S.push(async function serverRetryIdempotent() {
    var body = { from: "owner", op: "set", collection: "messages", id: "own-retry-1",
                 data: { kind: "owner", from: "owner", summary: "Retry idempotent note" } };
    var opts = { method: "POST", headers: { "Content-Type": "application/json" },
                 body: JSON.stringify(body), credentials: "same-origin" };
    var r1 = await fetch("/api/append", opts);
    var r2 = await fetch("/api/append", opts);
    var snap = await (await fetch("/api/snapshot", { credentials: "same-origin" })).json();
    var ids = Object.keys(snap.collections.messages || {}).filter(function (id) { return id === "own-retry-1"; });
    return res("serverRetryIdempotent", r1.status === 201 && r2.status === 201 && ids.length === 1,
      "r1=" + r1.status + " r2=" + r2.status + " docs=" + ids.length);
  });

  window.runAll = async function (only) {
    var out = [];
    for (var i = 0; i < S.length; i++) {
      if (only && only.indexOf(S[i].name) === -1) continue;
      try { out.push(await S[i]()); }
      catch (e) { out.push(res(S[i].name, false, "threw: " + (e && e.stack ? e.stack.split("\n")[0] : e))); }
    }
    return out;
  };
})();
