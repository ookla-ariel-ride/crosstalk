"""The inbox place of each participant: where its last `inbox --ack` stopped.

The places live in `inbox-state.json` in the data root, outside the message
log and out of git:

  {"alpha": {"beta": 42, "owner": 7}}

A place holds, for each writer, the highest `seq` of that writer's log lines
the ack covered. Each writer has one file, `log/<writer>.jsonl`, whose `seq`
runs 1, 2, 3 with no gap or repeat (`logs.read_log`), so the `seq` of a line is
the order in which its writer appended it. A log line is new when its `seq` is
above the number of its writer in the place, whatever its line time, its op or
its document id: an `update` in the same second, a row with an older line time
and a row ahead of the clock are all new until an ack covers them.

A place never moves backwards: two places merge by the higher number of each
writer. A file that does not parse, or nests too deep to parse, reads as empty
and stays on disk as it is until the next write.

A place of the form before 2026-11-12, `{"t": ..., "seen": [...]}`, converts
on read with the log in hand (`convert`); without the log it reads as empty.
Only the new form is written.

Each `relay.py inbox` is its own process, so a write takes an exclusive
`flock` on `inbox-state.json.lock`, reads the file again, merges and replaces
the file atomically.

Python 3.9, standard library only.
"""
import fcntl
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

from .files import atomic_write_text
from .logs import Line
from .participants import ID_RE
from .readstate import _TIME_FORMAT, is_time

FILE = "inbox-state.json"
LOCK = "inbox-state.json.lock"


def clean_place(raw) -> Optional[Dict[str, int]]:
    """`{writer: seq}` with each seq a whole number from 1, or None for a bad shape."""
    if not isinstance(raw, dict):
        return None
    for writer, seq in raw.items():
        if not ID_RE.fullmatch(writer) or isinstance(seq, bool) or not isinstance(seq, int) \
                or seq < 1:
            return None
    return dict(raw)


def _old_place(raw) -> Optional[dict]:
    """A place of the old form `{"t", "seen"}`, or None.

    The `t` must have the log's exact zero-padded form: `2026-11-1T22:17:00Z`
    sorts as a string after `2026-11-10T...` and would cover lines it never saw.
    """
    if not isinstance(raw, dict) or set(raw) != {"t", "seen"}:
        return None
    t, seen = raw["t"], raw["seen"]
    if not is_time(t) or datetime.strptime(t, _TIME_FORMAT).strftime(_TIME_FORMAT) != t:
        return None
    if not isinstance(seen, list) or not all(isinstance(s, str) and s for s in seen):
        return None
    return {"t": t, "seen": set(seen)}


def convert(old: dict, lines: Iterable[Line]) -> Dict[str, int]:
    """The new place of an old-form place: for each writer, the highest `seq` the
    old place covered (line time before `t`, or equal to `t` with the id in
    `seen`), up to the first line of that writer it did not cover."""
    by_writer = {}  # type: Dict[str, list]
    for ln in lines:
        by_writer.setdefault(ln.from_, []).append(ln)
    out = {}
    for writer, own in by_writer.items():
        seq = 0
        for ln in sorted(own, key=lambda x: x.seq):
            if not (ln.t < old["t"] or (ln.t == old["t"] and ln.id in old["seen"])):
                break
            seq = ln.seq
        if seq:
            out[writer] = seq
    return out


def _read(root: Path) -> Optional[dict]:
    try:
        raw = json.loads((Path(root) / FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    return raw if isinstance(raw, dict) else None


def load(root: Path, lines: Optional[Iterable[Line]] = None) -> Dict[str, Dict[str, int]]:
    """The clean places in `root`; `{}` when the file is missing or does not parse.

    With `lines` (the log), an old-form place converts; without them it is left out.
    """
    lines = None if lines is None else list(lines)
    places = {}
    for pid, raw in (_read(root) or {}).items():
        place = clean_place(raw)
        if place is None and lines is not None:
            old = _old_place(raw)
            place = None if old is None else convert(old, lines)
        if place is not None:
            places[pid] = place
    return places


def merge(a: Optional[dict], b: Optional[dict]) -> Optional[dict]:
    """The higher number of each writer of two places."""
    if a is None or b is None:
        return a if b is None else b
    out = dict(a)
    for writer, seq in b.items():
        out[writer] = max(seq, out.get(writer, 0))
    return out


def is_new(line: Line, place: Optional[dict]) -> bool:
    """True when the log line comes after `place` in its writer's order."""
    return place is None or line.seq > place.get(line.from_, 0)


def newest(lines: Iterable[Line]) -> Optional[Dict[str, int]]:
    """The place of the whole log: the highest `seq` of each writer; None for no line."""
    out = {}  # type: Dict[str, int]
    for ln in lines:
        out[ln.from_] = max(ln.seq, out.get(ln.from_, 0))
    return out or None


def move(root: Path, pid: str, place: dict, lines: Optional[Iterable[Line]] = None) -> dict:
    """Move `pid`'s place forward to `place`; return the stored place.

    The exclusive lock keeps two processes that read, merge and replace at once
    from dropping each other's place. `lines` (the log) converts the old-form
    places in the file; without them those places drop out at this write.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(str(root / LOCK), "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            places = load(root, lines)
            stored = merge(places.get(pid), clean_place(place))
            if stored is None:
                raise ValueError("not a place: %r" % (place,))
            if places.get(pid) != stored or _read(root) != places:
                places[pid] = stored
                atomic_write_text(root / FILE, json.dumps(places, indent=2, sort_keys=True) + "\n")
            return stored
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
