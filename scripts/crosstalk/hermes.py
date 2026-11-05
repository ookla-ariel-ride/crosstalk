"""Hermes Agent sessions: every inbox item, delivered without interrupting a turn.

Owner decisions (2026-11-04, issue #2): a Hermes session gets every actionable
item, as a Claude Code session does (actionable_items, not the urgent filter).
Nothing interrupts a running turn: items wait until the session is idle, and
between tool calls the most a session sees is a one-line note appended to a
tool result. The plugin also injects the check-in prompt every checkInMin
minutes when the session is idle, so nobody has to type `/heartbeat`.

Two paths deliver, and they share one told set per participant: the key
`hermes:<pid>` in the urgent state file (urgent.py; CROSSTALK_URGENT_STATE moves
it, empty turns both off). Each item reaches a participant once, through
whichever path takes it first; the urgent desktop fallback sees those
deliveries too.

- Shell hooks in the profile's config.yaml run `relay.py urgent --as <pid>
  --hook hermes` (hook_output): `on_session_start` marks every current item
  told (a new session reads its inbox); `pre_llm_call` prints
  `{"context": "<note>"}` with the new items, which Hermes adds to the turn's
  user message. They work without the plugin, but only at turn start.
- The Crosstalk plugin for Hermes (scripts/hermes-plugin/crosstalk) drives a
  Pump inside the Hermes process: it registers each session (live.py), polls
  the log on a thread, and calls the plugin's inject_message only while the
  session is idle.

Known Hermes bugs it works around (checked 2026-11-04 against v0.22.5):
- NousResearch/hermes-agent#141867, post_tool_call is skipped in turns that
  inject_message starts: busy/idle comes from pre_llm_call and on_session_end
  only, and the tool-result note is best effort.
- #130622, injected turns reach memory providers as the human's own words, and
  #136825, injected turns are never quiet: every injected text starts with a
  line saying the Crosstalk plugin sent it and nobody typed it.
- #139847, inject_message doesn't always say which chat a message is for: the
  pump always passes the session key it saw in that session's own hooks and
  names the participant in the text; it never injects without a key.

Python 3.9, standard library only.
"""
import calendar
import json
import shlex
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import live
from .notify import MAX_ITEM_LINES, actionable_items, render_message
from .participants import Roster, load_roster
from .replay import enrich_messages, load_all_lines, replay
from .requests import thread_replies_for

HARNESS = "hermes"
HOOK_EVENTS = ("on_session_start", "pre_llm_call")
POLL_SEC = 10.0
RETRY_SEC = 60.0
INJECT_GRACE_SEC = 120.0
DEFAULT_CHECK_IN_MIN = 30
CHECK_IN_KINDS = ("check-in", "checkin")
PLUGIN_NAME = "crosstalk"
INJECTED_LABEL = ("[Sent by the Crosstalk plugin for Hermes, not typed by the user. "
                  "It is a relay message for Crosstalk participant %s.]")


# the shared told set

def state_key(pid: str) -> str:
    return "%s:%s" % (HARNESS, pid)


def _update(state_path: Path, fn) -> None:
    from .urgent import _update as update  # urgent.py imports this module lazily
    update(Path(state_path), fn)


def seed(state_path: Path, pid: str, items: List[str]) -> None:
    """Count every item in `items` as told (a new session, or a check-in, reads its inbox)."""
    key = state_key(pid)

    def step(state):
        st = state["hooks"].get(key)
        told = set(st["told"]) if st is not None else set()
        state["hooks"][key] = {"told": sorted(told | set(items)),
                               "last_at": st["last_at"] if st is not None else None}
    _update(state_path, step)


def claim(state_path: Path, pid: str, items: List[str], now: float) -> List[str]:
    """Mark the items not yet told as told and return them (sorted).

    The first time a participant is seen, its items count as told and nothing
    is returned (like the Codex hook and the notifier). Answered items leave
    the told set.
    """
    key = state_key(pid)
    out = []  # type: List[str]

    def step(state):
        st = state["hooks"].get(key)
        if st is None:
            state["hooks"][key] = {"told": sorted(set(items)), "last_at": None}
            return
        st["told"] = [i for i in st["told"] if i in items]
        fresh = [i for i in items if i not in st["told"]]
        if fresh:
            st["told"] = sorted(set(st["told"]) | set(fresh))
            st["last_at"] = now
            out.extend(fresh)
    _update(state_path, step)
    return sorted(out)


def release(state_path: Path, pid: str, ids: List[str]) -> None:
    """Undo a claim whose delivery failed, so the items are offered again."""
    key = state_key(pid)

    def step(state):
        st = state["hooks"].get(key)
        if st is not None:
            st["told"] = [i for i in st["told"] if i not in ids]
    _update(state_path, step)


def told(state_path: Path, pid: str) -> Optional[List[str]]:
    """The told set (None before the participant was first seen)."""
    key = state_key(pid)
    out = []  # type: List[Optional[List[str]]]

    def step(state):
        st = state["hooks"].get(key)
        out.append(list(st["told"]) if st is not None else None)
    _update(state_path, step)
    return out[0]


# texts

def load_snapshot(root: Path) -> Tuple[Dict, Roster]:
    roster = load_roster(Path(root) / "participants.json")
    lines = load_all_lines(root, roster)
    return enrich_messages(replay(lines), lines), roster


def note(root: Path, docs: Dict, roster: Roster, pid: str, ids: List[str],
         injected: bool = False) -> str:
    """The Claude Code note (notify.render_message) for `ids`; with `injected`, a first
    line says the plugin sent it."""
    threaded = {did for did, _d, _r in thread_replies_for(docs, pid, roster)}
    items = []
    for did in ids:
        doc = docs.get(("messages", did)) or {}
        sender = doc.get("from")
        p = roster.participants.get(sender) if isinstance(sender, str) else None
        items.append(dict(doc, _id=did, from_name=p.name if p is not None else sender,
                          _thread_reply=did in threaded))
    me = roster.participants.get(pid)
    text = render_message(me.name if me is not None else pid, pid, items, Path(root))
    return (INJECTED_LABEL % pid + "\n" + text) if injected else text


def tool_note(pid: str, ids: List[str]) -> str:
    """The one line appended to a tool result while a turn runs."""
    n = len(ids)
    shown = ", ".join(ids[:MAX_ITEM_LINES]) + (", ..." if n > MAX_ITEM_LINES else "")
    return ("[Crosstalk: %d new item%s for %s (%s). Nothing to do now: finish this turn "
            "and they arrive when the session is idle.]" % (n, "" if n == 1 else "s", pid, shown))


def check_in_prompt(root: Path, pid: str, minutes: int) -> str:
    relay = Path(root) / "scripts" / "relay.py"
    return (INJECTED_LABEL % pid + "\n"
            "Crosstalk check-in for %s (every %d minutes). Run `python3 %s inbox %s` and\n"
            "answer what it lists with `relay.py reply`. Then post your check-in, a\n"
            "`kind: \"check-in\"` message from %s with your commits since the last one,\n"
            "what is in progress, and blockers (%s, \"Check-ins\"). If nothing changed,\n"
            "a one-line check-in is enough."
            % (pid, minutes, relay, pid, pid, Path(root) / "PROTOCOL.md"))


# the shell hook

def hook_output(root: Path, pid: str, stdin_text: str, state_path: Optional[Path],
                clock: Callable[[], float] = time.time) -> str:
    """stdout for `relay.py urgent --as <pid> --hook hermes` (urgent.hook_output calls
    this inside its never-raise wrapper)."""
    if state_path is None:
        return ""
    event = json.loads(stdin_text).get("hook_event_name")
    if event not in HOOK_EVENTS:
        return ""
    docs, roster = load_snapshot(Path(root))
    if pid not in roster.participants or pid == "owner":
        return ""
    items = actionable_items(docs, pid, roster)
    if event == "on_session_start":
        seed(state_path, pid, items)
        return ""
    fresh = claim(state_path, pid, items, clock())
    if not fresh:
        return ""
    return json.dumps({"context": note(Path(root), docs, roster, pid, fresh)})


# setup text (kit and `relay.py live hooks <pid> --harness hermes`)

def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def hooks_yaml(relay: str, pid: str) -> str:
    command = "python3 %s urgent --as %s --hook hermes" % (shlex.quote(relay), shlex.quote(pid))
    out = ["hooks:"]
    for event in HOOK_EVENTS:
        out += ["  %s:" % event, "    - command: %s" % _q(command), "      timeout: 10"]
    return "\n".join(out) + "\n"


def plugin_yaml(root: str, pid: str) -> str:
    return ("plugins:\n  enabled:\n    - %s\n  entries:\n    %s:\n"
            "      allow_gateway_injection: true\n      settings:\n"
            "        participant: %s\n        root: %s\n"
            % (PLUGIN_NAME, PLUGIN_NAME, _q(pid), _q(root)))


def setup_text(root, pid: str) -> str:
    """Markdown: how to set up a Hermes participant (one profile, plugin, hooks)."""
    root = Path(root).resolve()
    relay = str(root / "scripts" / "relay.py")
    plugin = root / "scripts" / "hermes-plugin" / PLUGIN_NAME
    home = "~/.hermes/profiles/%s" % pid
    return (
        "**Hermes Agent: push and check-ins.** Use one Hermes profile per participant, so "
        "this profile's config, MCP server and plugin settings belong to `%(pid)s` alone, "
        "and keep any `HERMES.md` or `.hermes.md` out of the project's repo: Hermes loads "
        "only one project rules file, so one of those would hide `AGENTS.md`.\n\n"
        "1. Create the profile and link the Crosstalk plugin into it:\n\n"
        "```\nhermes profile create %(pid)s\nmkdir -p %(home)s/plugins\n"
        "ln -s %(plugin)s %(home)s/plugins/%(name)s\n```\n\n"
        "2. Add this to `%(home)s/config.yaml` (merge with what is there). The plugin "
        "registers each session with Crosstalk, injects new inbox items only while the "
        "session is idle, appends a one-line note to a tool result when items arrive "
        "mid-turn, and injects the check-in prompt every checkInMin minutes. "
        "`allow_gateway_injection` is what lets it start a turn.\n\n"
        "```yaml\n%(plugin_yaml)s```\n\n"
        "3. The shell hooks deliver new items at the start of each turn even when the "
        "plugin isn't loaded; they share the plugin's record of what was told, so keeping "
        "both never repeats an item:\n\n"
        "```yaml\n%(hooks_yaml)s```\n\n"
        "4. Enable and check it, then restart the profile's gateway or start a new session "
        "(`hermes -p %(pid)s chat`). Hermes asks once to approve each new shell hook.\n\n"
        "```\nhermes -p %(pid)s plugins enable %(name)s\nhermes -p %(pid)s hooks list\n```\n\n"
        "A session registers on its first turn; `python3 %(relay)s live status` lists it "
        "with `(hermes)`. Details: `%(docs)s`, \"Hermes sessions\".\n"
        % {"pid": pid, "home": home, "plugin": shlex.quote(str(plugin)), "name": PLUGIN_NAME,
           "plugin_yaml": plugin_yaml(str(root), pid), "hooks_yaml": hooks_yaml(relay, pid),
           "relay": relay, "docs": root / "docs" / "live-sessions.md"})


# the plugin's pump

def _parse_t(value) -> Optional[float]:
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        return float(calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return None


def last_check_in(docs: Dict, pid: str) -> Optional[float]:
    """When pid last posted a check-in, from the log, or None."""
    best = None  # type: Optional[float]
    for (coll, _did), doc in docs.items():
        if coll != "messages" or doc.get("from") != pid or doc.get("kind") not in CHECK_IN_KINDS:
            continue
        t = _parse_t(doc.get("t"))
        if t is not None and (best is None or t > best):
            best = t
    return best


class _Session(object):
    def __init__(self, sid: str, key: str, now: float):
        self.sid = sid
        self.key = key
        self.busy = False
        self.awaiting = None  # type: Optional[float]  # an injection accepted, its turn not begun
        self.last_active = now
        self.started = now
        self.noted = set()  # type: set


class Pump(object):
    """The plugin's logic, independent of Hermes: hooks call the on_* methods, a
    thread calls poll_once. `inject(text, session_key) -> bool` is the plugin's
    ctx.inject_message. No method raises; a failure is logged through `log`.
    """

    def __init__(self, root: Path, pid: str, state_path: Optional[Path],
                 inject: Callable[[str, str], bool], clock: Callable[[], float] = time.time,
                 poll_sec: float = POLL_SEC, log: Callable[[str], None] = lambda m: None,
                 host_pid: Optional[int] = None):
        self.root = Path(root)
        self.pid = pid
        self.state_path = Path(state_path) if state_path is not None else None
        self.inject = inject
        self.clock = clock
        self.poll_sec = poll_sec
        self.log = log
        self.host_pid = host_pid
        self.sessions = {}  # type: Dict[str, _Session]
        self.pending = []  # type: List[str]  # untold items at the last poll
        self.retry_at = None  # type: Optional[float]
        self.last_check_in_sent = None  # type: Optional[float]
        self._lock = threading.RLock()
        self._sig = None
        self._snap = None  # type: Optional[Tuple[Dict, Roster]]
        self._stop = threading.Event()
        self._thread = None  # type: Optional[threading.Thread]

    # snapshot, reloaded only when a log or the roster changed

    def _signature(self):
        files = [self.root / "participants.json"]
        try:
            files += sorted((self.root / "log").glob("*.jsonl"))
        except OSError:
            pass
        sig = []
        for f in files:
            try:
                st = f.stat()
                sig.append((f.name, st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((f.name, None, None))
        return tuple(sig)

    def snapshot(self) -> Tuple[Dict, Roster]:
        sig = self._signature()
        if self._snap is None or sig != self._sig:
            self._snap = load_snapshot(self.root)
            self._sig = sig
        return self._snap

    def _items(self) -> Tuple[Dict, Roster, List[str]]:
        docs, roster = self.snapshot()
        if self.pid not in roster.participants or self.pid == "owner":
            raise ValueError("unknown participant %r" % self.pid)
        return docs, roster, actionable_items(docs, self.pid, roster)

    def _guard(self, what: str, fn, default=None):
        try:
            return fn()
        except Exception as e:  # a hook must never disturb the session
            self.log("crosstalk: %s failed: %s: %s" % (what, e.__class__.__name__, e))
            return default

    # sessions

    def _session(self, sid: str, key: str) -> _Session:
        """The session record, created (and registered) on first sight."""
        s = self.sessions.get(sid)
        if s is None:
            s = self.sessions[sid] = _Session(sid, key or sid, self.clock())
            if self.state_path is not None:
                self._guard("seed", lambda: seed(self.state_path, self.pid, self._items()[2]))
            self._guard("register", lambda: live.register_hermes(
                self.root, self.pid, sid, s.key, self.clock(), self.host_pid))
        elif key and key != s.key:
            s.key = key
            self._guard("register", lambda: live.register_hermes(
                self.root, self.pid, sid, key, self.clock(), self.host_pid))
        return s

    def _subagent(self, sid: str, key: str) -> bool:
        """A delegated subagent starts inside a running turn of a known session and
        carries that session's key; it is not a session of its own."""
        return bool(key) and any(s.busy and s.key == key and s.sid != sid
                                 for s in self.sessions.values())

    def on_session_start(self, sid: str, key: str = "") -> None:
        if not sid:
            return
        with self._lock:
            if not self._subagent(sid, key):
                self._session(sid, key)

    def on_turn_start(self, sid: str, key: str = "", parent: str = "") -> Optional[str]:
        """pre_llm_call: the session is busy; returns the context note for items not yet
        told (the plugin returns it as `{"context": ...}`), or None. A subagent's turn
        (`parent`, its parent_session_id) is ignored."""
        if not sid or parent:
            return None
        with self._lock:
            if sid not in self.sessions and self._subagent(sid, key):
                return None
            s = self._session(sid, key)
            s.busy, s.awaiting, s.last_active = True, None, self.clock()
            s.noted.clear()
            if self.state_path is None:
                return None

            def run():
                docs, roster, items = self._items()
                fresh = claim(self.state_path, self.pid, items, self.clock())
                self.pending = [i for i in self.pending if i not in fresh]
                return note(self.root, docs, roster, self.pid, fresh) if fresh else None
            return self._guard("turn note", run)

    def on_turn_end(self, sid: str) -> None:
        with self._lock:
            s = self.sessions.get(sid)
            if s is not None:
                s.busy, s.awaiting, s.last_active = False, None, self.clock()

    def on_tool_result(self, sid: str, result) -> Optional[str]:
        """transform_tool_result: `result` plus a one-line note when items arrived that this
        turn hasn't been told of; None to leave the result alone. No file I/O."""
        if not isinstance(result, str):
            return None
        with self._lock:
            s = self.sessions.get(sid)
            if s is None:
                return None
            new = [i for i in self.pending if i not in s.noted]
            if not new:
                return None
            s.noted.update(new)
            return result + "\n\n" + tool_note(self.pid, sorted(set(self.pending)))

    def on_compacted(self, old_sid: str, new_sid: str, key: str = "") -> None:
        """Compaction gives the session a new id: move the record and the registration."""
        if not old_sid or not new_sid or old_sid == new_sid:
            return
        with self._lock:
            s = self.sessions.pop(old_sid, None)
            if s is None:
                return
            self._guard("unregister", lambda: live.unregister_session(self.pid, old_sid))
            s.sid = new_sid
            s.key = key or s.key
            self.sessions[new_sid] = s
            self._guard("register", lambda: live.register_hermes(
                self.root, self.pid, new_sid, s.key, self.clock(), self.host_pid))

    def on_session_finalize(self, sid: str) -> None:
        with self._lock:
            if sid and self.sessions.pop(sid, None) is not None:
                self._guard("unregister", lambda: live.unregister_session(self.pid, sid))

    def unregister_all(self) -> None:
        with self._lock:
            for sid in list(self.sessions):
                self._guard("unregister", lambda: live.unregister_session(self.pid, sid))
            self.sessions.clear()

    # the pass

    def _idle(self, now: float) -> Optional[_Session]:
        """The most recently active idle session, or None. An injection whose turn hasn't
        started within INJECT_GRACE_SEC no longer counts as busy."""
        best = None
        for s in self.sessions.values():
            if s.busy and s.awaiting is not None and now - s.awaiting >= INJECT_GRACE_SEC:
                s.busy, s.awaiting = False, None
            if s.busy:
                continue
            if best is None or s.last_active > best.last_active:
                best = s
        return best

    def _check_in_due(self, docs: Dict, roster: Roster, now: float) -> Optional[int]:
        """The interval in minutes when a check-in prompt is due, else None."""
        p = roster.participants.get(self.pid)
        minutes = p.checkInMin if p is not None and p.checkInMin else DEFAULT_CHECK_IN_MIN
        refs = [s.started for s in self.sessions.values()]
        ref = min(refs) if refs else now
        for t in (last_check_in(docs, self.pid), self.last_check_in_sent):
            if t is not None and t > ref:
                ref = t
        return minutes if now - ref >= minutes * 60 else None

    def _send(self, s: _Session, text: str, now: float) -> bool:
        try:
            ok = bool(self.inject(text, s.key))
        except Exception as e:
            self.log("crosstalk: inject_message raised %s" % e.__class__.__name__)
            ok = False
        if ok:
            s.busy, s.awaiting = True, now
        else:
            self.retry_at = now + RETRY_SEC
            self.log("crosstalk: inject_message refused; retrying in %d s" % RETRY_SEC)
        return ok

    def poll_once(self) -> None:
        with self._lock:
            self._guard("poll", self._poll)

    def _poll(self) -> None:
        if self.state_path is None or not self.sessions:
            return
        docs, roster, items = self._items()
        known = told(self.state_path, self.pid)
        self.pending = [i for i in items if known is not None and i not in known]
        now = self.clock()
        s = self._idle(now)
        if s is None or (self.retry_at is not None and now < self.retry_at):
            return
        self.retry_at = None
        minutes = self._check_in_due(docs, roster, now)
        if minutes is not None:
            if self._send(s, check_in_prompt(self.root, self.pid, minutes), now):
                self.last_check_in_sent = now
                seed(self.state_path, self.pid, items)  # the check-in reads the inbox
                self.pending = []
            return
        fresh = claim(self.state_path, self.pid, items, now)
        if not fresh:
            return
        if self._send(s, note(self.root, docs, roster, self.pid, fresh, injected=True), now):
            self.pending = [i for i in self.pending if i not in fresh]
        else:
            release(self.state_path, self.pid, fresh)

    # thread

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.poll_sec)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="crosstalk-hermes", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(5)


def state_path_from_env() -> Optional[Path]:
    from .urgent import state_path_from_env as from_env
    return from_env()
