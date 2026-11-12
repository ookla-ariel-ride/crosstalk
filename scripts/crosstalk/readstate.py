"""The owner's read state: the read position of each thread, the muted threads,
and the time before which every message counts as read.

The state lives in `read-state.json` in the data root, outside the message log
and out of git:

  {"threads": {root: {"t": iso, "id": message id}}, "muted": [root], "all_before": iso or null,
   "rev": n}

`rev` never falls: each write that changes the state raises it by one, and
never sets it below the present time in milliseconds. A file with no usable
`rev` (missing, corrupt, or replaced) starts from the present time. The page
keeps the state with the higher `rev`, so an older answer that arrives late
never replaces a newer one, and a lost file does not freeze the page.

A thread's mark is the time and id of the newest message the owner read. A
mark never moves backwards, and neither does `all_before`. A file that does
not parse reads as the empty state and stays on disk as it is until the next
write replaces it.

Python 3.9, standard library only.
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .files import atomic_write_text

FILE = "read-state.json"
OWNER = "owner"
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# One server process writes the file; the lock keeps two writes from losing one.
_lock = threading.Lock()


def _now_ms() -> int:
    return int(time.time() * 1000)


def empty() -> dict:
    return {"threads": {}, "muted": [], "all_before": None, "rev": _now_ms()}


def is_time(value) -> bool:
    """True for a time in the log's format, the one that compares as a string."""
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, _TIME_FORMAT)
    except ValueError:
        return False
    return True


def _is_id(value) -> bool:
    return isinstance(value, str) and bool(value)


def _clean(raw) -> dict:
    """The parts of a parsed file that have the right shape; the rest is dropped."""
    state = empty()
    if not isinstance(raw, dict):
        return state
    threads = raw.get("threads")
    if isinstance(threads, dict):
        for root, entry in threads.items():
            if isinstance(entry, dict) and is_time(entry.get("t")) and _is_id(entry.get("id")):
                state["threads"][root] = {"t": entry["t"], "id": entry["id"]}
    muted = raw.get("muted")
    if isinstance(muted, list):
        state["muted"] = [root for i, root in enumerate(muted) if _is_id(root) and root not in muted[:i]]
    if is_time(raw.get("all_before")):
        state["all_before"] = raw["all_before"]
    rev = raw.get("rev")
    if isinstance(rev, int) and not isinstance(rev, bool) and rev >= 0:
        state["rev"] = rev
    return state


def load(root: Path) -> dict:
    """The state in `root`, or the empty state when the file is missing or corrupt."""
    try:
        text = (Path(root) / FILE).read_text(encoding="utf-8")
        raw = json.loads(text)
    except (OSError, UnicodeDecodeError, ValueError):
        return empty()
    return _clean(raw)


def _save(root: Path, state: dict) -> None:
    """Write a changed state, with `rev` one higher, and not below the present time."""
    state["rev"] = max(state["rev"] + 1, _now_ms())
    atomic_write_text(Path(root) / FILE, json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def mark(root: Path, thread: str, t: str, id: str) -> dict:
    """Record that the owner read `thread` up to the message (`t`, `id`); return the state."""
    with _lock:
        state = load(root)
        old = state["threads"].get(thread)
        if old is None or (t, id) > (old["t"], old["id"]):
            state["threads"][thread] = {"t": t, "id": id}
            _save(root, state)
        return state


def mark_all(root: Path, t: str, marks: Optional[Dict[str, Tuple[str, str]]] = None) -> dict:
    """Record that every message up to `t` is read, and each thread of `marks` up to its (t, id); return the state.

    One write: a mark that is not later than the thread's own mark is left out.
    """
    with _lock:
        state = load(root)
        changed = False
        if state["all_before"] is None or t > state["all_before"]:
            state["all_before"] = t
            changed = True
        for thread, (mt, mid) in (marks or {}).items():
            old = state["threads"].get(thread)
            if old is None or (mt, mid) > (old["t"], old["id"]):
                state["threads"][thread] = {"t": mt, "id": mid}
                changed = True
        if changed:
            _save(root, state)
        return state


def set_muted(root: Path, thread: str, muted: bool) -> dict:
    """Mute or unmute `thread`; return the state."""
    with _lock:
        state = load(root)
        if muted and thread not in state["muted"]:
            state["muted"].append(thread)
        elif not muted and thread in state["muted"]:
            state["muted"].remove(thread)
        else:
            return state
        _save(root, state)
        return state


def unread(thread: dict, messages: List[dict], state: dict, owner: str = OWNER) -> int:
    """How many messages of `thread` are new to the owner.

    A message counts when another participant than the owner wrote it and its
    (`t`, `id`) is later than the thread's mark and its `t` later than
    `all_before`. `t` must be the time the message was first written, so a later
    change to an old message does not make it new (serve.py passes that time).
    """
    mark_ = state.get("threads", {}).get(thread.get("root"))
    after = (mark_["t"], mark_["id"]) if mark_ else None
    all_before: Optional[str] = state.get("all_before")
    count = 0
    for m in messages:
        t = m.get("t")
        if not isinstance(t, str) or m.get("from") == owner:
            continue
        if all_before is not None and t <= all_before:
            continue
        if after is not None and (t, m.get("id") or "") <= after:
            continue
        count += 1
    return count
