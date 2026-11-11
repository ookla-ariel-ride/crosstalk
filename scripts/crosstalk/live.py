"""Live-session registration: running Claude Code and Hermes sessions check in here.

A SessionStart hook runs `relay.py live register <pid>`, which writes
<live_dir>/<pid>--<session_id>.json (dir 0700, file 0600, atomic) holding the
session's inbox socket and token. A SessionEnd hook removes it. Nothing here
ever prints or logs a token.

A Hermes session is registered by the Crosstalk plugin for Hermes
(scripts/hermes-plugin/crosstalk) with `"harness": "hermes"`, its Hermes
session key and the pid of the Hermes process that hosts it, and no socket or
token: the plugin delivers its items itself, so the server's notifier skips
it (notify.py). A file without `harness` is a Claude Code registration.
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
MAX_SESSION_KEY = 256
CLAUDE = "claude-code"
HERMES = "hermes"
ENV_SOCKET = "CLAUDE_CODE_MESSAGING_SOCKET"
ENV_TOKEN = "CLAUDE_CODE_MESSAGING_TOKEN"
ENV_BG_TOKENS = "CLAUDE_BG_SOCKET_TOKENS_PATH"


class Registration(NamedTuple):
    pid: str
    session_id: str
    socket: str
    token: str
    registered: str
    path: Path
    harness: str = CLAUDE
    session_key: str = ""   # Hermes only: the session's routing key
    host_pid: int = 0       # Hermes only: the Hermes process hosting the session


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


def session_key_problem(key) -> Optional[str]:
    if not isinstance(key, str):
        return "session_key is not a string"
    if len(key) > MAX_SESSION_KEY:
        return "session_key is too long"
    if not key.isprintable():
        return "session_key has unprintable characters"
    return None


def host_alive(pid) -> bool:
    """True when process `pid` exists (it may belong to another user)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def host_problem(pid) -> Optional[str]:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "bad host_pid"
    if not host_alive(pid):
        return "host process gone"
    return None


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def is_spare(env: Mapping[str, str]) -> bool:
    """True for an idle session Claude Code's daemon starts ahead of time.

    Spares run SessionStart in the project too, so without this check every
    project gets a second registration for a session nobody is using.
    """
    return os.path.basename(env.get(ENV_BG_TOKENS) or "").startswith("spare-")


def register(root: Path, pid: str, env: Mapping[str, str], stdin_text: str,
             now: float) -> Optional[Path]:
    """Write this session's registration; None (and a stderr note) on any problem.

    A pre-started spare session is skipped quietly.
    """
    if is_spare(env):
        return None
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
        _write(path, {"pid": pid, "session_id": sid, "socket": sock, "token": token,
                      "registered": _iso(now)})
        return path
    except OSError as e:
        _err("could not write registration: %s" % e.strerror)
        return None


def _write(path: Path, doc: dict) -> None:
    """Write one registration file atomically, 0600."""
    tmp = path.parent / (".%s.%d.tmp" % (path.name, os.getpid()))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(path))


def register_hermes(root: Path, pid: str, session_id: str, session_key: str, now: float,
                    host_pid: Optional[int] = None) -> Optional[Path]:
    """Register a Hermes session (the Crosstalk plugin calls this); None on any problem.

    The file holds no socket or token; `host_pid` (default: this process, the
    Hermes gateway the plugin runs in) lets a stale file be dropped once that
    process has gone.
    """
    if not _known_pid(root, pid):
        _err("unknown participant %r" % (pid,))
        return None
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        _err("bad session_id")
        return None
    host = os.getpid() if host_pid is None else host_pid
    why = session_key_problem(session_key) or host_problem(host)
    if why:
        _err(why)
        return None
    d = live_dir()
    try:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(str(d), 0o700)
        path = d / ("%s--%s.json" % (pid, session_id))
        _write(path, {"pid": pid, "session_id": session_id, "harness": HERMES,
                      "session_key": session_key, "host_pid": host, "registered": _iso(now)})
        return path
    except OSError as e:
        _err("could not write registration: %s" % e.strerror)
        return None


def unregister(pid: str, stdin_text: str) -> bool:
    """Remove this session's registration; True when a file was removed."""
    return unregister_session(pid, _session_id(stdin_text))


def unregister_session(pid: str, sid: Optional[str]) -> bool:
    """Remove the registration of session `sid`; True when a file was removed."""
    if (not isinstance(sid, str) or not SESSION_ID_RE.fullmatch(sid) or not isinstance(pid, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", pid)):
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
    harness = doc.get("harness", CLAUDE)
    if harness == HERMES:
        why = session_key_problem(doc.get("session_key")) or host_problem(doc.get("host_pid"))
        if why:
            return why
        return Registration(pid, sid, "", "", str(doc.get("registered", "")), f, HERMES,
                            doc["session_key"], doc["host_pid"])
    if harness != CLAUDE:
        return "unknown harness"
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


def relay_args(root, code_root=None) -> List[str]:
    """`<code_root>/scripts/relay.py`, plus `--data-root <root>` when the data is
    in another folder (code_root defaults to root), as a list of arguments."""
    code = Path(root) if code_root is None else Path(code_root)
    args = [str(code / "scripts" / "relay.py")]
    if code != Path(root):
        args += ["--data-root", str(root)]
    return args


def relay_command(root, code_root=None, quote=True) -> str:
    """relay_args as a shell command fragment, each path quoted.

    quote=False keeps the bare path when the two folders are one, for the
    texts that always printed it so.
    """
    args = relay_args(root, code_root)
    if not quote and len(args) == 1:
        return args[0]
    return " ".join(shlex.quote(a) for a in args)


def hooks_json(root: Path, pid: str, code_root: Optional[Path] = None) -> str:
    """The settings.local.json hook block for `pid`, with absolute paths.

    root is the data folder; the command names `<code_root>/scripts/relay.py`
    and carries `--data-root <root>` when the two differ (relay_command).
    """
    root = Path(root).resolve()
    relay = relay_command(root, None if code_root is None else Path(code_root).resolve())

    def hook(verb):
        return [{"hooks": [{"type": "command",
                            "command": "python3 %s live %s %s" % (relay, verb, pid)}]}]
    return json.dumps({"hooks": {"SessionStart": hook("register"),
                                 "SessionEnd": hook("unregister")}}, indent=2)
