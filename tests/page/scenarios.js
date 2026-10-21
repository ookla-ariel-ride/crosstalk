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

  S.push(async function unknownSenderNeutral() {
    await go("timeline");
    await addDoc("messages", "gemini-1", { t: T0, from: "gemini", kind: "sync", summary: "From an unlisted sender" });
    var c = card("gemini-1"), w = c && c.querySelector(".who");
    if (!w) return res("unknownSenderNeutral", false, "no card");
    var got = getComputedStyle(w).color, want = rgb("var(--muted)");
    return res("unknownSenderNeutral", w.textContent === "gemini" && got === want, "name=" + JSON.stringify(w.textContent) + " color=" + got + " muted=" + want);
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
      "d-2": { t: T0, by: "owner", summary: "Shared", project: "both", source: "test" } });
    await go("decisions");
    var cells = [].slice.call(document.querySelectorAll("#view tbody tr")).map(function (tr) { return tr.children[2].textContent; }).sort();
    await go("timeline");
    return res("forColumnUsesName", JSON.stringify(cells) === JSON.stringify(["Both projects", "Codex"]), "For=" + JSON.stringify(cells));
  });

  S.push(async function ownerColorThemed() {
    await addDoc("messages", "own-c", { t: T0, from: "owner", kind: "owner", summary: "Owner color" });
    var c = card("own-c"), w = c && c.querySelector(".who");
    var got = w ? getComputedStyle(w).color : null, want = rgb("var(--own)");
    return res("ownerColorThemed", got === want && c.classList.contains("own"), "color=" + got + " --own=" + want + " class=" + (c && c.className));
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

  window.runAll = async function () {
    var out = [];
    for (var i = 0; i < S.length; i++) {
      try { out.push(await S[i]()); }
      catch (e) { out.push(res(S[i].name, false, "threw: " + (e && e.stack ? e.stack.split("\n")[0] : e))); }
    }
    return out;
  };
})();
