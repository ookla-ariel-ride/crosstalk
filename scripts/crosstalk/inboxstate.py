"""The inbox place of each participant: where its last `inbox --ack` stopped.

The places live in `inbox-state.json` in the data root, outside the message
log and out of git:

  {"alpha": {"beta": [42, "9f2c1a0b77e4"], "owner": [7, "03d5e81c2a44"]}}

A place holds, for each writer, the highest `seq` of that writer's log lines
the ack covered, and the fingerprint of that line (`fingerprint`: 12 hex
digits of the SHA-256 of its exact text in the file). Each writer has one file,
`log/<writer>.jsonl`, whose `seq` runs 1, 2, 3 with no gap or repeat
(`logs.read_log`), so the `seq` of a line is the order in which its writer
appended it. A log line is new when its `seq` is above the number of its writer
in the place, whatever its line time, its op or its document id: an `update` in
the same second, a row with an older line time and a row ahead of the clock are
all new until an ack covers them.

A place never moves backwards: two places merge by the higher number of each
writer. A file that does not parse, or nests too deep to parse, reads as empty
and stays on disk as it is until the next write.

An entry can be false for the log in hand: its number is above the writer's
top, or the line at that number has another fingerprint. The log was restored
from an older copy, or the writer was removed and added again with a new file,
whatever its length now. The read leaves such a writer out of the place
(`trusted`), so every line of it is new, and the next ack with the log in hand
repairs it (`move`). The repair drops a stored entry only when the writer's
file, read again inside the lock, shows it false: a run that read an older log
never lowers the true number a run with a newer log stored. At an equal number
with two fingerprints, the one that matches the file on disk stays.

A bare number per writer, the form of the place before the fingerprint, is
trusted when it is not above the writer's top, and gets its fingerprint at the
next ack.

A place of the form before 2026-11-12, `{"t": ..., "seen": [...]}`, converts
on read with the log in hand (`convert`); without the log it reads as empty
(`load` leaves it out).
Only the new form is written.

Each `relay.py inbox` is its own process, so a write takes an exclusive
`flock` on `inbox-state.json.lock`, reads the file again, merges and replaces
the file atomically.

Python 3.9, standard library only.
"""
import fcntl
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from .files import atomic_write_text
from .logs import Line, read_log
from .participants import ID_RE
from .readstate import _TIME_FORMAT, is_time

FILE = "inbox-state.json"
LOCK = "inbox-state.json.lock"


FINGERPRINT_RE = re.compile(r"^[0-9a-f]{12}$")


def fingerprint(line: Line) -> str:
    """The first 12 hex digits of the SHA-256 of the line's exact text in its file."""
    text = line.raw if line.raw is not None else line.to_json()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def seq_of(entry) -> int:
    """The number of a place entry: a bare `seq`, or `[seq, fingerprint]`; 0 for none."""
    if entry is None:
        return 0
    return entry[0] if isinstance(entry, list) else entry


def fp_of(entry) -> Optional[str]:
    return entry[1] if isinstance(entry, list) else None


def _clean_entry(entry) -> bool:
    if isinstance(entry, list):
        if len(entry) != 2 or not isinstance(entry[1], str) or not FINGERPRINT_RE.match(entry[1]):
            return False
        entry = entry[0]
    return not isinstance(entry, bool) and isinstance(entry, int) and entry >= 1


def clean_place(raw) -> Optional[dict]:
    """`{writer: [seq, fingerprint]}` (or a bare `seq`, the form before the
    fingerprint), each seq a whole number from 1; None for a bad shape."""
    if not isinstance(raw, dict):
        return None
    for writer, entry in raw.items():
        if not ID_RE.fullmatch(writer) or not _clean_entry(entry):
            return None
    return {w: list(e) if isinstance(e, list) else e for w, e in raw.items()}


def index(lines: Iterable[Line]) -> Dict[Tuple[str, int], Line]:
    """Each line by (writer, seq)."""
    return {(ln.from_, ln.seq): ln for ln in lines}


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


def convert(old: dict, lines: Iterable[Line]) -> dict:
    """The new place of an old-form place: for each writer, the highest `seq` the
    old place covered (line time before `t`, or equal to `t` with the id in
    `seen`), up to the first line of that writer it did not cover."""
    by_writer = {}  # type: Dict[str, list]
    for ln in lines:
        by_writer.setdefault(ln.from_, []).append(ln)
    out = {}
    for writer, own in by_writer.items():
        last = None
        for ln in sorted(own, key=lambda x: x.seq):
            if not (ln.t < old["t"] or (ln.t == old["t"] and ln.id in old["seen"])):
                break
            last = ln
        if last is not None:
            out[writer] = [last.seq, fingerprint(last)]
    return out


def _read(root: Path) -> Optional[dict]:
    try:
        raw = json.loads((Path(root) / FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    return raw if isinstance(raw, dict) else None


def load(root: Path, lines: Optional[Iterable[Line]] = None) -> Dict[str, dict]:
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


def _later(a, b):
    """The entry with the higher number; at an equal number the one with a fingerprint, else `a`."""
    if seq_of(b) > seq_of(a) or (seq_of(b) == seq_of(a) and fp_of(a) is None and fp_of(b) is not None):
        return b
    return a


def merge(a: Optional[dict], b: Optional[dict]) -> Optional[dict]:
    """The higher number of each writer of two places."""
    if a is None or b is None:
        return a if b is None else b
    out = dict(a)
    for writer, entry in b.items():
        out[writer] = _later(out.get(writer), entry) if writer in out else entry
    return out


def trusted(place: Optional[dict], lines: Iterable[Line]) -> Optional[dict]:
    """`place` without the writers whose entry cannot be true for `lines`: a number
    above the writer's top, or a fingerprint that is not the one of that line."""
    if place is None:
        return None
    by_key = index(lines)
    out = {}
    for writer, entry in place.items():
        ln = by_key.get((writer, seq_of(entry)))
        if ln is not None and fp_of(entry) in (None, fingerprint(ln)):
            out[writer] = entry
    return out


def _true_on_disk(root: Path, writer: str, entry) -> Optional[bool]:
    """Whether `entry` is true for `log/<writer>.jsonl` as it is now: False when
    the number is above the file's top or the fingerprint differs; None when the
    file cannot be read, or does not read cleanly up to that number."""
    path = Path(root) / "log" / (writer + ".jsonl")
    if not path.exists():
        return False
    try:
        good, problems = read_log(path)
    except OSError:
        return None
    n = seq_of(entry)
    if n > len(good):
        # A file that does not read cleanly (a last line cut inside a multi-byte
        # character fails the decode of the whole file) cannot show the entry false.
        return None if problems else False
    return fp_of(entry) in (None, fingerprint(good[n - 1]))


def is_new(line: Line, place: Optional[dict]) -> bool:
    """True when the log line comes after `place` in its writer's order."""
    return place is None or line.seq > seq_of(place.get(line.from_))


def newest(lines: Iterable[Line]) -> Optional[dict]:
    """The place of the whole log: `[seq, fingerprint]` of each writer's last line; None for no line."""
    top = {}  # type: Dict[str, Line]
    for ln in lines:
        if ln.from_ not in top or ln.seq > top[ln.from_].seq:
            top[ln.from_] = ln
    return {w: [ln.seq, fingerprint(ln)] for w, ln in top.items()} or None


def _repair(root: Path, old: dict, clean: dict) -> dict:
    """`old` (the stored place) without the entries that are false for the log on
    disk, judged only where `old` would win against `clean` or differs at an equal
    number. At an equal number with two fingerprints, the one that matches the
    file on disk is kept."""
    out = dict(old)
    for writer, entry in old.items():
        given = clean.get(writer)
        if seq_of(entry) < seq_of(given) or entry == given:
            continue
        # A stored entry that is true (or that the disk cannot judge) stays; at an
        # equal number `merge` then keeps it over `clean`.
        if _true_on_disk(root, writer, entry) is False:
            del out[writer]
    return out


def move(root: Path, pid: str, place: dict, lines: Optional[Iterable[Line]] = None) -> dict:
    """Move `pid`'s place forward to `place`; return the stored place.

    The exclusive lock keeps two processes that read, merge and replace at once
    from dropping each other's place. `lines` (the log this run read) converts
    the old-form places in the file; without them those places drop out at this
    write. With `lines`, a stored entry that would win against `place` (a higher
    number, or the same number with another fingerprint) is checked against its
    writer's file, read again here inside the lock: when its number is above the
    file's top or its fingerprint is not that line's, it is false and goes. A
    true entry stays, so a run that read an older log cannot lower it.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(str(root / LOCK), "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            places = load(root, lines)
            clean = clean_place(place)
            if clean is None:
                raise ValueError("not a place: %r" % (place,))
            old = places.get(pid)
            if old is not None and lines is not None:
                old = _repair(root, old, clean)
            stored = merge(old, clean)
            if places.get(pid) != stored or _read(root) != places:
                places[pid] = stored
                atomic_write_text(root / FILE, json.dumps(places, indent=2, sort_keys=True) + "\n")
            return stored
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
