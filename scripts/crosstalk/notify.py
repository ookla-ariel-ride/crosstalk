"""The notifier: tells live Claude Code sessions about new inbox items.

Each running session registers its inbox socket (live.py). Every poll_sec the
Notifier computes each registered participant's actionable items and posts the
ones that session has not been told about as one user message over the socket.
State per registration (<pid>--<session_id>): told ids, the last send result
and, after a failed send, when to retry. A token is never logged, saved in the
state file, or returned by status().
"""
import errno
import fcntl
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import live
from .courier import atomic_write_text
from .participants import Roster, load_roster
from .requests import (inbox_for, peer_alerts_for, pending_recipients, thread_replies_for,
                       unanswered_owner_messages)

MAX_ITEM_LINES = 10  # the session reads the full inbox; the message only previews it

# Ids go into the message text, so only plain ones are told.
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
SUMMARY_MAX = 160
RETRY_SEC = 60.0
ISO = "%Y-%m-%dT%H:%M:%SZ"
KIND_LABELS = {"owner": "owner message"}
THREAD_REPLY_LABEL = "reply on your thread"


class SessionGone(Exception):
    """The session's socket is gone or refuses: the session has ended."""


def actionable_items(docs: Dict, pid: str, roster: Roster, urgent: bool = False) -> List[str]:
    """Sorted ids of the inbox items that should wake pid.

    Open requests addressed to pid that pid has not answered, owner messages
    (owner replies included) pid has not answered, and replies on pid's threads
    that pid has not posted or checked in after. With `urgent`, only the items
    that may interrupt a non-Claude session (urgent.py): the owner messages,
    plus alerts from peers that name pid in `to` (peer_alerts_for); requests
    and thread replies wait for the check-in. Items whose sender is not approved in the
    roster are dropped, so a quarantined participant cannot ring an agent; so
    are pid's own messages (an @Projects request, or one listing itself in
    `to`, must not wake its sender), and items whose id is not a plain
    SAFE_ID_RE id, since ids are written into the prompt and the argv.
    """
    ids = set()
    for did, _doc, _replies in unanswered_owner_messages(docs, pid, roster):
        ids.add(did)
    if urgent:
        for did, _doc, _replies in peer_alerts_for(docs, pid, roster):
            ids.add(did)
    else:
        for did, doc, replies in inbox_for(docs, pid, roster):
            if pid in pending_recipients(doc, replies, roster):
                ids.add(did)
        for did, _doc, _root in thread_replies_for(docs, pid, roster):
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


def _one_line(value, limit: Optional[int] = None) -> str:
    """value as text with all whitespace runs (newlines included) collapsed to one space."""
    text = " ".join(str(value).split()) if value is not None else ""
    text = "".join(c if c.isprintable() else " " for c in text)
    return text[:limit] if limit is not None else text


def item_lines(items: List[dict]) -> List[str]:
    """One `- <kind> from <sender>: <summary> (<id>)` line per item, at most MAX_ITEM_LINES."""
    lines = []
    for it in items[:MAX_ITEM_LINES]:
        kind = it.get("kind") if isinstance(it.get("kind"), str) and it.get("kind") else "message"
        if it.get("_thread_reply"):
            kind = THREAD_REPLY_LABEL
        elif kind == "owner" and it.get("replyTo"):
            kind = "owner reply"
        kind = KIND_LABELS.get(kind, kind)
        sender = it.get("from_name") or it.get("from") or "unknown"
        lines.append("- %s from %s: %s (%s)" % (_one_line(kind, 40), _one_line(sender, 80),
                                               _one_line(it.get("summary"), SUMMARY_MAX),
                                               it["_id"]))
    return lines


def render_message(name: str, pid: str, items: List[dict], root: Path) -> str:
    """The message posted into a session: one line per item, then what to do.

    items are message docs with `_id` added; `kind`, `from` and `summary` come
    from the log. `from` is shown by its roster name when known (pass the name
    in as `from_name`), else as the id. Summaries are untrusted: one line,
    at most SUMMARY_MAX characters.
    """
    n = len(items)
    lines = ["Crosstalk: %d new item%s for %s." % (n, "" if n == 1 else "s", pid)]
    lines.extend(item_lines(items))
    if n > MAX_ITEM_LINES:
        lines.append("- and %d more" % (n - MAX_ITEM_LINES))
    lines.append("Run `python3 %s/scripts/relay.py inbox %s` and answer each\n"
                 "with `relay.py reply`. If one asks for substantial work, say in your reply\n"
                 "what you will do and when." % (root, pid))
    if any(it.get("_thread_reply") for it in items):
        lines.append("Replies on your threads need an answer only if they ask you something.")
    return "\n".join(lines)


def send_to_session(socket_path: str, token: str, text: str, timeout: float = 5.0) -> None:
    """Post text to a session's inbox socket: an auth line, then a user line.

    Raises SessionGone when the socket is missing or refuses, OSError otherwise.
    """
    payload = (json.dumps({"type": "auth", "token": token}, separators=(",", ":")) + "\n"
               + json.dumps({"type": "user", "from": "crosstalk",
                             "message": {"content": text}}, separators=(",", ":")) + "\n")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        try:
            s.connect(socket_path)
        except OSError as e:
            if e.errno in (errno.ENOENT, errno.ECONNREFUSED):
                raise SessionGone(e.strerror)
            raise
        s.sendall(payload.encode("utf-8"))
    finally:
        s.close()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime(ISO)


def _warn(msg: str) -> None:
    print("crosstalk notify: " + msg, file=sys.stderr)


class Notifier:
    """Polls the log and tells each live session about its new inbox items.

    poll_once does one pass; start/stop run it on a background thread. Retry
    timing uses the injected clock. A second Notifier on the same state file
    is disabled.
    """

    def __init__(self, root: Path, snapshot: Callable[[], Tuple[Dict, Dict]],
                 state_path: Optional[Path], clock: Callable[[], float] = time.time,
                 poll_sec: float = 5.0,
                 send: Callable[..., None] = send_to_session,
                 fallback=None, reviews=None):
        self.root = Path(root)
        # reviews.Scanner (or None): run on every pass after the fallback; it
        # scans the review folders at most once a minute (reviews.SCAN_SEC).
        self.reviews = reviews
        # urgent.Fallback (or None): run on every pass, so a disabled Notifier
        # (a second server) posts no desktop notifications either.
        self.fallback = fallback
        self.snapshot = snapshot
        self.state_path = Path(state_path) if state_path is not None else None
        self.clock = clock
        self.poll_sec = poll_sec
        self.send = send
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None  # type: Optional[threading.Thread]
        self._saved = None  # type: Optional[str]
        self._lock_fd = None  # type: Optional[int]
        self._reported = set()  # type: set
        self._view = {}  # type: Dict[str, dict]
        self.disabled = not self._take_lock()
        try:
            self._state = {} if self.disabled else self._load()  # type: Dict[str, dict]
        except BaseException:
            self._release_lock()
            raise

    # persistence

    def _take_lock(self) -> bool:
        """Lock <state_path>.lock; False (with a warning) when that is not possible."""
        if self.state_path is None:
            return True
        lock = str(self.state_path) + ".lock"
        try:
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            _warn("disabled: cannot open lock %s: %s" % (lock, e))
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            _warn("disabled: another server holds the lock %s (%s); "
                  "this one tells no sessions" % (lock, e))
            return False
        self._lock_fd = fd
        return True

    def _release_lock(self) -> None:
        fd, self._lock_fd = self._lock_fd, None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _load(self) -> Dict[str, dict]:
        if self.state_path is None or not self.state_path.exists():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            _warn("ignoring unreadable state %s: %s" % (self.state_path, e))
            return {}
        state = {}
        if not isinstance(raw, dict):
            return state
        for key, st in raw.items():
            if not isinstance(key, str) or not isinstance(st, dict):
                continue
            told = st.get("told")
            told = [x for x in told if isinstance(x, str)] if isinstance(told, list) else []
            last = st.get("last")
            if not (isinstance(last, dict) and last.get("status") in ("told", "failed")
                    and isinstance(last.get("at"), str)):
                last = None
            else:
                last = {"status": last["status"], "at": last["at"]}
            retry = st.get("retry_at")
            if isinstance(retry, bool) or not isinstance(retry, (int, float)):
                retry = None
            state[key] = {"told": told, "last": last, "retry_at": retry}
        self._saved = json.dumps(state, indent=2, sort_keys=True) + "\n"
        return state

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
            _warn("cannot save state %s: %s" % (self.state_path, e))

    # registrations

    def _drop_gone(self, regs: List[live.Registration]) -> None:
        """Delete registration files whose socket no longer exists (a crashed session).

        load_registrations rejects these (the socket fails validation), so they
        never reach a send; removing them here is the ENOENT case of a send.
        """
        valid = {r.path for r in regs}
        try:
            files = sorted(live.live_dir().glob("*--*.json"))
        except OSError:
            return
        for f in files:
            if f in valid:
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(doc, dict):
                continue
            sock = doc.get("socket")
            if (f.name != "%s--%s.json" % (doc.get("pid"), doc.get("session_id"))
                    or not isinstance(sock, str) or not os.path.isabs(sock) or "\0" in sock
                    or not any(r.match(os.path.dirname(sock)) for r in live.SOCKET_DIR_RES)
                    or os.path.lexists(sock)):
                continue
            self._unregister(f, f.name[:-len(".json")])

    def _unregister(self, path: Path, key: str) -> None:
        try:
            path.unlink()
            _warn("%s: session gone; registration removed" % key)
        except FileNotFoundError:
            pass
        except OSError as e:
            _warn("%s: cannot remove registration: %s" % (key, e.strerror))
        self._state.pop(key, None)

    # view

    def status(self) -> Dict[str, dict]:
        """{pid: {sessions, since, last}} per pid with a live session; last is the newest
        failure if any session failed, else the newest told. No token or socket."""
        if self.disabled:
            return {}
        with self._lock:
            return {pid: dict(v, last=dict(v["last"]) if v["last"] else None)
                    for pid, v in self._view.items()}

    def _build_view(self, regs: List[live.Registration]) -> None:
        view = {}  # type: Dict[str, dict]
        for reg in regs:
            key = "%s--%s" % (reg.pid, reg.session_id)
            if key not in self._state:
                continue
            v = view.setdefault(reg.pid, {"sessions": 0, "since": None, "last": None})
            v["sessions"] += 1
            if reg.registered and (v["since"] is None or reg.registered < v["since"]):
                v["since"] = reg.registered
            # Any failed session wins (newest failure), so a sibling's success
            # cannot hide "couldn't reach session"; otherwise the newest told.
            last = self._state[key].get("last")
            cur = v["last"]
            if last and (cur is None
                         or (last["status"] == "failed") > (cur["status"] == "failed")
                         or (last["status"] == cur["status"] and last["at"] >= cur["at"])):
                v["last"] = dict(last)
        self._view = view

    # the pass

    def poll_once(self) -> None:
        """One pass: reload registrations and log, tell sessions what is new, save if changed."""
        if self.disabled:
            return
        with self._lock:
            try:
                regs, problems = live.load_registrations(self.root)
                roster = load_roster(self.root / "participants.json")
                docs, _entries = self.snapshot()
            except (OSError, ValueError) as e:
                _warn("skipping pass: %s" % e)
                return
            self._drop_gone(regs)
            for p in problems:
                if p not in self._reported and "socket not usable" not in p:
                    self._reported.add(p)
                    _warn("ignoring registration " + p)
            # Drop state only when the registration file is gone: a file that is
            # briefly invalid (roster mid-write, stat error) keeps its pending items.
            try:
                files = {f.name[:-len(".json")] for f in live.live_dir().glob("*--*.json")}
            except OSError:
                files = None
            if files is not None:
                for key in list(self._state):
                    if key not in files:
                        del self._state[key]
            now = self.clock()
            for reg in regs:
                key = "%s--%s" % (reg.pid, reg.session_id)
                items = actionable_items(docs, reg.pid, roster)
                st = self._state.get(key)
                if st is None:
                    # First sight: the session reads its inbox at start.
                    self._state[key] = {"told": list(items), "last": None, "retry_at": None}
                    continue
                st["told"] = [i for i in st["told"] if i in items]
                new = [i for i in items if i not in st["told"]]
                if not new:
                    continue
                if st.get("retry_at") is not None and now < st["retry_at"]:
                    continue
                self._tell(reg, key, st, new, docs, roster, now)
            self._build_view(regs)
            self._save()
            if self.fallback is not None:
                try:
                    self.fallback.poll(docs, roster)
                except Exception as e:  # the fallback never stops the live pass
                    _warn("urgent fallback failed: %s" % e.__class__.__name__)
            if self.reviews is not None:
                try:
                    self.reviews.poll(docs, roster)
                except Exception as e:  # nor does the review scan
                    _warn("review scan failed: %s: %s" % (e.__class__.__name__, e))

    def _tell(self, reg: live.Registration, key: str, st: dict, new: List[str],
              docs: Dict, roster: Roster, now: float) -> None:
        threaded = {did for did, _d, _r in thread_replies_for(docs, reg.pid, roster)}
        items = []
        for did in new:
            doc = docs.get(("messages", did)) or {}
            sender = doc.get("from")
            p = roster.participants.get(sender) if isinstance(sender, str) else None
            items.append(dict(doc, _id=did, from_name=p.name if p is not None else sender,
                              _thread_reply=did in threaded))
        me = roster.participants.get(reg.pid)
        text = render_message(me.name if me is not None else reg.pid, reg.pid, items, self.root)
        try:
            self.send(reg.socket, reg.token, text)
        except SessionGone:
            self._unregister(reg.path, key)
            return
        except OSError as e:
            _warn("%s: send failed (%s); retrying in %d s"
                  % (key, e.strerror or e.__class__.__name__, RETRY_SEC))
            st["last"] = {"status": "failed", "at": _iso(now)}
            st["retry_at"] = now + RETRY_SEC
            return
        st["told"] = sorted(set(st["told"]) | set(new))
        st["last"] = {"status": "told", "at": _iso(now)}
        st["retry_at"] = None

    # thread

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                traceback.print_exc(file=sys.stderr)
            self._stop.wait(self.poll_sec)

    def start(self) -> None:
        """Run poll_once every poll_sec on a daemon thread (not when disabled)."""
        if self._thread is not None or self.disabled:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="crosstalk-notifier",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling and release the state lock."""
        try:
            self._stop.set()
            if self._thread is not None:
                self._thread.join()
                self._thread = None
        finally:
            self._release_lock()
