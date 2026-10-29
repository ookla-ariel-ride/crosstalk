"""Read-side reports shared by relay.py and the MCP server: the inbox and recent messages."""
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .logs import Problem, check_all
from .participants import Roster
from .replay import enrich_messages, load_all_lines, replay
from .requests import (
    agreements_to_adopt_for,
    all_requests_for,
    inbox_for,
    pending_recipients,
    stale_locks_for,
    unanswered_owner_messages,
)
from .service import SERVICE

MESSAGES_LIMIT_MAX = 100


def trust_of(pid, roster: Roster) -> str:
    """The trust of a participant id: approved, quarantined, or unknown when absent."""
    p = roster.participants.get(pid)
    return p.trust if p is not None else "unknown"


def load_docs(root: Path, roster: Roster) -> Tuple[Optional[Dict], List[Problem]]:
    """Replayed, enriched docs, or (None, fatal problems) when any log is broken.

    relay.py read and inbox refuse to answer from a broken log; so does every
    caller of this function.
    """
    fatal = [p for p in check_all(root, roster) if p.fatal]
    if fatal:
        return None, fatal
    lines = load_all_lines(root, roster)
    return enrich_messages(replay(lines), lines), []


def inbox_report(docs: Dict, pid: str, roster: Roster, include_answered: bool = False,
                 now_iso: Optional[str] = None) -> dict:
    """What `relay.py inbox <pid>` prints: requests, owner messages, agreements, stale locks."""
    requests = (
        all_requests_for(docs, pid, roster=roster)
        if include_answered
        else inbox_for(docs, pid, roster=roster)
    )
    owner_msgs = unanswered_owner_messages(docs, pid, roster=roster)
    return {
        "pid": pid,
        "roster": {p: {"trust": e.trust} for p, e in roster.participants.items()},
        "requests": [
            {
                "request": dict(doc, id=did),
                "trust": trust_of(doc.get("from"), roster),
                "pending_from": pending_recipients(doc, replies, roster=roster),
                "replies": [dict(r, trust=trust_of(r.get("from"), roster)) for r in replies],
            }
            for (did, doc, replies) in requests
        ],
        "owner_messages": [
            {
                "message": dict(doc, id=did),
                "replies": [dict(r, trust=trust_of(r.get("from"), roster)) for r in replies],
            }
            for (did, doc, replies) in owner_msgs
        ],
        "agreements_to_adopt": agreements_to_adopt_for(docs, pid, roster=roster),
        "stale_locks": stale_locks_for(docs, pid, now_iso=now_iso),
        "service": SERVICE,
    }


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
