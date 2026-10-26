"""Per-participant wake config: the command that wakes a project's agent.

The config lives in wake.json at the repo root, keyed by participant id. It is
git-ignored (like tokens.json), so commands and absolute paths never reach the
pushed repo; participants.json, which the housekeeper pushes, never carries it.
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .courier import atomic_write_text
from .participants import Roster, load_roster
from .requests import inbox_for, pending_recipients, unanswered_owner_messages

WAKE_FILE = "wake.json"
DEFAULTS = {"timeoutSec": 900, "cooldownSec": 300, "dailyCap": 30}
KEYS = ("command", "cwd") + tuple(DEFAULTS)
# Ids go into the prompt and argv, so only plain ones can ring an agent.
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


@dataclass
class WakeConfig:
    command: List[str]
    cwd: str
    timeoutSec: int
    cooldownSec: int
    dailyCap: int


def wake_problem(pid: str, raw) -> Optional[str]:
    """The reason a wake value is invalid, or None when it is acceptable.

    A wake is an object with a non-empty command (list of non-empty strings)
    and a cwd naming an existing directory. timeoutSec, cooldownSec and
    dailyCap are optional positive ints. Unknown keys are rejected.
    """
    if not isinstance(raw, dict):
        return "participant %s: wake must be an object" % pid
    unknown = sorted(k for k in raw if k not in KEYS)
    if unknown:
        return "participant %s: wake has unknown key %s" % (pid, ", ".join(repr(k) for k in unknown))
    command = raw.get("command")
    if (not isinstance(command, list) or not command
            or not all(isinstance(a, str) and a for a in command)):
        return "participant %s: wake command must be a non-empty list of non-empty strings" % pid
    cwd = raw.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return "participant %s: wake cwd must be a directory path" % pid
    if not Path(cwd).is_dir():
        return "participant %s: wake cwd %r is not an existing directory" % (pid, cwd)
    for key in DEFAULTS:
        v = raw.get(key)
        if key in raw and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            return "participant %s: wake %s must be a positive integer, got %r" % (pid, key, v)
    return None


def wake_config(pid: str, raw) -> Optional[WakeConfig]:
    """The WakeConfig for a raw wake value, or None when absent or invalid."""
    if raw is None or wake_problem(pid, raw) is not None:
        return None
    opts = {k: raw.get(k, d) for k, d in DEFAULTS.items()}
    return WakeConfig(command=list(raw["command"]), cwd=raw["cwd"], **opts)


def load_wake(root: Path) -> Tuple[Dict[str, WakeConfig], Dict[str, str]]:
    """Read root/wake.json: (valid configs by pid, problems by pid).

    A missing wake.json wakes nobody: ({}, {}). A pid that is not in
    participants.json is a problem and is never woken. Raises ValueError when
    wake.json is not a JSON object, and ValueError or OSError when it or
    participants.json cannot be read.
    """
    path = Path(root) / WAKE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError("%s: invalid JSON: %s" % (path, e))
    if not isinstance(raw, dict):
        raise ValueError("%s: expected an object keyed by participant id" % path)
    known = load_roster(Path(root) / "participants.json").participants
    configs = {}  # type: Dict[str, WakeConfig]
    problems = {}  # type: Dict[str, str]
    for pid, value in raw.items():
        if pid not in known:
            problems[pid] = ("participant %s: in %s but not in participants.json; ignored"
                             % (pid, WAKE_FILE))
            continue
        problem = wake_problem(pid, value)
        if problem:
            problems[pid] = problem
        else:
            configs[pid] = wake_config(pid, value)
    return configs, problems


def roster_wake_keys(root: Path) -> List[str]:
    """Ids whose participants.json entry still has a `wake` key (ignored there).

    Raises ValueError or OSError when participants.json cannot be read.
    """
    path = Path(root) / "participants.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError("%s: invalid JSON: %s" % (path, e))
    entries = raw.get("participants") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return []
    return [e["id"] for e in entries
            if isinstance(e, dict) and isinstance(e.get("id"), str) and "wake" in e]


def actionable_items(docs: Dict, pid: str, roster: Roster) -> List[str]:
    """Sorted ids of the inbox items that should wake pid.

    Open requests addressed to pid that pid has not answered, plus owner
    messages pid has not replied to. Items whose sender is not approved in the
    roster are dropped, so a quarantined participant cannot ring an agent; so
    are pid's own messages (an @Projects request, or one listing itself in
    `to`, must not wake its sender), and items whose id is not a plain
    SAFE_ID_RE id, since ids are written into the prompt and the argv.
    """
    ids = set()
    for did, doc, replies in inbox_for(docs, pid, roster):
        if pid in pending_recipients(doc, replies, roster):
            ids.add(did)
    for did, _doc, _replies in unanswered_owner_messages(docs, pid, roster):
        ids.add(did)
    out = set()
    for did in ids:
        if not isinstance(did, str) or not SAFE_ID_RE.fullmatch(did):
            continue
        sender = docs[("messages", did)].get("from")
        if sender == pid:
            continue
        p = roster.participants.get(sender) if isinstance(sender, str) else None
        if p is not None and p.trust == "approved":
            out.add(did)
    return sorted(out)


def render_prompt(name: str, pid: str, ids: List[str], page: str, root: Path) -> str:
    """The prompt handed to a woken agent: which items wait and how to answer them."""
    return (
        "You are %s on Crosstalk (%s). New items for you: %s. "
        "Run `python3 %s/scripts/relay.py inbox %s`, answer each of those items "
        "with `relay.py reply`, then stop. If an item asks for substantial work, "
        "say in your reply what you will do and when, rather than doing it all now."
        % (name, page, ", ".join(ids), root, pid)
    )


def render_command(cfg: WakeConfig, prompt: str, pid: str, ids: List[str]) -> List[str]:
    """cfg.command with {prompt}, {pid} and {ids} (comma-joined) filled in per argument."""
    joined = ",".join(ids)
    return [a.replace("{prompt}", prompt).replace("{pid}", pid).replace("{ids}", joined)
            for a in cfg.command]


STATUSES = ("idle", "running", "ok", "failed", "timed-out", "cooling-down", "capped",
            "misconfigured", "interrupted")
TRANSIENT = ("cooling-down", "capped", "misconfigured")
ISO = "%Y-%m-%dT%H:%M:%SZ"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime(ISO)


def _parse_iso(s) -> Optional[float]:
    try:
        return datetime.strptime(s, ISO).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _day(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def _killpg(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _group_alive(pgid: int) -> bool:
    """True while any process is left in the process group pgid."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Waker:
    """Polls the log and wakes each configured participant's agent for new items.

    State per pid: rung ids (never ring again while actionable), run starts for
    the current UTC day, and the last run record. poll_once does one pass;
    start/stop run it on a background thread. All limit and timeout decisions
    use the injected clock.
    """

    def __init__(self, root: Path, snapshot: Callable[[], Tuple[Dict, Dict]],
                 state_path: Optional[Path], log_dir: Path,
                 clock: Callable[[], float] = time.time, poll_sec: float = 5.0,
                 kill_grace: float = 10.0):
        self.root = Path(root)
        self.snapshot = snapshot
        self.state_path = Path(state_path) if state_path is not None else None
        self.log_dir = Path(log_dir)
        self.clock = clock
        self.poll_sec = poll_sec
        self.kill_grace = kill_grace
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None  # type: Optional[threading.Thread]
        self._saved = None  # type: Optional[str]  # the text last written
        self._state = self._load()  # type: Dict[str, dict]
        self._running = {}  # type: Dict[str, dict]
        self._view = {}  # type: Dict[str, dict]
        self._shown = []  # type: List[str]
        for pid, st in self._state.items():
            self._view[pid] = self._view_of_last(st.get("last"))

    # persistence

    def _load(self) -> Dict[str, dict]:
        if self.state_path is None or not self.state_path.exists():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print("crosstalk wake: ignoring unreadable state %s: %s" % (self.state_path, e),
                  file=sys.stderr)
            return {}
        state = {}
        if not isinstance(raw, dict):
            return state
        for pid, st in raw.items():
            if not isinstance(st, dict):
                continue
            # A hand-edited file must not crash-loop the server: anything but a
            # list counts as empty.
            rung = st.get("rung")
            runs = st.get("runs")
            rung = [x for x in rung if isinstance(x, str)] if isinstance(rung, list) else []
            runs = [x for x in runs if isinstance(x, str)] if isinstance(runs, list) else []
            last = st.get("last") if isinstance(st.get("last"), dict) else None
            if last is not None and last.get("status") == "running":
                # The server died without stopping this run (a crash, or a
                # signal it could not handle). Its child has its own session
                # and may still be running: end it before anything can start
                # a second run beside it.
                last = dict(last, status="interrupted")
                self._kill_orphan(pid, last.pop("pgid", None))
            elif last is not None:
                last = dict(last)
                last.pop("pgid", None)
            state[pid] = {"rung": rung, "runs": runs, "last": last}
        return state

    def _kill_orphan(self, pid: str, pgid) -> None:
        """SIGTERM the recorded process group, then SIGKILL it after the grace."""
        if isinstance(pgid, bool) or not isinstance(pgid, int) or pgid <= 1:
            return
        if pgid == os.getpgrp() or not _group_alive(pgid):
            return
        print("crosstalk wake: %s: ending process group %d left running by a previous server"
              % (pid, pgid), file=sys.stderr)
        _killpg(pgid, signal.SIGTERM)
        deadline = time.monotonic() + self.kill_grace
        while time.monotonic() < deadline:
            if not _group_alive(pgid):
                return
            time.sleep(0.05)
        _killpg(pgid, signal.SIGKILL)

    def _save(self) -> None:
        """Write the state file, unless it already holds exactly this state."""
        if self.state_path is None:
            return
        text = json.dumps(self._state, indent=2, sort_keys=True) + "\n"
        if text == self._saved:
            return
        try:
            atomic_write_text(self.state_path, text)
            os.chmod(str(self.state_path), 0o600)
            self._saved = text
        except OSError as e:
            print("crosstalk wake: cannot save state %s: %s" % (self.state_path, e),
                  file=sys.stderr)

    # view

    @staticmethod
    def _view_of_last(last: Optional[dict]) -> dict:
        if not last:
            return {"status": "idle", "since": None, "ids": [], "log": None}
        return {"status": last.get("status", "idle"),
                "since": last.get("ended") or last.get("started"),
                "ids": list(last.get("ids") or []), "log": last.get("log")}

    def _set(self, pid: str, status: str, ids: Optional[List[str]] = None,
             log: Optional[str] = None, keep_log: bool = True) -> None:
        old = self._view.get(pid) or {}
        if log is None and keep_log:
            log = old.get("log")
        new = {"status": status, "ids": list(ids if ids is not None else old.get("ids", [])),
               "log": log}
        same = old.get("status") == status and old.get("ids") == new["ids"]
        new["since"] = old.get("since") if same else _iso(self.clock())
        self._view[pid] = new

    def status(self) -> Dict[str, dict]:
        """{pid: {status, since, ids, log}} for each roster pid with a wake.json entry."""
        with self._lock:
            return {pid: dict(self._view[pid], ids=list(self._view[pid]["ids"]))
                    for pid in self._shown if pid in self._view}

    # processes

    def _finish(self, pid: str, status: str, code: Optional[int]) -> None:
        run = self._running.pop(pid)
        last = self._state.setdefault(pid, {"rung": [], "runs": [], "last": None})["last"] or {}
        last.update(status=status, ended=_iso(self.clock()), exit=code)
        last.pop("pgid", None)
        self._state[pid]["last"] = last
        self._set(pid, status, ids=run["ids"], log=run["log"])

    def _reap(self) -> bool:
        changed = False
        now = self.clock()
        for pid in list(self._running):
            run = self._running[pid]
            pgid = run["proc"].pid
            if run["term_at"] is None:
                code = run["proc"].poll()
                if code is not None:
                    self._finish(pid, "ok" if code == 0 else "failed", code)
                    changed = True
                elif now >= run["started"] + run["timeout"]:
                    _killpg(pgid, signal.SIGTERM)
                    run["term_at"] = now
                continue
            # Timed out and sent SIGTERM. The run ends once the whole group is
            # gone, or after the grace when the group gets SIGKILL whether or
            # not the leader has already exited.
            if not run["killed"] and now >= run["term_at"] + self.kill_grace:
                _killpg(pgid, signal.SIGKILL)
                run["killed"] = True
            code = run["proc"].poll()
            if code is not None and (run["killed"] or not _group_alive(pgid)):
                self._finish(pid, "timed-out", code)
                changed = True
        return changed

    def _spawn(self, pid: str, cfg: WakeConfig, ids: List[str], roster: Roster) -> None:
        now = self.clock()
        started = _iso(now)
        st = self._state[pid]
        st["rung"] = sorted(set(st["rung"]) | set(ids))
        st["runs"].append(started)
        log = self.log_dir / ("%s-%s.log" % (pid, datetime.fromtimestamp(now, timezone.utc)
                                              .strftime("%Y%m%dT%H%M%SZ")))
        p = roster.participants.get(pid)
        prompt = render_prompt(p.name if p else pid, pid, ids, roster.page, self.root)
        argv = render_command(cfg, prompt, pid, ids)
        env = dict(os.environ, CROSSTALK_WAKE="1", CROSSTALK_PID=pid,
                   CROSSTALK_IDS=",".join(ids))
        st["last"] = {"status": "running", "started": started, "ended": None, "exit": None,
                      "ids": list(ids), "log": str(log)}

        def failed(msg: str, logged: bool) -> None:
            if not logged:
                print("crosstalk wake: %s: %s" % (pid, msg), file=sys.stderr)
            where = str(log) if logged else None
            st["last"].update(status="failed", ended=started, exit=None, log=where)
            self._set(pid, "failed", ids=ids, log=where, keep_log=False)

        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            f = open(str(log), "ab")
        except OSError as e:
            failed("cannot open run log %s: %s" % (log, e), logged=False)
            return
        with f:
            try:
                proc = subprocess.Popen(argv, cwd=cfg.cwd, env=env, stdout=f,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        start_new_session=True)
            except OSError as e:
                msg = "cannot start %r: %s" % (argv[0], e)
                try:
                    f.write(("crosstalk wake: %s\n" % msg).encode("utf-8"))
                    f.flush()
                    logged = True
                except OSError:
                    logged = False
                failed(msg, logged)
                return
        # The child leads its own process group; recorded so that a server
        # that dies without cleanup can end it on the next start.
        st["last"]["pgid"] = proc.pid
        self._running[pid] = {"proc": proc, "started": now, "timeout": cfg.timeoutSec,
                              "term_at": None, "killed": False, "ids": list(ids),
                              "log": str(log)}
        self._set(pid, "running", ids=ids, log=str(log))

    # the pass

    def poll_once(self) -> None:
        """One pass: reap, reload config and log, ring where allowed, save if changed."""
        with self._lock:
            changed = self._reap()
            try:
                configs, problems = load_wake(self.root)
                roster = load_roster(self.root / "participants.json")
                docs, _entries = self.snapshot()
            except (OSError, ValueError) as e:
                print("crosstalk wake: skipping pass: %s" % e, file=sys.stderr)
                if changed:
                    self._save()
                return
            now = self.clock()
            today = _day(now)
            # An id in wake.json but not in participants.json is never woken and
            # never shown (relay.py check reports it).
            self._shown = sorted(p for p in set(configs) | set(problems)
                                 if p in roster.participants)
            for pid in list(self._state):
                if pid not in configs and pid not in problems and pid not in self._running:
                    del self._state[pid]
                    self._view.pop(pid, None)
            for pid in self._shown:
                items = actionable_items(docs, pid, roster)
                st = self._state.get(pid)
                if pid in problems:
                    self._set(pid, "misconfigured")
                    if st is not None:
                        st["rung"] = [i for i in st["rung"] if i in items]
                    continue
                if st is None:
                    self._state[pid] = {"rung": list(items), "runs": [], "last": None}
                    self._set(pid, "idle", ids=[])
                    continue
                st["runs"] = [r for r in st["runs"] if (_parse_iso(r) is not None
                                                         and _day(_parse_iso(r)) == today)]
                st["rung"] = [i for i in st["rung"] if i in items]
                new = [i for i in items if i not in st["rung"]]
                cfg = configs[pid]
                if pid in self._running:
                    continue
                if not new:
                    # Nothing waits: show the last run's result, not a stale
                    # cooling-down, capped or misconfigured.
                    if self._view.get(pid, {}).get("status") in TRANSIENT:
                        self._view[pid] = self._view_of_last(st.get("last"))
                    continue
                if len(st["runs"]) >= cfg.dailyCap:
                    self._set(pid, "capped", ids=new)
                    continue
                last_start = _parse_iso((st.get("last") or {}).get("started"))
                if last_start is not None and now - last_start < cfg.cooldownSec:
                    self._set(pid, "cooling-down", ids=new)
                    continue
                self._spawn(pid, cfg, new, roster)
            self._save()

    # thread

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                traceback.print_exc(file=sys.stderr)
            self._stop.wait(self.poll_sec)

    def start(self) -> None:
        """Run poll_once every poll_sec on a daemon thread."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="crosstalk-waker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling; terminate running children and record them interrupted."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        with self._lock:
            procs = [(pid, run["proc"]) for pid, run in self._running.items()]
        if not procs:
            return
        # Signal and wait without the lock, so status() stays responsive.
        for _pid, proc in procs:
            _killpg(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + self.kill_grace
        while time.monotonic() < deadline:
            left = [proc for _pid, proc in procs
                    if proc.poll() is None or _group_alive(proc.pid)]
            if not left:
                break
            time.sleep(0.05)
        for _pid, proc in procs:
            _killpg(proc.pid, signal.SIGKILL)  # the whole group, leader or not
            proc.wait()
        with self._lock:
            for pid, proc in procs:
                if pid in self._running:
                    self._finish(pid, "interrupted", proc.returncode)
            self._save()
