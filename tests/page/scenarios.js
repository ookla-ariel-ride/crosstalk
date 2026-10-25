// Page scenarios for the stub harness. Inject into tests/page/out/index.html, then `await runAll()`.
// Each scenario changes __store, calls __emit, waits for a render and checks the DOM. They run in order and
// share one page; the two that stop subscriptions run last.
(function () {
  "use strict";
  var T0 = "2026-10-21T17:00:00Z";
  function tick(ms) { return new Promise(function (r) { setTimeout(r, ms == null ? 40 : ms); }); }
  function res(name, pass, detail) { return { name: name, pass: !!pass, detail: detail || "" }; }
  function $(id) { return document.getElementById(id); }
  async function put(c, docs) { __store[c] = docs; __emit(c); await tick(); }
  async function addDoc(c, id, doc) { __store[c] = __store[c] || {}; __store[c][id] = doc; __emit(c); await tick(); }
  async function go(tab) { $("tab-" + tab).click(); await tick(); }
  async function search(v) { var q = $("q"); q.focus(); q.value = v; q.dispatchEvent(new Event("input", { bubbles: true })); await tick(); }
  // The computed color a CSS color expression resolves to here, for comparing against an element's color.
  function rgb(expr) { var p = document.createElement("span"); p.style.color = expr; document.body.appendChild(p); var c = getComputedStyle(p).color; p.remove(); return c; }
  // A message's card, found by its star button (the stub user can edit, so every top-level card has one).
  function card(id) { var b = $("star-" + id); return b && b.closest("article"); }

  var S = [];

  // The page must use the injected seam (window.__crosstalkDataLayer), not window.claude.
  S.push(async function dataLayerSeamUsed() {
    return res("dataLayerSeamUsed", window.__crosstalkLayer === "seam", "layer=" + window.__crosstalkLayer);
  });

  // Under the stub seam the fetch layer must never be constructed, so the harness
  // never trips a network call.
  S.push(async function serverLayerUsed() {
    var ok = window.__crosstalkLayer === "seam" && !window.__crosstalkFetchLayer;
    return res("serverLayerUsed", ok, "layer=" + window.__crosstalkLayer + " fetchLayer=" + window.__crosstalkFetchLayer);
  });

  S.push(async function unknownSenderNeutral() {
    await go("timeline");
    await addDoc("messages", "gemini-1", { t: T0, from: "gemini", kind: "sync", summary: "From an unlisted sender" });
    var c = card("gemini-1"), w = c && c.querySelector(".who");
    if (!w) return res("unknownSenderNeutral", false, "no card");
    // The sender tag is a solid block of the sender's color (ruling R7).
    var got = getComputedStyle(w).backgroundColor, want = rgb("var(--muted)");
    return res("unknownSenderNeutral", w.textContent === "gemini" && got === want, "name=" + JSON.stringify(w.textContent) + " background=" + got + " muted=" + want);
  });

  S.push(async function subAgentTag() {
    await addDoc("participants", "bewareilive.verifier", { name: "verifier", kind: "agent", parent: "bewareilive", color: "#8a4b12", trust: "approved" });
    await addDoc("messages", "bv-1", { t: T0, from: "bewareilive.verifier", kind: "sync", summary: "Verifier report" });
    var c = card("bv-1"), w = c && c.querySelector(".who");
    var name = w ? w.textContent : null;
    return res("subAgentTag", name === "BewareILive › verifier", "name=" + JSON.stringify(name));
  });

  S.push(async function refViaRoster() {
    var codex = Object.assign({}, __store.participants.codex, { repo: "example-org/codex" });
    await addDoc("participants", "codex", codex);
    await addDoc("messages", "ref-1", { t: T0, from: "owner", kind: "owner", summary: "Ref test", refs: ["codex@ABC1234", "codex#12"] });
    var c = card("ref-1"), links = c ? [].slice.call(c.querySelectorAll(".refs a")).map(function (a) { return a.href; }) : [];
    var ok1 = links.length === 2 && /^https:\/\/github\.com\/example-org\/codex\/commit\/abc1234$/i.test(links[0]) && links[1] === "https://github.com/example-org/codex/issues/12";
    delete codex.repo;
    await addDoc("participants", "codex", codex);
    c = card("ref-1");
    var plain = c ? [].slice.call(c.querySelectorAll(".refs span")).map(function (s) { return s.textContent; }) : [];
    var ok2 = c && !c.querySelector(".refs a") && plain.indexOf("codex@ABC1234") !== -1;
    return res("refViaRoster", ok1 && ok2, "with repo=" + JSON.stringify(links) + " without repo plain=" + JSON.stringify(plain));
  });

  S.push(async function threeWayPositions() {
    await addDoc("messages", "dis-1", { t: T0, from: "bewareilive", kind: "disagreement", summary: "Three views",
      positions: { bewareilive: "Do A", vectorcraft: "Do B", codex: "Do C" } });
    var c = card("dis-1"), cols = c ? [].slice.call(c.querySelectorAll(".positions > div")) : [];
    var btns = cols.map(function (d) { var b = d.querySelector("button"); return b ? b.textContent : null; });
    var ok = cols.length === 3 && btns.every(function (t) { return t && t.indexOf("Go with") === 0; }) && btns[2] === "Go with Codex";
    return res("threeWayPositions", ok, "columns=" + cols.length + " buttons=" + JSON.stringify(btns));
  });

  S.push(async function forColumnUsesName() {
    await put("decisions", { "d-1": { t: T0, by: "owner", summary: "Codex reviews", project: "codex", source: "test" },
      "d-2": { t: T0, by: "owner", summary: "Shared", project: "both", source: "test" },
      "d-3": { t: T0, by: "owner", summary: "Shared too", project: "all", source: "test" } });
    await go("decisions");
    var cells = [].slice.call(document.querySelectorAll("#view tbody tr")).map(function (tr) { return tr.children[2].textContent; }).sort();
    await go("timeline");
    return res("forColumnUsesName", JSON.stringify(cells) === JSON.stringify(["All projects", "All projects", "Codex"]), "For=" + JSON.stringify(cells));
  });

  S.push(async function ownerColorThemed() {
    await addDoc("messages", "own-c", { t: T0, from: "owner", kind: "owner", summary: "Owner color" });
    var c = card("own-c"), w = c && c.querySelector(".who");
    var got = w ? getComputedStyle(w).backgroundColor : null, want = rgb("var(--own)");
    return res("ownerColorThemed", got === want && c.classList.contains("own"), "background=" + got + " --own=" + want + " class=" + (c && c.className));
  });

  S.push(async function composerKindKept() {
    await go("timeline");
    var k = $("noteKind"); k.value = "decision"; k.dispatchEvent(new Event("change", { bubbles: true }));
    __emit("messages"); await tick();
    var v = $("noteKind").value;
    k = $("noteKind"); k.value = "owner"; k.dispatchEvent(new Event("change", { bubbles: true }));
    return res("composerKindKept", v === "decision", "after snapshot=" + v);
  });

  S.push(async function noDuplicatePosts() {
    await go("timeline");
    var n0 = Object.keys(__store.messages || {}).length, d0 = Object.keys(__store.decisions || {}).length;
    var k = $("noteKind"); k.value = "decision"; k.dispatchEvent(new Event("change", { bubbles: true }));
    var ta = $("note"); ta.value = "dup test"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    __fail.decisions = true;
    $("notePost").click(); await tick(80);
    var st1 = $("noteStatus").textContent, n1 = Object.keys(__store.messages).length;
    __fail.decisions = false;
    $("notePost").click(); await tick(80);
    var st2 = $("noteStatus").textContent, n2 = Object.keys(__store.messages).length, d2 = Object.keys(__store.decisions || {}).length;
    var ok = /^Not posted/.test(st1) && n1 === n0 + 1 && n2 === n0 + 1 && d2 === d0 + 1 && st2 === "Posted.";
    return res("noDuplicatePosts", ok, "first=" + JSON.stringify(st1) + " second=" + JSON.stringify(st2) + " messages " + n0 + "->" + n1 + "->" + n2 + " decisions " + d0 + "->" + d2);
  });

  S.push(async function searchSpaces() {
    await go("timeline");
    await search("dup ");
    var v = $("q").value, shown = [].slice.call(document.querySelectorAll("#log .msg .summary")).map(function (s) { return s.textContent; });
    await search("");
    return res("searchSpaces", v === "dup " && shown.length === 1 && shown[0] === "dup test", "value=" + JSON.stringify(v) + " shown=" + JSON.stringify(shown));
  });

  S.push(async function focusKept() {
    await go("timeline");
    await search("x");
    __emit("messages"); await tick();
    var a = document.activeElement && document.activeElement.id;
    await search("");
    return res("focusKept", a === "q", "active=" + a);
  });

  // ---------- Task 9: feed, roster and quarantine ----------
  function ago(min) { return new Date(Date.now() - min * 60000).toISOString(); }
  function entry(id) { var b = $("who-" + id); return b && b.closest("li"); }
  function groupOf(id) { var e = entry(id), g = e && e.closest(".rgroup"); return g ? g.dataset.group : null; }
  function shownFrom() { return [].slice.call(document.querySelectorAll("#log article.msg")).map(function (a) { return a.dataset.from; }); }
  async function chip(k) { $("k-" + k).click(); await tick(); }

  S.push(async function rosterGroups() {
    await go("timeline");
    await addDoc("participants", "gemini-r", { name: "Gemini", kind: "researcher", color: "#9a5b00", trust: "approved" });
    await addDoc("participants", "githook", { name: "git hook", kind: "tool", color: "#555555", trust: "approved" });
    await addDoc("participants", "ghost.helper", { name: "helper", kind: "agent", parent: "ghost", color: "#555555", trust: "approved" });
    var r = $("roster");
    var heads = r ? [].slice.call(r.querySelectorAll(".rgroup h3")).map(function (h) { return h.textContent; }) : [];
    var verifier = entry("bewareilive.verifier"), bil = entry("bewareilive");
    var nested = !!(verifier && bil && verifier.parentElement.closest("li") === bil);
    var got = {
      bewareilive: groupOf("bewareilive"), vectorcraft: groupOf("vectorcraft"), verifier: groupOf("bewareilive.verifier"),
      codex: groupOf("codex"), "gemini-r": groupOf("gemini-r"), githook: groupOf("githook"), owner: groupOf("owner"),
      "ghost.helper": groupOf("ghost.helper"), gemini: groupOf("gemini")
    };
    var want = { bewareilive: "projects", vectorcraft: "projects", verifier: "projects", codex: "reviewers", "gemini-r": "reviewers",
      githook: "tools", owner: "tools", "ghost.helper": "tools", gemini: "unlisted" };
    var g = entry("gemini"), unlistedPill = !!(g && /not approved/i.test(g.textContent));
    var ok = r && r.tagName === "DETAILS" && JSON.stringify(heads) === JSON.stringify(["Projects", "Reviewers and research", "Tools and people", "Not in roster"]) &&
      nested && JSON.stringify(got) === JSON.stringify(want) && unlistedPill;
    return res("rosterGroups", ok, "heads=" + JSON.stringify(heads) + " nested=" + nested + " groups=" + JSON.stringify(got) + " unlistedPill=" + unlistedPill);
  });

  S.push(async function rosterFilter() {
    await go("timeline");
    await addDoc("messages", "vc-sync", { t: ago(3), from: "vectorcraft", kind: "sync", summary: "VC sync" });
    await addDoc("messages", "vc-alert", { t: ago(4), from: "vectorcraft", kind: "alert", summary: "VC alert" });
    var b = $("who-vectorcraft");
    if (!b) return res("rosterFilter", false, "no roster entry for vectorcraft");
    var isBtn = b.tagName === "BUTTON";
    b.focus(); b.click(); await tick();
    var pressed = $("who-vectorcraft").getAttribute("aria-pressed"), focused = document.activeElement && document.activeElement.id;
    var only = shownFrom(), onlyVc = only.length === 2 && only.every(function (f) { return f === "vectorcraft"; });
    await chip("sync");
    var both = shownFrom(), andOk = both.length === 1 && both[0] === "vectorcraft";
    await chip("all");
    $("who-vectorcraft").click(); await tick();
    var cleared = $("who-vectorcraft").getAttribute("aria-pressed"), all = shownFrom(), many = all.some(function (f) { return f !== "vectorcraft"; });
    var ok = isBtn && pressed === "true" && focused === "who-vectorcraft" && onlyVc && andOk && cleared === "false" && many;
    return res("rosterFilter", ok, "button=" + isBtn + " pressed=" + pressed + " focus=" + focused + " filtered=" + JSON.stringify(only) +
      " with sync=" + JSON.stringify(both) + " cleared=" + cleared + " after clear=" + all.length + " cards");
  });

  S.push(async function quarantineDimmedAndApprove() {
    await go("timeline");
    await addDoc("participants", "qbot", { name: "Q bot", kind: "tool", color: "#555555" });
    await addDoc("messages", "q-1", { t: ago(1), from: "qbot", kind: "sync", summary: "From a quarantined sender" });
    var c = card("q-1");
    if (!c) return res("quarantineDimmedAndApprove", false, "no card");
    var dim = c.classList.contains("quarantined") && getComputedStyle(c).opacity === "0.55";
    var pillOk = [].slice.call(c.querySelectorAll(".pill")).some(function (p) { return /not approved/i.test(p.textContent); });
    var a = $("approve-qbot");
    if (!a) return res("quarantineDimmedAndApprove", false, "dim=" + dim + " pill=" + pillOk + " no Approve button");
    __fail.participants = true;
    a.click(); await tick(80);
    __fail.participants = false;
    var err = $("approve-err-qbot"), errText = err ? err.textContent : "", stillDim = card("q-1").classList.contains("quarantined");
    $("approve-qbot").click(); await tick(80);
    var trust = __store.participants.qbot.trust, c2 = card("q-1");
    var undimmed = !c2.classList.contains("quarantined") && getComputedStyle(c2).opacity === "1";
    var pillGone = ![].slice.call(c2.querySelectorAll(".pill")).some(function (p) { return /not approved/i.test(p.textContent); });
    var btnGone = !$("approve-qbot") && !$("approve-err-qbot");
    var ok = dim && pillOk && /not approved/i.test(errText) && stillDim && trust === "approved" && undimmed && pillGone && btnGone;
    return res("quarantineDimmedAndApprove", ok, "dim=" + dim + " pill=" + pillOk + " failErr=" + JSON.stringify(errText) + " stillDimAfterFail=" + stillDim +
      " trust=" + trust + " undimmed=" + undimmed + " pillGone=" + pillGone + " approveGone=" + btnGone);
  });

  S.push(async function dotThresholds() {
    await go("timeline");
    var X = window.__crosstalk;
    if (!X || typeof X.dotState !== "function") return res("dotThresholds", false, "window.__crosstalk.dotState missing");
    // Every project checked in just now, so the test participant is the only one that can be late.
    await addDoc("messages", "ci-amb", { t: ago(2), from: "avoidmissingball", kind: "check-in", summary: "Check-in" });
    await addDoc("messages", "ci-bil", { t: ago(2), from: "bewareilive", kind: "check-in", summary: "Check-in" });
    await addDoc("messages", "ci-vc", { t: ago(2), from: "vectorcraft", kind: "check-in", summary: "Check-in" });
    await addDoc("participants", "dt", { name: "Dot test", kind: "tool", color: "#555555", checkInMin: 30, trust: "approved" });
    var never = X.dotState("dt", Date.now());
    var got = {}, tiles = {}, rosterDots = {};
    var cases = [34, 36, 46];
    for (var i = 0; i < cases.length; i++) {
      var n = cases[i];
      // A newer message of another kind must not count as a check-in.
      await addDoc("messages", "dt-ci", { t: ago(n), from: "dt", kind: "check-in", summary: "Check-in" });
      await addDoc("messages", "dt-sync", { t: ago(1), from: "dt", kind: "sync", summary: "Not a check-in" });
      got[n] = X.dotState("dt", Date.now());
      tiles[n] = $("checkVal").textContent;
      var d = entry("dt") && entry("dt").querySelector(".cdot");
      rosterDots[n] = d ? ["ok", "warn", "bad"].filter(function (k) { return d.classList.contains(k); })[0] || "?" : null;
    }
    var noMin = X.dotState("codex", Date.now());
    var lbl = document.querySelector("#t-check .lbl").textContent, since = entry("dt") && entry("dt").querySelector(".since");
    var ok = got[34] === "ok" && got[36] === "warn" && got[46] === "bad" && never === "bad" && noMin === null &&
      rosterDots[34] === "ok" && rosterDots[36] === "warn" && rosterDots[46] === "bad" &&
      lbl === "Check-ins" && tiles[34] === "all on time" && tiles[36] === "Dot test 6 min late" && tiles[46] === "Dot test 16 min late" &&
      since && since.textContent === "1m";
    // Leave the test participant on time so the screenshots show a calm strip.
    await addDoc("messages", "dt-ci", { t: ago(1), from: "dt", kind: "check-in", summary: "Check-in" });
    return res("dotThresholds", ok, "dotState=" + JSON.stringify(got) + " never=" + never + " noCheckInMin=" + noMin + " roster=" + JSON.stringify(rosterDots) +
      " label=" + lbl + " tile=" + JSON.stringify(tiles) + " since=" + (since && since.textContent));
  });

  S.push(async function toArrow() {
    await go("timeline");
    await addDoc("messages", "to-1", { t: ago(1), from: "codex", kind: "sync", summary: "Review: 3 findings", to: "bewareilive" });
    await addDoc("messages", "to-2", { t: ago(1), from: "codex", kind: "sync", summary: "Two recipients", to: ["bewareilive.verifier", "vectorcraft"] });
    await addDoc("messages", "to-3", { t: ago(1), from: "codex", kind: "sync", summary: "No recipient" });
    function arrow(id) { var c = card(id), t = c && c.querySelector(".to"); return t ? t.textContent : null; }
    var a = arrow("to-1"), b = arrow("to-2"), n = arrow("to-3");
    var ok = a === "→ BewareILive" && b === "→ BewareILive › verifier, VectorCraft" && n === null;
    return res("toArrow", ok, "one=" + JSON.stringify(a) + " two=" + JSON.stringify(b) + " none=" + JSON.stringify(n));
  });

  // "checkin" (no hyphen) counts as a check-in, shows as one and is found by the Check-ins chip.
  S.push(async function checkinSpelling() {
    await go("timeline");
    await addDoc("participants", "ck", { name: "Spelling", kind: "tool", color: "#555555", checkInMin: 30, trust: "approved" });
    await addDoc("messages", "ck-1", { t: ago(3), from: "ck", kind: "checkin", summary: "Old spelling" });
    var state = window.__crosstalk.dotState("ck", Date.now()), c = card("ck-1"), p = c && c.querySelector(".pill");
    $("k-check-in").click(); await tick();
    var listed = !!card("ck-1");
    $("k-all").click(); await tick();
    return res("checkinSpelling", state === "ok" && p && p.textContent === "check in" && listed,
      "dot=" + state + " pill=" + (p && p.textContent) + " inFilter=" + listed);
  });

  // Lateness reads in minutes under an hour, hours under two days, then days.
  S.push(async function lateInHours() {
    var got = [5, 59, 60, 1505, 2880].map(window.__crosstalk.spanText);
    return res("lateInHours", JSON.stringify(got) === JSON.stringify(["5 min", "59 min", "1 h", "25 h", "2 d"]), JSON.stringify(got));
  });

  // Each participant's light color in light mode and its colorDark in dark mode, on tags and the heading.
  S.push(async function themedColors() {
    await go("timeline");
    var root = document.documentElement, before = root.getAttribute("data-theme");
    root.setAttribute("data-theme", "light"); await tick();
    var light = window.__crosstalk.colorOf("vectorcraft"), h = $("heading").textContent;
    var tag = $("who-vectorcraft"), lightTag = tag && tag.style.getPropertyValue("--who");
    root.setAttribute("data-theme", "dark"); await tick();
    var dark = window.__crosstalk.colorOf("vectorcraft"), darkTag = $("who-vectorcraft") && $("who-vectorcraft").style.getPropertyValue("--who");
    if (before == null) root.removeAttribute("data-theme"); else root.setAttribute("data-theme", before);
    await tick();
    var ok = light === "#1f5f8b" && dark === "#6fb2e2" && lightTag === light && darkTag === dark &&
      h === "AvoidMissingBall · BewareILive · VectorCraft";
    return res("themedColors", ok, "light=" + light + "/" + lightTag + " dark=" + dark + "/" + darkTag + " heading=" + JSON.stringify(h));
  });

  // A game's line has its project's color; two games of one project differ by dash.
  S.push(async function progressByProject() {
    await put("progress", {
      "p1": { t: ago(60), project: "bewareilive", game: "joust", pct: 5 }, "p2": { t: ago(1), project: "bewareilive", game: "joust", pct: 8 },
      "p3": { t: ago(60), project: "bewareilive", game: "robotron", pct: 60 },
      "p4": { t: ago(60), project: "vectorcraft", game: "tempest", pct: 1 } });
    await go("progress");
    var lines = [].slice.call(document.querySelectorAll("#view .chart path")).map(function (e) { return [e.getAttribute("stroke"), e.getAttribute("stroke-dasharray") || ""]; });
    var legend = document.querySelectorAll("#view .legend line").length;
    await go("timeline");
    var bil = window.__crosstalk.colorOf("bewareilive"), vc = window.__crosstalk.colorOf("vectorcraft");
    var ok = lines.length === 3 && lines[0][0] === bil && lines[1][0] === bil && lines[0][1] !== lines[1][1] && lines[2][0] === vc && legend === 3;
    return res("progressByProject", ok, JSON.stringify(lines) + " legend=" + legend);
  });

  // Older decisions give `date` and no `by`; the table shows the date and an empty By cell, not "?".
  S.push(async function decisionDateNoBy() {
    await put("decisions", { "d-old": { date: "2026-10-21", project: "both", summary: "Dated only" } });
    await go("decisions");
    var row = document.querySelector("#view tbody tr"), by = row && row.children[1].textContent, when = row && row.children[4].textContent;
    await go("timeline");
    return res("decisionDateNoBy", by === "" && /Oct 2[01]/.test(when || ""), "by=" + JSON.stringify(by) + " when=" + JSON.stringify(when));
  });

  // ready-to-adopt is a known status: it sorts before landed-both and gets the in-progress pill.
  S.push(async function readyToAdopt() {
    await put("agreements", { "a-1": { t: T0, summary: "Done", status: "landed-both", builder: "vectorcraft", adopter: "bewareilive" },
      "a-2": { t: T0, summary: "Ready", status: "ready-to-adopt", builder: "vectorcraft", adopter: "bewareilive" } });
    await go("agreements");
    var rows = [].slice.call(document.querySelectorAll("#view tbody tr")).map(function (tr) { return tr.querySelector(".summary").textContent; });
    var pillEl = document.querySelector("#view .pill.ready-to-adopt"), bg = pillEl && getComputedStyle(pillEl).backgroundColor;
    await go("timeline");
    return res("readyToAdopt", rows[0] === "Ready" && bg === rgb("var(--vc-soft)"), "order=" + JSON.stringify(rows) + " bg=" + bg);
  });

  // A missing, empty or not-yet-loaded roster must not quarantine everyone.
  S.push(async function emptyRosterNoQuarantine() {
    await go("timeline");
    var saved = __store.participants;
    __store.participants = {}; __emit("participants"); __emit("messages"); await tick();
    var dimmed = document.querySelectorAll("#log .msg.quarantined").length;
    var pills = [].slice.call(document.querySelectorAll("#roster .pill, #log .pill")).filter(function (p) { return /not approved/i.test(p.textContent); }).length;
    var tile = $("checkVal").textContent, tileCls = $("t-check").className;
    var g = card("gemini-1"), tag = g && g.querySelector(".who");
    var neutral = !!tag && tag.textContent === "gemini" && getComputedStyle(tag).backgroundColor === rgb("var(--muted)");
    __store.participants = saved; __emit("participants"); await tick();
    var back = document.querySelectorAll("#log .msg.quarantined").length;
    var ok = dimmed === 0 && pills === 0 && tile === "roster not loaded" && tileCls === "tile" && neutral && back > 0;
    return res("emptyRosterNoQuarantine", ok, "dimmed=" + dimmed + " pills=" + pills + " tile=" + JSON.stringify(tile) + " class=" + tileCls +
      " neutralTag=" + neutral + " dimmedAfterReseed=" + back);
  });

  // Checks whichever width the window has: run once at desktop width, then again after browser_resize to 390 (see run.md).
  S.push(async function mobileRosterFolds() {
    await go("timeline");
    var r = $("roster"), log = $("log"), w = window.innerWidth;
    if (!r || !log) return res("mobileRosterFolds", false, "width=" + w + " roster=" + !!r);
    var sum = r.querySelector("summary"), sumText = sum ? sum.textContent : null, sumShown = sum && getComputedStyle(sum).display !== "none";
    var rb = r.getBoundingClientRect(), lb = log.getBoundingClientRect();
    var ok, detail;
    if (w <= 640) {
      var above = rb.bottom <= lb.top + 1, folded = !r.open;
      r.open = true; await tick();
      var opens = !!$("who-vectorcraft") && $("who-vectorcraft").getClientRects().length > 0;
      r.open = false; await tick();
      ok = r.tagName === "DETAILS" && sumText === "People" && sumShown && folded && above && opens;
      detail = "narrow width=" + w + " summary=" + JSON.stringify(sumText) + " shown=" + sumShown + " folded=" + folded + " aboveFeed=" + above + " opens=" + opens;
    } else {
      var beside = rb.right <= lb.left + 1 && rb.top < lb.bottom;
      ok = r.tagName === "DETAILS" && r.open && !sumShown && beside;
      detail = "wide width=" + w + " open=" + r.open + " summaryShown=" + sumShown + " besideFeed=" + beside;
    }
    return res("mobileRosterFolds", ok, detail);
  });

  S.push(async function holdRelease() {
    await put("agreements", { "a-1": { t: T0, summary: "Old-style held", status: "held", builder: "bewareilive", adopter: "vectorcraft" } });
    await go("agreements");
    var b = $("hold-a-1"), t1 = b && b.textContent;
    if (b) b.click();
    await tick(80);
    var a = __store.agreements["a-1"], b2 = $("hold-a-1"), t2 = b2 && b2.textContent;
    await go("timeline");
    return res("holdRelease", t1 === "Release hold" && a.status === "agreed" && a.hold === false && t2 === "Hold",
      "before=" + t1 + " after=" + t2 + " status=" + a.status + " hold=" + a.hold);
  });

  S.push(async function viewerRole() {
    // The footer says who is viewing and with what role: owner, editor or read-only viewer.
    var shown = $("viewer") ? $("viewer").textContent : null, f = window.__crosstalk.viewerText;
    var cases = [
      [{ name: "Jim", isOwner: false, canEdit: true }, "Viewing as Jim (editor)"],
      [{ name: "", isOwner: false, canEdit: false }, "Viewing as a viewer (read only)"],
      [{ name: "", isOwner: true, canEdit: true }, "Viewing as the owner"],
      [null, ""]
    ];
    var bad = typeof f !== "function" ? ["no viewerText"] : cases.filter(function (c) { return f(c[0]) !== c[1]; }).map(function (c) { return JSON.stringify(c[0]) + " -> " + JSON.stringify(f(c[0])); });
    return res("viewerRole", shown === "Viewing as Test Owner (owner)" && !bad.length, "shown=" + JSON.stringify(shown) + " bad=" + bad.join("; "));
  });

  S.push(async function readerNoControls() {
    // Only meaningful on index.html?viewer=reader; elsewhere it reports a skip.
    if (!/[?&]viewer=reader\b/.test(location.search)) return res("readerNoControls", true, "skipped: open index.html?viewer=reader");
    await go("timeline");
    await addDoc("messages", "rd-1", { t: T0, from: "codex", kind: "sync", summary: "Seen by a reader" });
    var controls = document.querySelectorAll("#composer, .star, .resolve, [id^=approve-], [id^=hold-], [id^=done-], #onboard-open").length;
    var shown = $("viewer").textContent;
    return res("readerNoControls", controls === 0 && shown === "Viewing as a viewer (read only)", "controls=" + controls + " shown=" + JSON.stringify(shown));
  });

  S.push(async function editorNoControls() {
    // Only meaningful on index.html?viewer=editor; elsewhere it reports a skip.
    if (!/[?&]viewer=editor\b/.test(location.search)) return res("editorNoControls", true, "skipped: open index.html?viewer=editor");
    await go("timeline");
    await addDoc("messages", "ed-1", { t: T0, from: "codex", kind: "sync", summary: "Seen by an editor" });
    var controls = document.querySelectorAll("#composer, .star, .resolve, [id^=approve-], [id^=hold-], [id^=done-], #onboard-open").length;
    var shown = $("viewer").textContent;
    return res("editorNoControls", controls === 0 && shown === "Viewing as Test Editor (editor)", "controls=" + controls + " shown=" + JSON.stringify(shown));
  });

  S.push(async function ownerSignIn() {
    // Only meaningful on index.html?viewer=reader; elsewhere it reports a skip.
    if (!/[?&]viewer=reader\b/.test(location.search)) return res("ownerSignIn", true, "skipped: open index.html?viewer=reader");
    var form = $("signin-form");
    if (!form) return res("ownerSignIn", false, "no owner sign-in form for a reader");
    var input = $("signin-pin");
    input.value = "000000";
    $("signin-submit").click(); await tick(80);
    var err = $("signin-status").textContent;
    var stillThere = !!$("signin-form");
    input = $("signin-pin");
    input.value = "123456";
    $("signin-submit").click(); await tick(80);
    var gone = !$("signin-form");
    var shown = $("viewer").textContent;
    var composer = !!$("composer");
    return res("ownerSignIn", /not valid/i.test(err) && stillThere && gone && shown === "Viewing as Test Owner (owner)" && composer,
      "err=" + JSON.stringify(err) + " stillThere=" + stillThere + " gone=" + gone + " shown=" + JSON.stringify(shown) + " composer=" + composer);
  });

  S.push(async function ownerAddParticipant() {
        await go("timeline");
        var open = $("onboard-open");
        if (!open) return res("ownerAddParticipant", false, "no Add participant button for the owner");
        open.click(); await tick();
        var form = $("onboard-form");
        if (!form) return res("ownerAddParticipant", false, "no form after opening the control");
        // The form must offer a Color picker: an Auto chip plus tappable swatches
        // (no raw hex inputs), the parent row for agents, and checkInMin.
        var parentIn = $("onboard-parent"), swatches = $("onboard-swatches"), checkIn = $("onboard-checkInMin"), auto = $("onboard-auto");
        if (!parentIn || !swatches || !checkIn || !auto) {
          return res("ownerAddParticipant", false, "missing fields: parent=" + !!parentIn + " swatches=" + !!swatches + " checkInMin=" + !!checkIn + " auto=" + !!auto);
        }
        var swatchCount = swatches.querySelectorAll("button.swatch").length;
        if (swatchCount < 2) return res("ownerAddParticipant", false, "fewer than two color swatches (" + swatchCount + ")");
        var first = swatches.querySelector("button.swatch");
        first.click(); await tick();
        var parentRow = $("onboard-parent-row");
        var parentHiddenInitially = parentRow && parentRow.style.display === "none";
        $("onboard-id").value = "smoke-agent";
        $("onboard-name").value = "Smoke Agent";
        $("onboard-kind").value = "agent";
        $("onboard-kind").dispatchEvent(new Event("change", { bubbles: true }));
        await tick();
        var parentShown = parentRow && parentRow.style.display !== "none";
        $("onboard-parent").value = "bewareilive";
        __onboardResult = { token: "fake-token-123", kit: "kit for fake-token-123" };
        $("onboard-submit").click(); await tick(80);
        var kit = $("onboard-kit"), tok = $("onboard-token"), copy = $("onboard-copy");
        var kitOk = !!kit && kit.readOnly && kit.value === "kit for fake-token-123";
        var tokOk = !!tok && tok.readOnly && tok.value === "fake-token-123";
        var tokenFields = document.querySelectorAll("#onboard-token").length;
        var parentSent = window.__lastOnboardFields && window.__lastOnboardFields.parent === "bewareilive";
        var colorSent = window.__lastOnboardFields && window.__lastOnboardFields.color === "#aa3366" && window.__lastOnboardFields.colorDark === "#dd88bb";
        var ok = kitOk && tokOk && !!copy && tokenFields === 1 && parentHiddenInitially && parentShown && parentSent && colorSent;
        return res("ownerAddParticipant", ok,
          "kit=" + (kit && JSON.stringify(kit.value)) + " token=" + (tok && JSON.stringify(tok.value)) +
          " copy=" + !!copy + " tokenFields=" + tokenFields + " parentHiddenInitially=" + parentHiddenInitially +
          " parentShown=" + parentShown + " parentSent=" + parentSent + " colorSent=" + colorSent);
      });

    S.push(async function partlyLive() {
    var before = $("liveText").textContent;
    __kill("locks", "revoked"); await tick();
    var after = $("liveText").textContent, latest = $("latest").textContent;
    return res("partlyLive", before === "Live" && after === "Partly live" && /File claims/.test(latest), "before=" + before + " after=" + after + " latest=" + JSON.stringify(latest));
  });

  S.push(async function participantsStopShown() {
    __kill("participants", "revoked"); await tick();
    var after = $("liveText").textContent, latest = $("latest").textContent;
    return res("participantsStopShown", after === "Partly live" && /People/.test(latest), "after=" + after + " latest=" + JSON.stringify(latest));
  });

  // runAll() runs every scenario; runAll(["name", ...]) runs only those.
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
