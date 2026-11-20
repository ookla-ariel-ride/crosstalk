"""The check-in watchdog: one alert when the coordinator's check-in is late.

The coordinator is the roster's `relay` (config.coordinator). Its last check-in is
its newest message that scorecard.is_check_in counts, retracted posts left out.
The next check-in is due one period after it: the `periodMin` of the coordinator's
`check-in` schedule job (`relay.py schedule ran --name check-in`), or config
`watchdog.everyMin` (30) when it has no such job. The check-in is late by the
minutes past that due time.

`relay.py watchdog` prints the reading. With `--post` it posts one `kind: alert`
message when all of these hold:

- config `watchdog.enabled` is true (off by default);
- the check-in is late by `watchdog.lateMin` (60) or more (`--late-min` overrides);
- the wind-down record in force (state.wind_down) is at step 0: at step 1 or 2
  check-ins pause by rule;
- no alert for this gap exists yet: no message of `source: watchdog` from anyone
  carries this coordinator and this last check-in id (`coordinator`, `checkIn`).
  So a second run is a no-op, and a new check-in starts a new gap. The id is
  `<sender>-watchdog-<coordinator>-<last check-in id>` (a writer's ids start
  with its own id), so the same sender can never post one gap twice.

The alert goes to every approved project but the sender, and to the owner, so it
wakes them. It cannot come from the coordinator (the one that is late) or the
owner. The sender is `--from`, else config `watchdog.from`, else the first
approved project by id that is neither. The housekeeper runs `watchdog --post`
every 30 minutes, so an alert lands at most 30 minutes after the threshold.
"""
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from . import config as config_mod
from .requests import _is_approved, _is_approved_project, _messages, _parse_iso, is_retracted
from .schedules import job_id
from .scorecard import is_check_in
from .state import wind_down

SOURCE = "watchdog"


def _iso(ts: Optional[float]) -> Optional[str]:
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_check_in(docs: Dict, pid: str) -> Optional[Tuple[str, float]]:
    """(id, time) of pid's newest check-in, or None."""
    best = None  # type: Optional[Tuple[str, float]]
    for did, doc in _messages(docs).items():
        if doc.get("from") != pid or is_retracted(doc) or not is_check_in(doc):
            continue
        ts = _parse_iso(doc.get("t"))
        if ts is not None and (best is None or (ts, did) > (best[1], best[0])):
            best = (did, ts)
    return best


def period_min(docs: Dict, pid: str, cfg: dict) -> float:
    """The check-in period: the schedule job's periodMin, else watchdog.everyMin."""
    doc = docs.get(("schedules", job_id(pid, "check-in")))
    if isinstance(doc, dict) and not doc.get("removed"):
        p = doc.get("periodMin")
        if isinstance(p, (int, float)) and not isinstance(p, bool) and p > 0:
            return float(p)
    return cfg["watchdog"]["everyMin"]


def alert_id(frm: str, coordinator: str, check_in_id: str) -> str:
    return "%s-watchdog-%s-%s" % (frm, coordinator, check_in_id)


def posted(docs: Dict, coordinator: str, check_in_id: str) -> Optional[str]:
    """The id of an alert for this gap from any sender, or None."""
    for did, doc in sorted(_messages(docs).items()):
        if doc.get("source") == SOURCE and doc.get("coordinator") == coordinator \
                and doc.get("checkIn") == check_in_id:
            return did
    return None


def reading(docs: Dict, roster, now: float, late_min: Optional[float] = None) -> dict:
    """The watchdog's view at `now`. `state` is one of: no coordinator, no check-in,
    wind-down, on time, late, posted (late, and this gap's alert exists)."""
    cfg = config_mod.of(roster)
    coord = config_mod.coordinator(roster)
    threshold = cfg["watchdog"]["lateMin"] if late_min is None else late_min
    out = {"coordinator": coord or None, "threshold_min": threshold, "last_id": None, "last_t": None,
           "period_min": None, "due": None, "late_min": None, "posted": None, "state": "no coordinator"}
    if not coord:
        return out
    last = last_check_in(docs, coord)
    if last is None:
        out["state"] = "no check-in"
        return out
    period = period_min(docs, coord, cfg)
    due = last[1] + period * 60.0
    out.update(last_id=last[0], last_t=_iso(last[1]), period_min=period, due=_iso(due),
               late_min=round((now - due) / 60.0, 1), posted=posted(docs, coord, last[0]))
    if wind_down(docs, roster)["step"] in (1, 2):
        out["state"] = "wind-down"
    elif out["late_min"] < threshold:
        out["state"] = "on time"
    else:
        out["state"] = "posted" if out["posted"] else "late"
    return out


def sender(roster, explicit: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(sender, None) or (None, why not). Never the coordinator or the owner."""
    coord = config_mod.coordinator(roster)
    participants = getattr(roster, "participants", roster)
    banned = {coord, "owner"}
    pick = explicit or config_mod.of(roster)["watchdog"]["from"]
    if pick:
        if pick in banned:
            return None, "the alert cannot come from %r (the coordinator or the owner)" % pick
        if pick not in participants or not _is_approved(pick, roster):
            return None, "%r is not an approved participant" % pick
        return pick, None
    for pid in sorted(participants):
        if pid not in banned and _is_approved_project(pid, roster):
            return pid, None
    return None, "no approved project other than the coordinator to post as"


def message(r: dict, frm: str, roster) -> dict:
    """The alert: to every approved project but the sender, and to the owner."""
    participants = getattr(roster, "participants", roster)
    to = sorted(p for p in participants if p != frm and _is_approved_project(p, roster))
    if "owner" in participants:
        to.append("owner")
    coord = r["coordinator"]
    late = "%d" % round(r["late_min"])
    return {
        "from": frm, "kind": "alert", "source": SOURCE, "important": True, "to": to,
        "coordinator": coord, "checkIn": r["last_id"],
        "summary": "Watchdog: %s has not checked in for %s min" % (coord, late),
        "body": ("%s's check-in is %s min late. Its last check-in is %s at %s; the next was due at %s "
                 "(every %g min).\n\nTell the owner if this is news, and check the %s session."
                 % (coord, late, r["last_id"], r["last_t"], r["due"], r["period_min"], coord)),
    }
