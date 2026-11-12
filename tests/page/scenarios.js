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
    // The sender tag is a solid block of the sender's color.
    var got = getComputedStyle(w).backgroundColor, want = rgb("var(--muted)");
    return res("unknownSenderNeutral", w.textContent === "gemini" && got === want, "name=" + JSON.stringify(w.textContent) + " background=" + got + " muted=" + want);
  });

  S.push(async function subAgentTag() {
    await addDoc("participants", "beta.verifier", { name: "verifier", kind: "agent", parent: "beta", color: "#8a4b12", trust: "approved" });
    await addDoc("messages", "bv-1", { t: T0, from: "beta.verifier", kind: "sync", summary: "Verifier report" });
    var c = card("bv-1"), w = c && c.querySelector(".who");
    var name = w ? w.textContent : null;
    return res("subAgentTag", name === "Beta › verifier", "name=" + JSON.stringify(name));
  });

  // A body that repeats the summary shows once: an equal body is hidden, a body that opens with the summary line drops it.
  S.push(async function bodyRepeatsSummary() {
    await go("timeline");
    await addDoc("messages", "dup-1", { t: T0, from: "owner", kind: "owner", summary: "Same text twice", body: "Same text twice " });
    await addDoc("messages", "dup-2", { t: T0, from: "owner", kind: "owner", summary: "First line", body: "First line\nSecond line" });
    var c1 = card("dup-1"), c2 = card("dup-2");
    if (!c1 || !c2) return res("bodyRepeatsSummary", false, "no card");
    var b1 = c1.querySelectorAll("p.body").length, b2 = c2.querySelector("p.body");
    var t2 = b2 ? b2.textContent : null;
    return res("bodyRepeatsSummary", b1 === 0 && t2 === "Second line", "equalBodies=" + b1 + " prefixBody=" + JSON.stringify(t2));
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
    var bet = link(entry("beta")), ver = link(entry("beta.verifier")), codex = link(entry("codex")), owner = link(entry("owner"));
    var ok = !!bet && bet.href === "https://github.com/example-org/beta" && bet.textContent === "repo \u2197" &&
      !!ver && ver.href === "https://github.com/example-org/beta" &&
      !codex && !owner;
    return res("rosterRepoLink", ok, "beta=" + (bet && bet.href) + " verifier=" + (ver && ver.href) + " codex=" + !!codex + " owner=" + !!owner);
  });

  // ---------- Harness: the tool a participant's sessions run in ----------
  function harnessOf(node) {
    var h = node && node.querySelector(".harness");
    return h ? { label: h.getAttribute("aria-label"), title: h.title, svg: !!h.querySelector("svg"), text: h.textContent } : null;
  }

  // A roster entry shows its harness after the name as a logo (Claude Code, Codex); the owner has none.
  S.push(async function harnessRosterIcons() {
    await go("timeline");
    var gam = harnessOf(entry("gamma")), cx = harnessOf(entry("codex")), own = harnessOf(entry("owner"));
    var ok = !!gam && gam.label === "Claude Code" && gam.title === "Claude Code" && gam.svg &&
      !!cx && cx.label === "Codex" && cx.svg && cx.text === "" && own === null;
    return res("harnessRosterIcons", ok, "gamma=" + JSON.stringify(gam) + " codex=" + JSON.stringify(cx) + " owner=" + JSON.stringify(own));
  });

  // Every harness the page knows has a logo: Hermes, Pi and OpenCode as well as the roster's two.
  S.push(async function harnessKnownLogos() {
    await go("timeline");
    var ids = ["hermes", "pi", "opencode", "gemini-cli", "copilot", "cursor"];
    ids.forEach(function (h) { __store.participants["zz-" + h] = { name: "Zz " + h, kind: "tool", color: "#5b2a86", colorDark: "#c9a2f0", trust: "approved", harness: h }; });
    __emit("participants"); await tick();
    var got = ids.map(function (h) { var x = harnessOf(entry("zz-" + h)); return x && x.svg ? x.label : null; });
    ids.forEach(function (h) { delete __store.participants["zz-" + h]; }); __emit("participants"); await tick();
    var ok = JSON.stringify(got) === JSON.stringify(["Hermes", "Pi", "OpenCode", "Gemini CLI", "GitHub Copilot", "Cursor"]);
    return res("harnessKnownLogos", ok, "logos=" + JSON.stringify(got));
  });

  // A sub-agent without a harness of its own shows its parent's; an unknown id gets a letter badge named by the
  // id; a malformed one shows nothing.
  S.push(async function harnessInheritAndUnknown() {
    await go("timeline");
    await addDoc("participants", "zz-warp", { name: "ZzWarp", kind: "tool", color: "#5b2a86", colorDark: "#c9a2f0", trust: "approved", harness: "warp" });
    await addDoc("participants", "zz-bad", { name: "ZzBad", kind: "tool", color: "#0b6e4f", colorDark: "#5fd3a8", trust: "approved", harness: "Bad Id!" });
    var ver = harnessOf(entry("beta.verifier")), warp = harnessOf(entry("zz-warp")), bad = harnessOf(entry("zz-bad"));
    delete __store.participants["zz-warp"]; delete __store.participants["zz-bad"]; __emit("participants"); await tick();
    var ok = !!ver && ver.label === "Claude Code" && !!warp && warp.label === "warp" && warp.text === "W" && !warp.svg && bad === null;
    return res("harnessInheritAndUnknown", ok, "verifier=" + JSON.stringify(ver) + " warp=" + JSON.stringify(warp) + " bad=" + JSON.stringify(bad));
  });

  // A message card shows its sender's harness beside the sender's name.
  S.push(async function harnessOnCards() {
    await go("timeline");
    await addDoc("messages", "hn-cx", { t: ago(1), from: "codex", kind: "sync", summary: "Harness card codex" });
    await addDoc("messages", "hn-gam", { t: ago(1), from: "gamma", kind: "sync", summary: "Harness card gamma" });
    var cx = card("hn-cx"), gam = card("hn-gam");
    var cxH = harnessOf(cx && cx.querySelector(".meta")), gamH = harnessOf(gam && gam.querySelector(".meta"));
    var ok = !!cxH && cxH.label === "Codex" && !!gamH && gamH.label === "Claude Code" && gamH.svg;
    return res("harnessOnCards", ok, "codex=" + JSON.stringify(cxH) + " gamma=" + JSON.stringify(gamH));
  });

  // The Harness buttons (one per harness on the roster) keep messages written in the picked harnesses; they
  // combine with the people filter, the note names them, and Clear filters drops them.
  S.push(async function harnessFilter() {
    await go("timeline");
    var group = $("harness-filter"), cc = $("h-claude-code"), cx = $("h-codex");
    if (!group || !cc || !cx) return res("harnessFilter", false, "group=" + !!group + " claude-code=" + !!cc + " codex=" + !!cx);
    var labels = [].slice.call(group.querySelectorAll("button")).map(function (b) { return b.getAttribute("aria-label") || b.textContent; });
    $("h-codex").click(); await tick();
    var pressedCx = $("h-codex").getAttribute("aria-pressed") === "true";
    var froms = [].slice.call(document.querySelectorAll("#log > article")).map(function (a) { return a.dataset.from; });
    var onlyCodex = froms.length > 0 && froms.every(function (f) { return f === "codex"; });
    var note1 = whoNote();
    $("who-gamma").click(); await tick();
    var none = document.querySelectorAll("#log > article").length === 0;
    $("h-claude-code").click(); await tick();
    var gamShown = !!card("hn-gam") && !card("hn-cx");
    var note2 = whoNote();
    $("who-clear").click(); await tick();
    var cleared = $("h-codex").getAttribute("aria-pressed") === "false" && $("h-claude-code").getAttribute("aria-pressed") === "false" && !!card("hn-cx");
    delete __store.messages["hn-cx"]; delete __store.messages["hn-gam"]; __emit("messages"); await tick();
    var ok = labels.indexOf("Claude Code") !== -1 && labels.indexOf("Codex") !== -1 && pressedCx && onlyCodex &&
      note1 === "Showing messages written in Codex." && none && gamShown &&
      note2 === "Showing messages sent by Gamma, written in Claude Code or Codex." && cleared;
    return res("harnessFilter", ok, "labels=" + JSON.stringify(labels) + " onlyCodex=" + onlyCodex + " note1=" + JSON.stringify(note1) +
      " none=" + none + " gamShown=" + gamShown + " note2=" + JSON.stringify(note2) + " cleared=" + cleared);
  });

  // A picked harness that leaves the roster stops filtering: its button is gone, so it can't stay on unseen.
  S.push(async function harnessStalePickDropped() {
    await go("timeline");
    $("h-codex").click(); await tick();
    var saved = __store.participants.codex;
    __store.participants.codex = Object.assign({}, saved); delete __store.participants.codex.harness; __emit("participants"); await tick();
    var gone = !$("h-codex"), unfiltered = shownFrom().some(function (f) { return f !== "codex"; }), noNote = whoNote() === null;
    __store.participants.codex = saved; __emit("participants"); await tick();
    var stillOff = $("h-codex") && $("h-codex").getAttribute("aria-pressed") === "false";
    var ok = gone && unfiltered && noNote && !!stillOff;
    return res("harnessStalePickDropped", ok, "gone=" + gone + " unfiltered=" + unfiltered + " noNote=" + noNote + " stillOff=" + stillOff);
  });

  S.push(async function threeWayPositions() {
    await addDoc("messages", "dis-1", { t: T0, from: "beta", kind: "disagreement", summary: "Three views",
      positions: { beta: "Do A", gamma: "Do B", codex: "Do C" } });
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
    await addDoc("messages", "stl-1", { t: ago(1), from: "beta", kind: "sync", summary: "Linked sender tag" });
    await addDoc("messages", "stl-2", { t: ago(1), from: "owner", kind: "owner", summary: "Owner has no repo" });
    var w1 = card("stl-1") && card("stl-1").querySelector(".who"), a1 = w1 && w1.closest("a");
    var w2 = card("stl-2") && card("stl-2").querySelector(".who"), a2 = w2 && w2.closest("a");
    var ok = !!a1 && a1.href === "https://github.com/example-org/beta" && a1.querySelector(".who") === w1 &&
      !a2 && w2 && w2.textContent === "Owner";
    return res("senderTagLinks", ok, "bet=" + (a1 && a1.href) + " ownerLinked=" + !!a2);
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
    var verifier = entry("beta.verifier"), bet = entry("beta");
    var nested = !!(verifier && bet && verifier.parentElement.closest("li") === bet);
    var got = {
      beta: groupOf("beta"), gamma: groupOf("gamma"), verifier: groupOf("beta.verifier"),
      codex: groupOf("codex"), "gemini-r": groupOf("gemini-r"), githook: groupOf("githook"), owner: groupOf("owner"),
      "ghost.helper": groupOf("ghost.helper"), gemini: groupOf("gemini")
    };
    var want = { beta: "projects", gamma: "projects", verifier: "projects", codex: "reviewers", "gemini-r": "reviewers",
      githook: "tools", owner: "tools", "ghost.helper": "tools", gemini: "unlisted" };
    var g = entry("gemini"), unlistedPill = !!(g && /not approved/i.test(g.textContent));
    var ok = r && r.tagName === "DETAILS" && JSON.stringify(heads) === JSON.stringify(["Projects", "Reviewers and research", "Tools and people", "Not in roster"]) &&
      nested && JSON.stringify(got) === JSON.stringify(want) && unlistedPill;
    return res("rosterGroups", ok, "heads=" + JSON.stringify(heads) + " nested=" + nested + " groups=" + JSON.stringify(got) + " unlistedPill=" + unlistedPill);
  });

  S.push(async function rosterFilter() {
    await go("timeline");
    await addDoc("messages", "gam-sync", { t: ago(3), from: "gamma", kind: "sync", summary: "GAM sync" });
    await addDoc("messages", "gam-alert", { t: ago(4), from: "gamma", kind: "alert", summary: "GAM alert" });
    var b = $("who-gamma");
    if (!b) return res("rosterFilter", false, "no roster entry for gamma");
    var isBtn = b.tagName === "BUTTON";
    b.focus(); b.click(); await tick();
    var pressed = $("who-gamma").getAttribute("aria-pressed"), focused = document.activeElement && document.activeElement.id;
    var only = shownFrom(), onlyVc = only.length === 2 && only.every(function (f) { return f === "gamma"; });
    await chip("sync");
    var both = shownFrom(), andOk = both.length === 1 && both[0] === "gamma";
    await chip("all");
    $("who-gamma").click(); await tick();
    var cleared = $("who-gamma").getAttribute("aria-pressed"), all = shownFrom(), many = all.some(function (f) { return f !== "gamma"; });
    var ok = isBtn && pressed === "true" && focused === "who-gamma" && onlyVc && andOk && cleared === "false" && many;
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
    // Everyone on the roster checked in just now, so the test participant is the only one that can be late.
    var pids = Object.keys(__store.participants || {});
    for (var j = 0; j < pids.length; j++) {
      await addDoc("messages", "ci-" + pids[j], { t: ago(2), from: pids[j], kind: "check-in", summary: "Check-in" });
    }
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
    await addDoc("messages", "to-1", { t: ago(1), from: "codex", kind: "sync", summary: "Review: 3 findings", to: "beta" });
    await addDoc("messages", "to-2", { t: ago(1), from: "codex", kind: "sync", summary: "Two recipients", to: ["beta.verifier", "gamma"] });
    await addDoc("messages", "to-3", { t: ago(1), from: "codex", kind: "sync", summary: "No recipient" });
    function arrow(id) { var c = card(id), t = c && c.querySelector(".to"); return t ? t.textContent : null; }
    var a = arrow("to-1"), b = arrow("to-2"), n = arrow("to-3");
    var ok = a === "→ Beta" && b === "→ Beta › verifier, Gamma" && n === null;
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
    await addDoc("messages", "imp-1", { t: ago(1), from: "gamma", kind: "alert", summary: "Attention needed" });
    var c = card("imp-1");
    if (!c) return res("importantMarker", false, "no card");
    var who = c.style.getPropertyValue("--who"), want = window.__crosstalk.colorOf("gamma");
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
    var light = window.__crosstalk.colorOf("gamma"), h = $("heading").textContent;
    var tag = $("who-gamma"), lightTag = tag && tag.style.getPropertyValue("--who");
    root.setAttribute("data-theme", "dark"); await tick();
    var dark = window.__crosstalk.colorOf("gamma"), darkTag = $("who-gamma") && $("who-gamma").style.getPropertyValue("--who");
    if (before == null) root.removeAttribute("data-theme"); else root.setAttribute("data-theme", before);
    await tick();
    var ok = light === "#1f5f8b" && dark === "#6fb2e2" && lightTag === light && darkTag === dark &&
      h === "Alpha · Beta · Delta · Epsilon Kit · Gamma · Relay Bot";
    return res("themedColors", ok, "light=" + light + "/" + lightTag + " dark=" + dark + "/" + darkTag + " heading=" + JSON.stringify(h));
  });

  // In light mode the heading names every project; with a repo each name is a link to that repo.
  S.push(async function repoLinksHeading() {
    await go("timeline");
    var saved = __store.participants;
    __store.participants = { beta: Object.assign({}, saved.beta), gamma: Object.assign({}, saved.gamma) };
    __emit("participants"); await tick();
    var links = [].slice.call(document.querySelectorAll("#heading a.proj")).map(function (a) { return [a.textContent, a.href]; });
    var title = $("heading").textContent;
    __store.participants = saved; __emit("participants"); await tick();
    var ok = links.length === 2 && links[0][0] === "Beta" && links[0][1] === "https://github.com/example-org/beta" &&
      links[1][0] === "Gamma" && links[1][1] === "https://github.com/example-org/gamma" &&
      title === "Beta · Gamma";
    return res("repoLinksHeading", ok, JSON.stringify(links) + " title=" + JSON.stringify(title));
  });

  // A game's line has its project's color; two games of one project differ by dash.
  S.push(async function progressBoard() {
    var day = 1440;
    await put("progress", {
      "p1": { t: ago(day * 2), project: "beta", game: "juniper", pct: 5, verified: 2, rows: 40 }, "p2": { t: ago(1), project: "beta", game: "Juniper", pct: 20, verified: 6, rows: 40 },
      "p3": { t: ago(day * 3), project: "gamma", game: "timber", pct: 30 },
      "p4": { t: ago(day * 20), project: "gamma", game: "classic", pct: 85 } });
    await go("progress");
    var rows = [].slice.call(document.querySelectorAll("#view > .pb-board > .pb-row"));
    var names = rows.map(function (r) { return r.querySelector(".pb-name strong").textContent; });
    var first = rows[0], v = first && first.querySelector(".pb-v").style.width, b = first && first.querySelector(".pb-b").style.width;
    var change = first && first.querySelector(".pb-change").textContent, hero = document.querySelector("#view .pb-hero").textContent;
    var stale = rows[1] && rows[1].classList.contains("stale"), older = document.querySelector("#view .pb-older summary").textContent;
    first.querySelector(".pb-head").click(); await tick();
    var head = document.querySelector("#view .pb-head"), detail = document.querySelector("#view .pb-detail svg path") !== null;
    var expanded = head.getAttribute("aria-expanded");
    var n = document.querySelector("#tab-progress .n").textContent;
    head.click(); await tick();
    await go("timeline");
    var ok = JSON.stringify(names) === '["Juniper","Timber"]' && v === "15%" && b === "5%" && /\+4 verified/.test(change) && /^\+4 verified rows in 24 hours/.test(hero) && stale && /^1 older item/.test(older) && detail && expanded === "true" && n === "2";
    return res("progressBoard", ok, JSON.stringify({ names: names, v: v, b: b, change: change, hero: hero, stale: stale, older: older, detail: detail, expanded: expanded, n: n }));
  });

  // The roster shows each project's latest progress per game, and nothing for a project with none.
  S.push(async function rosterProgress() {
    await go("timeline");
    await put("progress", {
      "rg-1": { t: ago(60), project: "beta", game: "juniper", pct: 62 },
      "rg-2": { t: ago(60), project: "beta", game: "ridgeline", pct: 41 },
      "rg-3": { t: ago(30), project: "gamma", game: "timber", pct: 30 },
      "rg-4": { t: ago(5), project: "gamma", game: "timber", pct: 45 }
    });
    function prog(id) { var e = entry(id), d = e && e.querySelector(".rprog"); return d ? d.textContent : null; }
    var bet = prog("beta"), gam = prog("gamma"), alp = prog("alpha"), codex = prog("codex");
    var ok = bet === "Juniper 62% · Ridgeline 41%" && gam === "Timber 45%" && alp === null && codex === null;
    return res("rosterProgress", ok, "bet=" + JSON.stringify(bet) + " gam=" + JSON.stringify(gam) + " alp=" + JSON.stringify(alp) + " codex=" + JSON.stringify(codex));
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
      "lk-1": { repo: "example-org/beta", path: "docs/plan.md", project: "beta", holder: "beta", note: "editing", since: now },
      "lk-2": { repo: "example-org/gamma", path: "src/a b.cpp", project: "gamma", holder: "gamma", note: "editing", since: now },
      "lk-3": { repo: "not a repo", path: "x", project: "codex", note: "bad repo", since: now }
    });
    await go("locks");
    var rows = [].slice.call(document.querySelectorAll("#view tbody tr")).map(function (tr) { var a = tr.querySelector("td a"); return a ? a.href : null; });
    var hintLinks = [].slice.call(document.querySelectorAll("#lockHint a")).map(function (a) { return a.href; });
    await go("timeline");
    var ok = rows.length === 3 &&
      rows[0] === "https://github.com/example-org/beta/blob/main/docs/plan.md" &&
      rows[1] === "https://github.com/example-org/gamma/blob/main/src/a%20b.cpp" &&
      rows[2] === null &&
      hintLinks.length === 2 && hintLinks[0] === rows[0] && hintLinks[1] === rows[1];
    return res("claimRowsLink", ok, "rows=" + JSON.stringify(rows) + " hint=" + JSON.stringify(hintLinks));
  });

  // ready-to-adopt is a known status: it sorts before landed-both and gets the in-progress pill.
  S.push(async function readyToAdopt() {
    await put("agreements", { "a-1": { t: T0, summary: "Done", status: "landed-both", builder: "gamma", adopter: "beta" },
      "a-2": { t: T0, summary: "Ready", status: "ready-to-adopt", builder: "gamma", adopter: "beta" } });
    await go("agreements");
    var rows = [].slice.call(document.querySelectorAll("#view tbody tr")).map(function (tr) { return tr.querySelector(".summary").textContent; });
    var pillEl = document.querySelector("#view .pill.ready-to-adopt"), bg = pillEl && getComputedStyle(pillEl).backgroundColor;
    await go("timeline");
    return res("readyToAdopt", rows[0] === "Ready" && bg === rgb("var(--accent-soft)"), "order=" + JSON.stringify(rows) + " bg=" + bg);
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
      var opens = !!$("who-gamma") && $("who-gamma").getClientRects().length > 0;
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
    await put("agreements", { "a-1": { t: T0, summary: "Old-style held", status: "held", builder: "beta", adopter: "gamma" } });
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
    var sel = "#composer, .star, .resolve, .resolve-inc, .reply-btn, .promote-btn, [id^=approve-], [id^=hold-], [id^=done-], #onboard-open";
    var controls = document.querySelectorAll(sel).length;
    var shown = $("viewer").textContent;
    // The Reviews tab shows an open override's positions without the "Go with" buttons.
    await addDoc("messages", "rd-ov", { t: T0, from: "relay-bot", kind: "disagreement", source: "codex-review", project: "beta", tip: "abc1234",
      status: "awaiting", summary: "Reader override", positions: { codex: "needs-attention", beta: "reason" } });
    await go("reviews");
    var rvControls = document.querySelectorAll(sel).length, rvShown = !!$("ov-rd-ov") && $("ov-rd-ov").querySelectorAll(".positions > div").length === 2;
    await go("timeline");
    return res("readerNoControls", controls === 0 && rvControls === 0 && rvShown && shown === "Viewing as a viewer (read only)",
      "controls=" + controls + " reviews=" + rvControls + "/" + rvShown + " shown=" + JSON.stringify(shown));
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
        $("onboard-parent").value = "beta";
        var harnessSel = $("onboard-harness");
        if (harnessSel) harnessSel.value = "hermes";
        __onboardResult = { token: "fake-token-123", kit: "kit for fake-token-123" };
        $("onboard-submit").click(); await tick(80);
        var kit = $("onboard-kit"), tok = $("onboard-token"), copy = $("onboard-copy");
        var kitOk = !!kit && kit.readOnly && kit.value === "kit for fake-token-123";
        var tokOk = !!tok && tok.readOnly && tok.value === "fake-token-123";
        var tokenFields = document.querySelectorAll("#onboard-token").length;
        var parentSent = window.__lastOnboardFields && window.__lastOnboardFields.parent === "beta";
        var colorSent = window.__lastOnboardFields && window.__lastOnboardFields.color === "#aa3366" && window.__lastOnboardFields.colorDark === "#dd88bb";
        var harnessSent = !!harnessSel && window.__lastOnboardFields && window.__lastOnboardFields.harness === "hermes";
        var ok = kitOk && tokOk && !!copy && tokenFields === 1 && parentHiddenInitially && parentShown && parentSent && colorSent && harnessSent;
        return res("ownerAddParticipant", ok,
          "kit=" + (kit && JSON.stringify(kit.value)) + " token=" + (tok && JSON.stringify(tok.value)) +
          " copy=" + !!copy + " tokenFields=" + tokenFields + " parentHiddenInitially=" + parentHiddenInitially +
          " parentShown=" + parentShown + " parentSent=" + parentSent + " colorSent=" + colorSent + " harnessSent=" + harnessSent);
      });

  // "Since your last visit" counts other people's messages that arrived while the page was closed: the rows newer
  // than the stored last visit when the page loads (reopenSince stands in for a reload). A first visit has nothing
  // new. Messages that arrive while the page is open and the owner's own don't count, opening the Timeline tab
  // doesn't clear it, Dismiss clears the box and the badge, and the stored time keeps up with what was shown.
  S.push(async function newSinceVisit() {
    var KEY = "crosstalk.lastVisit", C = window.__crosstalk;
    if (!C.reopenSince) return res("newSinceVisit", false, "no reopenSince hook");
    await go("agreements");
    var past = Date.now();
    while (Date.now() <= past) {}
    var nowIso = new Date().toISOString();
    await addDoc("messages", "new-1", { t: nowIso, from: "codex", kind: "sync", summary: "New one" });
    await addDoc("messages", "new-2", { t: nowIso, from: "codex", kind: "sync", summary: "New two" });
    await addDoc("messages", "new-own", { t: nowIso, from: "owner", kind: "owner", summary: "My own note" });
    C.reopenSince(null); await tick();
    var firstVisit = $("new-badge") === null;
    C.reopenSince(past); await tick();
    var badge = $("new-badge"), txt = badge ? badge.textContent : null;
    var t3 = Date.now();
    await addDoc("messages", "new-3", { t: new Date(t3).toISOString(), from: "codex", kind: "sync", summary: "Arrived while open" });
    var still = $("new-badge") ? $("new-badge").textContent : null;
    await go("timeline");
    var sum = $("digest-summary") ? $("digest-summary").textContent : "";
    var sumOk = /^2 messages \(Codex 2\)/.test(sum);
    var badgeAfterTab = $("new-badge") ? $("new-badge").textContent : null;
    if ($("digest-dismiss")) { $("digest-dismiss").click(); await tick(); }
    var cleared = $("digest") === null && $("new-badge") === null;
    var stored = Number(localStorage.getItem(KEY)), kept = stored >= t3;
    localStorage.setItem(KEY, "1"); window.dispatchEvent(new Event("pagehide"));
    var onHide = Number(localStorage.getItem(KEY)) >= t3;
    var ok = firstVisit && txt === "2 new" && still === "2 new" && sumOk && badgeAfterTab === "2 new" && cleared && kept && onHide;
    return res("newSinceVisit", ok, "firstVisit=" + firstVisit + " badge=" + JSON.stringify(txt) + " still=" + JSON.stringify(still) +
      " sum=" + JSON.stringify(sum) + " afterTab=" + JSON.stringify(badgeAfterTab) + " cleared=" + cleared + " kept=" + kept + " onHide=" + onHide);
  });

  // ---------- Help tab: open requests and their answers ----------
  function helpCards() { return [].slice.call(document.querySelectorAll("#view .card")); }
  function helpHas(summary) { return helpCards().some(function (c) { return c.textContent.indexOf(summary) !== -1; }); }
  function helpFrom() { return helpCards().map(function (c) { return c.dataset.from; }); }

  S.push(async function helpTabListed() {
    await go("help");
    await addDoc("messages", "hreq-1", { t: ago(1), from: "gamma", kind: "request", summary: "How do I land this?", to: ["beta"] });
    var listed = helpHas("How do I land this?");
    var badge = $("tab-help").querySelector(".n").textContent;
    await go("timeline");
    return res("helpTabListed", listed && badge === "1", "listed=" + listed + " badge=" + badge);
  });

  S.push(async function helpAnsweredDrops() {
    await go("help");
    await addDoc("messages", "hreq-2", { t: ago(1), from: "gamma", kind: "request", summary: "Still open?", to: ["beta"] });
    var before = helpHas("Still open?");
    await addDoc("messages", "hrep-2", { t: ago(1), from: "beta", kind: "reply", summary: "Yes", replyTo: "hreq-2" });
    var after = helpHas("Still open?");
    await go("timeline");
    return res("helpAnsweredDrops", before && !after, "before=" + before + " after=" + after);
  });

  S.push(async function helpToChips() {
    await go("help");
    await addDoc("messages", "hreq-3", { t: ago(1), from: "codex", kind: "request", summary: "Two recipients", to: ["beta", "gamma"] });
    await addDoc("messages", "hreq-4", { t: ago(1), from: "codex", kind: "request", summary: "Everyone" });
    function toText(summary) {
      var c = helpCards().filter(function (x) { return x.textContent.indexOf(summary) !== -1; })[0];
      var t = c && c.querySelector(".to");
      return t ? t.textContent : null;
    }
    var a = toText("Two recipients"), b = toText("Everyone");
    await go("timeline");
    return res("helpToChips", a === "to Beta · Gamma" && b === "everyone", "two=" + JSON.stringify(a) + " none=" + JSON.stringify(b));
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
    await addDoc("messages", "hreq-6", { t: ago(1), from: "beta", kind: "request", summary: "BET asks" });
    await addDoc("messages", "hreq-7", { t: ago(1), from: "gamma", kind: "request", summary: "GAM asks" });
    var b = $("who-beta");
    if (!b) return res("helpFilter", false, "no roster entry");
    b.click(); await tick();
    await go("help");
    var froms = helpFrom();
    var onlyBil = froms.length > 0 && froms.every(function (f) { return f === "beta"; });
    await go("timeline");
    $("who-beta").click(); await tick();
    return res("helpFilter", onlyBil, "froms=" + JSON.stringify(froms));
  });

  // A waiting row from a quarantined project is dimmed with a "not approved" pill;
  // an approved project's row stays undimmed.
  S.push(async function waitingQuarantineDimmed() {
    await go("waiting");
    await addDoc("participants", "wqbot", { name: "WQ bot", kind: "tool", color: "#555555" });
    await addDoc("waiting", "wq-1", { since: ago(1), project: "wqbot", summary: "From a quarantined project", done: null });
    await addDoc("waiting", "wq-2", { since: ago(1), project: "beta", summary: "From an approved project", done: null });
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
    await addDoc("messages", "m-0001", { t: ago(2), from: "beta", kind: "sync", summary: "Need owner input" });
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
    var ok = !!banner && /Beta/.test(bannerText) && /Need owner input/.test(bannerText) && !!cancelBtn &&
      canceledOk && !bannerAfter && !!posted && posted.replyTo === "m-0001" && toArr.indexOf("beta") !== -1;
    return res("ownerInlineReply", ok, "banner=" + JSON.stringify(bannerText) + " canceledOk=" + canceledOk + " bannerAfter=" + !!bannerAfter + " posted=" + JSON.stringify(posted));
  });

  S.push(async function ownerPromoteDecision() {
    await go("timeline");
    if (!(__store.messages && __store.messages["m-0001"])) {
      await addDoc("messages", "m-0001", { t: ago(2), from: "beta", kind: "sync", summary: "Need owner input" });
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
    await addDoc("messages", "hreq-multi", { t: ago(1), from: "codex", kind: "request", summary: "Multi-recipient question?", to: ["beta", "gamma"] });
    var initialListed = helpHas("Multi-recipient question?");
    await addDoc("messages", "hrep-multi-1", { t: ago(1), from: "beta", kind: "reply", summary: "BET answer", replyTo: "hreq-multi" });
    var c = helpCards().filter(function (x) { return x.textContent.indexOf("Multi-recipient question?") !== -1; })[0];
    var stillListed = !!c;
    var pendingPill = c && c.querySelector(".pill.pending-to");
    var waitingText = pendingPill ? pendingPill.textContent : "";
    var hasReply = !!c && c.textContent.indexOf("BET answer") !== -1;
    await addDoc("messages", "hrep-multi-2", { t: ago(1), from: "gamma", kind: "reply", summary: "GAM answer", replyTo: "hreq-multi" });
    var droppedAfterAll = !helpHas("Multi-recipient question?");
    await go("timeline");
    var ok = initialListed && stillListed && waitingText === "waiting on Gamma" && hasReply && droppedAfterAll;
    return res("helpPerRecipient", ok, "initial=" + initialListed + " still=" + stillListed + " waiting=" + JSON.stringify(waitingText) + " hasReply=" + hasReply + " dropped=" + droppedAfterAll);
  });

  // The text of a card's marks line as a copy gives it: each group, then "not yet", one space apart; "" when it has none.
  function marksText(c) {
    var line = c && c.querySelector(".marks-line");
    return line ? line.textContent : "";
  }
  // What the server puts on a marked row (marks.view): the page reads only this key.
  function marksOf(marks, owed, expects) { return { marks: marks, owed: owed || [], expects: !!expects }; }

  // The `none` marks that the server puts on a request (marks.page_views, from marks.marks_by_item) count as
  // replies from their writers, and the help card still waits on the other. The page reads only that key, not
  // the `acks` rows: a raw row the server did not count changes nothing. The `none` mark shows as "➖ name" in
  // the card's marks line, with no separate pill.
  S.push(async function helpAckedNoAction() {
    await go("help");
    var req = { t: ago(1), from: "codex", kind: "request", summary: "Ack me?", to: ["beta", "gamma"] };
    await addDoc("messages", "hreq-ack", Object.assign({}, req, { marks: marksOf({ gamma: "none" }) }));
    await addDoc("acks", "gamma--hreq-ack", { from: "gamma", item: "hreq-ack", none: true, mark: "none", t: ago(1) });
    // A raw row with no mark on the request in the server's data is not counted.
    await addDoc("acks", "beta--hreq-ack", { from: "beta", item: "hreq-ack", none: true, mark: "none", t: ago(1) });
    var c = helpCards().filter(function (x) { return x.textContent.indexOf("Ack me?") !== -1; })[0];
    var waiting = c && c.querySelector(".pill.pending-to");
    var line = marksText(c), pills = c ? c.querySelectorAll(".pill.acked").length : -1, waitingText = waiting ? waiting.textContent : "";
    // A mark other than `none` answers nothing.
    await addDoc("messages", "hreq-ack", Object.assign({}, req, { marks: marksOf({ gamma: "none", beta: "applies" }) }));
    var kept = helpHas("Ack me?");
    await addDoc("messages", "hreq-ack", Object.assign({}, req, { marks: marksOf({ gamma: "none", beta: "none" }) }));
    var dropped = !helpHas("Ack me?");
    // A request that names nobody: an ack clears no name, so it stays open.
    await addDoc("messages", "hreq-ack0", { t: ago(1), from: "codex", kind: "request", summary: "Ack nobody?", to: [],
      marks: marksOf({ gamma: "none" }) });
    var noneKept = helpHas("Ack nobody?");
    await go("timeline");
    var ok = line === "\u2796 Gamma" && pills === 0 && waitingText === "waiting on Beta" && kept && dropped && noneKept;
    return res("helpAckedNoAction", ok, "line=" + JSON.stringify(line) + " pills=" + pills + " waiting=" + JSON.stringify(waitingText) + " kept=" + kept + " dropped=" + dropped + " noneKept=" + noneKept);
  });

  // relay.py ack --none on request R, then --applies on lesson-R: the later mark wins (marks.marks_by_item), so the
  // server's marks of R hold `applies` and the Help tab waits on that agent again, as relay.py inbox does.
  S.push(async function helpLaterMarkThroughTheLesson() {
    await go("help");
    await addDoc("messages", "hreq-les", { t: ago(2), from: "codex", kind: "request", summary: "Marked through its lesson?", to: ["gamma"],
      marks: marksOf({ gamma: "applies" }) });
    await addDoc("lessons", "lesson-hreq-les", { t: ago(2), project: "codex", summary: "Marked through its lesson?" });
    await addDoc("acks", "gamma--hreq-les", { from: "gamma", item: "hreq-les", none: true, mark: "none", t: ago(2) });
    await addDoc("acks", "gamma--lesson-hreq-les", { from: "gamma", item: "lesson-hreq-les", mark: "applies", t: ago(1) });
    var waiting = helpWaiting("Marked through its lesson?");
    await go("timeline");
    var ok = waiting !== null;
    return res("helpLaterMarkThroughTheLesson", ok, "waiting=" + JSON.stringify(waiting));
  });

  // An ack never hides a request from the owner: once acks alone close it and it leaves Help, its timeline
  // card still names who marked it, in the marks line. No card has the old "acked, no action" pill.
  S.push(async function timelineAckedNoAction() {
    await go("help");
    await addDoc("messages", "treq-ack", { t: ago(1), from: "codex", kind: "request", summary: "Ack me on the timeline?", to: ["beta", "gamma"],
      marks: marksOf({ gamma: "none", beta: "none" }) });
    await addDoc("acks", "gamma--treq-ack", { from: "gamma", item: "treq-ack", none: true, t: ago(1) });
    await addDoc("acks", "beta--treq-ack", { from: "beta", item: "treq-ack", none: true, t: ago(1) });
    var dropped = !helpHas("Ack me on the timeline?");
    await go("timeline");
    var line = marksText(card("treq-ack")), pills = document.querySelectorAll(".pill.acked").length;
    var ok = dropped && line === "\u2796 Beta, Gamma" && pills === 0;
    return res("timelineAckedNoAction", ok, "dropped=" + dropped + " line=" + JSON.stringify(line) + " pills=" + pills);
  });

  // The marks line under the body of a card (spec "The pages"): marks that exist, then "not yet".
  S.push(async function marksLineLesson() {
    await go("timeline");
    await addDoc("messages", "mk-lesson", { t: ago(1), from: "relay-bot", kind: "lesson", summary: "A lesson with marks", body: "The rule.",
      marks: marksOf({ beta: "ack", alpha: "ack", gamma: "none" }, ["delta"], true) });
    var c = card("mk-lesson"), line = c && c.querySelector(".marks-line"), body = c && c.querySelector("p.body");
    var under = !!(line && body && (body.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING));
    var text = marksText(c);
    var ok = text === "\u2705 Alpha, Beta \u2796 Gamma not yet: Delta" && under;
    return res("marksLineLesson", ok, "line=" + JSON.stringify(text) + " underBody=" + under);
  });

  // A name with 👀 or ❓ shows with its mark and also in "not yet" (the server lists it in `owed`).
  S.push(async function marksLineSeenIsNotYet() {
    await go("timeline");
    await addDoc("messages", "mk-seen", { t: ago(1), from: "relay-bot", kind: "lesson", summary: "Seen but not acknowledged",
      marks: marksOf({ alpha: "seen", beta: "unclear", gamma: "applies" }, ["alpha", "beta", "delta"], true) });
    var text = marksText(card("mk-seen"));
    var want = "\ud83d\udd27 Gamma \ud83d\udc40 Alpha \u2753 Beta not yet: Alpha, Beta, Delta";
    return res("marksLineSeenIsNotYet", text === want, "line=" + JSON.stringify(text));
  });

  // No line on a post with no marks and no expectation, nor on a `marks` key of a wrong type.
  S.push(async function marksLineAbsent() {
    await go("timeline");
    await addDoc("messages", "mk-plain", { t: ago(1), from: "codex", kind: "check-in", summary: "A plain check-in" });
    await addDoc("messages", "mk-empty", { t: ago(1), from: "codex", kind: "sync", summary: "Empty marks", marks: marksOf({}, [], false) });
    await addDoc("messages", "mk-bad1", { t: ago(1), from: "codex", kind: "sync", summary: "Bad marks 1", marks: "ack" });
    await addDoc("messages", "mk-bad2", { t: ago(1), from: "codex", kind: "sync", summary: "Bad marks 2", marks: { marks: ["ack"], owed: "alpha", expects: "yes" } });
    await addDoc("messages", "mk-bad3", { t: ago(1), from: "codex", kind: "sync", summary: "Bad marks 3", marks: { marks: { alpha: "wave", beta: 7 }, owed: [3, null], expects: true } });
    // `owed` shows only when the post expects a mark.
    await addDoc("messages", "mk-noexp", { t: ago(1), from: "codex", kind: "sync", summary: "Owed, no expectation", marks: marksOf({ beta: "ack" }, ["alpha"], false) });
    var ids = ["mk-plain", "mk-empty", "mk-bad1", "mk-bad2", "mk-bad3"];
    var lines = ids.map(function (id) { var c = card(id); return c ? c.querySelectorAll(".marks-line").length : -1; });
    var noexp = marksText(card("mk-noexp"));
    var ok = lines.join() === "0,0,0,0,0" && noexp === "\u2705 Beta";
    return res("marksLineAbsent", ok, "lines=" + lines.join() + " noexp=" + JSON.stringify(noexp));
  });

  // Each emoji carries its word (marks.LABEL) as the hover title and the text for a screen reader, in the
  // order ✅ 🔧 ➖ 👀 ❓.
  S.push(async function marksLineLabels() {
    await go("timeline");
    await addDoc("messages", "mk-all", { t: ago(1), from: "relay-bot", kind: "lesson", summary: "Every mark",
      marks: marksOf({ alpha: "unclear", beta: "seen", gamma: "none", delta: "applies", codex: "ack" }, [], true) });
    var c = card("mk-all"), marks = c ? [].slice.call(c.querySelectorAll(".marks-line .mark")) : [];
    var got = marks.map(function (m) { return [m.textContent, m.title, m.getAttribute("aria-label"), m.getAttribute("role")].join("|"); });
    var want = ["\u2705|acknowledged|acknowledged|img", "\ud83d\udd27|applies|applies|img", "\u2796|no change|no change|img",
      "\ud83d\udc40|seen|seen|img", "\u2753|not clear|not clear|img"];
    return res("marksLineLabels", JSON.stringify(got) === JSON.stringify(want), JSON.stringify(got));
  });

  // A decision and a lesson row show the same marks line as a card, under their text; a row with no marks shows none.
  S.push(async function marksLineDecisionAndLesson() {
    function rowOf(summary) {
      return [].slice.call(document.querySelectorAll("#view tbody tr")).filter(function (tr) { return tr.textContent.indexOf(summary) !== -1; })[0];
    }
    await addDoc("decisions", "mk-dec", { t: ago(1), by: "owner", project: "all", summary: "A ruling with marks",
      marks: marksOf({ beta: "ack", gamma: "none" }, ["delta"], true) });
    await addDoc("decisions", "mk-dec-plain", { t: ago(1), by: "owner", project: "all", summary: "A ruling without marks" });
    await addDoc("lessons", "mk-les", { t: ago(1), project: "codex", summary: "A lesson row with marks", fix: "A tool",
      marks: marksOf({ alpha: "applies" }, ["beta"], true) });
    await go("decisions");
    var dec = marksText(rowOf("A ruling with marks")), plain = rowOf("A ruling without marks");
    var plainLines = plain ? plain.querySelectorAll(".marks-line").length : -1;
    await go("lessons");
    var les = marksText(rowOf("A lesson row with marks"));
    await go("timeline");
    var ok = dec === "\u2705 Beta \u2796 Gamma not yet: Delta" && plainLines === 0 && les === "\ud83d\udd27 Alpha not yet: Beta";
    return res("marksLineDecisionAndLesson", ok, "decision=" + JSON.stringify(dec) + " plainLines=" + plainLines + " lesson=" + JSON.stringify(les));
  });

  // Checks whichever width the window has (run again at 360 wide; see run.md): twelve names wrap inside the card.
  S.push(async function marksLineNarrow() {
    await go("timeline");
    var names = {}, owed = [];
    for (var i = 1; i <= 12; i++) { names["project-with-a-long-name-" + i] = "ack"; owed.push("other-project-with-a-long-name-" + i); }
    await addDoc("messages", "mk-wide", { t: ago(1), from: "relay-bot", kind: "lesson", summary: "Twelve names", marks: marksOf(names, owed, true) });
    var w = window.innerWidth, page = document.documentElement.scrollWidth, line = card("mk-wide") && card("mk-wide").querySelector(".marks-line");
    var fits = !!line && line.getBoundingClientRect().right <= w + 1;
    return res("marksLineNarrow", page <= w && fits, "width=" + w + " scrollWidth=" + page + " lineFits=" + fits);
  });

  // The Help tab follows relay.py inbox (requests.open_requests): who counts as having answered.
  function helpWaiting(summary) {
    var c = helpCards().filter(function (x) { return x.textContent.indexOf(summary) !== -1; })[0];
    var p = c && c.querySelector(".pill.pending-to");
    return c ? (p ? p.textContent : "") : null;
  }
  async function dropParticipant(id) { delete __store.participants[id]; __emit("participants"); await tick(); }

  // A quarantined recipient never holds a request open: it isn't waited on.
  S.push(async function helpQuarantinedRecipientIgnored() {
    await go("help");
    await addDoc("participants", "hq-recip", { name: "HQ recip", kind: "tool", color: "#555555" });
    await addDoc("messages", "hreq-qr", { t: ago(1), from: "codex", kind: "request", summary: "Quarantined recipient?", to: ["beta", "hq-recip"] });
    var before = helpWaiting("Quarantined recipient?");
    await addDoc("messages", "hrep-qr", { t: ago(1), from: "beta", kind: "reply", summary: "BET done", replyTo: "hreq-qr" });
    var after = helpWaiting("Quarantined recipient?");
    await dropParticipant("hq-recip");
    await go("timeline");
    return res("helpQuarantinedRecipientIgnored", before === "" && after === null, "before=" + JSON.stringify(before) + " after=" + JSON.stringify(after));
  });

  // @projects in the text means the approved projects, not a quarantined one.
  S.push(async function helpProjectsMentionApprovedOnly() {
    await go("help");
    await addDoc("participants", "hq-proj", { name: "HQ proj", kind: "project", color: "#555555" });
    await addDoc("messages", "hreq-pm", { t: ago(1), from: "codex", kind: "request", summary: "Projects mention?", body: "@projects please check" });
    var before = helpWaiting("Projects mention?");
    await addDoc("messages", "hrep-pm-1", { t: ago(1), from: "beta", kind: "reply", summary: "BET ok", replyTo: "hreq-pm" });
    await addDoc("messages", "hrep-pm-2", { t: ago(1), from: "gamma", kind: "reply", summary: "GAM ok", replyTo: "hreq-pm" });
    await addDoc("messages", "hrep-pm-3", { t: ago(1), from: "alpha", kind: "reply", summary: "ALP ok", replyTo: "hreq-pm" });
    await addDoc("messages", "hrep-pm-4", { t: ago(1), from: "relay-bot", kind: "reply", summary: "RB ok", replyTo: "hreq-pm" });
    await addDoc("messages", "hrep-pm-5", { t: ago(1), from: "delta", kind: "reply", summary: "DEL ok", replyTo: "hreq-pm" });
    await addDoc("messages", "hrep-pm-6", { t: ago(1), from: "epsilon-kit", kind: "reply", summary: "EPS ok", replyTo: "hreq-pm" });
    var after = helpWaiting("Projects mention?");
    await dropParticipant("hq-proj");
    await go("timeline");
    return res("helpProjectsMentionApprovedOnly", before === "" && after === null, "before=" + JSON.stringify(before) + " after=" + JSON.stringify(after));
  });

  // The owner replying to a project's multi-recipient request doesn't answer for the recipients.
  S.push(async function helpOwnerReplyNotForAll() {
    await go("help");
    await addDoc("messages", "hreq-or", { t: ago(1), from: "codex", kind: "request", summary: "Owner answers?", to: ["beta", "gamma"] });
    await addDoc("messages", "hrep-or", { t: ago(1), from: "owner", kind: "owner", summary: "My view", replyTo: "hreq-or" });
    var w = helpWaiting("Owner answers?");
    await go("timeline");
    return res("helpOwnerReplyNotForAll", w === "waiting on Beta · Gamma", "waiting=" + JSON.stringify(w));
  });

  // A sub-agent's reply doesn't count as its parent project answering.
  S.push(async function helpSubAgentReplyNotParent() {
    await go("help");
    await addDoc("participants", "hq-parent.helper", { name: "helper", kind: "agent", parent: "beta", color: "#8a4b12", trust: "approved" });
    await addDoc("messages", "hreq-sa", { t: ago(1), from: "codex", kind: "request", summary: "Sub-agent answers?", to: ["beta", "gamma"] });
    await addDoc("messages", "hrep-sa-1", { t: ago(1), from: "hq-parent.helper", kind: "reply", summary: "Helper view", replyTo: "hreq-sa" });
    await addDoc("messages", "hrep-sa-2", { t: ago(1), from: "gamma", kind: "reply", summary: "GAM view", replyTo: "hreq-sa" });
    var w = helpWaiting("Sub-agent answers?");
    await dropParticipant("hq-parent.helper");
    await go("timeline");
    return res("helpSubAgentReplyNotParent", w === "waiting on Beta", "waiting=" + JSON.stringify(w));
  });

  // A single-recipient request waits for that recipient or the owner, not a bystander.
  S.push(async function helpSingleRecipientBystander() {
    await go("help");
    await addDoc("messages", "hreq-sr", { t: ago(1), from: "gamma", kind: "request", summary: "Bystander answers?", to: ["beta"] });
    await addDoc("messages", "hrep-sr-1", { t: ago(1), from: "codex", kind: "reply", summary: "Codex view", replyTo: "hreq-sr" });
    var afterBystander = helpWaiting("Bystander answers?");
    await addDoc("messages", "hrep-sr-2", { t: ago(1), from: "owner", kind: "owner", summary: "Owner view", replyTo: "hreq-sr" });
    var afterOwner = helpWaiting("Bystander answers?");
    await go("timeline");
    return res("helpSingleRecipientBystander", afterBystander === "waiting on Beta" && afterOwner === null,
      "afterBystander=" + JSON.stringify(afterBystander) + " afterOwner=" + JSON.stringify(afterOwner));
  });

  S.push(async function ownerDigest() {
    await go("agreements");
    var past = Date.now();
    while (Date.now() <= past) {}
    var now = Date.now();
    var nowIso = new Date(now).toISOString();
    await addDoc("messages", "dig-m1", { t: nowIso, from: "codex", kind: "sync", summary: "Digest message" });
    await addDoc("progress", "dig-p1", { t: nowIso, project: "beta", game: "ridgeline", pct: 65 });
    window.__crosstalk.reopenSince(past);
    await go("timeline");
    var badgeBefore = $("new-badge") ? $("new-badge").textContent : null;
    var dig = $("digest"), sum = $("digest-summary"), sumText = sum ? sum.textContent : "";
    var dismiss = $("digest-dismiss");
    var visible = !!dig && /1 message \(Codex 1\)/.test(sumText) && /Ridgeline 65%/.test(sumText) && !!dismiss;
    if (dismiss) { dismiss.click(); await tick(); }
    var goneAfterDismiss = $("digest") === null && $("new-badge") === null;
    var ok = badgeBefore === "1 new" && visible && goneAfterDismiss;
    return res("ownerDigest", ok, "badgeBefore=" + JSON.stringify(badgeBefore) + " sum=" + JSON.stringify(sumText) + " goneAfterDismiss=" + goneAfterDismiss);
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
    await addDoc("messages", "hreq-dup", { t: ago(2), from: "codex", kind: "request", summary: "Dup recipient request?", to: ["beta", "beta"] });
    var tc = card("hreq-dup"), timelineTo = tc && tc.querySelector(".to") ? tc.querySelector(".to").textContent : null;
    await go("help");
    var hc = helpCards().filter(function (x) { return x.textContent.indexOf("Dup recipient request?") !== -1; })[0];
    var helpTo = hc && hc.querySelector(".to") ? hc.querySelector(".to").textContent : null;
    await addDoc("messages", "hrep-dup-q", { t: ago(1), from: "qreplybot", kind: "reply", summary: "Quarantined reply", replyTo: "hreq-dup" });
    var stillOpenAfterQuarantined = helpHas("Dup recipient request?");
    await addDoc("messages", "hrep-dup-ok", { t: ago(1), from: "beta", kind: "reply", summary: "Approved reply", replyTo: "hreq-dup" });
    var closedAfterApproved = !helpHas("Dup recipient request?");
    await go("timeline");
    var ok = timelineTo === "\u2192 Beta" && helpTo === "to Beta" && stillOpenAfterQuarantined && closedAfterApproved;
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

    await addDoc("messages", "m-inflight-dec", { t: ago(1), from: "beta", kind: "sync", summary: "Promote me" });
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
    await go("agreements");
    var past = Date.now();
    while (Date.now() <= past) {}
    var t1 = Date.now();
    var t1Iso = new Date(t1).toISOString();
    await addDoc("messages", "dig-anon", { t: t1Iso, from: "", kind: "sync", summary: "Blank sender" });
    await addDoc("progress", "dig-nan", { t: t1Iso, project: "beta", game: "juniper", pct: NaN });
    await addDoc("progress", "dig-inf", { t: t1Iso, project: "beta", game: "timber", pct: Infinity });
    await addDoc("progress", "dig-good", { t: t1Iso, project: "beta", game: "ridgeline", pct: 70 });
    window.__crosstalk.reopenSince(past);
    await go("timeline");
    var sum1 = $("digest-summary") ? $("digest-summary").textContent : "";
    var anonOk = /\(\? 1\)/.test(sum1);
    var finiteOk = /Ridgeline 70%/.test(sum1) && !/NaN|Infinity/.test(sum1);

    var t2 = Date.now() + 10;
    var t2Iso = new Date(t2).toISOString();
    await addDoc("progress", "dig-nogame", { t: t2Iso, project: "beta", game: "", pct: 80 });
    await addDoc("progress", "dig-nan2", { t: t2Iso, project: "beta", game: "juniper", pct: NaN });
    await go("timeline");
    var sum2 = $("digest-summary") ? $("digest-summary").textContent : "";
    var notWiped = /Ridgeline 70%/.test(sum2);

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
    await addDoc("messages", "m-lock-1", { t: ago(2), from: "beta", kind: "sync", summary: "First target" });
    await addDoc("messages", "m-lock-2", { t: ago(1), from: "gamma", kind: "sync", summary: "Second target" });
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
      !!vLink && vLink.href === "https://example.com/owner/crosstalk/commit/a39217d" &&
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
    var betChip = $("to-chip-beta"), everyChip = $("to-everyone"), projChip = $("to-projects");
    if (!betChip || !everyChip || !projChip) return res("composerToChipsAndMentions", false, "missing recipient chips");
    betChip.click(); await tick();
    var betPressed = $("to-chip-beta").getAttribute("aria-pressed") === "true" && $("to-everyone").getAttribute("aria-pressed") === "false";
    var ta = $("note");
    ta.value = "Syncing with @Gamma on shared audio";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    var labText = document.querySelector("#composer label").textContent;
    $("notePost").click(); await tick(80);
    var msgs = Object.keys(__store.messages || {}).map(function (id) { return Object.assign({ _id: id }, __store.messages[id]); });
    var posted1 = msgs.filter(function (m) { return m.summary === "Syncing with @Gamma on shared audio"; })[0];
    var toOk = !!posted1 && Array.isArray(posted1.to) && JSON.stringify(posted1.to) === JSON.stringify(["beta", "gamma"]);
    var c1 = posted1 ? card(posted1._id) : null;
    var mentionEl = c1 && c1.querySelector(".summary .mention");
    var mentionOk = !!mentionEl && mentionEl.textContent === "@Gamma";

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
    var ok = betPressed && /Beta/.test(labText) && /Gamma/.test(labText) && toOk && mentionOk && projPressed && everyReset && broadcastOk;
    return res("composerToChipsAndMentions", ok,
      "betPressed=" + betPressed + " lab=" + JSON.stringify(labText) + " to=" + JSON.stringify(posted1 && posted1.to) +
      " mentionOk=" + mentionOk + " projPressed=" + projPressed + " everyReset=" + everyReset + " broadcastOk=" + broadcastOk);
  });

  S.push(async function composerMentionMenu() {
    await go("timeline");
    var ta = $("note"), menu = $("mention-menu");
    if (!ta || !menu) return res("composerMentionMenu", false, "missing #note or #mention-menu");
    ta.focus();
    ta.value = "Hello @bet";
    ta.setSelectionRange(ta.value.length, ta.value.length);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    await tick();
    menu = $("mention-menu");
    var openOk = !menu.hidden && menu.querySelectorAll(".mention-item").length > 0;
    var item = menu.querySelector(".mention-item[data-handle='Beta']") || menu.querySelector(".mention-item");
    if (item) item.click();
    await tick();
    var valAfter = $("note").value;
    var closedAfter = $("mention-menu").hidden;
    $("note").value = "";
    $("note").dispatchEvent(new Event("input", { bubbles: true }));
    var ok = openOk && valAfter === "Hello @Beta " && closedAfter;
    return res("composerMentionMenu", ok, "openOk=" + openOk + " valAfter=" + JSON.stringify(valAfter) + " closedAfter=" + closedAfter);
  });

  S.push(async function composerAllMessageKinds() {
    await go("timeline");
    // 1. /ask slash command -> request (shows on Help tab and tracks @Beta)
    var ta = $("note");
    ta.value = "/ask @Beta Can we share the CRT shader?";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    var liveKindAsk = $("noteKind").value === "request";
    $("notePost").click(); await tick(80);
    await go("help");
    var onHelp = helpHas("Can we share the CRT shader?");
    await go("timeline");

    // 2. agreement -> writes agreements/a-<id>
    var k = $("noteKind"); k.value = "agreement"; k.dispatchEvent(new Event("change", { bubbles: true }));
    $("to-chip-gamma").click(); await tick();
    var subj = $("noteSubject"); subj.value = "Shared vector math header"; subj.dispatchEvent(new Event("input", { bubbles: true }));
    ta = $("note"); ta.value = "Both projects adopt vec2.h"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click(); await tick(80);
    var agrIds = Object.keys(__store.agreements || {}).filter(function (id) { return /^a-own-/.test(id) && __store.agreements[id].summary === "Shared vector math header"; });
    var agrOk = agrIds.length === 1 && __store.agreements[agrIds[0]].builder === "gamma" && __store.agreements[agrIds[0]].status === "agreed";

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
    subj = $("noteSubject"); subj.value = "Emulator window stole focus"; subj.dispatchEvent(new Event("input", { bubbles: true }));
    ta = $("note"); ta.value = "Headless flag missing in runner"; ta.dispatchEvent(new Event("input", { bubbles: true }));
    $("notePost").click(); await tick(80);
    var incIds = Object.keys(__store.incidents || {}).filter(function (id) { return /^i-own-/.test(id) && __store.incidents[id].summary === "Emulator window stole focus"; });
    var incId = incIds[0];
    var incCreated = !!incId && __store.incidents[incId].end === null;
    await go("incidents");
    var resBtn = incId ? $("resolve-inc-" + incId) : null;
    if (resBtn) { resBtn.click(); await tick(80); }
    var incResolved = !!incId && !!__store.incidents[incId].end && !$("resolve-inc-" + incId);
    await go("timeline");

    // Clean up open help request so later scenarios see clean state if needed
    var reqId = Object.keys(__store.messages || {}).filter(function (id) { return __store.messages[id].summary === "@Beta Can we share the CRT shader?"; })[0];
    if (reqId) await addDoc("messages", "rep-" + reqId, { t: ago(0), from: "beta", kind: "reply", summary: "Yes", replyTo: reqId });

    var ok = liveKindAsk && onHelp && agrOk && alertOk && lesOk && incCreated && !!resBtn && incResolved;
    return res("composerAllMessageKinds", ok,
      "liveKindAsk=" + liveKindAsk + " onHelp=" + onHelp + " agrOk=" + agrOk + " alertOk=" + alertOk +
      " lesOk=" + lesOk + " incCreated=" + incCreated + " resBtn=" + !!resBtn + " incResolved=" + incResolved);
  });

  // Aliases and item names come from the roster; a progress row may name its item in `item`.
  S.push(async function rosterAliasesAndItems() {
    var X = window.__crosstalk;
    var m = X.extractMentionsFromText("ping @alp and @GAM, not @zzz");
    var aliasOk = !m.broadcast && JSON.stringify(m.ids) === JSON.stringify(["alpha", "gamma"]);
    var nameOk = X.canonicalItemName("alpha", "docksidex") === "Dock Side" &&
      X.canonicalItemName("gamma", "docksidex") === "docksidex" && X.canonicalItemName("nobody", "x") === "x";
    var savedProg = Object.assign({}, __store.progress || {});
    await put("progress", { "it-1": { t: ago(10), project: "alpha", item: "maple", pct: 33 } });
    await go("timeline");
    var e = entry("alpha");
    var rprog = e && e.querySelector(".rprog") ? e.querySelector(".rprog").textContent : null;
    await put("progress", savedProg);
    await go("timeline");
    return res("rosterAliasesAndItems", aliasOk && nameOk && rprog === "Maple Grove 33%",
      "mentions=" + JSON.stringify(m) + " names=" + nameOk + " rprog=" + JSON.stringify(rprog));
  });

  S.push(async function canonicalGameProgress() {
    var savedProg = Object.assign({}, __store.progress || {});
    await put("progress", {
      "alp-1": { t: ago(30), project: "alpha", game: "cascad", pct: 0 },
      "alp-2": { t: ago(20), project: "alpha", game: "cascade", pct: 5 },
      "alp-3": { t: ago(10), project: "alpha", game: "Cascade", pct: 12 },
      "alp-4": { t: ago(25), project: "alpha", game: "docksidex", pct: 0 },
      "alp-5": { t: ago(10), project: "alpha", game: "Dock Side", pct: 8 },
      "alp-6": { t: ago(25), project: "alpha", game: "maple", pct: 0 },
      "alp-7": { t: ago(10), project: "alpha", game: "Maple Grove", pct: 15 }
    });
    await go("timeline");
    var alpEntry = entry("alpha");
    var rprog = alpEntry && alpEntry.querySelector(".rprog") ? alpEntry.querySelector(".rprog").textContent : null;
    var rosterOk = rprog === "Cascade 12% \u00b7 Dock Side 8% \u00b7 Maple Grove 15%";
    await go("progress");
    var paths = document.querySelectorAll("#view .pb-row").length;
    var legendText = document.querySelector("#view .pb-board") ? document.querySelector("#view .pb-board").textContent : "";
    var chartOk = paths === 3 &&
      legendText.indexOf("Cascade") !== -1 && legendText.indexOf("12%") !== -1 &&
      legendText.indexOf("Dock Side") !== -1 && legendText.indexOf("8%") !== -1 &&
      legendText.indexOf("Maple Grove") !== -1 && legendText.indexOf("15%") !== -1;
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
      owner: "beta",
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
    var agOk = agSum === "Legacy Agreement Title" && agBody === "Legacy agreement detail body" && agWho === "Beta" && agPill === "agreed";

    await addDoc("lessons", "leg-les-1", {
      t: T0,
      title: "Legacy Lesson Title",
      detail: "Legacy lesson detail body",
      from: "gamma",
      fix: "Prevented by pre-commit check"
    });
    await go("lessons");
    var lesRows = [].slice.call(document.querySelectorAll("#view tbody tr")).filter(function (tr) {
      return tr.textContent.indexOf("Legacy Lesson Title") !== -1;
    });
    var lesRow = lesRows[0];
    var lesSum = lesRow && lesRow.querySelector(".summary") ? lesRow.querySelector(".summary").textContent : "";
    var lesWho = lesRow && lesRow.children[2] && lesRow.children[2].querySelector(".who") ? lesRow.children[2].querySelector(".who").textContent : "";
    var lesOk = lesSum === "Legacy Lesson Title" && lesWho === "Gamma";
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
    await addDoc("messages", "own-bcast-rep-bet", {
      t: ago(1),
      from: "beta",
      kind: "sync",
      summary: "Beta is in sync",
      replyTo: "own-bcast-req"
    });
    await addDoc("messages", "own-bcast-rep-self", {
      t: ago(1),
      from: "owner",
      kind: "owner",
      summary: "Thanks, waiting on the others",
      replyTo: "own-bcast-req"
    });
    var c = helpCards().filter(function (x) { return x.textContent.indexOf("Are all the projects in sync?") !== -1; })[0];
    var stillOpenAfterOne = !!c;
    var pendingPill = c && c.querySelector(".pill.pending-to");
    var waitingText = pendingPill ? pendingPill.textContent : "";
    await addDoc("messages", "own-bcast-rep-gam", {
      t: ago(1),
      from: "gamma",
      kind: "sync",
      summary: "Gamma is in sync",
      replyTo: "own-bcast-req"
    });
    var stillOpenAfterTwo = helpHas("Are all the projects in sync?");
    await addDoc("messages", "own-bcast-rep-alp", {
      t: ago(1),
      from: "alpha",
      kind: "sync",
      summary: "Alpha is in sync",
      replyTo: "own-bcast-req"
    });
    await addDoc("messages", "own-bcast-rep-del", {
      t: ago(1),
      from: "delta",
      kind: "sync",
      summary: "Delta is in sync",
      replyTo: "own-bcast-req"
    });
    await addDoc("messages", "own-bcast-rep-eps", {
      t: ago(1),
      from: "epsilon-kit",
      kind: "sync",
      summary: "Epsilon Kit is in sync",
      replyTo: "own-bcast-req"
    });
    var openBeforeCoordinator = helpHas("Are all the projects in sync?");
    await addDoc("messages", "own-bcast-rep-rb", {
      t: ago(1),
      from: "relay-bot",
      kind: "sync",
      summary: "Relay Bot is in sync",
      replyTo: "own-bcast-req"
    });
    var closedAfterAll = !helpHas("Are all the projects in sync?");
    await go("timeline");
    var ok = listedInitially && stillOpenAfterOne && waitingText === "waiting on Alpha \u00b7 Delta \u00b7 Epsilon Kit \u00b7 Gamma \u00b7 Relay Bot" && stillOpenAfterTwo && openBeforeCoordinator && closedAfterAll;
    return res("ownerBroadcastRequestWaitsForAllProjects", ok,
      "initial=" + listedInitially + " afterOne=" + stillOpenAfterOne + " waiting=" + JSON.stringify(waitingText) +
      " afterTwo=" + stillOpenAfterTwo + " openBeforeCoordinator=" + openBeforeCoordinator + " closedAfterAll=" + closedAfterAll);
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
  // its thread stays closed; only a reply that arrives while the page is open (threadBump) opens it. The page is
  // reopened with a last visit just before hist-b (10 min old).
  S.push(async function threadSinceVisitClosed() {
    await go("timeline");
    window.__crosstalk.reopenSince(Date.now() - 12 * 60000); await tick();
    var c = topCard("hist-r"), mark = c && c.querySelector(".pill.new-reply");
    var closed = !threadBox("hist-r") && $("thread-toggle-hist-r").getAttribute("aria-expanded") === "false";
    var first = document.querySelector("#log > article"), firstId = first && first.dataset.id;
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
    await addDoc("messages", "th-new", { t: new Date().toISOString(), from: "gamma", kind: "sync", summary: "Fresh reply", replyTo: "th-old" });
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
      "th-ia": { t: ago(8), from: "gamma", kind: "alert", summary: "Alert in a thread", replyTo: "th-i" },
      "th-id": { t: ago(7), from: "beta", kind: "decision", summary: "Decision in a thread", replyTo: "th-i" },
      "th-ip": { t: ago(6), from: "beta", kind: "sync", summary: "Plain reply", replyTo: "th-i" }
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
      "th-c2": { t: ago(3), from: "gamma", kind: "sync", summary: "Cycle two", replyTo: "th-c1" },
      "th-c3": { t: ago(3), from: "beta", kind: "sync", summary: "Into the cycle", replyTo: "th-c1" }
    });
    __emit("messages"); await tick();
    var chainFoot = footText("th-L0"), chainTop = document.querySelectorAll('#log > article[data-id^="th-L"]').length;
    var a = topCard("th-c1"), b = topCard("th-c2"), c = topCard("th-c3");
    var lines = [a, b, c].map(function (x) { var r = x && x.querySelector(".re"); return r ? r.textContent : null; });
    var ok = !!a && !!b && !!c && lines[0] === "\u21a9 Replying to Gamma: Cycle two" && lines[1] === "\u21a9 Replying to Codex: Cycle one" &&
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

  // The roster badge for a project with a live session: "live" when the last message was told, a warning when it failed, nothing otherwise.
  S.push(async function liveBadges() {
    await go("timeline");
    var since = new Date(Date.now() - 120000).toISOString();
    __setLive({ gamma: { sessions: 1, since: since, last: { status: "told", at: since } },
      beta: { sessions: 1, since: since, last: { status: "failed", at: since } } }); await tick();
    var bad = [], v = $("live-gamma"), w = $("live-beta"), a = $("live-alpha");
    if (!v) bad.push("gamma missing");
    else {
      if (!v.classList.contains("live-badge")) bad.push("gamma no live-badge class");
      if (v.textContent !== "live") bad.push("gamma text=" + v.textContent);
      if (v.classList.contains("live-badge-failed")) bad.push("gamma has live-badge-failed");
      if (!$("who-gamma").contains(v)) bad.push("gamma badge outside its row");
    }
    if (!w) bad.push("beta missing");
    else {
      if (w.textContent !== "couldn't reach session") bad.push("beta text=" + w.textContent);
      if (!w.classList.contains("live-badge-failed")) bad.push("beta no live-badge-failed class");
      if (!w.title) bad.push("beta no title");
    }
    if (!$("who-alpha")) bad.push("alpha row missing");
    if (a) bad.push("alpha has badge");
    var fs = getComputedStyle($("live")).fontSize, tv = getComputedStyle($("live").closest(".val")).fontSize;
    if (fs === "11px" || fs !== tv) bad.push("feed #live font-size=" + fs + " tile val=" + tv);
    var wake = document.querySelectorAll('[id^="wake-"]').length;
    if (wake) bad.push(wake + " wake- elements");
    __setLive({}); await tick();
    var left = document.querySelectorAll('[id^="live-"]').length;
    if (left) bad.push(left + " badges left after clear");
    return res("liveBadges", !bad.length, bad.join("; "));
  });

  // ---------- Timeline filters: several people, sent by/to, and the Show toggles ----------
  function shownIds() { return [].slice.call(document.querySelectorAll("#log > article")).map(function (a) { return a.dataset.id; }); }
  function shows(id) { return shownIds().indexOf(id) !== -1; }
  function pressed(id) { var b = $(id); return !!b && b.getAttribute("aria-pressed") === "true"; }
  function whoNote() { var c = $("who-clear"), n = c && c.closest(".whonote"); return n ? n.querySelector("span").textContent : null; }
  async function press(id) { $(id).click(); await tick(); }
  async function clearFilters() { if ($("who-clear")) await press("who-clear"); if (!pressed("who-mode-from") && $("who-mode-from")) await press("who-mode-from"); }

  // Each click on a roster name adds or removes it; the note names everyone picked; Clear drops them all.
  S.push(async function filterSeveralPeople() {
    await go("timeline");
    await addDoc("messages", "fp-bet", { t: ago(2), from: "beta", kind: "sync", summary: "FP from BET" });
    await addDoc("messages", "fp-gam", { t: ago(2), from: "gamma", kind: "sync", summary: "FP from GAM" });
    await addDoc("messages", "fp-alp", { t: ago(2), from: "alpha", kind: "sync", summary: "FP from ALP" });
    await press("who-beta"); await press("who-gamma");
    var both = pressed("who-beta") && pressed("who-gamma");
    // Anyone else's card is a thread shown for a picked person's reply (an earlier scenario may have closed it by hand).
    function picks(f) { return f === "beta" || f === "beta.verifier" || f === "gamma"; }
    var all = Object.keys(__store.messages).map(function (k) { return __store.messages[k]; });
    var cards = [].slice.call(document.querySelectorAll("#log > article.msg"));
    var onlyTwo = cards.length > 0 && cards.every(function (a) {
      if (picks(a.dataset.from)) return true;
      return !!$("thread-toggle-" + a.dataset.id) && all.some(function (m) { return m.replyTo === a.dataset.id && picks(m.from); });
    });
    var picked = shows("fp-bet") && shows("fp-gam") && !shows("fp-alp");
    var note = whoNote();
    await press("who-gamma");
    var dropped = !pressed("who-gamma") && pressed("who-beta") && !shows("fp-gam") && shows("fp-bet");
    await press("who-gamma");
    await press("who-clear");
    var cleared = !pressed("who-beta") && !pressed("who-gamma") && shows("fp-alp") && !$("who-clear");
    var ok = both && onlyTwo && picked && note === "Showing messages sent by Beta, Gamma." && dropped && cleared;
    return res("filterSeveralPeople", ok, "both=" + both + " onlyTwo=" + onlyTwo + " picked=" + picked + " note=" + JSON.stringify(note) +
      " dropped=" + dropped + " cleared=" + cleared);
  });

  // Picking a project brings its sub-agents; picking a sub-agent alone shows only it.
  S.push(async function filterIncludesSubAgents() {
    await go("timeline");
    await addDoc("messages", "fs-bet", { t: ago(2), from: "beta", kind: "sync", summary: "FS from BET" });
    await addDoc("messages", "fs-ver", { t: ago(2), from: "beta.verifier", kind: "sync", summary: "FS from verifier" });
    await press("who-beta");
    var withKid = shows("fs-bet") && shows("fs-ver");
    await press("who-beta"); await press("who-beta.verifier");
    var kidOnly = shows("fs-ver") && !shows("fs-bet");
    await clearFilters();
    return res("filterIncludesSubAgents", withKid && kidOnly, "withKid=" + withKid + " kidOnly=" + kidOnly);
  });

  // Sent to: the message's `to` list or an @mention; a bare broadcast is sent to nobody in particular.
  // Either: sent by or to. The Help tab follows the same people filter.
  S.push(async function filterSentTo() {
    await go("timeline");
    await addDoc("messages", "ft-to", { t: ago(2), from: "gamma", kind: "request", to: ["codex"], summary: "FT addressed to Codex" });
    await addDoc("messages", "ft-at", { t: ago(2), from: "beta", kind: "sync", summary: "FT mention", body: "Thanks @codex for the review" });
    await addDoc("messages", "ft-by", { t: ago(2), from: "codex", kind: "sync", summary: "FT from Codex" });
    await addDoc("messages", "ft-all", { t: ago(2), from: "gamma", kind: "sync", summary: "FT broadcast" });
    var hasSwitch = !!$("who-mode-from") && !!$("who-mode-to") && !!$("who-mode-either") && pressed("who-mode-from");
    await press("who-codex");
    var by = shows("ft-by") && !shows("ft-to") && !shows("ft-at");
    await press("who-mode-to");
    var to = shows("ft-to") && shows("ft-at") && !shows("ft-by") && !shows("ft-all") && pressed("who-mode-to") && pressed("who-codex");
    var toNote = whoNote();
    await go("help");
    var helpTo = helpHas("FT addressed to Codex");
    await go("timeline");
    await press("who-mode-either");
    var either = shows("ft-to") && shows("ft-at") && shows("ft-by") && !shows("ft-all");
    var eitherNote = whoNote();
    await clearFilters();
    var ok = hasSwitch && by && to && toNote === "Showing messages sent to Codex." && helpTo && either && eitherNote === "Showing messages sent by or to Codex.";
    return res("filterSentTo", ok, "switch=" + hasSwitch + " by=" + by + " to=" + to + " toNote=" + JSON.stringify(toNote) + " helpTo=" + helpTo +
      " either=" + either + " eitherNote=" + JSON.stringify(eitherNote));
  });

  // Has replies: only threads with at least one reply.
  S.push(async function filterHasReplies() {
    await go("timeline");
    await addDoc("messages", "fr-root", { t: ago(3), from: "gamma", kind: "sync", summary: "FR root" });
    await addDoc("messages", "fr-reply", { t: ago(2), from: "beta", kind: "reply", replyTo: "fr-root", summary: "FR reply" });
    await addDoc("messages", "fr-lone", { t: ago(2), from: "gamma", kind: "sync", summary: "FR lone" });
    var off = !pressed("f-replies") && shows("fr-lone");
    await press("f-replies");
    var ids = shownIds(), allThreads = ids.length > 0 && ids.every(function (id) { return !!$("thread-toggle-" + id); });
    var on = pressed("f-replies") && shows("fr-root") && !shows("fr-lone") && allThreads;
    var note = whoNote();
    await press("f-replies");
    var back = !pressed("f-replies") && shows("fr-lone");
    var ok = off && on && note === "Showing threads with replies." && back;
    return res("filterHasReplies", ok, "off=" + off + " on=" + on + " allThreads=" + allThreads + " note=" + JSON.stringify(note) + " back=" + back);
  });

  // Unanswered: a request still waiting on someone, by the Help tab's rule.
  S.push(async function filterUnanswered() {
    await go("timeline");
    await addDoc("messages", "fu-open", { t: ago(3), from: "gamma", kind: "request", to: ["beta"], summary: "FU still open" });
    await addDoc("messages", "fu-done", { t: ago(3), from: "gamma", kind: "request", to: ["beta"], summary: "FU answered" });
    await addDoc("messages", "fu-ans", { t: ago(2), from: "beta", kind: "reply", replyTo: "fu-done", summary: "FU the answer" });
    await addDoc("messages", "fu-note", { t: ago(2), from: "gamma", kind: "sync", summary: "FU plain sync" });
    await press("f-open");
    var on = pressed("f-open") && shows("fu-open") && !shows("fu-done") && !shows("fu-note");
    var note = whoNote();
    await press("f-replies");
    var combined = !shows("fu-open") && !shows("fu-done");
    await press("f-replies"); await press("f-open");
    var back = shows("fu-note") && shows("fu-done");
    var ok = on && note === "Showing questions still waiting on someone." && combined && back;
    return res("filterUnanswered", ok, "on=" + on + " note=" + JSON.stringify(note) + " combined=" + combined + " back=" + back);
  });

  // A request with `needs_answer: false`, or whose body ends with "No answer needed", waits for nobody
  // (requests.needs_answer): Unanswered leaves it out; the sentence in the middle of a body does not count.
  S.push(async function filterUnansweredNoAnswer() {
    await go("timeline");
    await addDoc("messages", "fx-field", { t: ago(3), from: "gamma", kind: "request", to: ["beta"], needs_answer: false, summary: "FX field" });
    await addDoc("messages", "fx-sentence", { t: ago(3), from: "gamma", kind: "request", to: ["beta"], summary: "FX sentence", body: "Landed.\nNo answer needed.  " });
    await addDoc("messages", "fx-middle", { t: ago(3), from: "gamma", kind: "request", to: ["beta"], summary: "FX middle", body: "He wrote No answer needed, but why?" });
    await press("f-open");
    var ok = shows("fx-middle") && !shows("fx-field") && !shows("fx-sentence");
    await press("f-open");
    var back = shows("fx-field") && shows("fx-sentence");
    return res("filterUnansweredNoAnswer", ok && back, "open=" + ok + " back=" + back);
  });

  // A TURN ASK always needs an answer (requests.needs_answer): Unanswered keeps it, whatever its body ends with.
  S.push(async function filterUnansweredTurnAsk() {
    await go("timeline");
    await addDoc("messages", "fx-turn", { t: ago(3), from: "gamma", kind: "request", to: ["beta"], needs_answer: false, summary: "TURN ASK gamma fx-0101", body: "Ready. No answer needed." });
    // A withdrawal informs (landqueue.is_withdrawal): the usual rules apply, so this one needs no answer.
    await addDoc("messages", "fx-turn-wd", { t: ago(3), from: "gamma", kind: "request", to: ["beta"], summary: "TURN ASK WITHDRAWN gamma fx-0101", body: "Dropped. No answer needed." });
    await press("f-open");
    var ok = shows("fx-turn") && !shows("fx-turn-wd");
    await press("f-open");
    return res("filterUnansweredTurnAsk", ok, "open=" + ok);
  });

  // A question posted as a reply counts for Unanswered, as it does on the Help tab, and opens its thread to it.
  // A reply sent to someone counts as sent to them; a starred reply opens its thread under Starred.
  S.push(async function filterNestedMatches() {
    await go("timeline");
    await addDoc("messages", "fn-root", { t: ago(4), from: "gamma", kind: "sync", summary: "FN plain root" });
    await addDoc("messages", "fn-ask", { t: ago(3), from: "owner", kind: "request", to: ["beta"], replyTo: "fn-root", summary: "FN question in a thread" });
    await addDoc("messages", "fn-root2", { t: ago(4), from: "alpha", kind: "sync", summary: "FN second root" });
    await addDoc("messages", "fn-rep2", { t: ago(3), from: "codex", kind: "reply", to: ["alpha"], replyTo: "fn-root2", important: true, summary: "FN starred reply" });
    await go("help");
    var inHelp = helpHas("FN question in a thread");
    await go("timeline");
    await press("f-open");
    var open = shows("fn-root") && JSON.stringify(threadIds("fn-root")) === JSON.stringify(["fn-ask"]);
    await press("f-open");
    await press("f-starred");
    var starred = shows("fn-root2") && JSON.stringify(threadIds("fn-root2")) === JSON.stringify(["fn-rep2"]);
    await press("f-starred");
    await press("who-mode-to"); await press("who-alpha");
    var sentTo = shows("fn-root2");
    await clearFilters();
    var ok = inHelp && open && starred && sentTo;
    return res("filterNestedMatches", ok, "inHelp=" + inHelp + " open=" + open + " starred=" + starred + " sentTo=" + sentTo);
  });

  // Starred: only messages marked important by hand, not alerts that are important by kind.
  S.push(async function filterStarred() {
    await go("timeline");
    await addDoc("messages", "fst-star", { t: ago(2), from: "gamma", kind: "sync", important: true, summary: "FST starred" });
    await addDoc("messages", "fst-alert", { t: ago(2), from: "gamma", kind: "alert", summary: "FST alert" });
    await press("f-starred");
    var on = pressed("f-starred") && shows("fst-star") && !shows("fst-alert");
    await press("who-alpha");
    var note = whoNote(), both = !shows("fst-star");
    await press("who-clear");
    var cleared = !pressed("f-starred") && !pressed("who-alpha") && shows("fst-alert");
    var ok = on && note === "Showing starred messages sent by Alpha." && both && cleared;
    return res("filterStarred", ok, "on=" + on + " note=" + JSON.stringify(note) + " both=" + both + " cleared=" + cleared);
  });

  // ---------- Reviews tab: Codex branch reviews and overrides (scripts/crosstalk/reviews.py) ----------
  function rvDoc(project, tip, verdict, min) {
    return { t: ago(min), from: "relay-bot", kind: "review", source: "codex-review", project: project, tip: tip, tier: "full",
      verdict: verdict, findings: verdict === "approve" ? 0 : 2, summary: "RV review of " + tip.slice(0, 7), refs: [project + "@" + tip.slice(0, 7)] };
  }
  function ovDoc(project, tip, min, extra) {
    return Object.assign({ t: ago(min), from: "relay-bot", kind: "disagreement", source: "codex-review", project: project, tip: tip,
      status: "awaiting", findings: 2, headlines: ["[medium] RV first headline (src/a.rs:1)"], reason: "RV reason " + tip.slice(0, 7),
      summary: "RV override of " + tip.slice(0, 7), positions: { codex: "needs-attention, 2 findings", beta: "RV reason" } }, extra || {});
  }
  async function seedReviews() {
    var m = __store.messages = __store.messages || {};
    for (var i = 0; i < 12; i++) m["rv-b" + i] = rvDoc("beta", ("b" + i + "000000000").slice(0, 7) + "c0ffee", i % 2 ? "needs-attention" : "approve", 30 + i);
    m["rv-v0"] = rvDoc("gamma", "0a0a0a0deadbeef", "approve", 40);
    m["rv-v1"] = rvDoc("gamma", "1a1a1a1deadbeef", "needs-attention", 41);
    m["rv-x"] = Object.assign(rvDoc("beta", "ffff000deadbeef", "approve", 5), { from: "codex", summary: "RV not from the coordinator" });
    m["ov-await"] = ovDoc("beta", "b1000000c0ffee", 20, { reviewer: "gamma", review: "rv-b1" });
    m["ov-obj"] = ovDoc("gamma", "1a1a1a1deadbeef", 25, { status: "objected", reviewer: "alpha",
      objections: [{ id: "alp-9", from: "alpha", summary: "Object: RV real bug" }],
      positions: { codex: "needs-attention", gamma: "RV reason" } });
    m["ov-agreed"] = ovDoc("beta", "b3000000c0ffee", 22, { status: "agreed", agreed: ["alpha"] });
    m["ov-settled"] = ovDoc("beta", "b5000000c0ffee", 23, { resolution: { choice: "codex", by: "owner", t: ago(10) } });
    __emit("messages"); await tick();
  }
  function rvIds(sel) { return [].slice.call(document.querySelectorAll(sel)).map(function (e) { return e.id; }); }

  S.push(async function reviewsTab() {
    await seedReviews();
    await go("reviews");
    var badge = $("tab-reviews") && $("tab-reviews").querySelector(".n").textContent;
    var open = rvIds("#rv-open > .override");
    var obj = $("ov-ov-obj"), aw = $("ov-ov-await");
    var objText = obj ? obj.textContent : "", awText = aw ? aw.textContent : "";
    var objOk = /Object: RV real bug/.test(objText) && !!obj.querySelector(".pill.objected");
    var awOk = /RV first headline/.test(awText) && /Reason: RV reason/.test(awText) && /asked/.test(awText) && aw.querySelectorAll(".positions .resolve").length === 2;
    var settled = $("rv-settled"), settledText = settled ? settled.querySelector("summary").textContent : null;
    var bet = rvIds("#rv-beta tbody tr"), gam = rvIds("#rv-gamma tbody tr");
    var betHead = $("rv-beta") ? $("rv-beta").querySelector(".rvtitle").textContent : "";
    var over = $("rvrow-rv-b1") && !!$("rvrow-rv-b1").querySelector(".pill.overridden"), notOver = $("rvrow-rv-b0") && !$("rvrow-rv-b0").querySelector(".pill.overridden");
    var verdicts = $("rvrow-rv-b0") && !!$("rvrow-rv-b0").querySelector(".pill.approve") && !!$("rvrow-rv-b1").querySelector(".pill.needs-attention");
    var noForeign = !$("rvrow-rv-x");
    var order = $("rv-open").compareDocumentPosition($("rv-beta")) & Node.DOCUMENT_POSITION_FOLLOWING;
    await go("timeline");
    var ok = badge === "2" && JSON.stringify(open) === JSON.stringify(["ov-ov-obj", "ov-ov-await"]) && objOk && awOk && settledText === "2 settled" &&
      bet.length === 10 && bet[0] === "rvrow-rv-b0" && /latest 10 of 12/.test(betHead) && gam.length === 2 && gam[0] === "rvrow-rv-v0" &&
      over && notOver && verdicts && noForeign && !!order;
    return res("reviewsTab", ok, "badge=" + badge + " open=" + JSON.stringify(open) + " obj=" + objOk + " await=" + awOk + " settled=" + JSON.stringify(settledText) +
      " bet=" + bet.length + "/" + bet[0] + " head=" + JSON.stringify(betHead) + " gam=" + JSON.stringify(gam) + " overridden=" + over + "/" + notOver +
      " verdicts=" + verdicts + " noForeign=" + noForeign + " order=" + !!order);
  });

  S.push(async function reviewsSettle() {
    await go("reviews");
    var b = $("resolve-ov-await-beta");
    if (b) b.click();
    await tick(80);
    var r = __store.messages["ov-await"].resolution;
    var open = rvIds("#rv-open > .override"), badge = $("tab-reviews").querySelector(".n").textContent;
    var settled = $("rv-settled") ? $("rv-settled").querySelector("summary").textContent : null;
    await go("timeline");
    var ok = !!b && r && r.choice === "beta" && r.by === "owner" && JSON.stringify(open) === JSON.stringify(["ov-ov-obj"]) && badge === "1" && settled === "3 settled";
    return res("reviewsSettle", ok, "button=" + !!b + " resolution=" + JSON.stringify(r) + " open=" + JSON.stringify(open) + " badge=" + badge + " settled=" + JSON.stringify(settled));
  });

  // ---------- Questions for the owner ----------
  // Three open questions (oldest first: Beta, Gamma, Alpha), one answered, one withdrawn
  // and one from a sender outside the roster, which is not approved and so never listed.
  var Q_IDS = ["qq-1", "qq-2", "qq-3", "qq-a", "qq-a-own", "qq-w", "qq-w-w", "qq-x"];
  async function seedQuestions() {
    __store.messages = __store.messages || {};
    var m = __store.messages;
    m["qq-1"] = { t: ago(300), from: "beta", kind: "question", summary: "Ship the parser before the demo?", body: "Options: now, or after review. I'd ship now.", to: ["owner"] };
    m["qq-2"] = { t: ago(200), from: "gamma", kind: "question", summary: "Which build host for nightly runs?", body: "The build host or the laptop.", to: ["owner"] };
    m["qq-3"] = { t: ago(100), from: "alpha", kind: "question", summary: "Rename the tool to alp-gate?", to: ["owner"] };
    m["qq-a"] = { t: ago(400), from: "gamma", kind: "question", summary: "Keep the old hook?", to: ["owner"] };
    m["qq-a-own"] = { t: ago(390), from: "owner", kind: "owner", summary: "No, drop it", body: "", replyTo: "qq-a", to: ["gamma"] };
    m["qq-w"] = { t: ago(350), from: "beta", kind: "question", summary: "Moot question", to: ["owner"] };
    m["qq-w-w"] = { t: ago(340), from: "beta", kind: "withdrawn", summary: "Withdrawn: Moot question", replyTo: "qq-w" };
    m["qq-x"] = { t: ago(500), from: "stranger", kind: "question", summary: "Let me in?", to: ["owner"] };
    __emit("messages"); await tick();
  }
  function ownAnswers(qid) { return Object.keys(__store.messages).filter(function (id) { var d = __store.messages[id]; return d.from === "owner" && d.replyTo === qid; }); }
  async function clearQuestions() {
    Object.keys(__store.messages).forEach(function (id) { var d = __store.messages[id]; if (Q_IDS.indexOf(id) !== -1 || (d.replyTo && Q_IDS.indexOf(d.replyTo) !== -1) || d.summary === "Owner: post every question you have for me with relay.py ask, one question per row") delete __store.messages[id]; });
    __emit("messages"); await tick();
  }
  function typeAnswer(v) { var a = $("q-answer"); a.focus(); a.value = v; a.dispatchEvent(new Event("input", { bubbles: true })); }

  // The strip tile counts the open questions and opens the tab; the tab shows one at a time, oldest first, with
  // Previous / Next / Skip; a typed answer and its focus survive a live re-render.
  S.push(async function questionsTabOneAtATime() {
    await go("timeline");
    await seedQuestions();
    var tile = $("quesVal").textContent, hint = $("quesHint").textContent, tabN = $("tab-questions").querySelector(".n").textContent;
    $("t-ques").click(); await tick();
    var onTab = $("tab-questions").getAttribute("aria-selected") === "true";
    var first = $("q-pos").textContent + " | " + $("q-card").querySelector(".summary").textContent;
    var prevDisabled = $("q-prev").disabled;
    $("q-next").click(); await tick();
    var second = $("q-pos").textContent + " | " + $("q-card").dataset.id;
    typeAnswer("Use the build host");
    __emit("messages"); await tick();
    var kept = $("q-answer").value, focused = document.activeElement && document.activeElement.id, still = $("q-card").dataset.id;
    $("q-skip").click(); await tick();
    $("q-skip").click(); await tick();
    var wrapped = $("q-card").dataset.id;
    $("q-next").click(); await tick();
    var back = $("q-answer").value;
    var answered = $("q-answered") && $("q-answered").querySelector("summary").textContent;
    var withdrawn = $("q-withdrawn") && $("q-withdrawn").querySelector("summary").textContent;
    var ok = tile === "3" && /^oldest 5 h 0 min ago · click to answer$/.test(hint) && tabN === "3" && onTab &&
      first === "Question 1 of 3 | Ship the parser before the demo?" && prevDisabled &&
      second === "Question 2 of 3 | qq-2" && kept === "Use the build host" && focused === "q-answer" && still === "qq-2" &&
      wrapped === "qq-1" && back === "Use the build host" && answered === "1 answered" && withdrawn === "1 withdrawn";
    return res("questionsTabOneAtATime", ok, "tile=" + tile + " hint=" + JSON.stringify(hint) + " tab=" + tabN + " onTab=" + onTab + " first=" + JSON.stringify(first) +
      " second=" + JSON.stringify(second) + " kept=" + JSON.stringify(kept) + " focus=" + focused + " still=" + still + " wrapped=" + wrapped + " back=" + JSON.stringify(back) +
      " answered=" + JSON.stringify(answered) + " withdrawn=" + JSON.stringify(withdrawn));
  });

  // Send answer posts an owner row replying to the question and addressed to its asker; a failed send retried
  // rewrites the same row; then the tab moves to the next open question.
  S.push(async function questionsAnswerPosts() {
    if (!__store.messages["qq-2"]) await seedQuestions();
    await go("questions");
    if ($("q-card").dataset.id !== "qq-2") { $("q-next").click(); await tick(); }
    typeAnswer("Use the build host\nThe laptop sleeps.");
    __fail.messages = true;
    $("q-send").click(); await tick(80);
    var st1 = $("q-status").textContent, n1 = ownAnswers("qq-2").length;
    __fail.messages = false;
    $("q-send").click(); await tick(80);
    var ids = ownAnswers("qq-2"), row = ids.length ? __store.messages[ids[0]] : null;
    var payloadOk = !!row && row.from === "owner" && row.kind === "owner" && row.summary === "Use the build host" && row.body === "The laptop sleeps." &&
      row.replyTo === "qq-2" && JSON.stringify(row.to) === "[\"gamma\"]" && !isNaN(Date.parse(row.t)) && /^own-\d{14}-[a-z0-9]{1,4}$/.test(ids[0]);
    var after = $("q-pos").textContent + " | " + $("q-card").dataset.id, st2 = $("q-status").textContent;
    var tile = $("quesVal").textContent, answered = $("q-answered").querySelector("summary").textContent;
    var ok = /^Not sent/.test(st1) && n1 === 0 && ids.length === 1 && payloadOk && after === "Question 2 of 2 | qq-3" &&
      st2 === "Answer sent to Gamma." && tile === "2" && answered === "2 answered";
    return res("questionsAnswerPosts", ok, "first=" + JSON.stringify(st1) + " rows=" + n1 + "->" + ids.length + " row=" + JSON.stringify(row) + " id=" + ids[0] +
      " after=" + JSON.stringify(after) + " status=" + JSON.stringify(st2) + " tile=" + tile + " answered=" + JSON.stringify(answered));
  });

  // Cmd/Ctrl+Enter sends; an empty answer is refused with a note.
  S.push(async function questionsCtrlEnter() {
    if (!__store.messages["qq-3"]) await seedQuestions();
    await go("questions");
    var cur = $("q-card").dataset.id;
    $("q-send").click(); await tick();
    var empty = $("q-status").textContent;
    typeAnswer("Yes, rename it");
    $("q-answer").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true })); await tick(80);
    var ids = ownAnswers(cur);
    var ok = empty === "Write an answer first." && ids.length === 1 && __store.messages[ids[0]].summary === "Yes, rename it" && __store.messages[ids[0]].body === "";
    return res("questionsCtrlEnter", ok, "cur=" + cur + " empty=" + JSON.stringify(empty) + " rows=" + ids.length);
  });

  // "Ask everyone for their questions" asks to confirm in place; Cancel posts nothing; "Ask all" posts one owner
  // broadcast request with no `to`.
  S.push(async function questionsAskAll() {
    await go("questions");
    function asks() { return Object.keys(__store.messages).filter(function (id) { var d = __store.messages[id]; return d.from === "owner" && d.kind === "request" && /relay\.py ask/.test(d.summary || ""); }); }
    var n0 = asks().length;
    $("q-ask-all").click(); await tick();
    var confirmShown = !!$("q-ask-yes") && !!$("q-ask-no") && !$("q-ask-all");
    $("q-ask-no").click(); await tick();
    var cancelled = !!$("q-ask-all") && asks().length === n0;
    $("q-ask-all").click(); await tick();
    $("q-ask-yes").click(); await tick(80);
    var ids = asks(), row = ids.length ? __store.messages[ids[ids.length - 1]] : null;
    var ok = confirmShown && cancelled && ids.length === n0 + 1 && !!row && !("to" in row) &&
      row.summary === "Owner: post every question you have for me with relay.py ask, one question per row" &&
      /relay\.py withdraw/.test(row.body) && /inbox/.test(row.body) && !!$("q-ask-all") && /^Asked/.test($("q-ask-status").textContent);
    return res("questionsAskAll", ok, "confirm=" + confirmShown + " cancelled=" + cancelled + " asks=" + n0 + "->" + ids.length + " row=" + JSON.stringify(row));
  });

  // On the timeline a question carries a "question for owner" pill and its state; a sender outside the roster
  // is not approved, so its question is not counted.
  S.push(async function questionsTimelinePills() {
    await clearQuestions();
    await seedQuestions();
    await go("timeline");
    function pills(id) { var c = card(id), m = c && c.querySelector(".meta"); return m ? [].slice.call(m.querySelectorAll(":scope > .pill")).map(function (p) { return p.textContent; }) : null; }
    var open = pills("qq-1"), answered = pills("qq-a"), withdrawn = pills("qq-w"), tile = $("quesVal").textContent;
    await clearQuestions();
    var ok = JSON.stringify(open) === JSON.stringify(["question for owner", "open"]) &&
      JSON.stringify(answered) === JSON.stringify(["question for owner", "answered"]) &&
      JSON.stringify(withdrawn) === JSON.stringify(["question for owner", "withdrawn"]) && tile === "3" && $("quesVal").textContent === "0";
    return res("questionsTimelinePills", ok, "open=" + JSON.stringify(open) + " answered=" + JSON.stringify(answered) + " withdrawn=" + JSON.stringify(withdrawn) + " tile=" + tile);
  });

  // Open questions count toward Waiting on you, beside the open waiting rows, and link to the Questions tab.
  S.push(async function waitingShowsQuestions() {
    await clearQuestions();
    var saved = __store.waiting; __store.waiting = {};
    __emit("waiting"); await tick();
    var none = $("waitVal").textContent, noneHint = $("waitHint").textContent;
    await go("waiting");
    var empty = /Nothing is waiting on you/.test($("view").textContent);
    await addDoc("waiting", "wq-row", { since: ago(50), project: "codex", summary: "Crosstalk feature: a thing", done: null });
    await seedQuestions();
    var tile = $("waitVal").textContent, tabN = $("tab-waiting").querySelector(".n").textContent, hint = $("waitHint").textContent;
    var firstItem = $("waitHint").querySelector("li").textContent;
    var cardIds = [].slice.call($("view").querySelectorAll(".card[data-id]")).map(function (c) { return c.dataset.id; });
    var stillRow = $("done-wq-row") !== null, lacksEmpty = !/Nothing is waiting on you/.test($("view").textContent);
    $("waitq-qq-2").click(); await tick();
    var onTab = $("tab-questions").getAttribute("aria-selected") === "true", shown = $("q-card").dataset.id;
    var focused = document.activeElement && document.activeElement.id;
    await clearQuestions();
    __store.waiting = saved || {}; __emit("waiting"); await go("timeline");
    var ok = none === "0" && noneHint === "nothing waiting" && empty && tile === "4" && tabN === "4" &&
      /^oldest 5 h 0 min ago · click to see them/.test(hint) && firstItem === "Question: Ship the parser before the demo?" &&
      JSON.stringify(cardIds) === JSON.stringify(["qq-1", "qq-2", "qq-3"]) && stillRow && lacksEmpty &&
      onTab && shown === "qq-2" && focused === "q-answer";
    return res("waitingShowsQuestions", ok, "none=" + none + " hint0=" + JSON.stringify(noneHint) + " empty=" + empty + " tile=" + tile + " tab=" + tabN + " hint=" + JSON.stringify(hint) +
      " first=" + JSON.stringify(firstItem) + " cards=" + JSON.stringify(cardIds) + " row=" + stillRow + " onTab=" + onTab + " shown=" + shown + " focus=" + focused);
  });

  // A question that closes while the owner types keeps its card and draft, with no way to send, until the owner
  // moves on; the draft never lands on the next question, and the status doesn't carry over.
  S.push(async function questionsClosedHoldsDraft() {
    await clearQuestions();
    await seedQuestions();
    await go("questions");
    while (!$("q-prev").disabled) { $("q-prev").click(); await tick(); }
    $("q-send").click(); await tick();
    var refused = $("q-status").textContent;
    typeAnswer("Ship it after review");
    await addDoc("messages", "qq-1-w", { t: ago(1), from: "beta", kind: "withdrawn", summary: "Withdrawn", replyTo: "qq-1" });
    var held = $("q-card").dataset.id, note = $("q-closed").textContent, draft = $("q-draft").value, readOnly = $("q-draft").readOnly;
    var noBox = !$("q-answer") && !$("q-send"), focus = document.activeElement && document.activeElement.id;
    var tileNow = $("quesVal").textContent;
    __emit("messages"); await tick();
    var stillHeld = $("q-card").dataset.id === "qq-1" && $("q-draft").value === draft;
    var sentNothing = ownAnswers("qq-1").length === 0 && ownAnswers("qq-2").length === 0;
    var moveLabel = $("q-moveon").textContent;
    $("q-moveon").click(); await tick();
    var next = $("q-card").dataset.id, box = $("q-answer").value, status = $("q-status").textContent;
    // The last open question closing, answered from elsewhere: no draft, and Dismiss leaves the empty state.
    __store.messages["qq-2-own"] = { t: ago(1), from: "owner", kind: "owner", summary: "buildbox", replyTo: "qq-2" };
    __store.messages["qq-3-w"] = { t: ago(1), from: "alpha", kind: "withdrawn", summary: "Withdrawn", replyTo: "qq-3" };
    __emit("messages"); await tick();
    var note2 = $("q-closed").textContent, noDraft = !$("q-draft"), label2 = $("q-moveon").textContent;
    $("q-moveon").click(); await tick();
    var emptied = !$("q-card") && /No open questions/.test($("view").textContent);
    delete __store.messages["qq-1-w"]; delete __store.messages["qq-2-own"]; delete __store.messages["qq-3-w"];
    await clearQuestions();
    await go("timeline");
    var ok = refused === "Write an answer first." && held === "qq-1" && note === "This question closed. Beta withdrew it. Your draft was not sent." &&
      draft === "Ship it after review" && readOnly && noBox && focus === "q-draft" && tileNow === "2" && stillHeld && sentNothing && moveLabel === "Next question" &&
      next === "qq-2" && box === "" && status === "" && note2 === "This question closed. It was answered elsewhere." && noDraft && label2 === "Dismiss" && emptied;
    return res("questionsClosedHoldsDraft", ok, "refused=" + JSON.stringify(refused) + " held=" + held + " note=" + JSON.stringify(note) + " draft=" + JSON.stringify(draft) + " ro=" + readOnly + " noBox=" + noBox +
      " focus=" + focus + " tile=" + tileNow + " stillHeld=" + stillHeld + " sent0=" + sentNothing + " move=" + moveLabel + " next=" + next + " box=" + JSON.stringify(box) + " status=" + JSON.stringify(status) +
      " note2=" + JSON.stringify(note2) + " noDraft=" + noDraft + " label2=" + label2 + " emptied=" + emptied);
  });

  // Checks whichever width the window has (run again at 390 wide; see run.md): nothing in the Reviews tab
  // pushes the page sideways; a wide table scrolls inside its own box.
  S.push(async function reviewsNarrow() {
    if (!__store.messages || !__store.messages["ov-obj"]) await seedReviews();
    await go("reviews");
    var w = window.innerWidth, page = document.documentElement.scrollWidth;
    var cards = [].slice.call(document.querySelectorAll("#view .override")).filter(function (c) { return c.getClientRects().length; });
    var cardsFit = cards.length > 0 && cards.every(function (c) { return c.getBoundingClientRect().right <= w + 1; });
    var wraps = [].slice.call(document.querySelectorAll("#view .rvproj .tablewrap"));
    var wrapsFit = wraps.length > 0 && wraps.every(function (t) { return t.getBoundingClientRect().right <= w + 1; });
    await go("timeline");
    return res("reviewsNarrow", page <= w + 1 && cardsFit && wrapsFit, "width=" + w + " scrollWidth=" + page + " cards=" + cards.length + "/" + cardsFit + " tables=" + wraps.length + "/" + wrapsFit);
  });

  S.push(async function schedulesCountdownStatesAndEscaping() {
    function at(min) { return new Date(Date.now() + min * 60000).toISOString().replace(/\.\d+Z$/, "Z"); }
    await put("schedules", {
      "codex-sched-a": { from: "codex", name: "<b>soon</b>", cron: "*/30 * * * *", next: at(10), periodMin: 30, reported: at(-1), runs: [{ at: at(-1), result: "failed", note: "<i>disk</i>" }] },
      "codex-sched-b": { from: "codex", name: "late job", cron: "*/30 * * * *", next: at(-20), periodMin: 30, reported: at(-50), runs: [] },
      "codex-sched-c": { from: "codex", name: "gone session", cron: "*/30 * * * *", next: at(-200), periodMin: 30, reported: at(-230), runs: [] },
      "codex-sched-d": { from: "codex", name: "removed", cron: "* * * * *", next: at(1), reported: at(0), removed: at(0), runs: [] }
    });
    await go("schedules");
    var rows = [].slice.call(document.querySelectorAll("#view tbody tr"));
    var texts = rows.map(function (r) { return r.textContent; });
    var states = rows.map(function (r) { return r.querySelector("[data-sched-state]").textContent; });
    var count = rows[1].querySelector("[data-sched-count]");
    var first = count.textContent;
    var det = document.querySelectorAll("#view details")[0];
    det.open = true; await tick();
    var noRaw = !document.querySelector("#view td b, #view td i");
    var note = det.textContent.indexOf("<i>disk</i>") !== -1;
    await tick(1100);
    var moved = count.textContent !== first;
    var openKept = (__emit("schedules"), await tick(), document.querySelectorAll("#view details")[0].open);
    var ok = rows.length === 3 && states.join() === "late,scheduled,stale" && /was due/.test(texts[0]) && /^in /.test(first)
      && noRaw && note && moved && openKept && /3/.test($("tab-schedules").textContent);
    return res("schedulesCountdownStatesAndEscaping", ok, JSON.stringify({ states: states, first: first, noRaw: noRaw, note: note, moved: moved, openKept: openKept, tab: $("tab-schedules").textContent }));
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
