"""Serve a replay of the logs over local HTTP.

The server is the page host and the database:

  GET  /                -> index.html, as it sits in the checkout
  GET  /api/snapshot    -> every collection replayed, plus server_time and commit
  GET  /api/threads     -> the threads in one sort order, a page at a time (threads.py)
  GET  /api/thread/<id> -> the thread that holds a message: its messages, with depth and quote, and its list row
  GET  /api/read-state  -> the owner's read state (readstate.py); the owner only
  PUT  /api/read-state  -> a thread's read mark, the mark-all time, or a mute; the owner only
  GET  /api/events      -> a Server-Sent Events stream of the ids of new log rows (events.py)
  GET  /api/vitals      -> the health numbers: disk, usage, build host (vitals.py), and the state row (state.py)
  GET  /api/section/<name> -> one section of the v2 page, open rows first, a page at a time (sections.py)
  GET  /api/<collection>-> one collection replayed
  GET  /api/queue       -> the land queue of the build host (landqueue.py)
  GET  /api/me          -> the role of the caller (owner, editor, viewer)
  POST /api/login       -> the startup PIN or the owner token, then a session cookie
  POST /api/append      -> one line appended, with the same checks `relay.py append` runs
  GET  /v2/<path>       -> the built v2 page from <code root>/web/dist/; GET and HEAD only

Reads are replays of `log/` (the source of truth), cached on each log
file's size and mtime, so a session that appends straight to a file is
visible on the next request without a restart. Writes go through
`append_line`, so the file lock, the trust rules and the message shape
rules are the same ones the command line uses. The server alone stamps a
line's `t`, from its own clock.

Python 3.9, standard library only.
"""
import hmac
import json
import mimetypes
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from .files import atomic_write_text, utf8_json
from .logs import COLLECTIONS, append_line, check_all, check_all
from .onboard import PALETTE, _locked_onboard, onboard
from .participants import ensure_roster, load_roster
from .landqueue import land_queue
from .replay import enrich_messages, first_set_map, last_line_map, load_all_lines, replay
from .deploy import read_manifest
from .service import service_block, version
from . import config as config_mod
from .tokens import TOKEN_FILE, Result, Tokens
from . import ownerpin
from .notify import Notifier
from . import urgent as urgent_mod
from . import reviews as reviews_mod
from . import autogrant as autogrant_mod
from . import threads as threads_mod
from . import readstate
from . import events as events_mod
from . import state as state_mod
from . import vitals as vitals_mod
from . import buildhost as buildhost_mod
from . import sections as sections_mod

# The collections the page reads. "participants" is the owner's roster.
KNOWN_COLLECTIONS = tuple(COLLECTIONS) + ("participants",)

JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
SVG_TYPE = "image/svg+xml; charset=utf-8"
PNG_TYPE = "image/png"
ICO_TYPE = "image/x-icon"
MAX_BODY = 1024 * 1024
# A slow client cannot hold a request thread forever.
READ_TIMEOUT = 30.0
COOKIE_NAME = "crosstalk_session"
# The startup PIN is good for ten minutes and for one login; a session for twelve hours.
PIN_TTL = 10 * 60.0
SESSION_TTL = 12 * 60 * 60.0
# Login throttling: after this many failed attempts in a rolling window,
# refuse further logins until the window clears.
LOGIN_WINDOW = 10 * 60.0
LOGIN_MAX_FAILURES = 5
# The fields of a roster entry the owner may write. A stale `wake` key (from the
# removed agent-wake feature) is not one, so the API rejects it.
# The push channel (/api/events) looks at the log this often, and with no new
# row sends a comment line this often, so a proxy or the browser keeps it open.
EVENTS_CHECK_SEC = 1.0
EVENTS_PING_SEC = 15.0
# The v2 page: built files under <code root>/web/dist/. Vite names each file
# under assets/ by its content, so a browser may keep it for a year.
V2_DIST = ("web", "dist")
V2_IMMUTABLE = "public, max-age=31536000, immutable"
V2_NOT_BUILT = "The v2 page is not built: run `npm run build` in web/."
# Text types that mimetypes names outside text/*; each gets charset=utf-8.
TEXT_TYPES = ("application/javascript", "application/json", "application/xml",
              "application/manifest+json", "image/svg+xml")
# Font types that an older mimetypes table may not know.
EXTRA_TYPES = {".woff2": "font/woff2", ".woff": "font/woff"}
ROSTER_FIELDS = ("name", "kind", "color", "colorDark", "checkInMin", "trust", "repo", "parent", "harness", "aliases", "itemNames")


def now_iso() -> str:
    """The server's clock, in the log's timestamp format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# The served checkout's commit, cached on .git/HEAD's mtime so the page's 5s
# polls do not spawn git each time. A small TTL is the fallback for refs that
# move without touching HEAD itself (a symref HEAD).
COMMIT_TTL = 60.0

_commit_cache = {}  # type: Dict[str, Tuple[Optional[tuple], float, Optional[str]]]
_commit_lock = threading.Lock()


def _head_stamp(root: Path) -> Optional[tuple]:
    try:
        st = (root / ".git" / "HEAD").stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def repo_commit(root) -> Optional[str]:
    """The short hash of the served checkout's HEAD, or None when there is none.

    Read-only: the server never commits and never touches the working tree.
    """
    root = Path(root)
    stamp = _head_stamp(root)
    now = time.time()
    with _commit_lock:
        cached = _commit_cache.get(str(root))
        if cached is not None:
            cstamp, ctime, cvalue = cached
            if cstamp == stamp and now - ctime < COMMIT_TTL:
                return cvalue
    value = _git_commit(root)
    with _commit_lock:
        _commit_cache[str(root)] = (stamp, time.time(), value)
    return value


def _git_commit(root: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace").strip() or None


_build_info_cache = {}  # type: Dict[str, Tuple[Optional[tuple], float, dict]]


def repo_build_info(root) -> dict:
    """{"commit": short_hash, "build_number": int_count, "commit_date": iso_utc} for HEAD."""
    root = Path(root)
    manifest = read_manifest(root)
    if manifest:  # a deployment has no .git: `relay.py deploy` recorded the commit
        return {"commit": manifest.get("short") or manifest["sha"][:7],
                "build_number": manifest.get("build_number"), "commit_date": manifest.get("commit_date")}
    stamp = _head_stamp(root)
    if stamp is None:
        return {"commit": None, "build_number": None, "commit_date": None}
    now = time.time()
    with _commit_lock:
        cached = _build_info_cache.get(str(root))
        if cached is not None:
            cstamp, ctime, cvalue = cached
            if cstamp == stamp and now - ctime < COMMIT_TTL:
                return dict(cvalue)
    value = _git_build_info(root)
    with _commit_lock:
        _build_info_cache[str(root)] = (stamp, time.time(), dict(value))
    return value


def _git_build_info(root: Path) -> dict:
    empty = {"commit": None, "build_number": None, "commit_date": None}
    try:
        log_out = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%h%n%ct", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        cnt_out = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--count", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return empty
    if log_out.returncode != 0 or cnt_out.returncode != 0:
        return empty
    lines = log_out.stdout.decode("utf-8", "replace").strip().splitlines()
    cnt_str = cnt_out.stdout.decode("utf-8", "replace").strip()
    if len(lines) < 2 or not lines[0] or not cnt_str.isdigit():
        return empty
    try:
        epoch = int(lines[1].strip())
        commit_date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
        build_number = int(cnt_str)
    except (ValueError, OSError, OverflowError):
        return empty
    return {"commit": lines[0].strip(), "build_number": build_number, "commit_date": commit_date}


class ReplayCache:
    """The replayed documents, recomputed when the log files or the roster change."""

    def __init__(self, root):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._key = None
        self._docs = None
        self._entries = None
        self._roster = None
        self._created = {}  # type: Dict[str, str]
        self._changed = {}  # type: Dict[Tuple[str, str], str]
        self._thread_key = None
        self._thread_index = None
        self.thread_builds = 0

    def roster(self):
        """The Roster of the current snapshot, with the install's config (participants.py)."""
        self.snapshot()
        return self._roster

    def _stamp(self) -> tuple:
        """Every input's size and mtime: the cache key."""
        parts = []  # type: List[tuple]
        log_dir = self.root / "log"
        if log_dir.is_dir():
            for path in sorted(log_dir.glob("*.jsonl")):
                try:
                    st = path.stat()
                except OSError:
                    continue
                parts.append((path.name, st.st_size, st.st_mtime_ns))
        for name in ("participants.json", config_mod.FILE):
            try:
                st = (self.root / name).stat()
                parts.append((name, st.st_size, st.st_mtime_ns))
            except OSError:
                parts.append((name, None, None))
        return tuple(parts)

    def snapshot(self) -> Tuple[Dict[Tuple[str, str], dict], Dict[str, dict]]:
        """(documents by (collection, id), roster entries by participant id)."""
        key = self._stamp()
        with self._lock:
            if key == self._key and self._docs is not None:
                return self._docs, self._entries
            roster_path = ensure_roster(self.root / "participants.json")
            raw = json.loads(roster_path.read_text(encoding="utf-8"))
            roster = load_roster(roster_path)
            lines = load_all_lines(self.root, roster)
            docs = enrich_messages(replay(lines), lines)
            entries = {}
            for entry in raw.get("participants", []):
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    entries[entry["id"]] = entry
            self._key = key
            self._docs = docs
            self._entries = entries
            self._roster = roster
            # A message's created time: its first `set` line's `data.t` when that is a
            # time, else that line's `t`. An edit moves the replayed `t`, not this.
            self._created = {did: ln.data["t"] if readstate.is_time(ln.data.get("t")) else ln.t
                             for (coll, did), ln in first_set_map(lines).items() if coll == "messages"}
            # Each doc's last change: the time of the last line that touched it.
            self._changed = {key: ln.t for key, ln in last_line_map(lines).items()}
            return docs, entries

    def changed(self, key: Tuple[str, str]) -> Optional[str]:
        """The time of the last line that touched (collection, id) in the current snapshot."""
        return self._changed.get(key)

    def threads(self) -> "threads_mod.ThreadIndex":
        """The threads of the current snapshot, built once for each cache key."""
        self.snapshot()
        with self._lock:
            if self._thread_key != self._key or self._thread_index is None:
                messages = {did: doc for (coll, did), doc in self._docs.items() if coll == "messages"}
                waiting = {did for (coll, did), doc in self._docs.items()
                           if coll == "waiting" and isinstance(doc, dict) and not doc.get("done")}
                acks = {did: doc for (coll, did), doc in self._docs.items() if coll == "acks"}
                self._thread_index = threads_mod.ThreadIndex(messages, self._roster, created=self._created,
                                                             waiting=waiting, acks=acks)
                self._thread_key = self._key
                self.thread_builds += 1
            return self._thread_index


def roster_problem(raw, directory) -> Optional[str]:
    """The reason this roster would be invalid, or None when it would load.

    The roster is checked with the same loader the rest of the system uses, so
    a write that would break participants.json never reaches the disk.
    """
    fd, tmp = tempfile.mkstemp(prefix=".participants.", suffix=".json", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        load_roster(Path(tmp))
    except ValueError as e:
        return str(e)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return None


class TokenStore:
    """The tokens the server accepts: an injected map, or tokens.json in the root."""

    def __init__(self, root, tokens=None):
        self.root = Path(root)
        self.injected = dict(tokens) if tokens else None
        self._lock = threading.Lock()
        self._entries = {}  # type: Dict[str, str]
        self._stamp = None

    def _key(self):
        try:
            st = (self.root / TOKEN_FILE).stat()
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

    def _current(self) -> Dict[str, str]:
        if self.injected is not None:
            return self.injected
        key = self._key()
        with self._lock:
            if key != self._stamp:
                self._entries = Tokens.load(self.root / TOKEN_FILE).entries
                self._stamp = key
            return self._entries

    def verify(self, auth_header, pid: Optional[str] = None) -> Result:
        """Check an Authorization header against the store."""
        return Tokens(self.root / TOKEN_FILE, self._current()).verify(auth_header, pid)


class Sessions:
    """Owner sessions.

    Without a path, sessions are memory-only: a restart forgets them all.
    With a path, they persist across restarts in a 0600 JSON file outside
    the repo (the housekeeper must never commit them).
    """

    def __init__(self, path=None):
        self._lock = threading.RLock()
        self._path = Path(path) if path else None
        self._sessions = {}  # type: Dict[str, float]
        self._load()

    def create(self, ttl: float = SESSION_TTL) -> str:
        """A new opaque session id."""
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            self._sessions[sid] = time.time() + ttl
            self._save()
        return sid

    def valid(self, sid: Optional[str]) -> bool:
        if not sid:
            return False
        with self._lock:
            expires = self._sessions.get(sid)
            if expires is None:
                return False
            if expires < time.time():
                del self._sessions[sid]
                self._save()
                return False
            return True

    def _prune(self) -> None:
        now = time.time()
        stale = [s for s, e in self._sessions.items() if e < now]
        if stale:
            for sid in stale:
                del self._sessions[sid]
            self._save()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            now = time.time()
            for sid, expires in raw.items():
                if isinstance(sid, str) and isinstance(expires, (int, float)) and expires > now:
                    self._sessions[sid] = expires
        except (OSError, ValueError):
            return

    def _save(self) -> None:
        if self._path is None:
            return
        with self._lock:
            try:
                atomic_write_text(self._path, json.dumps(self._sessions, sort_keys=True) + "\n")
                os.chmod(str(self._path), 0o600)
            except OSError:
                pass


class ServerState:
    """What the handlers share: the served root, the replay cache, the tokens.

    `root` holds the data (log/, participants.json, tokens.json and the rest);
    `code_root` holds the code (index.html, the icons, the git checkout). It
    defaults to `root`, the layout of an install that keeps both in one folder.
    """

    def __init__(self, root, tokens=None, sessions=None, code_root=None):
        self.root = Path(root)
        self.code_root = Path(code_root) if code_root is not None else self.root
        self.tokens = TokenStore(self.root, tokens)
        self.cache = ReplayCache(self.root)
        self.sessions = Sessions(sessions)
        self.pin = None  # type: Optional[str]
        self.pin_expires = 0.0
        self.pin_used = False
        self.login_failures = []  # type: List[float]
        self.login_lock = threading.Lock()
        self.notifier = None  # type: Optional[Notifier]
        self.events_check_sec = EVENTS_CHECK_SEC
        self.events_ping_sec = EVENTS_PING_SEC
        # Set when the server stops, so an open event stream ends.
        self.stopping = threading.Event()
        self.buildhost_state = buildhost_mod.state_path_from_env()

    def make_pin(self) -> str:
        """The one-time startup PIN: six digits, good for ten minutes."""
        self.pin = "%06d" % secrets.randbelow(1000000)
        self.pin_expires = time.time() + PIN_TTL
        self.pin_used = False
        return self.pin

    def login_locked(self) -> Optional[float]:
        """Retry-After seconds when login is throttled, or None when allowed."""
        now = time.time()
        with self.login_lock:
            self.login_failures = [t for t in self.login_failures
                                   if now - t < LOGIN_WINDOW]
            if len(self.login_failures) >= LOGIN_MAX_FAILURES:
                oldest = min(self.login_failures)
                return max(1.0, LOGIN_WINDOW - (now - oldest))
            return None

    def record_login_failure(self) -> None:
        with self.login_lock:
            self.login_failures.append(time.time())


def _name(value) -> bool:
    """A non-empty string: a thread root or a message id."""
    return isinstance(value, str) and bool(value)


class CrosstalkHandler(BaseHTTPRequestHandler):
    """The JSON API. Every response is JSON, except the page itself."""

    server_version = "Crosstalk/1.0"
    protocol_version = "HTTP/1.1"
    timeout = READ_TIMEOUT

    @property
    def state(self) -> ServerState:
        return self.server.state  # type: ignore[attr-defined]

    def handle(self):
        """One connection: internal errors become JSON, never a dead thread."""
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception as e:
            # The method, the path without its query, and where it failed: no
            # header, no body and no value from the request.
            frame = traceback.extract_tb(e.__traceback__)[-1]
            sys.stderr.write("internal error on %s %s: %s at %s:%d\n" % (
                getattr(self, "command", "?"), urlsplit(getattr(self, "path", "")).path,
                type(e).__name__, os.path.basename(frame.filename), frame.lineno))
            try:
                self._json(500, {"error": "internal error"})
            except Exception:
                pass

    def send_error(self, code, message=None, explain=None):
        """Errors unsupported by a do_* method stay JSON (e.g. a 501)."""
        try:
            self._error(code, message or "error")
        except Exception:
            try:
                self.send_response(code)
                self.end_headers()
            except Exception:
                pass

    def log_message(self, fmt, *args):
        # One line per request: never a header, never a body, so no secret
        # (a token, a PIN, a cookie) can reach the log.
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # -- responses ---------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or []):
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload, headers=None) -> None:
        body = utf8_json(payload).encode("utf-8")
        self._send(status, body, JSON_TYPE, headers)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # -- reads -------------------------------------------------------------

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/snapshot":
            self._snapshot()
        elif path == "/api/health":
            self._health()
        elif path == "/api/me":
            self._me()
        elif path == "/api/palette":
            self._palette()
        elif path == "/api/queue":
            self._queue()
        elif path == "/api/threads":
            self._threads()
        elif path.startswith("/api/thread/"):
            self._thread(unquote(path[len("/api/thread/"):]))
        elif path == "/api/read-state":
            self._read_state_get()
        elif path == "/api/events":
            self._events()
        elif path == "/api/vitals":
            self._vitals()
        elif path.startswith("/api/section/"):
            self._section(unquote(path[len("/api/section/"):]))
        elif path.startswith("/api/"):
            self._collection(path[len("/api/"):])
        elif path == "/":
            self._index()
        elif path == "/v2" or path.startswith("/v2/"):
            self._v2(path)
        elif path == "/favicon.svg":
            self._static_asset("favicon.svg", SVG_TYPE)
        elif path == "/favicon.ico":
            self._static_asset("favicon.ico", ICO_TYPE)
        elif path in ("/favicon.png", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
            self._static_asset("favicon.png", PNG_TYPE)
        elif path == "/logo.svg":
            self._static_asset("logo.svg", SVG_TYPE)
        else:
            self._error(404, "no such endpoint")

    def do_HEAD(self):
        self.do_GET()

    def _snapshot(self):
        try:
            docs, entries = self.state.cache.snapshot()
        except (OSError, ValueError) as e:
            self._error(500, "cannot read the log: %s" % e)
            return
        collections = {}  # type: Dict[str, Dict[str, dict]]
        for (collection, doc_id), fields in docs.items():
            collections.setdefault(collection, {})[doc_id] = fields
        # The agent-wake feature is gone, but an old participants.json may still
        # hold a `wake` command and path: strip it so a stale key never leaks.
        roster = {pid: {k: v for k, v in entry.items() if k != "wake"}
                  for pid, entry in entries.items()}
        notifier = self.state.notifier
        live = notifier.status() if notifier is not None else {}
        info = repo_build_info(self.state.code_root)
        loaded = self.state.cache.roster()
        cfg = config_mod.of(loaded)
        self._json(200, {"server_time": now_iso(),
                         # What the page needs of the install's settings: who coordinates, where
                         # this code's commits are shown, and from when a ruling is a standard.
                         "coordinator": config_mod.coordinator(loaded),
                         "repo_url": cfg["repoUrl"],
                         "rulings_since": cfg["rulingsSince"],
                         "commit": info["commit"],
                         "build_number": info["build_number"],
                         "commit_date": info["commit_date"],
                         "roster": roster,
                         "live": live,
                         "collections": collections,
                         "service": service_block(self.state.code_root, data_root=self.state.root)})

    def _health(self):
        """The log's health: ok when nothing fatal, plus the fatal lines named.

        The page's "live" strip reads this: a fatal line surfaces as the
        Offline reason, and the server_time is the freshness clock.
        """
        root = self.state.root
        # which commit runs: `relay.py deploy --restart` waits for its own
        ver = version(self.state.code_root)
        try:
            roster = load_roster(root / "participants.json")
        except (OSError, ValueError) as e:
            self._json(200, {"ok": False, "server_time": now_iso(), "sha": ver["sha"], "version": ver,
                             "fatal": ["participants.json:0: %s" % e]})
            return
        problems = check_all(root, roster)
        fatal = ["%s:%d: %s" % (p.file, p.line, p.message) for p in problems if p.fatal]
        self._json(200, {"ok": not fatal, "server_time": now_iso(), "sha": ver["sha"], "version": ver,
                         "fatal": fatal})

    def _queue(self):
        try:
            docs, entries = self.state.cache.snapshot()
        except (OSError, ValueError) as e:
            self._error(500, "cannot read the log: %s" % e)
            return
        self._json(200, land_queue(docs, projects=entries, now=time.time(),
                                   coordinator=config_mod.coordinator(self.state.cache.roster())))

    def _threads(self):
        """One page of threads: ?sort=needs|new|hot|unanswered (default new), &cursor=, &limit=."""
        query = parse_qs(urlsplit(self.path).query)
        order = query.get("sort", ["new"])[-1] or "new"
        if order not in threads_mod.ORDERS:
            self._error(400, "unknown sort %r: use one of %s" % (order, ", ".join(threads_mod.ORDERS)))
            return
        raw_limit = query.get("limit", [""])[-1]
        try:
            limit = int(raw_limit) if raw_limit else None
        except ValueError:
            self._error(400, "the limit must be a whole number")
            return
        try:
            index = self.state.cache.threads()
        except (OSError, ValueError) as e:
            self._error(500, "cannot read the log: %s" % e)
            return
        now = now_iso()
        try:
            items, nxt = index.page(order, now, query.get("cursor", [""])[-1], limit)
        except ValueError as e:
            self._error(400, str(e))
            return
        if self._role() == "owner":
            items = self._with_unread(index, items)
        self._json(200, {"threads": items, "next": nxt, "server_time": now})

    def _section(self, name: str):
        """One page of a section: ?cursor=&limit= as for /api/threads, &q= to search."""
        if name not in sections_mod.SECTIONS:
            self._error(404, "no such section: %s" % name)
            return
        query = parse_qs(urlsplit(self.path).query)
        raw_limit = query.get("limit", [""])[-1]
        try:
            limit = int(raw_limit) if raw_limit else None
        except ValueError:
            self._error(400, "the limit must be a whole number")
            return
        cache = self.state.cache
        try:
            docs, _entries = cache.snapshot()
            roster = cache.roster()
        except (OSError, ValueError) as e:
            self._error(500, "cannot read the log: %s" % e)
            return
        found, open_count = sections_mod.rows(name, docs, roster, changed=cache.changed,
                                              q=query.get("q", [""])[-1])
        try:
            items, nxt = threads_mod.page(found, query.get("cursor", [""])[-1], limit)
        except ValueError as e:
            self._error(400, str(e))
            return
        self._json(200, {"rows": items, "next": nxt, "open": open_count, "total": len(found)})

    def _with_unread(self, index, items: List[dict]) -> List[dict]:
        """Copies of the threads with the owner's `unread` count (readstate.py)."""
        state = readstate.load(self.state.root)
        out = []
        for item in items:
            messages = []
            for did in index.members.get(item["root"], []):
                messages.append({"id": did, "from": index.messages[did].get("from"), "t": index.when[did]})
            out.append(dict(item, unread=readstate.unread(item, messages, state)))
        return out

    def _thread(self, did: str):
        """The thread that holds message `did` (its root or any reply), with its list row."""
        try:
            index = self.state.cache.threads()
        except (OSError, ValueError) as e:
            self._error(500, "cannot read the log: %s" % e)
            return
        root = index.root_of.get(did)
        messages = index.thread(root) if root is not None else None
        if messages is None:
            self._error(404, "no thread holds the message %r" % did)
            return
        row = index.row(root, now_iso())
        if self._role() == "owner":
            row = self._with_unread(index, [row])[0]
        self._json(200, {"root": root, "messages": messages, "thread": row})

    def _events(self):
        """The push channel: Server-Sent Events, open until the client goes or the server stops.

        The start mark comes first, then the headers, then `event: ready`: the
        client refetches what it shows on each ready event, so a line written
        before the mark is in that refetch and a line written after it comes as
        an event. The browser's own open event fires on the headers, which is
        why they wait for the mark too. Then each check that finds new log
        lines sends `event: rows` with `data: {"rows": [...]}` (ids only,
        events.py), and a quiet stream sends `: ping` every events_ping_sec.
        A write error ends it.
        """
        state = self.state
        self.close_connection = True
        # The stamp before the mark: a line written between the two shows as a
        # changed stamp on the next check, and since() then finds it.
        seen = events_mod.stamp(state.root)
        mark = events_mod.mark(state.root)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            self.wfile.write(b"event: ready\ndata: {}\n\n")
            last = time.monotonic()
            while not state.stopping.wait(state.events_check_sec):
                now_stamp = events_mod.stamp(state.root)
                if now_stamp != seen:
                    seen = now_stamp
                    rows, mark = events_mod.since(state.root, mark)
                    if rows:
                        body = utf8_json({"rows": rows})
                        self.wfile.write(("event: rows\ndata: %s\n\n" % body).encode("utf-8"))
                        last = time.monotonic()
                        continue
                if time.monotonic() - last >= state.events_ping_sec:
                    self.wfile.write(b": ping\n\n")
                    last = time.monotonic()
        except OSError:
            return  # the client went away

    def _vitals(self):
        cfg = config_mod.load(self.state.root)
        now = time.time()
        out = vitals_mod.read(self.state.root, cfg, buildhost_state=self.state.buildhost_state, now=now)
        # The readings need no log: a log that cannot be read leaves out only the state row.
        try:
            docs, entries = self.state.cache.snapshot()
        except (OSError, ValueError):
            out["state"] = None
        else:
            # The same row as `relay.py state`, from these readings: no second vitals.read.
            out["state"] = state_mod.read(self.state.root, docs, self.state.cache.roster(), entries, cfg,
                                          buildhost_state=self.state.buildhost_state, now=now,
                                          readings=dict(out))
        out["model_families"] = cfg["modelFamilies"]
        self._json(200, out)

    def _collection(self, name: str):
        if name not in KNOWN_COLLECTIONS:
            self._error(404, "no such collection: %s" % name)
            return
        try:
            docs, _entries = self.state.cache.snapshot()
        except (OSError, ValueError) as e:
            self._error(500, "cannot read the log: %s" % e)
            return
        out = {}  # type: Dict[str, dict]
        for (collection, doc_id), fields in docs.items():
            if collection == name:
                out[doc_id] = fields
        self._json(200, {"docs": out})

    def _index(self):
        path = self.state.code_root / "index.html"
        try:
            body = path.read_bytes()
        except OSError:
            self._error(404, "no page is served from this checkout")
            return
        self._send(200, body, HTML_TYPE, [("Cache-Control", "no-cache")])

    def _v2(self, path: str):
        """A file of the built v2 page; a route with no file extension gets index.html."""
        if path == "/v2":
            query = urlsplit(self.path).query
            self._send(301, b"", "text/plain; charset=utf-8",
                       [("Location", "/v2/" + ("?" + query if query else ""))])
            return
        dist = self.state.code_root.joinpath(*V2_DIST)
        if not dist.is_dir():
            self._error(404, V2_NOT_BUILT)
            return
        # Decode once; a NUL or a backslash is never part of a built file's name.
        rel = unquote(path[len("/v2/"):])
        # A part that starts with a dot (.build-hash, .vite/) is a build file, not part of the page.
        if "\x00" in rel or "\\" in rel or any(part.startswith(".") for part in rel.split("/")):
            self._error(404, "no such file")
            return
        try:
            base = dist.resolve()
            target = (base / rel).resolve()  # symlinks followed, so the check sees where they go
        except (OSError, RuntimeError, ValueError):
            self._error(404, "no such file")
            return
        if target != base and base not in target.parents:
            self._error(404, "no such file")
            return
        if not target.is_file():
            if os.path.splitext(rel.rstrip("/").rsplit("/", 1)[-1])[1]:
                self._error(404, "no such file")
                return
            target = base / "index.html"
        try:
            body = target.read_bytes()
        except OSError:
            self._error(404, "no such file")
            return
        ctype = mimetypes.guess_type(target.name)[0] or EXTRA_TYPES.get(target.suffix.lower()) \
            or "application/octet-stream"
        if ctype.startswith("text/") or ctype in TEXT_TYPES:
            ctype += "; charset=utf-8"
        cache = V2_IMMUTABLE if (base / "assets") in target.parents else "no-cache"
        self._send(200, body, ctype, [("Cache-Control", cache), ("X-Content-Type-Options", "nosniff")])

    def _v2_method(self) -> bool:
        """True once a method other than GET or HEAD on /v2 got 405."""
        path = urlsplit(self.path).path
        if path != "/v2" and not path.startswith("/v2/"):
            return False
        self.close_connection = True  # the body, if any, is never read
        self._json(405, {"error": "the v2 page answers GET and HEAD only"}, headers=[("Allow", "GET, HEAD")])
        return True

    def _static_asset(self, filename: str, content_type: str):
        for root in (self.state.code_root, Path(__file__).resolve().parent.parent.parent):
            path = root / filename
            if path.is_file():
                try:
                    self._send(200, path.read_bytes(), content_type, [("Cache-Control", "no-cache")])
                    return
                except OSError:
                    break
        self._error(404, "no such asset")

    # -- writes ------------------------------------------------------------

    def do_PUT(self):
        if self._v2_method():
            return
        path = urlsplit(self.path).path
        if path == "/api/read-state":
            self._read_state_put()
        else:
            # Every other path answers PUT as it did before this route: 501.
            self.send_error(501, "Unsupported method (%r)" % self.command)

    def do_POST(self):
        if self._v2_method():
            return
        path = urlsplit(self.path).path
        if path == "/api/append":
            self._append()
        elif path == "/api/login":
            self._login()
        elif path == "/api/onboard":
            self._onboard()
        else:
            self._error(404, "no such endpoint")

    def _read_json(self):
        """The parsed body, or None once 400 has been answered."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._error(400, "bad Content-Length")
            return None
        if length <= 0:
            self._error(400, "the body is empty")
            return None
        if length > MAX_BODY:
            self._error(400, "the body is too big")
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(400, "the body is not JSON")
            return None

    # -- who is writing ----------------------------------------------------

    def _owner_session(self) -> bool:
        """True when the request carries a live owner session cookie."""
        raw = self.headers.get("Cookie")
        if not raw:
            return False
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except CookieError:
            return False
        morsel = jar.get(COOKIE_NAME)
        return bool(morsel) and self.state.sessions.valid(morsel.value)

    def _role(self) -> str:
        """owner, editor or viewer."""
        if self._owner_session():
            return "owner"
        header = self.headers.get("Authorization")
        if header:
            result = self.state.tokens.verify(header)
            if result.ok:
                # The owner's own token carries the owner role, not editor.
                return "owner" if result.pid == "owner" else "editor"
        return "viewer"

    def _identity(self):
        """(role, participant id) of the writer."""
        role = self._role()
        if role == "owner":
            return ("owner", "owner")
        if role == "editor":
            result = self.state.tokens.verify(self.headers.get("Authorization"))
            return ("participant", result.pid)
        return ("viewer", None)

    # -- the owner's session ------------------------------------------------

    def _check_pin(self, pin) -> bool:
        """The permanent PIN (owner-pin.json), or the startup PIN: right,
        still valid, and not used yet."""
        state = self.state
        if ownerpin.check_pin(state.root, pin):
            return True
        if not isinstance(pin, str) or not state.pin or state.pin_used:
            return False
        if state.pin_expires < time.time():
            return False
        if not hmac.compare_digest(pin, state.pin):
            return False
        state.pin_used = True
        return True

    def _check_owner_token(self, token) -> bool:
        """The owner's own token: the credential that outlives the PIN."""
        if not isinstance(token, str):
            return False
        return self.state.tokens.verify("Bearer " + token, "owner").ok

    def _login(self):
        retry = self.state.login_locked()
        if retry is not None:
            self._json(429, {"error": "too many failed logins; retry later"},
                       headers=[("Retry-After", str(int(retry)))])
            return
        body = self._read_json()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(400, "the body must be a JSON object")
            return
        pin = body.get("pin")
        token = body.get("token")
        if pin is None and token is None:
            self._error(400, "expected a pin or a token")
            return
        ok = self._check_pin(pin) if pin is not None else self._check_owner_token(token)
        if not ok:
            self.state.record_login_failure()
            self._error(401, "that pin or token is not valid")
            return
        sid = self.state.sessions.create()
        cookie = "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Strict" % (
            COOKIE_NAME, sid, int(SESSION_TTL))
        self._json(200, {"ok": True, "viewer": "owner"}, headers=[("Set-Cookie", cookie)])

    def _onboard(self):
        """The owner session adds a participant: roster entry, key, kit.

        The minted token is returned once, in this response only; it is never
        logged and never appears in an error body.
        """
        if self._role() != "owner":
            header = self.headers.get("Authorization")
            if header and self.state.tokens.verify(header).ok:
                self._error(403, "only the owner may onboard a participant")
            else:
                self._error(401, "no valid owner session")
            return
        body = self._read_json()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(400, "the body must be a JSON object")
            return
        try:
            result = onboard(self.state.root, body.get("id"), body.get("name"),
                             body.get("kind"), repo=body.get("repo"),
                             check_in_min=body.get("checkInMin"),
                             parent=body.get("parent"),
                             color=body.get("color"), color_dark=body.get("colorDark"),
                             harness=body.get("harness"), code_root=self.state.code_root)
        except ValueError as e:
            self._error(400, str(e))
            return
        try:
            kit = Path(result["kit_path"]).read_text(encoding="utf-8")
        except OSError as e:
            self._error(500, "cannot read the kit: %s" % e)
            return
        self._json(200, {"token": result["token"], "kit": kit})

    # -- the owner's read state ----------------------------------------------

    def _read_state_get(self):
        if self._role() != "owner":
            self._error(403, "only the owner has a read state")
            return
        self._json(200, readstate.load(self.state.root))

    def _read_state_put(self):
        """One of {"thread", "t", "id"}, {"all_before": t} or {"thread", "muted": bool}."""
        if self._role() != "owner":
            self._error(403, "only the owner has a read state")
            return
        body = self._read_json()
        if body is None:
            return
        keys = set(body) if isinstance(body, dict) else None
        root = self.state.root
        try:
            if keys == {"thread", "t", "id"}:
                if not (_name(body["thread"]) and readstate.is_time(body["t"]) and _name(body["id"])):
                    raise ValueError("thread and id must be ids and t a time like 2026-01-02T03:04:05Z")
                state = readstate.mark(root, body["thread"], body["t"], body["id"])
            elif keys == {"all_before"}:
                if not readstate.is_time(body["all_before"]):
                    raise ValueError("all_before must be a time like 2026-01-02T03:04:05Z")
                state = readstate.mark_all(root, body["all_before"])
            elif keys == {"thread", "muted"}:
                if not (_name(body["thread"]) and isinstance(body["muted"], bool)):
                    raise ValueError("thread must be an id and muted true or false")
                state = readstate.set_muted(root, body["thread"], body["muted"])
            else:
                raise ValueError('expected {"thread", "t", "id"}, {"all_before"} or {"thread", "muted"}')
        except ValueError as e:
            self._error(400, str(e))
            return
        except OSError as e:
            self._error(500, "cannot write the read state: %s" % e)
            return
        self._json(200, state)

    def _me(self):
        role = self._role()
        if role == "owner":
            self._json(200, {"name": "Owner", "isOwner": True, "canEdit": True, "viewer": "owner"})
        elif role == "editor":
            self._json(200, {"name": None, "isOwner": False, "canEdit": True, "viewer": "editor"})
        else:
            self._json(200, {"name": None, "isOwner": False, "canEdit": False, "viewer": "viewer"})

    def _palette(self):
        # The candidate (color, colorDark) pairs the page's picker shows. The
        # roster check still runs at onboard time, so a stale pair is refused
        # with an honest 400 rather than silently stored.
        self._json(200, {"pairs": [list(p) for p in PALETTE]})

    # -- writes -------------------------------------------------------------

    def _line(self, status: int, ln) -> None:
        payload = {"seq": ln.seq, "t": ln.t, "from": ln.from_, "op": ln.op,
                   "collection": ln.collection, "id": ln.id, "data": ln.data}
        if ln.via is not None:
            payload["via"] = ln.via
        self._json(status, payload)

    def _append(self):
        body = self._read_json()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(400, "the body must be a JSON object")
            return
        if "t" in body:
            self._error(400, "the server stamps the line's t; leave it out")
            return
        data = body.get("data")
        if not isinstance(data, dict):
            self._error(400, "data must be a JSON object")
            return
        via = body.get("via")
        if via is not None:
            if not isinstance(via, str):
                self._error(400, "via must be a string")
                return
            if via == "import":
                self._error(400, "via 'import' marks rows from an old import; the API does not take it")
                return
        role, pid = self._identity()
        frm = body.get("from")
        collection = body.get("collection")
        if role == "viewer":
            # A viewer may read everything and write nothing; the owner's own
            # documents and the roster are the owner's alone.
            if frm == "owner" or collection == "participants":
                self._error(403, "only the owner may write this")
            else:
                self._error(401, "no valid token")
            return
        if role == "owner":
            if frm != "owner":
                self._error(403, "the owner writes as 'owner'")
                return
            if via is None:
                via = "page"
            if collection == "participants":
                if body.get("op") != "update":
                    self._error(400, "participants rows can only be updated")
                    return
                self._append_roster(body.get("id"), data, via)
                return
        else:
            if frm != pid:
                self._error(403, "the token belongs to another participant")
                return
            if collection == "participants":
                self._error(403, "the participants collection is the owner's alone")
                return
        # The same checks `relay.py append` runs.
        try:
            roster = load_roster(self.state.root / "participants.json")
            ln = append_line(self.state.root, frm, body.get("op"), collection, body.get("id"),
                             data, t=None, via=via, roster=roster)
        except ValueError as e:
            self._error(400, str(e))
            return
        except OSError as e:
            self._error(500, "cannot write the log: %s" % e)
            return
        self._line(201, ln)

    def _append_roster(self, doc_id, data, via):
        """The owner's write to participants/<pid>: the log line and the roster file."""
        if not isinstance(doc_id, str) or not doc_id:
            self._error(400, "the participants collection is keyed by participant id")
            return
        path = self.state.root / "participants.json"
        with _locked_onboard(self.state.root):
            try:
                ensure_roster(path)
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                self._error(500, "cannot read participants.json: %s" % e)
                return
            entries = raw.get("participants") if isinstance(raw, dict) else None
            if not isinstance(entries, list):
                self._error(500, "participants.json has no participants list")
                return
            index = None
            for i, entry in enumerate(entries):
                if isinstance(entry, dict) and entry.get("id") == doc_id:
                    index = i
                    break
            if index is None:
                self._error(400, "no participant %r in the roster" % doc_id)
                return
            merged = dict(entries[index])
            unknown = []
            for key, value in data.items():
                if key == "id":
                    if value != doc_id:
                        self._error(400, "the roster id and the doc id differ")
                        return
                    continue
                if key not in ROSTER_FIELDS:
                    unknown.append(key)
                    continue
                merged[key] = value
            if unknown:
                self._error(400, "unknown roster field: %s" % ", ".join(sorted(unknown)))
                return
            new_raw = dict(raw)
            new_entries = list(entries)
            new_entries[index] = merged
            new_raw["participants"] = new_entries
            problem = roster_problem(new_raw, path.parent)
            if problem:
                self._error(400, problem)
                return
            # A stale `wake` key stays in the file but must not enter the log line
            # (pushed with the repo).
            logged = {k: v for k, v in merged.items() if k != "wake"}
            try:
                roster = load_roster(path)
                ln = append_line(self.state.root, "owner", "update", "participants", doc_id, logged,
                                 t=None, via=via, roster=roster)
            except ValueError as e:
                self._error(400, str(e))
                return
            except OSError as e:
                self._error(500, "cannot write the log: %s" % e)
                return
            try:
                atomic_write_text(path, json.dumps(new_raw, indent=2, ensure_ascii=False) + "\n")
            except OSError as e:
                self._error(500, "cannot write participants.json: %s" % e)
                return
        self._line(201, ln)


class RunningServer:
    """The server in a thread: `with RunningServer(root) as srv: ...`.

    With `notify`, a Notifier polls the log on its own thread and tells live
    Claude Code sessions about new inbox items; its state persists at
    `notify_state` (None keeps it in memory). With `urgent_state` as well, each
    notifier pass also runs the urgent fallback (urgent.Fallback) on that file.
    With `reviews_state`, each pass also runs the Codex review scanner
    (reviews.Scanner) with its state in that file.
    """

    def __init__(self, root, port: int = 0, host: str = "127.0.0.1", tokens=None,
                 no_pin: bool = False, sessions=None, notify: bool = False,
                 notify_state=None, notify_poll_sec: float = 5.0, urgent_state=None,
                 reviews_state=None, autogrant: bool = False, code_root=None):
        self.root = Path(root)
        self.code_root = code_root
        self.autogrant = autogrant
        self._granter = None  # type: Optional[autogrant_mod.Granter]
        self.urgent_state = urgent_state
        self.reviews_state = reviews_state
        self.host = host
        self.port = port
        self.tokens = tokens
        self.no_pin = no_pin
        self.sessions = sessions
        self.notify = notify
        self.notify_state = notify_state
        self.notify_poll_sec = notify_poll_sec
        self._notifier = None  # type: Optional[Notifier]
        self._httpd = None
        self._thread = None

    # -- lifecycle ---------------------------------------------------------

    def start_server(self) -> "RunningServer":
        if self._httpd is not None:
            return self
        httpd = ThreadingHTTPServer((self.host, self.port), CrosstalkHandler)
        httpd.daemon_threads = True
        httpd.state = ServerState(self.root, self.tokens, sessions=self.sessions,
                                   code_root=self.code_root)  # type: ignore[attr-defined]
        if not self.no_pin:
            httpd.state.make_pin()  # type: ignore[attr-defined]
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05})
        self._thread.daemon = True
        self._thread.start()
        if self.notify:
            state = httpd.state  # type: ignore[attr-defined]
            fallback = (urgent_mod.Fallback(Path(self.urgent_state))
                        if self.urgent_state is not None else None)
            scanner = (reviews_mod.Scanner(self.root, Path(self.reviews_state))
                       if self.reviews_state is not None else None)
            state.notifier = Notifier(self.root, state.cache.snapshot, self.notify_state,
                                      poll_sec=self.notify_poll_sec, fallback=fallback,
                                      reviews=scanner, code_root=self.code_root)
            self._notifier = state.notifier
            self._notifier.start()
            # The granter posts as the coordinator, so only the server that owns the
            # notifier state runs it (a second server's notifier is disabled).
            if self.autogrant and not self._notifier.disabled:
                self._granter = autogrant_mod.Granter(self.root, state.cache.snapshot,
                                                      roster=state.cache.roster)
                self._granter.start()
        return self

    def close(self) -> None:
        # The notifier first, so it stops polling before the server goes away.
        granter, self._granter = self._granter, None
        if granter is not None:
            granter.stop()
        notifier, self._notifier = self._notifier, None
        if notifier is not None:
            notifier.stop()
        httpd, self._httpd = self._httpd, None
        thread, self._thread = self._thread, None
        if httpd is not None:
            httpd.state.stopping.set()  # type: ignore[attr-defined]
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=10)

    def __enter__(self) -> "RunningServer":
        return self.start_server()

    def __exit__(self, *exc):
        self.close()
        return False

    # -- the address -------------------------------------------------------

    @property
    def url(self) -> str:
        return "http://%s:%d" % (self.host, self.port)

    @property
    def state(self) -> Optional[ServerState]:
        """The shared state (sessions, the startup PIN); None before the start."""
        return self._httpd.state if self._httpd is not None else None  # type: ignore[attr-defined]

    @property
    def pin(self) -> Optional[str]:
        """The one-time startup PIN, when the server made one."""
        state = self.state
        return state.pin if state is not None else None


def start(root, port: int = 0, host: str = "127.0.0.1", tokens=None,
          no_pin: bool = False, sessions=None, notify: bool = False,
          notify_state=None, notify_poll_sec: float = 5.0) -> RunningServer:
    """Start a server over `root` and return it (a context manager).

    Port 0 asks the OS for an ephemeral port, which the tests use.
    `sessions` is the path where owner sessions persist across restarts;
    None keeps them in memory only (the tests' default). `notify` runs the
    live-session notifier (off by default here).
    """
    return RunningServer(root, port=port, host=host, tokens=tokens,
                         no_pin=no_pin, sessions=sessions, notify=notify,
                         notify_state=notify_state,
                         notify_poll_sec=notify_poll_sec).start_server()


DEFAULT_SESSIONS = Path.home() / ".crosstalk-sessions.json"
DEFAULT_NOTIFY_STATE = Path.home() / ".crosstalk-notify-state.json"


def serve(root, host: str = "127.0.0.1", port: int = 8484, no_pin: bool = False,
          tokens=None, sessions=None, notify: bool = True, autogrant: bool = True,
          code_root=None) -> None:
    """Run the server in the foreground and print the one-time startup PIN.

    The PIN is how the owner logs in on the page; it is good for one login
    and ten minutes. `--no-pin` turns it off (the owner token still works).
    Owner sessions persist (default ~/.crosstalk-sessions.json, 0600), so a
    restart no longer logs the owner out. `CROSSTALK_SESSIONS` overrides
    the path; an empty value keeps sessions in memory only.

    `notify` (on by default; `--no-notify` turns it off) runs the notifier
    that tells live Claude Code sessions about new inbox items. Its state
    lives in ~/.crosstalk-notify-state.json (`CROSSTALK_NOTIFY_STATE`; empty
    keeps it in memory). The same pass runs the urgent fallback: a macOS
    notification for an urgent item that no Codex hook has picked up in ten
    minutes (crosstalk/urgent.py; state ~/.crosstalk-urgent-state.json,
    `CROSSTALK_URGENT_STATE`, empty turns the fallback off). It also runs the
    Codex review scanner once a minute (crosstalk/reviews.py; state
    ~/.crosstalk-reviews-state.json, `CROSSTALK_REVIEWS_STATE`, empty turns it off).

    `autogrant` (on by default; `--no-autogrant` turns it off) runs the
    auto-granter with the notifier (crosstalk/autogrant.py): it posts the
    coordinator's GRANTED for the next land turn when a slot on the build host
    is free and the usage and load limits of config.json allow it. With no
    coordinator in the roster it does nothing. The file
    ~/.crosstalk-autogrant-off pauses it.

    `root` is the data root; `code_root` (default: `root`) is the folder the
    page, the icons and the version come from (`relay.py --data-root`).

    SIGTERM (how launchd stops the service) shuts down like Ctrl-C: the
    notifier stops cleanly, then the process exits with status 143
    (128 + SIGTERM).
    """
    if sessions is None:
        sessions = os.environ.get("CROSSTALK_SESSIONS", DEFAULT_SESSIONS) or None
    notify_state = os.environ.get("CROSSTALK_NOTIFY_STATE", DEFAULT_NOTIFY_STATE) or None
    server = RunningServer(root, port=port, host=host, tokens=tokens,
                           no_pin=no_pin, sessions=sessions, notify=notify,
                           notify_state=notify_state,
                           urgent_state=urgent_mod.state_path_from_env(),
                           reviews_state=reviews_mod.state_path_from_env(),
                           autogrant=notify and autogrant, code_root=code_root)

    class Terminated(Exception):
        pass

    def on_sigterm(_signum, _frame):
        raise Terminated()

    # Python's default SIGTERM ends the process without running `finally`,
    # which would skip the notifier's clean stop. The handler goes in before
    # the server starts and before the "serving" line: whoever reads that line
    # may send SIGTERM at once.
    main = threading.current_thread() is threading.main_thread()
    old_term = signal.signal(signal.SIGTERM, on_sigterm) if main else None
    terminated = False
    try:
        server.start_server()
        if server.pin:
            print("Login PIN: %s  (one login, ten minutes)" % server.pin, flush=True)
        print("Crosstalk is serving %s on %s" % (root, server.url), flush=True)
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    except Terminated:
        terminated = True
    finally:
        # A second SIGTERM or Ctrl-C must not cut the cleanup short.
        if main:
            old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            server.close()
        finally:
            if main:
                # None: a handler not set from Python; fall back to the default.
                signal.signal(signal.SIGINT, old_int if old_int is not None else signal.SIG_DFL)
                signal.signal(signal.SIGTERM, old_term if old_term is not None else signal.SIG_DFL)
    if terminated:
        sys.exit(128 + signal.SIGTERM)
