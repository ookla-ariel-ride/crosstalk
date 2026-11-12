"""The inbox place of each participant: where its last `inbox --ack` stopped.

The places live in `inbox-state.json` in the data root, outside the message
log and out of git:

  {"alpha": {"t": "2026-11-11T22:17:00Z", "seen": ["beta-0042"]}}

`t` is the time of the newest row the ack covered and `seen` the ids of every
row of that same second it covered. Times have a resolution of one second and
ids are not in write order, so the `seen` list keeps a row that lands in the
same second from hiding. A place never moves backwards. A file that does not
parse, or nests too deep to parse, reads as empty and stays on disk as it is
until the next write. A `t` must have the log's exact zero-padded form.

Each `relay.py inbox` is its own process, so a write takes an exclusive
`flock` on `inbox-state.json.lock`, reads the file again, merges and replaces
the file atomically.

Python 3.9, standard library only.
"""
import fcntl
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from .files import atomic_write_text
from .readstate import _TIME_FORMAT, is_time

FILE = "inbox-state.json"
LOCK = "inbox-state.json.lock"


def _is_log_time(value) -> bool:
    """True for a time in the log's exact zero-padded form.

    `is_time` also takes `2026-11-1T22:17:00Z`, which sorts as a string after
    `2026-11-10T...` and so could put a place in the future and hide rows.
    """
    return is_time(value) and datetime.strptime(value, _TIME_FORMAT).strftime(_TIME_FORMAT) == value


def clean_place(raw) -> Optional[dict]:
    """`{"t", "seen"}` with `seen` sorted and without repeats, or None for a bad shape."""
    if not isinstance(raw, dict) or not _is_log_time(raw.get("t")):
        return None
    seen = raw.get("seen")
    if not isinstance(seen, list) or not all(isinstance(s, str) and s for s in seen):
        return None
    return {"t": raw["t"], "seen": sorted(set(seen))}


def _read(root: Path) -> Optional[dict]:
    try:
        raw = json.loads((Path(root) / FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    return raw if isinstance(raw, dict) else None


def load(root: Path) -> Dict[str, dict]:
    """The clean places in `root`; `{}` when the file is missing or does not parse."""
    places = {}
    for pid, raw in (_read(root) or {}).items():
        place = clean_place(raw)
        if place is not None:
            places[pid] = place
    return places


def later(a: Optional[dict], b: Optional[dict]) -> Optional[dict]:
    """The later of two places; at equal times the `seen` lists join."""
    if a is None or b is None:
        return a if b is None else b
    if a["t"] != b["t"]:
        return a if a["t"] > b["t"] else b
    return {"t": a["t"], "seen": sorted(set(a["seen"]) | set(b["seen"]))}


def is_new(t: str, did: str, place: Optional[dict]) -> bool:
    """True when the row (`t`, `did`) comes after `place`."""
    if place is None or t > place["t"]:
        return True
    return t == place["t"] and did not in place["seen"]


def newest(times: Dict[Tuple[str, str], str], now: Optional[str] = None) -> Optional[dict]:
    """The place of the newest row: the largest time and every id with that time.

    `times` maps (collection, id) to the row's time. With `now`, a row after
    `now` does not count: a place ahead of the clock would hide every row that
    lands before the clock reaches it. None when there is no row.
    """
    if now is not None:
        times = {k: v for k, v in times.items() if v <= now}
    if not times:
        return None
    t = max(times.values())
    return {"t": t, "seen": sorted({did for (_, did), rt in times.items() if rt == t})}


def move(root: Path, pid: str, place: dict) -> dict:
    """Move `pid`'s place forward to `place`; return the stored place.

    The exclusive lock keeps two processes that read, merge and replace at once
    from dropping each other's place.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(str(root / LOCK), "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            places = load(root)
            stored = later(places.get(pid), clean_place(place))
            if stored is None:
                raise ValueError("not a place: %r" % (place,))
            if places.get(pid) != stored:
                places[pid] = stored
                atomic_write_text(root / FILE, json.dumps(places, indent=2, sort_keys=True) + "\n")
            return stored
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
