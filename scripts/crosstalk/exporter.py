"""Write one self-contained HTML page with the log inlined.

The export opens from disk with no server and no sibling files: the replay
travels inside the HTML as JSON in a <script> block, because fetch on file://
is blocked in several browsers. The page reads that snapshot through a third
seam implementation (see LOADER_JS) — the same shape the stub harness and the
server's fetch layer use — so the page's own code needs no changes.

Nothing secret goes in: the export carries the page, the replay and the roster.
"""
import json
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

from .logs import check_all
from .participants import load_roster
from .replay import load_all_lines, replay

SNAPSHOT_ID = "crosstalk-snapshot"
UNKNOWN_COMMIT = "unknown"


def repo_commit(root: Path) -> Optional[str]:
    """The commit of the checkout under root, or None when it isn't a repo."""
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True)
    except OSError:
        return None
    if out.returncode != 0:
        return None
    commit = out.stdout.strip()
    return commit or None


def published_now() -> str:
    """The publish time stamp.sh writes."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_snapshot(root: Path) -> dict:
    """The snapshot the export carries: the replay, plus the roster.

    collections: {collection: {id: fields}} — the replayed documents.
    roster: {"participants": [...]} — participants.json, the roster of record.
    """
    root = Path(root)
    roster_path = root / "participants.json"
    roster = load_roster(roster_path)
    raw = json.loads(roster_path.read_text(encoding="utf-8"))
    problems = check_all(root, roster)
    fatal = [p for p in problems if p.fatal]
    if fatal:
        p = fatal[0]
        raise RuntimeError("%s:%d: %s" % (p.file, p.line, p.message))
    docs = replay(load_all_lines(root, roster))
    collections = {}  # type: Dict[str, Dict[str, dict]]
    for (coll, did), fields in sorted(docs.items()):
        collections.setdefault(coll, {})[did] = fields
    return {
        "server_time": published_now(),
        "roster": {"participants": raw.get("participants", [])},
        "collections": collections,
    }


def _json_for_script(obj: dict) -> str:
    """JSON safe to drop inside a <script> element."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True).replace("<", "\\u003c")


# The page's data layer for a static snapshot: the same seam the stub harness
# and the server use (db.collection(c).onSnapshot(cb), user.me()), backed by the
# inlined JSON instead of a network call. It never writes: this page is a copy.
LOADER_JS = """(function () {
  "use strict";
  var el = document.getElementById("%(snapshot_id)s");
  var SNAP = el ? JSON.parse(el.textContent) : { collections: {}, roster: {} };
  var store = {};
  Object.keys(SNAP.collections || {}).forEach(function (c) { store[c] = SNAP.collections[c]; });
  // The roster of record fills the participants collection in; the log's own
  // participant lines win where they say something (an owner approval).
  var logged = store.participants || {};
  var people = {};
  ((SNAP.roster && SNAP.roster.participants) || []).forEach(function (p) {
    if (!p || !p.id) return;
    var doc = {};
    Object.keys(p).forEach(function (k) { if (k !== "id") doc[k] = p[k]; });
    people[p.id] = doc;
  });
  Object.keys(logged).forEach(function (id) {
    var doc = people[id] || {};
    var fields = logged[id] || {};
    Object.keys(fields).forEach(function (k) { doc[k] = fields[k]; });
    people[id] = doc;
  });
  store.participants = people;
  var subs = {};
  function snapFor(c) {
    var rows = store[c] || {};
    return { docs: Object.keys(rows).map(function (id) {
      return { id: id, data: function () { return rows[id]; } };
    }) };
  }
  function readonly() {
    return {
      set: function () { return Promise.reject(new Error("this page is a read-only snapshot")); },
      update: function () { return Promise.reject(new Error("this page is a read-only snapshot")); }
    };
  }
  function coll(c) {
    var api = {
      limit: function () { return api; },
      doc: function () { return readonly(); },
      onSnapshot: function (cb) {
        (subs[c] = subs[c] || []).push(cb);
        setTimeout(function () { cb(snapFor(c)); }, 0);
      }
    };
    return api;
  }
  var db = { collection: coll, doc: function () { return readonly(); } };
  var user = {
    me: function () { return Promise.resolve({ id: "snapshot", name: "", isOwner: false, canEdit: false, email: null, avatarUrl: "", color: "#888" }); },
    canEdit: function () { return Promise.resolve(false); },
    isOwner: function () { return Promise.resolve(false); }
  };
  window.claude = { use: function (n) { return Promise.resolve(n === "db" ? db : n === "user" ? user : null); } };
  window.__crosstalkDataLayer = { db: db, user: user };
  window.__crosstalkExport = {
    snapshot: SNAP,
    store: store,
    emit: function (c) { (subs[c] || []).forEach(function (cb) { cb(snapFor(c)); }); }
  };
  // A static page is offline by nature: it never updates, so the strip says so
  // even after the page's own render pass puts "Live" there.
  function markOffline() {
    var live = document.getElementById("live"), text = document.getElementById("liveText");
    if (live && live.classList.contains("on")) live.classList.remove("on");
    if (text && text.textContent !== "Offline") text.textContent = "Offline";
  }
  function watchStrip() {
    var live = document.getElementById("live");
    if (!live || !window.MutationObserver) return;
    new MutationObserver(markOffline).observe(live, { attributes: true, attributeFilter: ["class"],
      childList: true, subtree: true, characterData: true });
    markOffline();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchStrip);
  else watchStrip();
})();""" % {"snapshot_id": SNAPSHOT_ID}


CHARSET = '<meta charset="utf-8">'


def render_page(html: str, snapshot: dict, commit: str, published: str) -> str:
    """index.html with the commit and publish time stamped, snapshot inlined."""
    stamped = html.replace("__COMMIT__", commit).replace("__PUBLISHED__", published)
    # The export is a file on disk: without a charset the browser may read it as
    # windows-1252 and mangle every non-ASCII character (the page's "·", and any
    # message body with an accent in it).
    if "charset" not in stamped[:1024].lower():
        stamped = CHARSET + "\n" + stamped
    block = ('<script id="%s" type="application/json">%s</script>\n'
             '<script>%s</script>\n' % (SNAPSHOT_ID, _json_for_script(snapshot), LOADER_JS))
    i = stamped.find("<script")
    if i == -1:
        return stamped + "\n" + block
    return stamped[:i] + block + stamped[i:]


def export_page(root: Path, out_path: Path, commit: Optional[str] = None,
                published: Optional[str] = None) -> dict:
    """Write the self-contained page to out_path; returns what it wrote."""
    root = Path(root)
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError("no index.html in %s" % root)
    snapshot = build_snapshot(root)
    if commit is None:
        commit = repo_commit(root) or UNKNOWN_COMMIT
    if published is None:
        published = published_now()
    snapshot["commit"] = commit
    html = render_page(index.read_text(encoding="utf-8"), snapshot, commit, published)
    out_path = Path(out_path)
    if out_path.is_symlink():
        raise RuntimeError("refusing to write through a symlink: %s" % out_path)
    if out_path.parent and not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return {
        "path": str(out_path),
        "documents": sum(len(v) for v in snapshot["collections"].values()),
        "commit": commit,
        "published": published,
    }
