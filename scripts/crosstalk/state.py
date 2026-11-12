"""The wind-down record: a step that a tool can read, in the `state` collection.

Step 0 means work may run; steps 1 and 2 mean the projects wind down, and the
auto-granter posts no GRANTED. Each writer keeps one row, `<writer>-wind-down`
(logs._validate refuses any other id in `state`). The record in force is the
newest row of the owner or the coordinator (config.coordinator); with no
coordinator set, only the owner's counts. A row of any other writer counts for
nothing. Rows are compared by their data `t`; on an equal `t` the owner's wins.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from . import config
from .logs import Line, append_line, parse_utc, utc_text
from .participants import load_roster
from .replay import load_all_lines, replay

COLLECTION = "state"
WIND_DOWN_SUFFIX = "-wind-down"
WIND_DOWN_STEPS = (0, 1, 2)


def wind_down_id(pid: str) -> str:
    return pid + WIND_DOWN_SUFFIX


def _setters(roster) -> tuple:
    """The writers whose record counts: the owner, then the coordinator if one is set."""
    coord = config.coordinator(roster)
    return ("owner", coord) if coord and coord != "owner" else ("owner",)


def _step(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value not in WIND_DOWN_STEPS:
        return None
    return value


def wind_down(docs: Dict, roster) -> dict:
    """The record in force: {"step", "started", "by", "reason"}; step 0 and nulls with none."""
    best, best_t, by = None, None, None
    for pid in _setters(roster):
        doc = docs.get((COLLECTION, wind_down_id(pid)))
        if not isinstance(doc, dict) or _step(doc.get("step")) is None:
            continue
        t = parse_utc(doc.get("t"))
        if t is None:
            continue
        if best_t is None or t > best_t:
            best, best_t, by = doc, t, pid
    if best is None:
        return {"step": 0, "started": None, "by": None, "reason": None}
    return {"step": best["step"], "started": best.get("started"), "by": by,
            "reason": best.get("reason")}


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
    now = utc_text(datetime.now(timezone.utc))
    started = None
    if step:
        current = wind_down(replay(load_all_lines(root, roster)), roster)
        started = current["started"] if current["step"] and current["started"] else now
    data = {"from": pid, "step": step, "started": started, "by": pid,
            "reason": reason or None, "t": now}
    return append_line(root, pid, "set", COLLECTION, wind_down_id(pid), data, t=now, roster=roster)

