"""Serve a replay of the logs over local HTTP.

The server is the page host and the database. Reads are replays of
`log/` (the source of truth), replayed per request but cached on each
log file's size and mtime, so a session that appends straight to a file
is visible on the next request without a restart.

Python 3.9, standard library only.
"""
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .logs import COLLECTIONS, append_line
from .participants import load_roster
from .replay import load_all_lines, replay
from .tokens import TOKEN_FILE, Result, Tokens

# The collections the page reads. "participants" is the owner's roster.
KNOWN_COLLECTIONS = tuple(COLLECTIONS) + ("participants",)

JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
MAX_BODY = 1024 * 1024


def now_iso() -> str:
    """The server's clock, in the log's timestamp format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def repo_commit(root) -> Optional[str]:
    """The short hash of the served checkout's HEAD, or None when there is none.

    Read-only: the server never commits and never touches the working tree.
    """
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
            docs = replay(load_all_lines(self.root, roster))
            entries = {}
            for entry in raw.get("participants", []):
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    entries[entry["id"]] = entry
            self._key = key
            self._docs = docs
            self._entries = entries
            return docs, entries


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


class ServerState:
    """What the handlers share: the served root, the replay cache, the tokens."""

    def __init__(self, root, tokens=None):
        self.root = Path(root)
        self.tokens = TokenStore(self.root, tokens)
        self.cache = ReplayCache(self.root)


class CrosstalkHandler(BaseHTTPRequestHandler):
    """The JSON API. Every response is JSON, except the page itself."""

    server_version = "Crosstalk/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> ServerState:
        return self.server.state  # type: ignore[attr-defined]

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

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, JSON_TYPE)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # -- reads -------------------------------------------------------------

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/snapshot":
            self._snapshot()
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
        self._send(200, body, HTML_TYPE)

    # -- writes ------------------------------------------------------------

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/append":
            self._append()
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

    def _identity(self):
        """(role, participant id) of the writer, or None when there is no token."""
        header = self.headers.get("Authorization")
        if not header:
            return None
        result = self.state.tokens.verify(header)
        if not result.ok:
            return None
        if result.pid == "owner":
            return ("owner", "owner")
        return ("participant", result.pid)

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
        identity = self._identity()
        if identity is None:
            self._error(401, "no valid token")
            return
        role, pid = identity
        frm = body.get("from")
        collection = body.get("collection")
        if role == "owner":
            if frm != "owner":
                self._error(403, "the owner writes as 'owner'")
                return
        else:
            if frm != pid:
                self._error(403, "the token belongs to another participant")
                return
        if collection == "participants":
            self._error(403, "the participants collection is the owner's alone")
            return
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
        payload = {"seq": ln.seq, "t": ln.t, "from": ln.from_, "op": ln.op,
                   "collection": ln.collection, "id": ln.id, "data": ln.data}
        if ln.via is not None:
            payload["via"] = ln.via
        self._json(201, payload)


class RunningServer:
    """The server in a thread: `with RunningServer(root) as srv: ...`."""

    def __init__(self, root, port: int = 0, host: str = "127.0.0.1", tokens=None):
        self.root = Path(root)
        self.host = host
        self.port = port
        self.tokens = tokens
        self._httpd = None
        self._thread = None

    # -- lifecycle ---------------------------------------------------------

    def start_server(self) -> "RunningServer":
        if self._httpd is not None:
            return self
        httpd = ThreadingHTTPServer((self.host, self.port), CrosstalkHandler)
        httpd.daemon_threads = True
        httpd.state = ServerState(self.root, self.tokens)  # type: ignore[attr-defined]
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2})
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


def start(root, port: int = 0, host: str = "127.0.0.1", tokens=None) -> RunningServer:
    """Start a server over `root` and return it (a context manager).

    Port 0 asks the OS for an ephemeral port, which the tests use.
    """
    return RunningServer(root, port=port, host=host, tokens=tokens).start_server()
