"""Hermes Agent sessions: every inbox item, delivered without interrupting a turn.

The design: a Hermes session gets every actionable
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

- The Crosstalk plugin for Hermes (scripts/hermes-plugin/crosstalk) drives a
  Pump inside the Hermes process: it registers each session on its first turn
  (live.py), polls the log on a thread, and calls the plugin's inject_message
  only while the session is idle.
- A shell hook, for a profile that can't load the plugin, runs `relay.py urgent
  --as <pid> --hook hermes` (hook_output) at `pre_llm_call` and prints
  `{"context": "<note>"}` with the new items, which Hermes adds to the turn's
  user message. It works only at turn start.

Nothing counts as told until a session has been given it. A delegated subagent
or a cron job is not a session: on_session_start can't show which is which
(Hermes fires it for children too, without parent_session_id, and again on a
prompt rebuild), so neither path acts on it. pre_llm_call shows it, as
parent_session_id for plugins and as extra.platform for shell hooks, whose
payload drops parent_session_id.

Hook, injection and delegation behaviour read from the v0.22.7 source on
2026-11-06. Known Hermes bugs it works around (checked 2026-11-04 against
v0.22.5, not rechecked since):
- NousResearch/hermes-agent#141867, post_tool_call is skipped in turns that
  inject_message starts: busy/idle comes from pre_llm_call and on_session_end
  only, and the tool-result note is best effort.
- #130622, injected turns reach memory providers as the human's own words, and
  #136825, injected turns are never quiet: every injected text starts with a
  line saying the Crosstalk plugin sent it and nobody typed it.
- #139847, inject_message doesn't always say which chat a message is for: the
  pump passes the session key it saw in that session's own hooks and names the
  participant in the text. A session with no key (the classic CLI has none)
  passes its session id instead: the CLI ignores the value, and a gateway finds
  no such key and refuses.

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
from .notify import MAX_ITEM_LINES, actionable_items, render_message, sender_label
from .participants import Roster, load_roster
from .replay import enrich_messages, load_all_lines, replay
from .requests import thread_replies_for

HARNESS = "hermes"
HOOK_EVENTS = ("pre_llm_call",)
NON_SESSION_PLATFORMS = ("subagent", "cron")  # agents that run turns but aren't sessions
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


def claim(state_path: Path, pid: str, items: List[str], now: float) -> List[str]:
    """Mark the items not yet told as told and return them (sorted).

    The first time a participant is seen, that is all of them: nothing assumes a
    new session reads its inbox, so a backlog is delivered, not skipped. Answered
    items leave the told set.
    """
    key = state_key(pid)
    out = []  # type: List[str]

    def step(state):
        st = state["hooks"].get(key)
        if st is None:
            st = state["hooks"][key] = {"told": [], "last_at": None}
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
         injected: bool = False, code_root: Optional[Path] = None) -> str:
    """The Claude Code note (notify.render_message) for `ids`; with `injected`, a first
    line says the plugin sent it. code_root: as in render_message."""
    threaded = {did for did, _d, _r in thread_replies_for(docs, pid, roster)}
    items = []
    for did in ids:
        doc = docs.get(("messages", did)) or {}
        sender = doc.get("from")
        p = roster.participants.get(sender) if isinstance(sender, str) else None
        items.append(dict(doc, _id=did, from_name=sender_label(p, sender),
                          _thread_reply=did in threaded))
    me = roster.participants.get(pid)
    text = render_message(me.name if me is not None else pid, pid, items, Path(root),
                          code_root=code_root)
    return (INJECTED_LABEL % pid + "\n" + text) if injected else text


def tool_note(pid: str, ids: List[str]) -> str:
    """The one line appended to a tool result while a turn runs."""
    n = len(ids)
    shown = ", ".join(ids[:MAX_ITEM_LINES]) + (", ..." if n > MAX_ITEM_LINES else "")
    return ("[Crosstalk: %d new item%s for %s (%s). Nothing to do now: finish this turn "
            "and they arrive when the session is idle.]" % (n, "" if n == 1 else "s", pid, shown))


def check_in_prompt(root: Path, pid: str, minutes: int, code_root: Optional[Path] = None) -> str:
    """The check-in prompt; it names `<code_root>/scripts/relay.py` (code_root
    defaults to root) and carries `--data-root <root>` when the two differ."""
    code = Path(root) if code_root is None else Path(code_root)
    relay = live.relay_command(Path(root), code, quote=False)
    return (INJECTED_LABEL % pid + "\n"
            "Crosstalk check-in for %s (every %d minutes). Run `python3 %s inbox %s` and\n"
            "answer what it lists with `relay.py reply`. Then post your check-in, a\n"
            "`kind: \"check-in\"` message from %s with your commits since the last one,\n"
            "what is in progress, and blockers (%s, \"At every check-in\"). If nothing\n"
            "changed, a one-line check-in is enough."
            % (pid, minutes, relay, pid, pid, code / "PROTOCOL.md"))


# the shell hook

def hook_output(root: Path, pid: str, stdin_text: str, state_path: Optional[Path],
                clock: Callable[[], float] = time.time,
                code_root: Optional[Path] = None) -> str:
    """stdout for `relay.py urgent --as <pid> --hook hermes` (urgent.hook_output calls
    this inside its never-raise wrapper)."""
    if state_path is None:
        return ""
    payload = json.loads(stdin_text)
    if payload.get("hook_event_name") not in HOOK_EVENTS:
        return ""
    extra = payload.get("extra")
    if isinstance(extra, dict) and extra.get("platform") in NON_SESSION_PLATFORMS:
        return ""
    docs, roster = load_snapshot(Path(root))
    if pid not in roster.participants or pid == "owner":
        return ""
    fresh = claim(state_path, pid, actionable_items(docs, pid, roster), clock())
    if not fresh:
        return ""
    return json.dumps({"context": note(Path(root), docs, roster, pid, fresh,
                                       code_root=code_root)})


# setup text (kit and `relay.py live hooks <pid> --harness hermes`)

def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def hooks_yaml(relay: str, pid: str) -> str:
    """relay: the quoted command fragment that runs relay.py (live.relay_command)."""
    command = "python3 %s urgent --as %s --hook hermes" % (relay, shlex.quote(pid))
    out = ["hooks:"]
    for event in HOOK_EVENTS:
        out += ["  %s:" % event, "    - command: %s" % _q(command), "      timeout: 10"]
    return "\n".join(out) + "\n"


def plugin_yaml(root: str, pid: str, data_root: Optional[str] = None) -> str:
    """The plugin's settings: root is the code; data_root only when the data is elsewhere."""
    data = "        data_root: %s\n" % _q(data_root) if data_root not in (None, root) else ""
    return ("plugins:\n  enabled:\n    - %s\n  entries:\n    %s:\n"
            "      allow_gateway_injection: true\n      settings:\n"
            "        participant: %s\n        root: %s\n%s"
            % (PLUGIN_NAME, PLUGIN_NAME, _q(pid), _q(root), data))


def setup_text(root, pid: str, code_root: Optional[Path] = None) -> str:
    """Markdown: how to set up a Hermes participant (one profile, the plugin).

    root is the data folder. The plugin, relay.py and the docs are named in
    code_root (default root); the commands and the plugin settings carry the
    data root when the two differ.
    """
    data = Path(root).resolve()
    root = data if code_root is None else Path(code_root).resolve()
    relay = live.relay_command(data, root)
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
        "`allow_gateway_injection` is what lets it start a turn in a gateway, TUI or "
        "desktop session; the classic CLI doesn't check it.\n\n"
        "```yaml\n%(plugin_yaml)s```\n\n"
        "3. Enable it; a running gateway reloads at once, else start a new session "
        "(`hermes -p %(pid)s chat`).\n\n"
        "```\nhermes -p %(pid)s plugins enable %(name)s\n```\n\n"
        "Only if this profile can't load the plugin, add this shell hook instead. It "
        "delivers new items at the start of a turn and does nothing else: no idle push, no "
        "check-ins. Don't add it beside the plugin, which already does this. `hermes -p "
        "%(pid)s chat` asks once to approve it; a gateway never asks, so approve it there "
        "first or set `hooks_auto_accept: true`. `hermes -p %(pid)s hooks list` shows it.\n\n"
        "```yaml\n%(hooks_yaml)s```\n\n"
        "A session registers on its first turn; `python3 %(status)s live status` lists it "
        "with `(hermes)`. Details: `%(docs)s`, \"Hermes sessions\".\n"
        % {"pid": pid, "home": home, "plugin": shlex.quote(str(plugin)), "name": PLUGIN_NAME,
           "plugin_yaml": plugin_yaml(str(root), pid, str(data)),
           "hooks_yaml": hooks_yaml(relay, pid),
           "status": live.relay_command(data, root, quote=False),
           "docs": root / "docs" / "live-sessions.md"})


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
        self.claimed = []  # type: List[str]  # told by an injection whose turn hasn't begun


class Pump(object):
    """The plugin's logic, independent of Hermes: hooks call the on_* methods, a
    thread calls poll_once. `inject(text, session_key) -> bool` is the plugin's
    ctx.inject_message. No method raises; a failure is logged through `log`.
    """

    def __init__(self, root: Path, pid: str, state_path: Optional[Path],
                 inject: Callable[[str, str], bool], clock: Callable[[], float] = time.time,
                 poll_sec: float = POLL_SEC, log: Callable[[str], None] = lambda m: None,
                 host_pid: Optional[int] = None, code_root: Optional[Path] = None):
        self.root = Path(root)  # the data
        self.code_root = self.root if code_root is None else Path(code_root)  # names relay.py
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
            self._guard("register", lambda: live.register_hermes(
                self.root, self.pid, sid, s.key, self.clock(), self.host_pid))
        elif key and key != s.key:
            s.key = key
            self._guard("register", lambda: live.register_hermes(
                self.root, self.pid, sid, key, self.clock(), self.host_pid))
        return s

    def _release_claimed(self, s: _Session) -> None:
        """Offer again what an injection claimed: its turn is not going to happen."""
        if s.claimed and self.state_path is not None:
            release(self.state_path, self.pid, s.claimed)
        s.claimed = []

    def on_turn_start(self, sid: str, key: str = "", parent: str = "",
                      platform: str = "") -> Optional[str]:
        """pre_llm_call, the only place a session is created: it is busy; returns the context
        note for items not yet told (the plugin returns it as `{"context": ...}`), or None.
        A subagent's turn (`parent`, its parent_session_id) or a cron job's is ignored."""
        if not sid or parent or platform in NON_SESSION_PLATFORMS:
            return None
        with self._lock:
            s = self._session(sid, key)
            s.busy, s.awaiting, s.last_active = True, None, self.clock()
            s.claimed = []  # the injected turn began, so its items arrived
            s.noted.clear()
            if self.state_path is None:
                return None

            def run():
                docs, roster, items = self._items()
                fresh = claim(self.state_path, self.pid, items, self.clock())
                self.pending = [i for i in self.pending if i not in fresh]
                return note(self.root, docs, roster, self.pid, fresh,
                            code_root=self.code_root) if fresh else None
            return self._guard("turn note", run)

    def on_turn_end(self, sid: str) -> None:
        with self._lock:
            s = self.sessions.get(sid)
            if s is not None and s.awaiting is None:  # else the grace period decides
                s.busy, s.last_active = False, self.clock()

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
            s = self.sessions.pop(sid, None) if sid else None
            if s is not None:
                self._guard("release", lambda: self._release_claimed(s))
                self._guard("unregister", lambda: live.unregister_session(self.pid, sid))

    def unregister_all(self) -> None:
        with self._lock:
            for sid, s in list(self.sessions.items()):
                self._guard("release", lambda: self._release_claimed(s))
                self._guard("unregister", lambda: live.unregister_session(self.pid, sid))
            self.sessions.clear()

    # the pass

    def _expire_injections(self, now: float) -> None:
        """inject_message's True means only scheduled: an injection whose turn hasn't started
        within INJECT_GRACE_SEC counts as lost, and its items are offered again."""
        for s in self.sessions.values():
            if s.awaiting is not None and now - s.awaiting >= INJECT_GRACE_SEC:
                self._release_claimed(s)
                s.busy, s.awaiting = False, None

    def _idle(self) -> Optional[_Session]:
        """The most recently active idle session, or None."""
        best = None
        for s in self.sessions.values():
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

    def _send(self, s: _Session, text: str, now: float, claimed: List[str]) -> bool:
        """Inject `text`, which tells the items in `claimed`; a refusal releases them."""
        try:
            ok = bool(self.inject(text, s.key))
        except Exception as e:
            self.log("crosstalk: inject_message raised %s" % e.__class__.__name__)
            ok = False
        if ok:
            s.busy, s.awaiting, s.claimed = True, now, list(claimed)
        else:
            release(self.state_path, self.pid, claimed)
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
        now = self.clock()
        self._expire_injections(now)
        known = told(self.state_path, self.pid) or []
        self.pending = [i for i in items if i not in known]
        s = self._idle()
        if s is None or (self.retry_at is not None and now < self.retry_at):
            return
        self.retry_at = None
        minutes = self._check_in_due(docs, roster, now)
        if minutes is not None:
            fresh = claim(self.state_path, self.pid, items, now)  # the check-in reads the inbox
            if self._send(s, check_in_prompt(self.root, self.pid, minutes, self.code_root), now, fresh):
                self.last_check_in_sent = now
                self.pending = []
            return
        fresh = claim(self.state_path, self.pid, items, now)
        if not fresh:
            return
        text = note(self.root, docs, roster, self.pid, fresh, injected=True,
                    code_root=self.code_root)
        if self._send(s, text, now, fresh):
            self.pending = [i for i in self.pending if i not in fresh]

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
