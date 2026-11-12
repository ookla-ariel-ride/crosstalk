"""The wind-down record: a step that a tool can read, in the `state` collection.

Step 0 means work may run; steps 1 and 2 mean the projects wind down, and the
auto-granter posts no GRANTED. Each writer keeps one row, `<writer>-wind-down`
(logs._validate refuses any other id in `state`). The record in force is the
newest row of the owner or the coordinator (config.coordinator); with no
coordinator set, only the owner's counts. A row of any other writer counts for
nothing. Rows are compared by their `t`, which logs.state_problem ties to the
log line's time; on an equal `t` the owner's wins. set_wind_down writes a `t`
later than both records, so a later command always takes force.

The state row (read, state_row) puts the shared readings in one object for
tools: the numbers of vitals.read, the wind-down record in force, the land
queue, and `may_start` with the reasons it is false. `relay.py state` prints
it, and the server adds it to /api/vitals under the key `state`.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

from . import config
from .landqueue import land_queue
from .logs import Line, append_line, parse_utc, state_lock, utc_text
from .participants import load_roster
from .replay import load_all_lines, replay

COLLECTION = "state"
WIND_DOWN_SUFFIX = "-wind-down"
WIND_DOWN_STEPS = (0, 1, 2)


def wind_down_id(pid: str) -> str:
    return pid + WIND_DOWN_SUFFIX


def setters_of(coord: str) -> tuple:
    """The writers whose record counts: the owner, then the coordinator `coord` if one is set."""
    return ("owner", coord) if coord and coord != "owner" else ("owner",)


def _setters(roster) -> tuple:
    return setters_of(config.coordinator(roster))


def _step(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value not in WIND_DOWN_STEPS:
        return None
    return value


def _records(docs: Dict, roster, setters: Optional[tuple] = None) -> list:
    """(time, pid, doc) for each setter's row that counts, the owner's first."""
    out = []
    for pid in setters or _setters(roster):
        doc = docs.get((COLLECTION, wind_down_id(pid)))
        if not isinstance(doc, dict) or _step(doc.get("step")) is None:
            continue
        t = parse_utc(doc.get("t"))
        if t is not None:
            out.append((t, pid, doc))
    return out


def wind_down(docs: Dict, roster, setters: Optional[tuple] = None) -> dict:
    """The record in force: {"step", "started", "by", "reason"}; step 0 and nulls with none.
    `setters` (setters_of) replaces the roster's when the caller has only the coordinator."""
    best = None
    for rec in _records(docs, roster, setters):
        if best is None or rec[0] > best[0]:
            best = rec
    if best is None:
        return {"step": 0, "started": None, "by": None, "reason": None}
    doc = best[2]
    return {"step": doc["step"], "started": _text(doc, "started"), "by": best[1],
            "reason": _text(doc, "reason")}


def _text(doc: dict, name: str) -> Optional[str]:
    """A text field, or None when it holds no text (as threads.readable reads a row):
    the log check leaves the types inside a row's data free."""
    value = doc.get(name)
    return value if isinstance(value, str) else None


def set_wind_down(root: Path, pid: str, step: int, reason: Optional[str] = None,
                  roster=None) -> Line:
    """Write pid's wind-down row. Only the owner and the coordinator may; `started` is now
    for a new wind-down, kept while the record in force is already at step 1 or 2, and null at 0."""
    root = Path(root)
    if roster is None:
        roster = load_roster(root / "participants.json")
    if pid not in _setters(roster):
        raise ValueError("only the owner or the coordinator may set the wind-down record, not %r" % (pid,))
    if _step(step) is None:
        raise ValueError("wind-down step must be one of %s, not %r"
                         % (", ".join(str(s) for s in WIND_DOWN_STEPS), step))
    reason = reason.strip() if isinstance(reason, str) else None
    # The time is taken under the state lock, so it is not earlier than a GRANTED
    # the granter appended while this command waited for the lock.
    # A stuck holder delays it for at most logs.STATE_LOCK_WAIT seconds: then the
    # owner's record goes in without the lock, with a line on stderr.
    with state_lock(root, force=True):
        return _set_wind_down(root, pid, step, reason, roster)


def _set_wind_down(root: Path, pid: str, step: int, reason: Optional[str], roster) -> Line:
    docs = replay(load_all_lines(root, roster))
    # Later than every record that counts, so this one takes force even within the same second.
    when = max([datetime.now(timezone.utc)] + [r[0] + timedelta(seconds=1) for r in _records(docs, roster)])
    now = utc_text(when)
    started = None
    if step:
        current = wind_down(docs, roster)
        started = current["started"] if current["step"] and current["started"] else now
    data = {"from": pid, "step": step, "started": started, "by": pid,
            "reason": reason or None, "t": now}
    return append_line(root, pid, "set", COLLECTION, wind_down_id(pid), data, t=now, roster=roster)



def state_row(vitals: dict, land: dict, wind: dict, now_iso: str) -> dict:
    """The state row from the readings of vitals.read, the land queue as it is and the
    wind-down record in force. `may_start` is false for each reason in `may_start_why`;
    a null reading gives no reason."""
    usage, limit, host = vitals["usage"], vitals["usage_limit"], vitals["build_host"]
    why = []
    if wind["step"] in (1, 2):
        why.append("wind-down step %d" % wind["step"])
    if usage is not None and limit is not None and usage >= limit:
        why.append("usage %s at or over the limit %s" % (usage, limit))
    if host is not None and host.get("reachable") is False:
        why.append("build host not reachable")
    return {"t": now_iso, "disk_free_gb": vitals["disk_free_gb"], "usage": usage,
            "usage_limit": limit, "build_host": host, "wind_down": wind, "land": land,
            "may_start": not why, "may_start_why": why}


def read(root, docs: Dict, roster, projects: Iterable[str], cfg: dict, buildhost_state=None,
         now: Optional[float] = None, readings: Optional[dict] = None) -> dict:
    """The state row of the install at root, for `relay.py state` and /api/vitals. `docs` are
    the replayed documents, `projects` the roster ids, `cfg` and `buildhost_state` what
    vitals.read takes. `readings` is a vitals.read result the caller already has; without
    it, read calls vitals.read once. Reads only: it writes no file."""
    from . import vitals  # here, not at the top: vitals imports autogrant, which imports this module
    if now is None:
        now = datetime.now(timezone.utc).timestamp()
    if readings is None:
        readings = vitals.read(root, cfg, buildhost_state=buildhost_state, now=now)
    land = land_queue(docs, projects=projects, now=now, coordinator=config.coordinator(roster))
    when = utc_text(datetime.fromtimestamp(now, timezone.utc))
    return state_row(readings, land, wind_down(docs, roster), when)


# The keys of a reading that can be null as a whole, so that a key under it is null, not unknown.
NULLABLE_KEYS = {"build_host": ("reachable", "load", "t")}


def get(row: dict, key: str):
    """One value of the row by a dotted key (`wind_down.step`). A key under a null
    build_host is null; a key that is not there raises KeyError."""
    parts = key.split(".")
    if len(parts) == 2 and parts[0] in NULLABLE_KEYS and row.get(parts[0]) is None:
        if parts[1] in NULLABLE_KEYS[parts[0]]:
            return None
        raise KeyError(key)
    value = row
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            raise KeyError(key)
        value = value[part]
    return value
