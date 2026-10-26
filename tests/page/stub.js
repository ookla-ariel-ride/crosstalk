// Fake claude db: in-memory store, live listeners, controllable failures.
// __store[collection][id] = data holds the rows; __emit(c) pushes a snapshot; __kill(c, code) errors the
// subscriptions; __fail[c] makes set() and update() reject; __delay sets the write delay; __subscribeCount counts subscriptions.
// __seed({collection: {id: data}}) fills __store; build.sh calls it with participants.json before the page subscribes.
window.__store = {}; window.__subs = {}; window.__fail = {}; window.__delay = 0; window.__errs = {};
window.__seed = function (obj) { Object.keys(obj || {}).forEach(function (c) { __store[c] = Object.assign(__store[c] || {}, obj[c]); }); };
// History the page already holds on its first snapshot, so it doesn't count as new: two reply threads (one with a
// reply to a reply). A stale "last visit" from an earlier run would make these replies new, so it is cleared.
try { localStorage.removeItem("crosstalk.lastVisit"); } catch (e) {}
(function () {
  function ago(min) { return new Date(Date.now() - min * 60000).toISOString(); }
  __seed({ messages: {
    "hist-r": { t: ago(30), from: "codex", kind: "sync", summary: "History root" },
    "hist-a": { t: ago(20), from: "owner", kind: "sync", summary: "History answer", replyTo: "hist-r" },
    "hist-b": { t: ago(10), from: "codex", kind: "sync", summary: "Answer to the answer", replyTo: "hist-a" },
    "hist-s": { t: ago(25), from: "codex", kind: "sync", summary: "Single root" },
    "hist-s1": { t: ago(15), from: "owner", kind: "lesson", summary: "zebra-needle found", replyTo: "hist-s" }
  } });
})();
function snapFor(c){ var docs = Object.keys(__store[c]||{}).map(function(id){ return { id:id, data:function(){ return __store[c][id]; } }; }); return { docs: docs }; }
window.__emit = function(c){ (__subs[c]||[]).forEach(function(s){ s.cb(snapFor(c)); }); };
window.__kill = function(c, code){ (__subs[c]||[]).forEach(function(s){ s.err({code:code,message:code}); }); __subs[c]=[]; };
window.__subscribeCount = {};
function coll(c){ return {
  limit:function(){ return this; },
  doc:function(id){ return {
    set:function(d){ return new Promise(function(res,rej){ setTimeout(function(){ if(__fail[c]){ rej({code:"unavailable",message:"boom"}); return; } (__store[c]=__store[c]||{})[id]=d; __emit(c); res(); }, __delay); }); },
    update:function(d){ return new Promise(function(res,rej){ setTimeout(function(){ if(__fail[c]){ rej({code:"unavailable",message:"boom"}); return; } __store[c]=__store[c]||{}; __store[c][id]=Object.assign({}, __store[c][id], d); __emit(c); res(); }, __delay); }); } }; },
  onSnapshot:function(cb, err){ __subscribeCount[c]=(__subscribeCount[c]||0)+1; (__subs[c]=__subs[c]||[]).push({cb:cb,err:err}); setTimeout(function(){ cb(snapFor(c)); }, 10); }
}; }
// __viewer is what user.me() reports: the stub viewer is a named owner who can edit.
window.__viewer = { id: "u_test", name: "Test Owner", isOwner: true, canEdit: true, email: null, avatarUrl: "", color: "#888" };
// Opened with ?viewer=reader, the stub viewer is an unnamed read-only viewer.
if (/[?&]viewer=reader\b/.test(location.search)) __viewer = { id: "u_reader", name: "", isOwner: false, canEdit: false, email: null, avatarUrl: "", color: "#888" };
// Opened with ?viewer=editor, the stub viewer is a bearer-token editor (can edit, not the owner).
if (/[?&]viewer=editor\b/.test(location.search)) __viewer = { id: "u_editor", name: "Test Editor", isOwner: false, canEdit: true, email: null, avatarUrl: "", color: "#888" };
// The fake /api/login: "123456" signs the stub viewer in as the owner; anything else is a 401.
window.__pin = "123456";
// The fake /api/onboard: returns a canned kit and token; scenarios override __onboardResult.
window.__onboardResult = { token: "fake-token-123", kit: "kit for fake-token-123" };
var db = { collection: coll };
var user = { canEdit:async function(){return __viewer.canEdit;}, isOwner:async function(){return __viewer.isOwner;}, me:async function(){ return Object.assign({}, __viewer); },
  login: async function(pinOrToken) {
    if (String(pinOrToken) === __pin) {
      __viewer = { id: "u_test", name: "Test Owner", isOwner: true, canEdit: true, email: null, avatarUrl: "", color: "#888" };
      return { ok: true, viewer: "owner" };
    }
    var err = new Error("that pin or token is not valid"); err.code = "401"; throw err;
  },
  onboard: async function(fields) { window.__lastOnboardFields = fields; return Object.assign({}, __onboardResult); },
  palette: async function() { return { pairs: window.__palettePairs || [["#aa3366", "#dd88bb"], ["#3366aa", "#88bbdd"]] }; } };
window.claude = { use: async function(n){ if(n==="db") return db;
  if(n==="user") return user;
  return null; } };
// The seam the page prefers: the same db/user objects, exposed directly.
window.__crosstalkDataLayer = { db: db, user: user };
// __setWake({pid: {status, since, ids, log}}) feeds the page's wake badges, as /api/snapshot's `wake` block does.
window.__setWake = function (map) { window.__crosstalk.setWake(map); };
