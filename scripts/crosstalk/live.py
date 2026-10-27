"""Live-session registration: running Claude Code sessions check in here.

A SessionStart hook runs `relay.py live register <pid>`, which writes
<live_dir>/<pid>--<session_id>.json (dir 0700, file 0600, atomic) holding the
session's inbox socket and token. A SessionEnd hook removes it. Nothing here
ever prints or logs a token.
"""

import json
import os
import re
import shlex
import stat
import sys
import time
from pathlib import Path
from typing import List, Mapping, NamedTuple, Optional, Tuple

from .participants import load_roster

SOCKET_DIR_RES = [
    re.compile(r"^/(private/)?tmp/cc-socks(-\d+)?$"),
    re.compile(r"^/(private/)?var/folders/.+/cc-socks$"),
    re.compile(r"^/run/user/\d+/cc-socks$"),
]
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
MAX_TOKEN = 512
ENV_SOCKET = "CLAUDE_CODE_MESSAGING_SOCKET"
ENV_TOKEN = "CLAUDE_CODE_MESSAGING_TOKEN"


class Registration(NamedTuple):
    pid: str
    session_id: str
    socket: str
    token: str
    registered: str
    path: Path


def live_dir() -> Path:
    env = os.environ.get("CROSSTALK_LIVE_DIR")
    return Path(env) if env else Path.home() / ".crosstalk-live"


def _err(msg: str) -> None:
    print("live: " + msg, file=sys.stderr)


def socket_problem(path) -> Optional[str]:
    """Why `path` is not an acceptable inbox socket, or None."""
    if not isinstance(path, str) or not path or "\0" in path or not os.path.isabs(path):
        return "socket is not an absolute path"
    if not any(r.match(os.path.dirname(path)) for r in SOCKET_DIR_RES):
        return "socket is outside Claude Code's socket directories"
    try:
        st = os.stat(path)
    except (OSError, ValueError) as e:
        return "socket not usable: %s" % (getattr(e, "strerror", None) or e.__class__.__name__)
    if not stat.S_ISSOCK(st.st_mode):
        return "socket path is not a socket"
    if st.st_uid != os.getuid():
        return "socket is not owned by the current user"
    return None


def token_problem(token) -> Optional[str]:
    if not isinstance(token, str) or not token:
        return "token is empty"
    if len(token) > MAX_TOKEN:
        return "token is too long"
    if not token.isprintable():
        return "token has unprintable characters"
    return None


def _known_pid(root: Path, pid) -> bool:
    try:
        return isinstance(pid, str) and pid in load_roster(Path(root) / "participants.json").participants
    except (ValueError, OSError):
        return False


def _session_id(stdin_text: str) -> Optional[str]:
    try:
        doc = json.loads(stdin_text)
    except (ValueError, TypeError):
        return None
    sid = doc.get("session_id") if isinstance(doc, dict) else None
    return sid if isinstance(sid, str) and SESSION_ID_RE.fullmatch(sid) else None


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def register(root: Path, pid: str, env: Mapping[str, str], stdin_text: str,
             now: float) -> Optional[Path]:
    """Write this session's registration; None (and a stderr note) on any problem."""
    if not _known_pid(root, pid):
        _err("unknown participant %r" % (pid,))
        return None
    sid = _session_id(stdin_text)
    if sid is None:
        _err("hook input has no valid session_id")
        return None
    sock = env.get(ENV_SOCKET)
    token = env.get(ENV_TOKEN)
    if not sock or not token:
        _err("%s / %s not set" % (ENV_SOCKET, ENV_TOKEN))
        return None
    why = socket_problem(sock) or token_problem(token)
    if why:
        _err(why)
        return None
    d = live_dir()
    try:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(str(d), 0o700)
        path = d / ("%s--%s.json" % (pid, sid))
        # /clear and resume start a new session_id on the same socket: replace the old one.
        for other in d.glob("*--*.json"):
            if other == path:
                continue
            try:
                if json.loads(other.read_text(encoding="utf-8")).get("socket") == sock:
                    other.unlink()
            except (OSError, ValueError, AttributeError):
                continue
        doc = {"pid": pid, "session_id": sid, "socket": sock, "token": token,
               "registered": _iso(now)}
        tmp = d / (".%s.%d.tmp" % (path.name, os.getpid()))
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.chmod(str(tmp), 0o600)
        os.replace(str(tmp), str(path))
        return path
    except OSError as e:
        _err("could not write registration: %s" % e.strerror)
        return None


def unregister(pid: str, stdin_text: str) -> bool:
    """Remove this session's registration; True when a file was removed."""
    sid = _session_id(stdin_text)
    if sid is None or not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", pid):
        return False
    try:
        (live_dir() / ("%s--%s.json" % (pid, sid))).unlink()
        return True
    except OSError:
        return False


def _load_one(root: Path, f: Path):
    """A Registration for file `f`, or a string saying why it is invalid."""
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return "unreadable (%s)" % e.__class__.__name__
    if not isinstance(doc, dict):
        return "unreadable (ValueError)"
    pid, sid = doc.get("pid"), doc.get("session_id")
    if not _known_pid(root, pid):
        return "unknown participant"
    if not isinstance(sid, str) or not SESSION_ID_RE.fullmatch(sid):
        return "bad session_id"
    if f.name != "%s--%s.json" % (pid, sid):
        return "file name does not match contents"
    why = socket_problem(doc.get("socket")) or token_problem(doc.get("token"))
    if why:
        return why
    return Registration(pid, sid, doc["socket"], doc["token"], str(doc.get("registered", "")), f)


def load_registrations(root: Path) -> Tuple[List[Registration], List[str]]:
    """Valid registrations, plus one problem string per invalid file (never with a token)."""
    d = live_dir()
    regs, problems = [], []  # type: List[Registration], List[str]
    try:
        files = sorted(d.glob("*--*.json"))
    except OSError:
        return regs, problems
    for f in files:
        try:
            reg = _load_one(root, f)
        except Exception as e:  # one bad file must never take the others down
            problems.append("%s: unreadable (%s)" % (f.name, e.__class__.__name__))
            continue
        if isinstance(reg, str):
            problems.append("%s: %s" % (f.name, reg))
        else:
            regs.append(reg)
    return regs, problems


def hooks_json(root: Path, pid: str) -> str:
    """The settings.local.json hook block for `pid`, with absolute paths."""
    relay = shlex.quote(str(Path(root).resolve() / "scripts" / "relay.py"))

    def hook(verb):
        return [{"hooks": [{"type": "command",
                            "command": "python3 %s live %s %s" % (relay, verb, pid)}]}]
    return json.dumps({"hooks": {"SessionStart": hook("register"),
                                 "SessionEnd": hook("unregister")}}, indent=2)
