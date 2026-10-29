"""Urgent-only push for sessions in harnesses the Claude Code notifier can't reach.

Owner decisions (2026-10-29): a non-Claude session is interrupted only for
urgent items, which are every owner message to it (owner replies included) and
alerts from peers that name it in `to` (actionable_items with urgent=True).
Requests, thread replies and incidents wait for the 30-minute check-in.

Two parts share one state file (default ~/.crosstalk-urgent-state.json, 0600,
with a `.lock` beside it; CROSSTALK_URGENT_STATE moves it, empty turns both off):

- hook_output(): what `relay.py urgent --as <pid> --hook codex` prints. Codex
  runs it after every tool call (PostToolUse), when a turn stops (Stop) and at
  session start (SessionStart). It reads only local files: the roster, log/
  and the state file. It prints the event's JSON only when an urgent item is
  new and the participant's last interruption is at least GAP_SEC old; each
  item is delivered once per participant and harness. Any error prints
  nothing.
- Fallback: run by the server's notifier pass. An urgent item for a
  participant whose roster `harness` is in HOOK_HARNESSES that no hook has
  picked up FALLBACK_SEC after the server first saw it gets one macOS
  notification (osascript; nothing happens off macOS).

State: {"hooks": {"<harness>:<pid>": {"told": [ids], "last_at": time or null}},
        "fallback": {"<pid>": {"seen": {id: time}, "posted": [ids]}}}
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .courier import atomic_write_text
from .notify import MAX_ITEM_LINES, _one_line, actionable_items, item_lines
from .participants import Roster, load_roster
from .replay import enrich_messages, load_all_lines, replay

GAP_SEC = 600.0
FALLBACK_SEC = 600.0
HOOK_HARNESSES = ("codex",)
LOCK_WAIT_SEC = 1.0
DEFAULT_STATE = Path.home() / ".crosstalk-urgent-state.json"


def state_path_from_env() -> Optional[Path]:
    """CROSSTALK_URGENT_STATE, else the default; None when it is set to empty."""
    env = os.environ.get("CROSSTALK_URGENT_STATE")
    if env is None:
        return DEFAULT_STATE
    return Path(env) if env else None


# state file

def _clean(raw) -> Dict[str, dict]:
    """The state with every malformed part dropped."""
    hooks, fallback = {}, {}  # type: Dict[str, dict], Dict[str, dict]
    if isinstance(raw, dict):
        for key, st in (raw.get("hooks") or {}).items() if isinstance(raw.get("hooks"), dict) else ():
            if not isinstance(key, str) or not isinstance(st, dict):
                continue
            told = st.get("told")
            last = st.get("last_at")
            hooks[key] = {
                "told": [x for x in told if isinstance(x, str)] if isinstance(told, list) else [],
                "last_at": last if isinstance(last, (int, float)) and not isinstance(last, bool)
                else None,
            }
        fb = raw.get("fallback")
        for pid, st in fb.items() if isinstance(fb, dict) else ():
            if not isinstance(pid, str) or not isinstance(st, dict):
                continue
            seen = st.get("seen") if isinstance(st.get("seen"), dict) else {}
            posted = st.get("posted") if isinstance(st.get("posted"), list) else []
            fallback[pid] = {
                "seen": {k: v for k, v in seen.items() if isinstance(k, str)
                         and isinstance(v, (int, float)) and not isinstance(v, bool)},
                "posted": [x for x in posted if isinstance(x, str)],
            }
    return {"hooks": hooks, "fallback": fallback}


def _update(path: Path, fn: Callable[[Dict[str, dict]], None]) -> None:
    """Run fn on the state under an exclusive lock and save it if fn changed it.

    Raises OSError when the lock can't be had within LOCK_WAIT_SEC or the file
    can't be written; an unreadable state file counts as empty.
    """
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + LOCK_WAIT_SEC
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        state = _clean(raw)
        before = json.dumps(state, sort_keys=True)
        fn(state)
        text = json.dumps(state, indent=2, sort_keys=True) + "\n"
        if json.dumps(state, sort_keys=True) != before or raw is None:
            atomic_write_text(path, text)
            os.chmod(str(path), 0o600)
    finally:
        os.close(fd)


# the note

def _items(docs: Dict, ids: List[str], roster: Roster) -> List[dict]:
    out = []
    for did in ids:
        doc = docs.get(("messages", did)) or {}
        sender = doc.get("from")
        p = roster.participants.get(sender) if isinstance(sender, str) else None
        out.append(dict(doc, _id=did, from_name=p.name if p is not None else sender))
    return out


def render_urgent(pid: str, items: List[dict], root: Path) -> str:
    """The note injected into the session: one line per item, then what to do."""
    n = len(items)
    lines = ["Crosstalk: %d urgent item%s for %s." % (n, "" if n == 1 else "s", pid)]
    lines.extend(item_lines(items))
    if n > MAX_ITEM_LINES:
        lines.append("- and %d more" % (n - MAX_ITEM_LINES))
    lines.append("Finish the step you are on, then answer each with `relay.py reply`.\n"
                 "Owner messages are in `python3 %s/scripts/relay.py inbox %s`; an alert's\n"
                 "full text is under its id in `relay.py read --collection messages`.\n"
                 "Other inbox items wait for your next check-in." % (root, pid))
    return "\n".join(lines)


def _codex_output(event: str, text: str) -> str:
    if event == "Stop":
        return json.dumps({"decision": "block", "reason": text})
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                              "additionalContext": text}})


# the hook

CODEX_EVENTS = ("SessionStart", "PostToolUse", "Stop")


def load_snapshot(root: Path) -> Tuple[Dict, Roster]:
    roster = load_roster(Path(root) / "participants.json")
    lines = load_all_lines(root, roster)
    return enrich_messages(replay(lines), lines), roster


def _hook(root: Path, pid: str, harness: str, stdin_text: str, state_path: Optional[Path],
          clock: Callable[[], float]) -> str:
    if harness != "codex" or state_path is None:
        return ""
    event = json.loads(stdin_text).get("hook_event_name")
    if event not in CODEX_EVENTS:
        return ""
    docs, roster = load_snapshot(Path(root))
    if pid not in roster.participants or pid == "owner":
        return ""
    items = actionable_items(docs, pid, roster, urgent=True)
    key = "%s:%s" % (harness, pid)
    now = clock()
    new = []  # type: List[str]

    def step(state: Dict[str, dict]) -> None:
        st = state["hooks"].get(key)
        if st is None or event == "SessionStart":
            # First sight, or a new session: it reads its inbox at start.
            told = set(st["told"]) if st is not None else set()
            state["hooks"][key] = {"told": sorted(told | set(items)),
                                   "last_at": st["last_at"] if st is not None else None}
            return
        st["told"] = [i for i in st["told"] if i in items]
        fresh = [i for i in items if i not in st["told"]]
        if not fresh or (st["last_at"] is not None and now - st["last_at"] < GAP_SEC):
            return
        st["told"] = sorted(set(st["told"]) | set(fresh))
        st["last_at"] = now
        new.extend(fresh)

    _update(Path(state_path), step)
    if not new:
        return ""
    return _codex_output(event, render_urgent(pid, _items(docs, new, roster), Path(root)))


def hook_output(root: Path, pid: str, harness: str, stdin_text: str,
                state_path: Optional[Path], clock: Callable[[], float] = time.time) -> str:
    """The hook's stdout for this event: the event's JSON when something urgent is
    new and may interrupt now, else "". Never raises and never writes to stderr,
    so a broken setup can't disturb the session."""
    try:
        return _hook(root, pid, harness, stdin_text, state_path, clock)
    except Exception:
        return ""


# the fallback

def macos_notify(title: str, message: str) -> None:
    """Post a macOS notification; nothing off macOS or on any error.

    The texts go in as osascript arguments, never into the script source.
    """
    if sys.platform != "darwin":
        return
    try:
        exe = shutil.which("osascript")
        if exe is None:
            return
        subprocess.run([exe, "-e", "on run argv",
                        "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
                        "-e", "end run", message, title],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5, check=False)
    except Exception:
        pass


class Fallback:
    """Desktop notification for urgent items no hook has picked up in time.

    poll() runs on the server's notifier pass with that pass's docs and roster.
    A participant's items at its first sight count as handled (like the
    Notifier's backlog); later ones are timed from when poll first saw them.
    """

    def __init__(self, state_path: Path, clock: Callable[[], float] = time.time,
                 notify: Callable[[str, str], None] = macos_notify,
                 after: float = FALLBACK_SEC, harnesses=HOOK_HARNESSES):
        self.state_path = Path(state_path)
        self.clock = clock
        self.notify = notify
        self.after = after
        self.harnesses = tuple(harnesses)

    def poll(self, docs: Dict, roster: Roster) -> None:
        now = self.clock()
        pending = {pid: actionable_items(docs, pid, roster, urgent=True)
                   for pid, p in roster.participants.items()
                   if p.harness in self.harnesses and pid != "owner"}
        due = {}  # type: Dict[str, List[str]]

        def step(state: Dict[str, dict]) -> None:
            for pid, items in pending.items():
                picked = set()
                for key, st in state["hooks"].items():
                    if key.split(":", 1)[-1] == pid:
                        picked.update(st["told"])
                fb = state["fallback"].get(pid)
                if fb is None:
                    state["fallback"][pid] = {"seen": {i: now for i in items},
                                              "posted": list(items)}
                    continue
                fb["seen"] = {i: fb["seen"].get(i, now) for i in items}
                fb["posted"] = [i for i in fb["posted"] if i in items]
                ready = [i for i in items if i not in picked and i not in fb["posted"]
                         and now - fb["seen"][i] >= self.after]
                if ready:
                    fb["posted"] = sorted(set(fb["posted"]) | set(ready))
                    due[pid] = ready
            for pid in [p for p in state["fallback"] if p not in pending]:
                del state["fallback"][pid]

        _update(self.state_path, step)
        for pid, ids in due.items():
            p = roster.participants.get(pid)
            items = _items(docs, ids, roster)
            n = len(items)
            title = "Crosstalk: %d urgent item%s for %s" % (n, "" if n == 1 else "s",
                                                           p.name if p is not None else pid)
            first = item_lines(items[:1])[0][2:]
            message = first if n == 1 else "%s (and %d more)" % (first, n - 1)
            self.notify(_one_line(title, 120), _one_line(message, 240))
