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

  // A roster entry links its repo (or an agent's inherited parent repo) right of the name.
  S.push(async function rosterRepoLink() {
    await go("timeline");
    function link(e) { return e && e.querySelector("a.rrepo"); }
    var bil = link(entry("bewareilive")), ver = link(entry("bewareilive.verifier")), codex = link(entry("codex")), owner = link(entry("owner"));
    var ok = !!bil && bil.href === "https://github.com/ookla-ariel-ride/bewareilive" && bil.textContent === "repo \u2197" &&
      !!ver && ver.href === "https://github.com/ookla-ariel-ride/bewareilive" &&
      !codex && !owner;
    return res("rosterRepoLink", ok, "bewareilive=" + (bil && bil.href) + " verifier=" + (ver && ver.href) + " codex=" + !!codex + " owner=" + !!owner);
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

  // A sender tag links to its participant's repo; a repo-less sender stays a plain tag.
  S.push(async function senderTagLinks() {
    await go("timeline");
    await addDoc("messages", "stl-1", { t: ago(1), from: "bewareilive", kind: "sync", summary: "Linked sender tag" });
    await addDoc("messages", "stl-2", { t: ago(1), from: "owner", kind: "owner", summary: "Owner has no repo" });
    var w1 = card("stl-1") && card("stl-1").querySelector(".who"), a1 = w1 && w1.closest("a");
    var w2 = card("stl-2") && card("stl-2").querySelector(".who"), a2 = w2 && w2.closest("a");
    var ok = !!a1 && a1.href === "https://github.com/ookla-ariel-ride/bewareilive" && a1.querySelector(".who") === w1 &&
      !a2 && w2 && w2.textContent === "Owner";
    return res("senderTagLinks", ok, "bil=" + (a1 && a1.href) + " ownerLinked=" + !!a2);
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

  // The Check-ins tile names the worst late participant and adds "N of M on time"; all-on-time keeps its wording.
  S.push(async function checkTileCounts() {
    await go("timeline");
    var saved = __store.participants;
    __store.participants = {
      "aa": { name: "On time A", kind: "tool", color: "#555555", checkInMin: 30, trust: "approved" },
      "bb": { name: "On time B", kind: "tool", color: "#555555", checkInMin: 30, trust: "approved" },
      "cc": { name: "Late C", kind: "tool", color: "#555555", checkInMin: 30, trust: "approved" }
    };
    __emit("participants"); await tick();
    var now = Date.now();
    await addDoc("messages", "cct-a", { t: new Date(now - 60000).toISOString(), from: "aa", kind: "check-in", summary: "ci" });
    await addDoc("messages", "cct-b", { t: new Date(now - 60000).toISOString(), from: "bb", kind: "check-in", summary: "ci" });
    await addDoc("messages", "cct-c", { t: new Date(now - 40 * 60000).toISOString(), from: "cc", kind: "check-in", summary: "ci" });
    var val = $("checkVal").textContent, hint = $("checkHint").textContent;
    await addDoc("messages", "cct-c2", { t: new Date(now).toISOString(), from: "cc", kind: "check-in", summary: "ci" });
    var allOn = $("checkVal").textContent;
    __store.participants = saved; __emit("participants"); await tick();
    var ok = /^2 of 3 on time /.test(hint) && /due every 30 min$/.test(hint) && /10 min late$/.test(val) && allOn === "all on time";
    return res("checkTileCounts", ok, "val=" + JSON.stringify(val) + " hint=" + JSON.stringify(hint) + " allOn=" + JSON.stringify(allOn));
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

  // An important (alert/owner) card carries .important and a sender-colored 3px left border.
  S.push(async function importantMarker() {
    await go("timeline");
    await addDoc("messages", "imp-1", { t: ago(1), from: "vectorcraft", kind: "alert", summary: "Attention needed" });
    var c = card("imp-1");
    if (!c) return res("importantMarker", false, "no card");
    var who = c.style.getPropertyValue("--who"), want = window.__crosstalk.colorOf("vectorcraft");
    var bw = getComputedStyle(c).borderLeftWidth, bc = getComputedStyle(c).borderLeftColor;
    var ok = c.classList.contains("important") && who === want && bw === "3px" && bc === rgb(want);
    return res("importantMarker", ok, "cls=" + c.classList.contains("important") + " --who=" + who + " width=" + bw + " color=" + bc + " want=" + rgb(want));
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

  // In light mode the heading names every project; with a repo each name is a link to that repo.
  S.push(async function repoLinksHeading() {
    await go("timeline");
    var saved = __store.participants;
    __store.participants = { bewareilive: Object.assign({}, saved.bewareilive), vectorcraft: Object.assign({}, saved.vectorcraft) };
    __emit("participants"); await tick();
    var links = [].slice.call(document.querySelectorAll("#heading a.proj")).map(function (a) { return [a.textContent, a.href]; });
    var title = $("heading").textContent;
    __store.participants = saved; __emit("participants"); await tick();
    var ok = links.length === 2 && links[0][0] === "BewareILive" && links[0][1] === "https://github.com/ookla-ariel-ride/bewareilive" &&
      links[1][0] === "VectorCraft" && links[1][1] === "https://github.com/ookla-ariel-ride/vectorcraft" &&
      title === "BewareILive · VectorCraft";
    return res("repoLinksHeading", ok, JSON.stringify(links) + " title=" + JSON.stringify(title));
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

  // The roster shows each project's latest progress per game, and nothing for a project with none.
  S.push(async function rosterProgress() {
    await go("timeline");
    await put("progress", {
      "rg-1": { t: ago(60), project: "bewareilive", game: "joust", pct: 62 },
      "rg-2": { t: ago(60), project: "bewareilive", game: "robotron", pct: 41 },
      "rg-3": { t: ago(30), project: "vectorcraft", game: "tempest", pct: 30 },
      "rg-4": { t: ago(5), project: "vectorcraft", game: "tempest", pct: 45 }
    });
    function prog(id) { var e = entry(id), d = e && e.querySelector(".rprog"); return d ? d.textContent : null; }
    var bil = prog("bewareilive"), vc = prog("vectorcraft"), amb = prog("avoidmissingball"), codex = prog("codex");
    var ok = bil === "joust 62% · robotron 41%" && vc === "tempest 45%" && amb === null && codex === null;
    return res("rosterProgress", ok, "bil=" + JSON.stringify(bil) + " vc=" + JSON.stringify(vc) + " amb=" + JSON.stringify(amb) + " codex=" + JSON.stringify(codex));
  });

  // Older decisions give `date` and no `by`; the table shows the date and an empty By cell, not "?".
  S.push(async function decisionDateNoBy() {
    await put("decisions", { "d-old": { date: "2026-10-21", project: "both", summary: "Dated only" } });
    await go("decisions");
    var row = document.querySelector("#view tbody tr"), by = row && row.children[1].textContent, when = row && row.children[4].textContent;
    await go("timeline");
    return res("decisionDateNoBy", by === "" && /Oct 2[01]/.test(when || ""), "by=" + JSON.stringify(by) + " when=" + JSON.stringify(when));
  });

  // The Open-agreements hint reads "N open of M".
  S.push(async function agreementsCopy() {
    var docs = {};
    for (var i = 0; i < 29; i++) docs["ag-" + i] = { t: T0, summary: "Agreement " + i, status: i < 2 ? "landed-both" : "agreed" };
    await put("agreements", docs);
    var val = $("agreeVal").textContent, hint = $("agreeHint").textContent;
    await go("timeline");
    return res("agreementsCopy", val === "27" && hint === "27 open of 29", "val=" + val + " hint=" + JSON.stringify(hint));
  });

  // A seeded lock's repo + path render as a github blob link in the tile hint and the tab rows.
  S.push(async function claimRowsLink() {
    var now = new Date().toISOString();
    await put("locks", {
      "lk-1": { repo: "ookla-ariel-ride/bewareilive", path: "docs/plan.md", project: "bewareilive", holder: "bewareilive", note: "editing", since: now },
      "lk-2": { repo: "ookla-ariel-ride/vectorcraft", path: "src/a b.cpp", project: "vectorcraft", holder: "vectorcraft", note: "editing", since: now },
      "lk-3": { repo: "not a repo", path: "x", project: "codex", note: "bad repo", since: now }
    });
    await go("locks");
    var rows = [].slice.call(document.querySelectorAll("#view tbody tr")).map(function (tr) { var a = tr.querySelector("td a"); return a ? a.href : null; });
    var hintLinks = [].slice.call(document.querySelectorAll("#lockHint a")).map(function (a) { return a.href; });
    await go("timeline");
    var ok = rows.length === 3 &&
      rows[0] === "https://github.com/ookla-ariel-ride/bewareilive/blob/main/docs/plan.md" &&
      rows[1] === "https://github.com/ookla-ariel-ride/vectorcraft/blob/main/src/a%20b.cpp" &&
      rows[2] === null &&
      hintLinks.length === 2 && hintLinks[0] === rows[0] && hintLinks[1] === rows[1];
    return res("claimRowsLink", ok, "rows=" + JSON.stringify(rows) + " hint=" + JSON.stringify(hintLinks));
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
    var controls = document.querySelectorAll("#composer, .star, .resolve, .resolve-inc, .reply-btn, .promote-btn, [id^=approve-], [id^=hold-], [id^=done-], #onboard-open").length;
    var shown = $("viewer").textContent;
    return res("readerNoControls", controls === 0 && shown === "Viewing as a viewer (read only)", "controls=" + controls + " shown=" + JSON.stringify(shown));
  });

  S.push(async function editorNoControls() {
    // Only meaningful on index.html?viewer=editor; elsewhere it reports a skip.
    if (!/[?&]viewer=editor\b/.test(location.search)) return res("editorNoControls", true, "skipped: open index.html?viewer=editor");
    await go("timeline");
    await addDoc("messages", "ed-1", { t: T0, from: "codex", kind: "sync", summary: "Seen by an editor" });
    var controls = document.querySelectorAll("#composer, .star, .resolve, .resolve-inc, .reply-btn, .promote-btn, [id^=approve-], [id^=hold-], [id^=done-], #onboard-open").length;
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

    // "N new" since the last visit: fresh messages badge the Timeline tab until it is opened.
  S.push(async function newSinceVisit() {
    var KEY = "crosstalk.lastVisit";
    try { localStorage.removeItem(KEY); } catch (e) {}
    await go("timeline"); // opening the timeline records "now" as the last visit
    await go("agreements");
    var before = null; try { before = localStorage.getItem(KEY); } catch (e) {}
    var now = new Date();
    await addDoc("messages", "new-1", { t: now.toISOString(), from: "codex", kind: "sync", summary: "New one" });
    await addDoc("messages", "new-2", { t: now.toISOString(), from: "codex", kind: "sync", summary: "New two" });
    var badge = document.getElementById("new-badge"), txt = badge ? badge.textContent : null;
    await go("timeline"); // opening the timeline clears the badge and advances the stored time
    var after = null; try { after = localStorage.getItem(KEY); } catch (e) {}
    var gone = document.getElementById("new-badge") === null;
    var advanced = before != null && after != null && Number(after) > Number(before);
    return res("newSinceVisit", txt === "2 new" && gone && advanced, "badge=" + JSON.stringify(txt) + " gone=" + gone + " stored " + before + " -> " + after);
  });

  // ---------- Help tab: open requests and their answers ----------
  function helpCards() { return [].slice.call(document.querySelectorAll("#view .card")); }
  function helpHas(summary) { return helpCards().some(function (c) { return c.textContent.indexOf(summary) !== -1; }); }
  function helpFrom() { return helpCards().map(function (c) { return c.dataset.from; }); }

  S.push(async function helpTabListed() {
    await go("help");
    await addDoc("messages", "hreq-1", { t: ago(1), from: "vectorcraft", kind: "request", summary: "How do I land this?", to: ["bewareilive"] });
    var listed = helpHas("How do I land this?");
    var badge = $("tab-help").querySelector(".n").textContent;
    await go("timeline");
    return res("helpTabListed", listed && badge === "1", "listed=" + listed + " badge=" + badge);
  });

  S.push(async function helpAnsweredDrops() {
    await go("help");
    await addDoc("messages", "hreq-2", { t: ago(1), from: "vectorcraft", kind: "request", summary: "Still open?", to: ["bewareilive"] });
    var before = helpHas("Still open?");
    await addDoc("messages", "hrep-2", { t: ago(1), from: "bewareilive", kind: "reply", summary: "Yes", replyTo: "hreq-2" });
    var after = helpHas("Still open?");
    await go("timeline");
    return res("helpAnsweredDrops", before && !after, "before=" + before + " after=" + after);
  });

  S.push(async function helpToChips() {
    await go("help");
    await addDoc("messages", "hreq-3", { t: ago(1), from: "codex", kind: "request", summary: "Two recipients", to: ["bewareilive", "vectorcraft"] });
    await addDoc("messages", "hreq-4", { t: ago(1), from: "codex", kind: "request", summary: "Everyone" });
    function toText(summary) {
      var c = helpCards().filter(function (x) { return x.textContent.indexOf(summary) !== -1; })[0];
      var t = c && c.querySelector(".to");
      return t ? t.textContent : null;
    }
    var a = toText("Two recipients"), b = toText("Everyone");
    await go("timeline");
    return res("helpToChips", a === "to BewareILive · VectorCraft" && b === "everyone", "two=" + JSON.stringify(a) + " none=" + JSON.stringify(b));
  });

  S.push(async function helpQuarantineDimmed() {
    await go("help");
    await addDoc("participants", "hqbot", { name: "Help Q bot", kind: "tool", color: "#555555" });
    await addDoc("messages", "hreq-5", { t: ago(1), from: "hqbot", kind: "request", summary: "From a quarantined requester" });
    var c = helpCards().filter(function (x) { return x.dataset.from === "hqbot"; })[0];
    var dim = !!c && c.classList.contains("quarantined") && getComputedStyle(c).opacity === "0.55";
    await go("timeline");
    return res("helpQuarantineDimmed", dim, "dim=" + dim);
  });

  S.push(async function helpFilter() {
    await go("timeline");
    await addDoc("messages", "hreq-6", { t: ago(1), from: "bewareilive", kind: "request", summary: "BIL asks" });
    await addDoc("messages", "hreq-7", { t: ago(1), from: "vectorcraft", kind: "request", summary: "VC asks" });
    var b = $("who-bewareilive");
    if (!b) return res("helpFilter", false, "no roster entry");
    b.click(); await tick();
    await go("help");
    var froms = helpFrom();
    var onlyBil = froms.length > 0 && froms.every(function (f) { return f === "bewareilive"; });
    await go("timeline");
    $("who-bewareilive").click(); await tick();
    return res("helpFilter", onlyBil, "froms=" + JSON.stringify(froms));
  });

  // A waiting row from a quarantined project is dimmed with a "not approved" pill;
  // an approved project's row stays undimmed.
  S.push(async function waitingQuarantineDimmed() {
    await go("waiting");
    await addDoc("participants", "wqbot", { name: "WQ bot", kind: "tool", color: "#555555" });
    await addDoc("waiting", "wq-1", { since: ago(1), project: "wqbot", summary: "From a quarantined project", done: null });
    await addDoc("waiting", "wq-2", { since: ago(1), project: "bewareilive", summary: "From an approved project", done: null });
    function wcard(summary) {
      return [].slice.call(document.querySelectorAll("#view .card")).filter(function (c) {
        return c.textContent.indexOf(summary) !== -1;
      })[0];
    }
    var q = wcard("From a quarantined project"), a = wcard("From an approved project");
    var qDim = !!q && q.classList.contains("quarantined") && getComputedStyle(q).opacity === "0.55";
    var qPill = !!q && [].slice.call(q.querySelectorAll(".pill")).some(function (p) { return /not approved/i.test(p.textContent); });
    var aUndim = !!a && !a.classList.contains("quarantined") && getComputedStyle(a).opacity === "1";
    var aNoPill = !!a && ![].slice.call(a.querySelectorAll(".pill")).some(function (p) { return /not approved/i.test(p.textContent); });
    await go("timeline");
    return res("waitingQuarantineDimmed", qDim && qPill && aUndim && aNoPill,
      "qDim=" + qDim + " qPill=" + qPill + " aUndim=" + aUndim + " aNoPill=" + aNoPill);
  });

  S.push(async function ownerInlineReply() {
    await go("timeline");
    await addDoc("messages", "m-0001", { t: ago(2), from: "bewareilive", kind: "sync", summary: "Need owner input" });
    var btn = $("reply-btn-m-0001");
    if (!btn) return res("ownerInlineReply", false, "no #reply-btn-m-0001");
    btn.click(); await tick();
    var banner = $("reply-banner"), bannerText = banner ? banner.textContent : "";
    var cancelBtn = $("reply-cancel");
    if (cancelBtn) { cancelBtn.click(); await tick(); }
    var canceledOk = $("reply-banner") === null;
    $("reply-btn-m-0001").click(); await tick();
    var ta = $("note");
    ta.value = "Owner inline answer";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click(); await tick(80);
    var bannerAfter = $("reply-banner");
    var msgs = Object.keys(__store.messages || {}).map(function (id) { return __store.messages[id]; });
    var posted = msgs.filter(function (m) { return m && m.summary === "Owner inline answer"; })[0];
    var toArr = posted && Array.isArray(posted.to) ? posted.to : [];
    var ok = !!banner && /BewareILive/.test(bannerText) && /Need owner input/.test(bannerText) && !!cancelBtn &&
      canceledOk && !bannerAfter && !!posted && posted.replyTo === "m-0001" && toArr.indexOf("bewareilive") !== -1;
    return res("ownerInlineReply", ok, "banner=" + JSON.stringify(bannerText) + " canceledOk=" + canceledOk + " bannerAfter=" + !!bannerAfter + " posted=" + JSON.stringify(posted));
  });

  S.push(async function ownerPromoteDecision() {
    await go("timeline");
    if (!(__store.messages && __store.messages["m-0001"])) {
      await addDoc("messages", "m-0001", { t: ago(2), from: "bewareilive", kind: "sync", summary: "Need owner input" });
    }
    var btn = $("promote-decision-m-0001");
    if (!btn) return res("ownerPromoteDecision", false, "no #promote-decision-m-0001");
    btn.click(); await tick(80);
    var doc = __store.decisions && __store.decisions["d-from-m-0001"];
    var btnAfter = $("promote-decision-m-0001"), btnText = btnAfter ? btnAfter.textContent : "";
    await go("decisions");
    var summaries = [].slice.call(document.querySelectorAll("#view tbody tr .summary")).map(function (s) { return s.textContent; });
    var inTable = summaries.indexOf("Need owner input") !== -1;
    await go("timeline");
    var ok = !!doc && doc.by === "owner" && doc.project === "all" && doc.summary === "Need owner input" && btnText === "Recorded" && inTable;
    return res("ownerPromoteDecision", ok, "doc=" + JSON.stringify(doc) + " btnText=" + JSON.stringify(btnText) + " inTable=" + inTable);
  });

  S.push(async function helpPerRecipient() {
    await go("help");
    await addDoc("messages", "hreq-multi", { t: ago(1), from: "codex", kind: "request", summary: "Multi-recipient question?", to: ["bewareilive", "vectorcraft"] });
    var initialListed = helpHas("Multi-recipient question?");
    await addDoc("messages", "hrep-multi-1", { t: ago(1), from: "bewareilive", kind: "reply", summary: "BIL answer", replyTo: "hreq-multi" });
    var c = helpCards().filter(function (x) { return x.textContent.indexOf("Multi-recipient question?") !== -1; })[0];
    var stillListed = !!c;
    var pendingPill = c && c.querySelector(".pill.pending-to");
    var waitingText = pendingPill ? pendingPill.textContent : "";
    var hasReply = !!c && c.textContent.indexOf("BIL answer") !== -1;
    await addDoc("messages", "hrep-multi-2", { t: ago(1), from: "vectorcraft", kind: "reply", summary: "VC answer", replyTo: "hreq-multi" });
    var droppedAfterAll = !helpHas("Multi-recipient question?");
    await go("timeline");
    var ok = initialListed && stillListed && waitingText === "waiting on VectorCraft" && hasReply && droppedAfterAll;
    return res("helpPerRecipient", ok, "initial=" + initialListed + " still=" + stillListed + " waiting=" + JSON.stringify(waitingText) + " hasReply=" + hasReply + " dropped=" + droppedAfterAll);
  });

  S.push(async function ownerDigest() {
    var KEY = "crosstalk.lastVisit";
    await go("agreements");
    var past = Date.now();
    while (Date.now() <= past) {}
    try { localStorage.setItem(KEY, String(past)); } catch (e) {}
    var now = Date.now();
    var nowIso = new Date(now).toISOString();
    await addDoc("messages", "dig-m1", { t: nowIso, from: "codex", kind: "sync", summary: "Digest message" });
    await addDoc("progress", "dig-p1", { t: nowIso, project: "bewareilive", game: "robotron", pct: 65 });
    while (Date.now() <= now) {}
    await go("timeline");
    var badgeGone = $("new-badge") === null;
    var dig = $("digest"), sum = $("digest-summary"), sumText = sum ? sum.textContent : "";
    var dismiss = $("digest-dismiss");
    var visible = !!dig && /1 message \(Codex 1\)/.test(sumText) && /robotron 65%/.test(sumText) && !!dismiss;
    if (dismiss) { dismiss.click(); await tick(); }
    var goneAfterDismiss = $("digest") === null;
    var ok = badgeGone && visible && goneAfterDismiss;
    return res("ownerDigest", ok, "badgeGone=" + badgeGone + " sum=" + JSON.stringify(sumText) + " goneAfterDismiss=" + goneAfterDismiss);
  });

  S.push(async function ownerReplyUnlistedAndSelf() {
    await go("timeline");
    await addDoc("messages", "m-unlisted", { t: ago(2), from: "rogue-sender", kind: "sync", summary: "Unlisted message" });
    await addDoc("messages", "m-self", { t: ago(1), from: "owner", kind: "owner", summary: "Owner message" });
    $("reply-btn-m-unlisted").click(); await tick();
    var ta = $("note"); ta.value = "Reply to unlisted"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click(); await tick(80);
    var msgs = Object.keys(__store.messages || {}).map(function (id) { return __store.messages[id]; });
    var rUnlisted = msgs.filter(function (m) { return m && m.summary === "Reply to unlisted"; })[0];
    var unlistedNoTo = !!rUnlisted && rUnlisted.replyTo === "m-unlisted" && rUnlisted.to === undefined;

    $("reply-btn-m-self").click(); await tick();
    ta = $("note"); ta.value = "Reply to self"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click(); await tick(80);
    msgs = Object.keys(__store.messages || {}).map(function (id) { return __store.messages[id]; });
    var rSelf = msgs.filter(function (m) { return m && m.summary === "Reply to self"; })[0];
    var selfNoTo = !!rSelf && rSelf.replyTo === "m-self" && rSelf.to === undefined;

    var decBefore = Object.keys(__store.decisions || {}).length;
    var k = $("noteKind"); k.value = "decision"; k.dispatchEvent(new Event("change", { bubbles: true }));
    ta = $("note"); ta.value = "Should not orphan decision"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    __fail.messages = true;
    $("notePost").click(); await tick(80);
    var decAfterFail = Object.keys(__store.decisions || {}).length;
    __fail.messages = false;
    k = $("noteKind"); k.value = "owner"; k.dispatchEvent(new Event("change", { bubbles: true }));
    ta = $("note"); ta.value = ""; ta.dispatchEvent(new Event("input", { bubbles: true }));
    var noOrphan = decAfterFail === decBefore;
    var ok = unlistedNoTo && selfNoTo && noOrphan;
    return res("ownerReplyUnlistedAndSelf", ok,
      "unlistedNoTo=" + unlistedNoTo + " selfNoTo=" + selfNoTo + " noOrphan=" + noOrphan + " (" + decBefore + "->" + decAfterFail + ")");
  });

  S.push(async function helpDeduplicatesRecipients() {
    await go("timeline");
    await addDoc("participants", "qreplybot", { name: "Q Reply Bot", kind: "tool", color: "#555555" });
    await addDoc("messages", "hreq-dup", { t: ago(2), from: "codex", kind: "request", summary: "Dup recipient request?", to: ["bewareilive", "bewareilive"] });
    var tc = card("hreq-dup"), timelineTo = tc && tc.querySelector(".to") ? tc.querySelector(".to").textContent : null;
    await go("help");
    var hc = helpCards().filter(function (x) { return x.textContent.indexOf("Dup recipient request?") !== -1; })[0];
    var helpTo = hc && hc.querySelector(".to") ? hc.querySelector(".to").textContent : null;
    await addDoc("messages", "hrep-dup-q", { t: ago(1), from: "qreplybot", kind: "reply", summary: "Quarantined reply", replyTo: "hreq-dup" });
    var stillOpenAfterQuarantined = helpHas("Dup recipient request?");
    await addDoc("messages", "hrep-dup-ok", { t: ago(1), from: "vectorcraft", kind: "reply", summary: "Approved reply", replyTo: "hreq-dup" });
    var closedAfterApproved = !helpHas("Dup recipient request?");
    await go("timeline");
    var ok = timelineTo === "\u2192 BewareILive" && helpTo === "to BewareILive" && stillOpenAfterQuarantined && closedAfterApproved;
    return res("helpDeduplicatesRecipients", ok,
      "timelineTo=" + JSON.stringify(timelineTo) + " helpTo=" + JSON.stringify(helpTo) +
      " stillOpenAfterQuarantined=" + stillOpenAfterQuarantined + " closedAfterApproved=" + closedAfterApproved);
  });

  S.push(async function promoteDecisionIdempotentAcrossRender() {
    await go("timeline");
    await addDoc("messages", "own-dec-1", { t: ago(2), from: "owner", kind: "owner", summary: "Composer decision row" });
    await addDoc("decisions", "own-dec-1", { t: ago(2), by: "owner", project: "all", summary: "Composer decision row", source: "Crosstalk page" });
    var b1 = $("promote-decision-own-dec-1");
    var composerRecognized = !!b1 && b1.disabled && b1.textContent === "Recorded";

    await addDoc("messages", "m-inflight-dec", { t: ago(1), from: "bewareilive", kind: "sync", summary: "Promote me" });
    __delay = 80;
    var b2 = $("promote-decision-m-inflight-dec");
    if (b2) b2.click();
    __emit("messages");
    var b2During = $("promote-decision-m-inflight-dec");
    var keptAcrossRender = !!b2During && b2During.disabled && b2During.textContent === "Recorded";
    await tick(120);
    __delay = 0;
    var b2After = $("promote-decision-m-inflight-dec");
    var recordedAfter = !!b2After && b2After.disabled && b2After.textContent === "Recorded";
    var ok = composerRecognized && keptAcrossRender && recordedAfter;
    return res("promoteDecisionIdempotentAcrossRender", ok,
      "composerRecognized=" + composerRecognized + " keptAcrossRender=" + keptAcrossRender + " recordedAfter=" + recordedAfter);
  });

  S.push(async function ownerDigestMalformedRows() {
    var KEY = "crosstalk.lastVisit";
    await go("agreements");
    var past = Date.now();
    while (Date.now() <= past) {}
    try { localStorage.setItem(KEY, String(past)); } catch (e) {}
    var t1 = Date.now();
    var t1Iso = new Date(t1).toISOString();
    await addDoc("messages", "dig-anon", { t: t1Iso, from: "", kind: "sync", summary: "Blank sender" });
    await addDoc("progress", "dig-nan", { t: t1Iso, project: "bewareilive", game: "joust", pct: NaN });
    await addDoc("progress", "dig-inf", { t: t1Iso, project: "bewareilive", game: "tempest", pct: Infinity });
    await addDoc("progress", "dig-good", { t: t1Iso, project: "bewareilive", game: "robotron", pct: 70 });
    while (Date.now() <= t1) {}
    await go("timeline");
    var sum1 = $("digest-summary") ? $("digest-summary").textContent : "";
    var anonOk = /\(\? 1\)/.test(sum1);
    var finiteOk = /robotron 70%/.test(sum1) && !/NaN|Infinity/.test(sum1);

    var t2 = Date.now() + 10;
    var t2Iso = new Date(t2).toISOString();
    await addDoc("progress", "dig-nogame", { t: t2Iso, project: "bewareilive", game: "", pct: 80 });
    await addDoc("progress", "dig-nan2", { t: t2Iso, project: "bewareilive", game: "joust", pct: NaN });
    await go("timeline");
    var sum2 = $("digest-summary") ? $("digest-summary").textContent : "";
    var notWiped = /robotron 70%/.test(sum2);

    delete __store.messages["dig-anon"];
    delete __store.progress["dig-nan"];
    delete __store.progress["dig-inf"];
    delete __store.progress["dig-nogame"];
    delete __store.progress["dig-nan2"];
    __emit("messages"); __emit("progress"); await tick();
    if ($("digest-dismiss")) { $("digest-dismiss").click(); await tick(); }
    var ok = anonOk && finiteOk && notWiped;
    return res("ownerDigestMalformedRows", ok,
      "sum1=" + JSON.stringify(sum1) + " sum2=" + JSON.stringify(sum2) + " anonOk=" + anonOk + " finiteOk=" + finiteOk + " notWiped=" + notWiped);
  });

  S.push(async function replyLockedWhilePosting() {
    await go("timeline");
    await addDoc("messages", "m-lock-1", { t: ago(2), from: "bewareilive", kind: "sync", summary: "First target" });
    await addDoc("messages", "m-lock-2", { t: ago(1), from: "vectorcraft", kind: "sync", summary: "Second target" });
    $("reply-btn-m-lock-1").click(); await tick();
    var ta = $("note"); ta.value = "Slow reply post"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    __delay = 80;
    $("notePost").click();
    __emit("messages");
    var rc = $("reply-cancel"), rb2 = $("reply-btn-m-lock-2");
    var cancelDisabled = !!rc && rc.disabled;
    var replyBtnDisabled = !!rb2 && rb2.disabled;
    if (rc) rc.click();
    if (rb2) rb2.click();
    var bannerDuring = $("reply-banner") ? $("reply-banner").textContent : "";
    var guardedDuring = /First target/.test(bannerDuring);
    await tick(120);
    __delay = 0;
    var bannerClearedAfter = $("reply-banner") === null;
    var ok = cancelDisabled && replyBtnDisabled && guardedDuring && bannerClearedAfter;
    return res("replyLockedWhilePosting", ok,
      "cancelDisabled=" + cancelDisabled + " replyBtnDisabled=" + replyBtnDisabled +
      " guardedDuring=" + guardedDuring + " bannerClearedAfter=" + bannerClearedAfter);
  });

  S.push(async function versionBadgeRendered() {
    window.__crosstalk.setBuildMeta({ commit: "a39217d", buildNumber: 102, commitDate: "2026-10-25T14:50:00Z" });
    var v = $("version"), b = $("build");
    var vText = v ? v.textContent : "", bText = b ? b.textContent : "";
    var vLink = v ? v.querySelector("a") : null;
    var ok = /build #102/.test(vText) && /a39217d/.test(vText) && /Oct 25/.test(vText) &&
      !!vLink && vLink.href === "https://github.com/ookla-ariel-ride/crosstalk/commit/a39217d" &&
      /Page version build #102/.test(bText) && /a39217d/.test(bText);
    return res("versionBadgeRendered", ok, "v=" + JSON.stringify(vText) + " b=" + JSON.stringify(bText));
  });

  S.push(async function logoAndFaviconPresent() {
    var logo = $("logo"), fav = $("favicon");
    var ok = !!logo && logo.tagName.toLowerCase() === "svg" &&
      !!fav && /^data:image\/(svg\+xml|png)[;,]/.test(fav.getAttribute("href") || "");
    return res("logoAndFaviconPresent", ok, "logo=" + !!logo + " fav=" + (fav ? fav.getAttribute("rel") : "none"));
  });

  S.push(async function composerSubjectAndBody() {
    await go("timeline");
    var subj = $("noteSubject"), ta = $("note");
    if (!subj || !ta) return res("composerSubjectAndBody", false, "missing #noteSubject or #note");
    subj.value = "Explicit subject line";
    subj.dispatchEvent(new Event("input", { bubbles: true }));
    ta.value = "Short body";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", metaKey: true, bubbles: true, cancelable: true }));
    await tick(80);
    var msgs = Object.keys(__store.messages || {}).map(function (id) { return __store.messages[id]; });
    var posted = msgs.filter(function (m) { return m && m.summary === "Explicit subject line"; })[0];
    var ok = !!posted && posted.summary === "Explicit subject line" && posted.body === "Short body" && $("noteSubject").value === "" && $("note").value === "";
    return res("composerSubjectAndBody", ok, "posted=" + JSON.stringify(posted));
  });

  S.push(async function composerToChipsAndMentions() {
    await go("timeline");
    var bilChip = $("to-chip-bewareilive"), everyChip = $("to-everyone"), projChip = $("to-projects");
    if (!bilChip || !everyChip || !projChip) return res("composerToChipsAndMentions", false, "missing recipient chips");
    bilChip.click(); await tick();
    var bilPressed = $("to-chip-bewareilive").getAttribute("aria-pressed") === "true" && $("to-everyone").getAttribute("aria-pressed") === "false";
    var ta = $("note");
    ta.value = "Syncing with @VectorCraft on shared audio";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    var labText = document.querySelector("#composer label").textContent;
    $("notePost").click(); await tick(80);
    var msgs = Object.keys(__store.messages || {}).map(function (id) { return Object.assign({ _id: id }, __store.messages[id]); });
    var posted1 = msgs.filter(function (m) { return m.summary === "Syncing with @VectorCraft on shared audio"; })[0];
    var toOk = !!posted1 && Array.isArray(posted1.to) && JSON.stringify(posted1.to) === JSON.stringify(["bewareilive", "vectorcraft"]);
    var c1 = posted1 ? card(posted1._id) : null;
    var mentionEl = c1 && c1.querySelector(".summary .mention");
    var mentionOk = !!mentionEl && mentionEl.textContent === "@VectorCraft";

    $("to-projects").click(); await tick();
    var projPressed = $("to-projects").getAttribute("aria-pressed") === "true";
    $("to-everyone").click(); await tick();
    var everyReset = $("to-everyone").getAttribute("aria-pressed") === "true";
    ta = $("note");
    ta.value = "Broadcast update for @Everyone";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click(); await tick(80);
    msgs = Object.keys(__store.messages || {}).map(function (id) { return __store.messages[id]; });
    var posted2 = msgs.filter(function (m) { return m && m.summary === "Broadcast update for @Everyone"; })[0];
    var broadcastOk = !!posted2 && posted2.to === undefined;
    var ok = bilPressed && /BewareILive/.test(labText) && /VectorCraft/.test(labText) && toOk && mentionOk && projPressed && everyReset && broadcastOk;
    return res("composerToChipsAndMentions", ok,
      "bilPressed=" + bilPressed + " lab=" + JSON.stringify(labText) + " to=" + JSON.stringify(posted1 && posted1.to) +
      " mentionOk=" + mentionOk + " projPressed=" + projPressed + " everyReset=" + everyReset + " broadcastOk=" + broadcastOk);
  });

  S.push(async function composerMentionMenu() {
    await go("timeline");
    var ta = $("note"), menu = $("mention-menu");
    if (!ta || !menu) return res("composerMentionMenu", false, "missing #note or #mention-menu");
    ta.focus();
    ta.value = "Hello @bew";
    ta.setSelectionRange(ta.value.length, ta.value.length);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    await tick();
    menu = $("mention-menu");
    var openOk = !menu.hidden && menu.querySelectorAll(".mention-item").length > 0;
    var item = menu.querySelector(".mention-item[data-handle='BewareILive']") || menu.querySelector(".mention-item");
    if (item) item.click();
    await tick();
    var valAfter = $("note").value;
    var closedAfter = $("mention-menu").hidden;
    $("note").value = "";
    $("note").dispatchEvent(new Event("input", { bubbles: true }));
    var ok = openOk && valAfter === "Hello @BewareILive " && closedAfter;
    return res("composerMentionMenu", ok, "openOk=" + openOk + " valAfter=" + JSON.stringify(valAfter) + " closedAfter=" + closedAfter);
  });

  S.push(async function composerAllMessageKinds() {
    await go("timeline");
    // 1. /ask slash command -> request (shows on Help tab and tracks @BewareILive)
    var ta = $("note");
    ta.value = "/ask @BewareILive Can we share the CRT shader?";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    var liveKindAsk = $("noteKind").value === "request";
    $("notePost").click(); await tick(80);
    await go("help");
    var onHelp = helpHas("Can we share the CRT shader?");
    await go("timeline");

    // 2. agreement -> writes agreements/a-<id>
    var k = $("noteKind"); k.value = "agreement"; k.dispatchEvent(new Event("change", { bubbles: true }));
    $("to-chip-vectorcraft").click(); await tick();
    var subj = $("noteSubject"); subj.value = "Shared vector math header"; subj.dispatchEvent(new Event("input", { bubbles: true }));
    ta = $("note"); ta.value = "Both projects adopt vec2.h"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click(); await tick(80);
    var agrIds = Object.keys(__store.agreements || {}).filter(function (id) { return /^a-own-/.test(id) && __store.agreements[id].summary === "Shared vector math header"; });
    var agrOk = agrIds.length === 1 && __store.agreements[agrIds[0]].builder === "vectorcraft" && __store.agreements[agrIds[0]].status === "agreed";

    // 3. /alert slash command -> kind: "alert", important: true
    ta = $("note"); ta.value = "/alert CI runner disk is 95% full"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    var liveKindAlert = $("noteKind").value === "alert";
    $("notePost").click(); await tick(80);
    var msgs = Object.keys(__store.messages || {}).map(function (id) { return __store.messages[id]; });
    var alertMsg = msgs.filter(function (m) { return m && m.summary === "CI runner disk is 95% full"; })[0];
    var alertOk = liveKindAlert && !!alertMsg && alertMsg.kind === "alert" && alertMsg.important === true;

    // 4. lesson -> writes lessons/lesson-<id>
    k = $("noteKind"); k.value = "lesson"; k.dispatchEvent(new Event("change", { bubbles: true }));
    subj = $("noteSubject"); subj.value = "Never block on audio init"; subj.dispatchEvent(new Event("input", { bubbles: true }));
    ta = $("note"); ta.value = "Gate audio behind user gesture"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click(); await tick(80);
    var lesIds = Object.keys(__store.lessons || {}).filter(function (id) { return /^lesson-own-/.test(id) && __store.lessons[id].summary === "Never block on audio init"; });
    var lesOk = lesIds.length === 1 && __store.lessons[lesIds[0]].fix === "Gate audio behind user gesture";

    // 5. incident -> writes incidents/i-<id> and resolves on Incidents tab
    k = $("noteKind"); k.value = "incident"; k.dispatchEvent(new Event("change", { bubbles: true }));
    subj = $("noteSubject"); subj.value = "MAME window stole focus"; subj.dispatchEvent(new Event("input", { bubbles: true }));
    ta = $("note"); ta.value = "Headless flag missing in runner"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click(); await tick(80);
    var incIds = Object.keys(__store.incidents || {}).filter(function (id) { return /^i-own-/.test(id) && __store.incidents[id].summary === "MAME window stole focus"; });
    var incId = incIds[0];
    var incCreated = !!incId && __store.incidents[incId].end === null;
    await go("incidents");
    var resBtn = incId ? $("resolve-inc-" + incId) : null;
    if (resBtn) { resBtn.click(); await tick(80); }
    var incResolved = !!incId && !!__store.incidents[incId].end && !$("resolve-inc-" + incId);
    await go("timeline");

    // Clean up open help request so later scenarios see clean state if needed
    var reqId = Object.keys(__store.messages || {}).filter(function (id) { return __store.messages[id].summary === "@BewareILive Can we share the CRT shader?"; })[0];
    if (reqId) await addDoc("messages", "rep-" + reqId, { t: ago(0), from: "bewareilive", kind: "reply", summary: "Yes", replyTo: reqId });

    var ok = liveKindAsk && onHelp && agrOk && alertOk && lesOk && incCreated && !!resBtn && incResolved;
    return res("composerAllMessageKinds", ok,
      "liveKindAsk=" + liveKindAsk + " onHelp=" + onHelp + " agrOk=" + agrOk + " alertOk=" + alertOk +
      " lesOk=" + lesOk + " incCreated=" + incCreated + " resBtn=" + !!resBtn + " incResolved=" + incResolved);
  });

  S.push(async function canonicalGameProgress() {
    var savedProg = Object.assign({}, __store.progress || {});
    await put("progress", {
      "amb-1": { t: ago(30), project: "avoidmissingball", game: "centiped", pct: 0 },
      "amb-2": { t: ago(20), project: "avoidmissingball", game: "centipede", pct: 5 },
      "amb-3": { t: ago(10), project: "avoidmissingball", game: "Centipede", pct: 12 },
      "amb-4": { t: ago(25), project: "avoidmissingball", game: "digdugat", pct: 0 },
      "amb-5": { t: ago(10), project: "avoidmissingball", game: "Dig Dug", pct: 8 },
      "amb-6": { t: ago(25), project: "avoidmissingball", game: "missile", pct: 0 },
      "amb-7": { t: ago(10), project: "avoidmissingball", game: "Missile Command", pct: 15 }
    });
    await go("timeline");
    var ambEntry = entry("avoidmissingball");
    var rprog = ambEntry && ambEntry.querySelector(".rprog") ? ambEntry.querySelector(".rprog").textContent : null;
    var rosterOk = rprog === "Centipede 12% \u00b7 Dig Dug 8% \u00b7 Missile Command 15%";
    await go("progress");
    var paths = document.querySelectorAll("#view .chart path").length;
    var legendText = document.querySelector("#view .legend") ? document.querySelector("#view .legend").textContent : "";
    var chartOk = paths === 3 &&
      legendText.indexOf("AvoidMissingBall: Centipede 12%") !== -1 &&
      legendText.indexOf("AvoidMissingBall: Dig Dug 8%") !== -1 &&
      legendText.indexOf("AvoidMissingBall: Missile Command 15%") !== -1;
    await put("progress", savedProg);
    await go("timeline");
    return res("canonicalGameProgress", rosterOk && chartOk,
      "rprog=" + JSON.stringify(rprog) + " paths=" + paths + " legend=" + JSON.stringify(legendText));
  });

  S.push(async function legacyAgreementsAndLessonsRendered() {
    await addDoc("agreements", "leg-ag-1", {
      t: T0,
      title: "Legacy Agreement Title",
      detail: "Legacy agreement detail body",
      owner: "bewareilive",
      status: "Completed on builder branch and waiting for downstream verification"
    });
    await go("agreements");
    var agRows = [].slice.call(document.querySelectorAll("#view tbody tr")).filter(function (tr) {
      return tr.textContent.indexOf("Legacy Agreement Title") !== -1;
    });
    var agRow = agRows[0];
    var agSum = agRow && agRow.querySelector(".summary") ? agRow.querySelector(".summary").textContent : "";
    var agBody = agRow && agRow.querySelector(".body") ? agRow.querySelector(".body").textContent : "";
    var agWho = agRow && agRow.children[2] && agRow.children[2].querySelector(".who") ? agRow.children[2].querySelector(".who").textContent : "";
    var agPill = agRow && agRow.children[1] && agRow.children[1].querySelector(".pill") ? agRow.children[1].querySelector(".pill").textContent : "";
    var agOk = agSum === "Legacy Agreement Title" && agBody === "Legacy agreement detail body" && agWho === "BewareILive" && agPill === "agreed";

    await addDoc("lessons", "leg-les-1", {
      t: T0,
      title: "Legacy Lesson Title",
      detail: "Legacy lesson detail body",
      from: "vectorcraft",
      fix: "Prevented by pre-commit check"
    });
    await go("lessons");
    var lesRows = [].slice.call(document.querySelectorAll("#view tbody tr")).filter(function (tr) {
      return tr.textContent.indexOf("Legacy Lesson Title") !== -1;
    });
    var lesRow = lesRows[0];
    var lesSum = lesRow && lesRow.querySelector(".summary") ? lesRow.querySelector(".summary").textContent : "";
    var lesWho = lesRow && lesRow.children[2] && lesRow.children[2].querySelector(".who") ? lesRow.children[2].querySelector(".who").textContent : "";
    var lesOk = lesSum === "Legacy Lesson Title" && lesWho === "VectorCraft";
    await go("timeline");
    return res("legacyAgreementsAndLessonsRendered", agOk && lesOk,
      "agSum=" + JSON.stringify(agSum) + " agWho=" + JSON.stringify(agWho) + " agPill=" + JSON.stringify(agPill) +
      " lesSum=" + JSON.stringify(lesSum) + " lesWho=" + JSON.stringify(lesWho));
  });

  S.push(async function ownerBroadcastRequestWaitsForAllProjects() {
    await go("help");
    await addDoc("messages", "own-bcast-req", {
      t: ago(2),
      from: "owner",
      kind: "request",
      summary: "Are all the projects in sync?"
    });
    var listedInitially = helpHas("Are all the projects in sync?");
    await addDoc("messages", "own-bcast-rep-bil", {
      t: ago(1),
      from: "bewareilive",
      kind: "sync",
      summary: "BewareILive is in sync",
      replyTo: "own-bcast-req"
    });
    await addDoc("messages", "own-bcast-rep-self", {
      t: ago(1),
      from: "owner",
      kind: "owner",
      summary: "Thanks, waiting on the other two",
      replyTo: "own-bcast-req"
    });
    var c = helpCards().filter(function (x) { return x.textContent.indexOf("Are all the projects in sync?") !== -1; })[0];
    var stillOpenAfterOne = !!c;
    var pendingPill = c && c.querySelector(".pill.pending-to");
    var waitingText = pendingPill ? pendingPill.textContent : "";
    await addDoc("messages", "own-bcast-rep-vc", {
      t: ago(1),
      from: "vectorcraft",
      kind: "sync",
      summary: "VectorCraft is in sync",
      replyTo: "own-bcast-req"
    });
    var stillOpenAfterTwo = helpHas("Are all the projects in sync?");
    await addDoc("messages", "own-bcast-rep-amb", {
      t: ago(1),
      from: "avoidmissingball",
      kind: "sync",
      summary: "AvoidMissingBall is in sync",
      replyTo: "own-bcast-req"
    });
    var closedAfterAllThree = !helpHas("Are all the projects in sync?");
    await go("timeline");
    var ok = listedInitially && stillOpenAfterOne && waitingText === "waiting on AvoidMissingBall \u00b7 VectorCraft" && stillOpenAfterTwo && closedAfterAllThree;
    return res("ownerBroadcastRequestWaitsForAllProjects", ok,
      "initial=" + listedInitially + " afterOne=" + stillOpenAfterOne + " waiting=" + JSON.stringify(waitingText) +
      " afterTwo=" + stillOpenAfterTwo + " closedAfterThree=" + closedAfterAllThree);
  });

  // ---------- Threads: a root card with its replies folded under it ----------
  function topCard(id) { return document.querySelector('#log > article[data-id="' + id + '"]'); }
  function threadBox(id) { return $("thread-" + id); }
  function threadIds(id) { var b = threadBox(id); return b ? [].slice.call(b.querySelectorAll(":scope > .reply")).map(function (r) { return r.dataset.id; }) : null; }
  function footText(id) { var b = $("thread-toggle-" + id); return b ? b.textContent : null; }

  // Replies (and replies to replies) fold into the root's thread; the root shows a footer, a reply-less card none.
  // hist-* is the history thread stub.js seeds before the page loads.
  S.push(async function threadGrouping() {
    await go("timeline");
    var once = document.querySelectorAll('#log > article[data-id="hist-r"]').length === 1;
    var noneTop = !topCard("hist-a") && !topCard("hist-b") && !topCard("hist-s1");
    var f = footText("hist-r"), f1 = footText("hist-s");
    var plain = card("gemini-1"), plainFoot = plain && plain.querySelector(".thread-foot");
    var ok = once && noneTop && /^2 replies \u00b7 last from Codex 1[01] min ago \u00b7 Show replies$/.test(f) &&
      /^1 reply \u00b7 last from Owner 1[56] min ago \u00b7 Show replies$/.test(f1) && !!plain && !plainFoot;
    return res("threadGrouping", ok, "once=" + once + " noneTop=" + noneTop + " foot=" + JSON.stringify(f) + " foot1=" + JSON.stringify(f1) + " plainFoot=" + !!plainFoot);
  });

  // A reply newer than the last visit that was already there when the page loaded carries the "new reply" tag but
  // its thread stays closed; only a reply that arrives while the page is open (threadBump) opens it. The stored
  // last visit is set to just before hist-b (10 min old), which the page picks up on the next render.
  S.push(async function threadSinceVisitClosed() {
    await go("timeline");
    var KEY = "crosstalk.lastVisit", prev = localStorage.getItem(KEY);
    localStorage.setItem(KEY, String(Date.now() - 12 * 60000));
    __emit("messages"); await tick();
    var c = topCard("hist-r"), mark = c && c.querySelector(".pill.new-reply");
    var closed = !threadBox("hist-r") && $("thread-toggle-hist-r").getAttribute("aria-expanded") === "false";
    var first = document.querySelector("#log > article"), firstId = first && first.dataset.id;
    if (prev == null) localStorage.removeItem(KEY); else localStorage.setItem(KEY, prev);
    var ok = !!mark && mark.textContent === "new reply" && closed;
    return res("threadSinceVisitClosed", ok, "mark=" + !!mark + " closed=" + closed + " first=" + firstId);
  });

  // Threads start collapsed; the footer toggles them, and the state survives a snapshot re-render. Replying from
  // inside an open thread keeps it open and never marks it "new reply" (the viewer's own reply). A search the
  // root matches doesn't reset the stored choice.
  S.push(async function threadToggle() {
    await go("timeline");
    var startClosed = !threadBox("hist-r") && $("thread-toggle-hist-r").getAttribute("aria-expanded") === "false";
    $("thread-toggle-hist-r").click(); await tick();
    var ids = threadIds("hist-r");
    __emit("messages"); await tick();
    var keptOpen = JSON.stringify(threadIds("hist-r")) === JSON.stringify(["hist-a", "hist-b"]) && /Hide replies$/.test(footText("hist-r"));
    var b = threadBox("hist-r"), rb = b && b.querySelector("#reply-btn-hist-b");
    var re = b && b.querySelector('[data-id="hist-b"] .re'), reText = re ? re.textContent : null;
    var bannerOk = false, posted = null;
    if (rb) {
      rb.click(); await tick();
      var banner = $("reply-banner"); bannerOk = !!banner && /Answer to the answer/.test(banner.textContent);
      var ta = $("note"); ta.value = "Own reply in a thread"; ta.dispatchEvent(new Event("input", { bubbles: true }));
      $("notePost").click(); await tick(80);
      posted = Object.keys(__store.messages).map(function (id) { return __store.messages[id]; }).filter(function (m) { return m.summary === "Own reply in a thread"; })[0];
    }
    __emit("messages"); await tick();
    var c = topCard("hist-r");
    var ownOk = !!posted && posted.replyTo === "hist-b" && !!threadBox("hist-r") && /^3 replies \u00b7 last from Owner /.test(footText("hist-r")) &&
      !(c && c.querySelector(".pill.new-reply"));
    await search("History root");
    var searchKept = !!threadBox("hist-r");
    await search("");
    $("thread-toggle-hist-r").click(); await tick();
    __emit("messages"); await tick();
    var keptClosed = !threadBox("hist-r");
    var ok = startClosed && JSON.stringify(ids) === JSON.stringify(["hist-a", "hist-b"]) && keptOpen && !!rb && bannerOk &&
      reText === "\u21a9 Replying to Owner: History answer" && ownOk && searchKept && keptClosed;
    return res("threadToggle", ok, "startClosed=" + startClosed + " ids=" + JSON.stringify(ids) + " keptOpen=" + keptOpen + " replyBtn=" + !!rb +
      " banner=" + bannerOk + " re=" + JSON.stringify(reText) + " ownReply=" + ownOk + " foot=" + JSON.stringify(footText("hist-r")) +
      " searchKept=" + searchKept + " keptClosed=" + keptClosed);
  });

  // A new reply bumps an old thread to the top, under today's separator, marked "new reply" and expanded;
  // a user collapse beats the auto-expand.
  S.push(async function threadBump() {
    await go("timeline");
    await addDoc("messages", "th-old", { t: ago(60 * 30), from: "codex", kind: "sync", summary: "Old thread" });
    __emit("messages"); await tick();
    await addDoc("messages", "th-new", { t: new Date().toISOString(), from: "vectorcraft", kind: "sync", summary: "Fresh reply", replyTo: "th-old" });
    var first = document.querySelector("#log > article"), firstId = first && first.dataset.id;
    var day = document.querySelector("#log > .day"), dayText = day ? day.textContent : null;
    var today = new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", weekday: "long", month: "long", day: "numeric" }).format(new Date());
    var c = topCard("th-old"), mark = c && c.querySelector(".pill.new-reply");
    var opened = JSON.stringify(threadIds("th-old")) === JSON.stringify(["th-new"]);
    __emit("messages"); await tick();
    var stillOpen = !!threadBox("th-old");
    $("thread-toggle-th-old").click(); await tick();
    __emit("messages"); await tick();
    var userClosed = !threadBox("th-old");
    var ok = firstId === "th-old" && dayText === today && !!mark && mark.textContent === "new reply" && opened && stillOpen && userClosed;
    return res("threadBump", ok, "first=" + firstId + " day=" + JSON.stringify(dayText) + " today=" + JSON.stringify(today) + " mark=" + !!mark +
      " opened=" + opened + " stillOpen=" + stillOpen + " userClosed=" + userClosed);
  });

  // An important or decision reply sits in its thread and also as its own feed card with its "Replying to" line.
  S.push(async function threadImportantTwice() {
    await go("timeline");
    __store.messages = Object.assign({}, __store.messages, {
      "th-i": { t: ago(9), from: "codex", kind: "sync", summary: "Root with an alert" },
      "th-ia": { t: ago(8), from: "vectorcraft", kind: "alert", summary: "Alert in a thread", replyTo: "th-i" },
      "th-id": { t: ago(7), from: "bewareilive", kind: "decision", summary: "Decision in a thread", replyTo: "th-i" },
      "th-ip": { t: ago(6), from: "bewareilive", kind: "sync", summary: "Plain reply", replyTo: "th-i" }
    });
    __emit("messages"); await tick();
    var ta = topCard("th-ia"), td = topCard("th-id"), tp = topCard("th-ip");
    var re = ta && ta.querySelector(".re"), reText = re ? re.textContent : null;
    if (!threadBox("th-i")) { $("thread-toggle-th-i").click(); await tick(); }
    var ids = threadIds("th-i");
    var uniq = document.querySelectorAll("#reply-btn-th-ia").length === 1 && document.querySelectorAll("#star-th-ia").length === 1;
    $("thread-toggle-th-i").click(); await tick();
    var ok = !!ta && !!td && !tp && reText === "\u21a9 Replying to Codex: Root with an alert" &&
      JSON.stringify(ids) === JSON.stringify(["th-ia", "th-id", "th-ip"]) && uniq;
    return res("threadImportantTwice", ok, "alertTop=" + !!ta + " decisionTop=" + !!td + " plainTop=" + !!tp + " re=" + JSON.stringify(reText) +
      " ids=" + JSON.stringify(ids) + " uniqueIds=" + uniq);
  });

  // Search, a kind chip or a person matching only a reply shows its (collapsed) thread expanded.
  S.push(async function threadFilterReply() {
    await go("timeline");
    function opened() { return !!topCard("hist-s") && JSON.stringify(threadIds("hist-s")) === JSON.stringify(["hist-s1"]); }
    var closed = !threadBox("hist-s");
    await search("zebra-needle");
    var bySearch = opened();
    await search("");
    await chip("lesson");
    var byKind = opened();
    await chip("all");
    $("who-owner").click(); await tick();
    var byWho = opened();
    $("who-owner").click(); await tick();
    var closedAgain = !threadBox("hist-s");
    var ok = closed && bySearch && byKind && byWho && closedAgain;
    return res("threadFilterReply", ok, "closed=" + closed + " search=" + bySearch + " kind=" + byKind + " who=" + byWho + " closedAgain=" + closedAgain);
  });

  // A replyTo cycle renders (no hang); its messages stand alone with their "Replying to" lines.
  // A 60-deep reply chain, deepest first in the store, still folds into one thread.
  S.push(async function threadCycle() {
    await go("timeline");
    var chain = {};
    for (var k = 59; k >= 0; k--) chain["th-L" + k] = { t: ago(3), from: "codex", kind: "sync", summary: "Chain " + k, replyTo: k ? "th-L" + (k - 1) : undefined };
    __store.messages = Object.assign({}, __store.messages, chain, {
      "th-c1": { t: ago(3), from: "codex", kind: "sync", summary: "Cycle one", replyTo: "th-c2" },
      "th-c2": { t: ago(3), from: "vectorcraft", kind: "sync", summary: "Cycle two", replyTo: "th-c1" },
      "th-c3": { t: ago(3), from: "bewareilive", kind: "sync", summary: "Into the cycle", replyTo: "th-c1" }
    });
    __emit("messages"); await tick();
    var chainFoot = footText("th-L0"), chainTop = document.querySelectorAll('#log > article[data-id^="th-L"]').length;
    var a = topCard("th-c1"), b = topCard("th-c2"), c = topCard("th-c3");
    var lines = [a, b, c].map(function (x) { var r = x && x.querySelector(".re"); return r ? r.textContent : null; });
    var ok = !!a && !!b && !!c && lines[0] === "\u21a9 Replying to VectorCraft: Cycle two" && lines[1] === "\u21a9 Replying to Codex: Cycle one" &&
      lines[2] === "\u21a9 Replying to Codex: Cycle one" && /^59 replies /.test(chainFoot) && chainTop === 1;
    return res("threadCycle", ok, "cards=" + [!!a, !!b, !!c].join(",") + " re=" + JSON.stringify(lines) + " chain=" + JSON.stringify(chainFoot) + " chainTop=" + chainTop);
  });

  // Ids that name Object.prototype members ("constructor", "toString", "__proto__") are plain ids: a reply to one
  // that isn't loaded stands alone, and a message whose id is "constructor" is an ordinary root.
  S.push(async function threadProtoKeys() {
    await go("timeline");
    await addDoc("messages", "th-pk0", { t: ago(2), from: "codex", kind: "sync", summary: "Reply to constructor", replyTo: "constructor" });
    var alone = !!topCard("th-pk0") && !topCard("th-pk0").querySelector(".re");
    __store.messages = Object.assign({}, __store.messages, {
      "constructor": { t: ago(3), from: "codex", kind: "sync", summary: "Root called constructor" },
      "th-pk1": { t: ago(2), from: "codex", kind: "sync", summary: "Reply to toString", replyTo: "toString" },
      "th-pk2": { t: ago(2), from: "codex", kind: "sync", summary: "Reply to proto", replyTo: "__proto__" }
    });
    __emit("messages"); await tick();
    var root = topCard("constructor"), f = footText("constructor");
    var ok = alone && !!root && /^1 reply /.test(f || "") && !topCard("th-pk0") && !!topCard("th-pk1") && !!topCard("th-pk2");
    return res("threadProtoKeys", ok, "aloneFirst=" + alone + " root=" + !!root + " foot=" + JSON.stringify(f) + " pk1=" + !!topCard("th-pk1") + " pk2=" + !!topCard("th-pk2"));
  });

  // A reply whose parent isn't loaded stays a standalone card, with no thread footer.
  S.push(async function threadOrphan() {
    await go("timeline");
    await addDoc("messages", "th-o", { t: ago(2), from: "codex", kind: "sync", summary: "Orphan reply", replyTo: "no-such-message" });
    var c = topCard("th-o");
    var ok = !!c && !c.querySelector(".re") && !c.querySelector(".thread-foot");
    return res("threadOrphan", ok, "card=" + !!c + " re=" + !!(c && c.querySelector(".re")) + " foot=" + !!(c && c.querySelector(".thread-foot")));
  });

  // The roster badge for a project's agent wake: one text per status, nothing for idle or no entry.
  S.push(async function wakeBadges() {
    await go("timeline");
    // Enough roster entries for every status, whatever an earlier scenario left in the store.
    var extra = {};
    for (var n = 0; n < 9; n++) extra["wakeproj" + n] = { name: "WakeProj" + n, kind: "project", trust: "approved" };
    __store.participants = Object.assign({}, __store.participants, extra); __emit("participants"); await tick();
    var ids = Object.keys(extra);
    var want = [["running", "waking\u2026"], ["ok", /^woke \S+$/], ["failed", "wake failed"], ["timed-out", "wake failed"], ["interrupted", "wake interrupted"],
      ["cooling-down", "cooling down"], ["capped", "daily cap reached"], ["misconfigured", "wake misconfigured"], ["idle", null]];
    if (ids.length < want.length) return res("wakeBadges", false, "only " + ids.length + " roster ids");
    var map = {}, bad = [];
    want.forEach(function (w, i) { map[ids[i]] = { status: w[0], since: new Date(Date.now() - 120000).toISOString(), ids: [], log: w[0] === "failed" ? "/tmp/wake.log" : undefined }; });
    __setWake(map); await tick();
    want.forEach(function (w, i) {
      var b = $("wake-" + ids[i]);
      if (w[1] === null) { if (b) bad.push(w[0] + " has badge"); return; }
      if (!b) { bad.push(w[0] + " missing"); return; }
      var t = b.textContent;
      if (!(w[1] instanceof RegExp ? w[1].test(t) : t === w[1])) bad.push(w[0] + " text=" + t);
      if (!/wake-/.test(b.className) || b.className.indexOf("wake-" + w[0]) < 0) bad.push(w[0] + " class=" + b.className);
      if (!b.title) bad.push(w[0] + " no title");
    });
    var failedB = $("wake-" + ids[2]);
    if (failedB && failedB.title.indexOf("/tmp/wake.log") < 0) bad.push("log missing from title");
    __setWake({}); await tick();
    var left = document.querySelectorAll('[id^="wake-"]').length;
    if (left) bad.push(left + " badges left after clear");
    return res("wakeBadges", !bad.length, bad.join("; "));
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
