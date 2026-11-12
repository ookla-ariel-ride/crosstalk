"""Read-side reports shared by relay.py and the MCP server: the inbox and recent messages."""
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .inboxstate import is_new
from .logs import Line, Problem, check_all
from .participants import Roster
from .replay import enrich_messages, last_line_map, load_all_lines, replay
from .requests import (
    acks_by_item,
    agreements_to_adopt_for,
    all_requests_for,
    inbox_for,
    needs_answer,
    open_owner_questions,
    owner_questions,
    pending_recipients,
    stale_locks_for,
    thread_replies_for,
    unanswered_owner_messages,
)
from .service import SERVICE

MESSAGES_LIMIT_MAX = 100
STILL_OPEN_MAX = 20


def trust_of(pid, roster: Roster) -> str:
    """The trust of a participant id: approved, quarantined, or unknown when absent."""
    p = roster.participants.get(pid)
    return p.trust if p is not None else "unknown"


def load_docs_and_lines(root: Path, roster: Roster
                        ) -> Tuple[Optional[Dict], List[Line], List[Problem]]:
    """Replayed, enriched docs and the log lines they came from, or (None, [], fatal problems).

    `inbox --new` reads the lines: each carries its writer and its `seq`.
    """
    fatal = [p for p in check_all(root, roster) if p.fatal]
    if fatal:
        return None, [], fatal
    lines = load_all_lines(root, roster)
    return enrich_messages(replay(lines), lines), lines, []


def load_docs(root: Path, roster: Roster) -> Tuple[Optional[Dict], List[Problem]]:
    """Replayed, enriched docs, or (None, fatal problems) when any log is broken.

    relay.py read and inbox refuse to answer from a broken log; so does every
    caller of this function.
    """
    docs, _lines, fatal = load_docs_and_lines(root, roster)
    return docs, fatal


def inbox_report(docs: Dict, pid: str, roster: Roster, include_answered: bool = False,
                 now_iso: Optional[str] = None, service: Optional[dict] = None) -> dict:
    """What `relay.py inbox <pid>` prints: requests, owner messages, replies on pid's
    threads, agreements, stale locks, and for the owner the open questions for it.
    `service` defaults to SERVICE (service.service_for gives one with a data root)."""
    requests = (
        all_requests_for(docs, pid, roster=roster)
        if include_answered
        else inbox_for(docs, pid, roster=roster)
    )
    owner_msgs = unanswered_owner_messages(docs, pid, roster=roster)
    acks = acks_by_item(docs, roster=roster)
    out = {
        "pid": pid,
        "roster": {p: {"trust": e.trust} for p, e in roster.participants.items()},
        "requests": [
            {
                "request": dict(doc, id=did),
                "trust": trust_of(doc.get("from"), roster),
                "needs_answer": needs_answer(doc),
                "pending_from": pending_recipients(doc, replies, roster=roster,
                                                   acked=acks.get(did, ())),
                "acked": sorted(acks.get(did, ())),
                "replies": [dict(r, trust=trust_of(r.get("from"), roster)) for r in replies],
            }
            for (did, doc, replies) in requests
        ],
        "owner_messages": [
            {
                "message": dict(doc, id=did),
                "needs_answer": needs_answer(doc),
                "replies": [dict(r, trust=trust_of(r.get("from"), roster)) for r in replies],
            }
            for (did, doc, replies) in owner_msgs
        ],
        "thread_replies": [
            {
                "message": dict(doc, id=did),
                "trust": trust_of(doc.get("from"), roster),
                "needs_answer": needs_answer(doc),
                "root": root,
            }
            for (did, doc, root) in thread_replies_for(docs, pid, roster=roster)
        ],
        "agreements_to_adopt": agreements_to_adopt_for(docs, pid, roster=roster),
        "stale_locks": stale_locks_for(docs, pid, now_iso=now_iso),
        "service": SERVICE if service is None else service,
    }
    if pid == "owner":
        out["owner_questions"] = [
            {"question": dict(doc, id=did), "trust": trust_of(doc.get("from"), roster)}
            for (did, doc) in open_owner_questions(docs, roster=roster)
        ]
    return out


def new_report(full: dict, lines: List[Line], place: Optional[dict],
               nxt: Optional[dict]) -> dict:
    """What `relay.py inbox <pid> --new` prints: the items of `full` (an
    `inbox_report`) that a log line after `place` touched (a new row, or an
    edit of an old one), with their present content.

    The open requests and owner messages from before the place that still need
    an answer stay as `still_open`, without bodies, newest first, at most
    STILL_OPEN_MAX; `still_open_more` counts the rest. `next` is the place to
    ack: the highest `seq` of each writer of the log as this run loaded it.
    """
    touched = {(ln.collection, ln.id) for ln in lines if is_new(ln, place)}
    times = {key: ln.t for key, ln in last_line_map(lines).items()}

    def new(coll, did):
        return (coll, did) in touched or (coll, did) not in times

    out = {k: full[k] for k in ("pid", "roster", "stale_locks", "service", "owner_questions")
           if k in full}
    old = []
    for key, field in (("requests", "request"), ("owner_messages", "message"),
                       ("thread_replies", "message")):
        out[key] = []
        for item in full.get(key, []):
            doc = item[field]
            if new("messages", doc["id"]):
                out[key].append(item)
            elif key != "thread_replies" and item.get("needs_answer"):
                old.append((times.get(("messages", doc["id"]), ""), doc))
    old.sort(key=lambda r: (r[0], r[1]["id"]), reverse=True)
    out["still_open"] = [{"id": d["id"], "from": d.get("from"), "t": d.get("t"),
                          "summary": d.get("summary")} for _rt, d in old[:STILL_OPEN_MAX]]
    out["still_open_more"] = max(0, len(old) - STILL_OPEN_MAX)
    agreements = full.get("agreements_to_adopt", [])
    out["agreements_to_adopt"] = [a for a in agreements if new("agreements", a["id"])]
    out["agreements_open"] = len(agreements)
    out["place"] = place
    out["next"] = nxt
    return out


def questions_report(docs: Dict, roster: Roster, include_closed: bool = False) -> dict:
    """What `relay.py questions` prints: the questions for the owner, oldest first.

    Open ones only, or with include_closed also the answered and withdrawn ones,
    each with the row that closed it.
    """
    rows = []
    for did, doc, status, closer in owner_questions(docs, roster=roster):
        if status != "open" and not include_closed:
            continue
        item = {"question": dict(doc, id=did), "status": status,
                "trust": trust_of(doc.get("from"), roster)}
        if status == "answered":
            item["answer"] = closer
        elif status == "withdrawn":
            item["withdrawn"] = closer
        rows.append(item)
    return {"questions": rows, "open": sum(1 for r in rows if r["status"] == "open")}


def recent_messages(docs: Dict, roster: Roster, limit: int = 20, since: Optional[str] = None,
                    sender: Optional[str] = None) -> List[dict]:
    """The newest `limit` messages (oldest first), each with its id and its sender's trust.

    `since` keeps messages whose `t` is later than it; `sender` keeps one sender's.
    """
    limit = max(1, min(int(limit), MESSAGES_LIMIT_MAX))
    rows = []
    for (coll, did), doc in docs.items():
        if coll != "messages":
            continue
        t = doc.get("t") if isinstance(doc.get("t"), str) else ""
        if since is not None and not t > since:
            continue
        if sender is not None and doc.get("from") != sender:
            continue
        rows.append((t, did, doc))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [dict(doc, id=did, trust=trust_of(doc.get("from"), roster))
            for (_t, did, doc) in rows[-limit:]]
