// Fake claude db: in-memory store, live listeners, controllable failures.
// __store[collection][id] = data holds the rows; __emit(c) pushes a snapshot; __kill(c, code) errors the
// subscriptions; __fail[c] makes set() reject; __delay sets the write delay; __subscribeCount counts subscriptions.
// __seed({collection: {id: data}}) fills __store; build.sh calls it with participants.json before the page subscribes.
window.__store = {}; window.__subs = {}; window.__fail = {}; window.__delay = 0; window.__errs = {};
window.__seed = function (obj) { Object.keys(obj || {}).forEach(function (c) { __store[c] = Object.assign(__store[c] || {}, obj[c]); }); };
function snapFor(c){ var docs = Object.keys(__store[c]||{}).map(function(id){ return { id:id, data:function(){ return __store[c][id]; } }; }); return { docs: docs }; }
window.__emit = function(c){ (__subs[c]||[]).forEach(function(s){ s.cb(snapFor(c)); }); };
window.__kill = function(c, code){ (__subs[c]||[]).forEach(function(s){ s.err({code:code,message:code}); }); __subs[c]=[]; };
window.__subscribeCount = {};
function coll(c){ return {
  limit:function(){ return this; },
  doc:function(id){ return {
    set:function(d){ return new Promise(function(res,rej){ setTimeout(function(){ if(__fail[c]){ rej({code:"unavailable",message:"boom"}); return; } (__store[c]=__store[c]||{})[id]=d; __emit(c); res(); }, __delay); }); },
    update:function(d){ return new Promise(function(res){ setTimeout(function(){ __store[c]=__store[c]||{}; __store[c][id]=Object.assign({}, __store[c][id], d); __emit(c); res(); }, __delay); }); } }; },
  onSnapshot:function(cb, err){ __subscribeCount[c]=(__subscribeCount[c]||0)+1; (__subs[c]=__subs[c]||[]).push({cb:cb,err:err}); setTimeout(function(){ cb(snapFor(c)); }, 10); }
}; }
window.claude = { use: async function(n){ if(n==="db") return { collection: coll }; if(n==="user") return { canEdit:function(){return true;} }; return null; } };
