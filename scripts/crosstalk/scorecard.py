"""The scorecard: how each participant kept up over a window, from the messages store.

`relay.py scorecard` prints one line per approved participant (the owner is left
out), then one line for the questions to the owner. The window is the `hours`
before `end`; a row is in it when start < t <= end. Retracted posts count for
nothing.

    posts         messages from the participant in the window
    check-ins     those of kind `check-in` or `checkin`, or whose summary holds
                  "check-in" (any case)
    longest gap   the longest time between two check-ins one after the other. The
                  edges count too: from the window start (or the participant's first
                  row, when that is later) to the first check-in, and from the last
                  check-in to the window end. With no check-in it is the whole span.
    requests      `kind: request` messages from someone else whose `to` names the
                  participant (requests._recipients: a TURN ASK names only the
                  coordinator), that need an answer (requests.needs_answer)
    answered      the participant replied (`replyTo`), or put a `none` mark or a vote
                  on it (marks.ANSWERS), at or before the window end. The answer time
                  is the earliest of these; the median is over the answered requests.
    owner questions  `kind: question` messages (requests.owner_questions), by asker

The daily post (`relay.py scorecard --daily`, run by the housekeeper) is opt-in:
config `scorecard.daily`. It posts once per local day, at or after `scorecard.at`
in `scorecard.zone`, a `kind: status` message from the coordinator with the id
`<coordinator>-scorecard-<local date>`, so a second run that day posts nothing.
"""
import statistics
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import config as config_mod
from .marks import ANSWERS, _mark_rows
from .requests import (_is_approved, _is_approved_project, _messages, _parse_iso, _recipients, first_seen,
                       is_retracted, needs_answer, no_answer, owner_questions)

CHECK_IN_KINDS = ("check-in", "checkin")


def is_check_in(doc: dict) -> bool:
    summary = doc.get("summary")
    return doc.get("kind") in CHECK_IN_KINDS or (isinstance(summary, str) and "check-in" in summary.lower())


def _participants(roster) -> List[str]:
    participants = getattr(roster, "participants", roster)
    if not isinstance(participants, dict):
        return []
    return sorted(pid for pid in participants if pid != "owner" and _is_approved(pid, roster))


def _minutes(seconds: float) -> float:
    return round(seconds / 60.0, 1)


def _answer_times(docs: Dict, messages: Dict[str, dict], roster) -> Dict[Tuple[str, str], float]:
    """(request id, participant) -> the earliest time it replied, marked `none` or voted."""
    out = {}  # type: Dict[Tuple[str, str], float]

    def note(key, ts):
        if ts is not None and (key not in out or ts < out[key]):
            out[key] = ts

    for doc in messages.values():
        rt, frm = doc.get("replyTo"), doc.get("from")
        if isinstance(rt, str) and isinstance(frm, str) and not is_retracted(doc):
            note((rt, frm), _parse_iso(doc.get("t")))
    for (coll, item), frm, word, row in _mark_rows(docs, roster):
        if coll == "messages" and word in ANSWERS:
            note((item, frm), _parse_iso(row.get("t")))
    return out


def scorecard(docs: Dict, roster, end: float, hours: float = 48.0) -> dict:
    """The numbers of the window of `hours` that ends at `end` (seconds since the epoch)."""
    start = end - hours * 3600.0
    messages = _messages(docs)

    def in_window(doc) -> Optional[float]:
        ts = _parse_iso(doc.get("t"))
        return ts if ts is not None and start < ts <= end and not is_retracted(doc) else None

    answers = _answer_times(docs, messages, roster)
    rows = []
    for pid in _participants(roster):
        posts, checks = 0, []  # type: Tuple[int, List[float]]
        for doc in messages.values():
            ts = in_window(doc) if doc.get("from") == pid else None
            if ts is None:
                continue
            posts += 1
            if is_check_in(doc):
                checks.append(ts)
        checks.sort()
        seen = first_seen(docs, pid)
        gap = None  # type: Optional[float]
        if seen is not None and seen <= end:
            marks = [max(start, seen)] + checks + [end]
            gap = _minutes(max(b - a for a, b in zip(marks, marks[1:])))
        waits = []  # type: List[float]
        unanswered = []  # type: List[str]
        for did, doc in sorted(messages.items()):
            ts = in_window(doc)
            if ts is None or doc.get("kind") != "request" or doc.get("from") == pid or doc.get("to") is None:
                continue
            if pid not in _recipients(doc, roster=roster) or not needs_answer(doc):
                continue
            at = answers.get((did, pid))
            if at is not None and at <= end:
                waits.append(max(0.0, at - ts))
            else:
                unanswered.append(did)
        rows.append({
            "id": pid, "posts": posts, "check_ins": len(checks), "longest_gap_min": gap,
            "requests": len(waits) + len(unanswered), "answered": len(waits), "unanswered": unanswered,
            "median_answer_min": _minutes(statistics.median(waits)) if waits else None,
        })
    askers = {}  # type: Dict[str, int]
    for _did, doc, _status, _closer in owner_questions(docs, roster=roster):
        if in_window(doc) is not None:
            askers[doc["from"]] = askers.get(doc["from"], 0) + 1
    return {
        "start": _iso(start), "end": _iso(end), "hours": hours, "participants": rows,
        "owner_questions": {"count": sum(askers.values()), "by_asker": dict(sorted(askers.items()))},
    }


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _n(x: float) -> str:
    return "%d" % round(x)


def _names(ids: List[str]) -> str:
    return ids[0] if len(ids) == 1 else "%s and %s" % (", ".join(ids[:-1]), ids[-1])


def lines(card: dict) -> List[str]:
    """The plain lines of a scorecard."""
    out = []
    for r in card["participants"]:
        gap = "no posts" if r["longest_gap_min"] is None else "longest gap %s min" % _n(r["longest_gap_min"])
        text = "%s: %d posts, %d check-ins, %s." % (r["id"], r["posts"], r["check_ins"], gap)
        n, missed = r["requests"], len(r["unanswered"])
        if not n:
            text += " 0 requests to it."
        else:
            text += " %d request%s to it, %s" % (n, "" if n == 1 else "s",
                                                 "all answered" if not missed else "%d unanswered" % missed)
            text += ", median %s min." % _n(r["median_answer_min"]) if r["answered"] else "."
        out.append(text)
    q = card["owner_questions"]
    text = "Owner questions: %d in %g hours" % (q["count"], card["hours"])
    by = sorted(q["by_asker"].items(), key=lambda kv: (-kv[1], kv[0]))
    if len(by) > 1 and len({n for _a, n in by}) == 1:
        text += ", %d each from %s" % (by[0][1], _names([a for a, _n in by]))
    elif by:
        text += ", " + ", ".join("%d from %s" % (n, a) for a, n in by)
    out.append(text + ".")
    return out


def message(card: dict, sender: str, roster) -> dict:
    """The `kind: status` post of a scorecard, to every approved project but the sender."""
    data = {"from": sender, "kind": "status",
            "summary": "Scorecard: the %g hours to %s" % (card["hours"], card["end"]),
            "body": "\n".join(lines(card))}  # type: Dict[str, object]
    participants = getattr(roster, "participants", roster)
    to = sorted(p for p in participants if p != sender and _is_approved_project(p, roster)) \
        if isinstance(participants, dict) else []
    if to:
        data["to"] = to
    return no_answer(data)


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except (ImportError, KeyError, ValueError):
        return timezone(timedelta(hours=-8), "PST")


def daily_slot(cfg: dict, now: float) -> Optional[Tuple[str, float]]:
    """(local date, the slot time) when today's daily post is due at `now`, else None.

    Due means `scorecard.daily` is on and the local time in `scorecard.zone` is at or
    past `scorecard.at` (HH:MM). The caller skips a date that already has its post.
    """
    sc = cfg.get("scorecard") if isinstance(cfg, dict) else None
    if not isinstance(sc, dict) or sc.get("daily") is not True:
        return None
    try:
        hh, mm = (int(x) for x in str(sc.get("at") or "09:00").split(":"))
        local = datetime.fromtimestamp(now, _zone(str(sc.get("zone") or "")))
        slot = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        return None
    if local < slot:
        return None
    return slot.strftime("%Y-%m-%d"), slot.timestamp()


def daily_id(sender: str, date: str) -> str:
    return "%s-scorecard-%s" % (sender, date)


def default_hours(roster) -> float:
    hours = config_mod.of(roster).get("scorecard", {}).get("hours")
    return hours if isinstance(hours, float) and hours > 0 else 48.0
