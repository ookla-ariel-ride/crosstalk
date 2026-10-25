"""Serve a replay of the logs over local HTTP.

The server is the page host and the database:

  GET  /                -> index.html, as it sits in the checkout
  GET  /api/snapshot    -> every collection replayed, plus server_time and commit
  GET  /api/<collection>-> one collection replayed
  GET  /api/me          -> the role of the caller (owner, editor, viewer)
  POST /api/login       -> the startup PIN or the owner token, then a session cookie
  POST /api/append      -> one line appended, with the same checks `relay.py append` runs

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
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .courier import atomic_write_text
from .logs import COLLECTIONS, append_line, check_all, check_all
from .onboard import PALETTE, onboard
from .participants import load_roster
from .replay import enrich_messages, load_all_lines, replay
from .tokens import TOKEN_FILE, Result, Tokens

# The collections the page reads. "participants" is the owner's roster.
KNOWN_COLLECTIONS = tuple(COLLECTIONS) + ("participants",)

JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
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
# The fields of a roster entry the owner may write.
ROSTER_FIELDS = ("name", "kind", "color", "colorDark", "checkInMin", "trust", "repo", "parent")


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


class ReplayCache:
    """The replayed documents, recomputed when the log files or the roster change."""

    def __init__(self, root):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._key = None
        self._docs = None
        self._entries = None

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
        roster = self.root / "participants.json"
        try:
            st = roster.stat()
            parts.append((roster.name, st.st_size, st.st_mtime_ns))
        except OSError:
            parts.append((roster.name, None, None))
        return tuple(parts)

    def snapshot(self) -> Tuple[Dict[Tuple[str, str], dict], Dict[str, dict]]:
        """(documents by (collection, id), roster entries by participant id)."""
        key = self._stamp()
        with self._lock:
            if key == self._key and self._docs is not None:
                return self._docs, self._entries
            roster_path = self.root / "participants.json"
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
            return docs, entries


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
    """What the handlers share: the served root, the replay cache, the tokens."""

    def __init__(self, root, tokens=None, sessions=None):
        self.root = Path(root)
        self.tokens = TokenStore(self.root, tokens)
        self.cache = ReplayCache(self.root)
        self.sessions = Sessions(sessions)
        self.pin = None  # type: Optional[str]
        self.pin_expires = 0.0
        self.pin_used = False
        self.login_failures = []  # type: List[float]
        self.login_lock = threading.Lock()

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
        except Exception:
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
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
        elif path.startswith("/api/"):
            self._collection(path[len("/api/"):])
        elif path == "/":
            self._index()
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
        self._json(200, {"server_time": now_iso(),
                         "commit": repo_commit(self.state.root),
                         "roster": entries,
                         "collections": collections})

    def _health(self):
        """The log's health: ok when nothing fatal, plus the fatal lines named.

        The page's "live" strip reads this: a fatal line surfaces as the
        Offline reason, and the server_time is the freshness clock.
        """
        root = self.state.root
        try:
            roster = load_roster(root / "participants.json")
        except (OSError, ValueError) as e:
            self._json(200, {"ok": False, "server_time": now_iso(),
                             "fatal": ["participants.json:0: %s" % e]})
            return
        problems = check_all(root, roster)
        fatal = ["%s:%d: %s" % (p.file, p.line, p.message) for p in problems if p.fatal]
        self._json(200, {"ok": not fatal, "server_time": now_iso(), "fatal": fatal})

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
        path = self.state.root / "index.html"
        try:
            body = path.read_bytes()
        except OSError:
            self._error(404, "no page is served from this checkout")
            return
        self._send(200, body, HTML_TYPE, [("Cache-Control", "no-cache")])

    # -- writes ------------------------------------------------------------

    def do_POST(self):
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
        """The startup PIN: right, still valid, and not used yet."""
        state = self.state
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
                             color=body.get("color"), color_dark=body.get("colorDark"))
        except ValueError as e:
            self._error(400, str(e))
            return
        try:
            kit = Path(result["kit_path"]).read_text(encoding="utf-8")
        except OSError as e:
            self._error(500, "cannot read the kit: %s" % e)
            return
        self._json(200, {"token": result["token"], "kit": kit})

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
                self._error(400, "via 'import' belongs to the importer, not to the API")
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
        try:
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
        try:
            roster = load_roster(path)
            ln = append_line(self.state.root, "owner", "update", "participants", doc_id, merged,
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
    """The server in a thread: `with RunningServer(root) as srv: ...`."""

    def __init__(self, root, port: int = 0, host: str = "127.0.0.1", tokens=None,
                 no_pin: bool = False, sessions=None):
        self.root = Path(root)
        self.host = host
        self.port = port
        self.tokens = tokens
        self.no_pin = no_pin
        self.sessions = sessions
        self._httpd = None
        self._thread = None

    # -- lifecycle ---------------------------------------------------------

    def start_server(self) -> "RunningServer":
        if self._httpd is not None:
            return self
        httpd = ThreadingHTTPServer((self.host, self.port), CrosstalkHandler)
        httpd.daemon_threads = True
        httpd.state = ServerState(self.root, self.tokens, sessions=self.sessions)  # type: ignore[attr-defined]
        if not self.no_pin:
            httpd.state.make_pin()  # type: ignore[attr-defined]
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05})
        self._thread.daemon = True
        self._thread.start()
        return self

    def close(self) -> None:
        httpd, self._httpd = self._httpd, None
        thread, self._thread = self._thread, None
        if httpd is not None:
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
          no_pin: bool = False, sessions=None) -> RunningServer:
    """Start a server over `root` and return it (a context manager).

    Port 0 asks the OS for an ephemeral port, which the tests use.
    `sessions` is the path where owner sessions persist across restarts;
    None keeps them in memory only (the tests' default).
    """
    return RunningServer(root, port=port, host=host, tokens=tokens,
                         no_pin=no_pin, sessions=sessions).start_server()


DEFAULT_SESSIONS = Path.home() / ".crosstalk-sessions.json"


def serve(root, host: str = "127.0.0.1", port: int = 8484, no_pin: bool = False,
          tokens=None, sessions=None) -> None:
    """Run the server in the foreground and print the one-time startup PIN.

    The PIN is how the owner logs in on the page; it is good for one login
    and ten minutes. `--no-pin` turns it off (the owner token still works).
    Owner sessions persist (default ~/.crosstalk-sessions.json, 0600), so a
    restart no longer logs the owner out. `CROSSTALK_SESSIONS` overrides
    the path; an empty value keeps sessions in memory only.
    """
    if sessions is None:
        sessions = os.environ.get("CROSSTALK_SESSIONS", DEFAULT_SESSIONS) or None
    server = RunningServer(root, port=port, host=host, tokens=tokens,
                           no_pin=no_pin, sessions=sessions)
    server.start_server()
    if server.pin:
        print("Login PIN: %s  (one login, ten minutes)" % server.pin, flush=True)
    print("Crosstalk is serving %s on %s" % (root, server.url), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
