"""Open help requests derived from the replayed messages.

A help request is a `messages` doc with `kind: "request"`. It is open while no
messages doc's `replyTo` points at its id. `to` lists the asked participants;
omitted means everyone (a broadcast). Open-ness is derived, never stored, so
it cannot drift or race.
"""
from typing import Dict, List, Tuple


def _messages(docs: Dict) -> Dict[str, dict]:
    out = {}
    for (coll, did), doc in docs.items():
        if coll == "messages":
            out[did] = doc
    return out


def _answered_ids(messages: Dict[str, dict]) -> set:
    answered = set()
    for doc in messages.values():
        rt = doc.get("replyTo")
        if isinstance(rt, str) and rt:
            answered.add(rt)
    return answered


def _replies_by_request(messages: Dict[str, dict]) -> Dict[str, List[dict]]:
    replies = {}
    for did, doc in messages.items():
        rt = doc.get("replyTo")
        if isinstance(rt, str) and rt:
            replies.setdefault(rt, []).append(doc)
    for rt in replies:
        replies[rt].sort(key=_t)
    return replies


def _t(doc: dict) -> str:
    t = doc.get("t")
    return t if isinstance(t, str) else ""


def _addressed(doc: dict, pid: str) -> bool:
    to = doc.get("to")
    if to is None:
        return True
    if isinstance(to, str):
        return pid == to
    if isinstance(to, list):
        return pid in to
    return False


def _recipients(doc: dict) -> List[str]:
    to = doc.get("to")
    if isinstance(to, str):
        raw = [to] if to else []
    elif isinstance(to, list):
        raw = [x for x in to if isinstance(x, str) and x]
    else:
        return []
    out = []  # type: List[str]
    seen = set()
    for pid in raw:
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def pending_recipients(doc: dict, replies: List[dict]) -> List[str]:
    """Recipient pids in `to` (preserving order, deduplicated) that have not replied."""
    replied = {r.get("from") for r in replies if isinstance(r.get("from"), str)}
    return [pid for pid in _recipients(doc) if pid not in replied]


def _requests(docs: Dict, pid=None, open_only: bool = True) -> List[Tuple[str, dict, List[dict]]]:
    messages = _messages(docs)
    replies = _replies_by_request(messages)
    out = []
    for did, doc in messages.items():
        if doc.get("kind") != "request":
            continue
        doc_replies = replies.get(did, [])
        recipients = _recipients(doc)
        if pid is not None and not _addressed(doc, pid):
            continue
        if open_only:
            if pid is None:
                if len(recipients) >= 2:
                    if not pending_recipients(doc, doc_replies):
                        continue
                elif doc_replies:
                    continue
            else:
                if doc.get("from") == pid and pid not in recipients:
                    continue
                if len(recipients) == 1:
                    if doc_replies:
                        continue
                elif any(r.get("from") == pid for r in doc_replies):
                    continue
        out.append((did, doc, doc_replies))
    out.sort(key=lambda item: _t(item[1]), reverse=True)
    return out


def open_requests(docs: Dict) -> List[Tuple[str, dict, List[dict]]]:
    """Open requests over the replayed docs, newest first.

    Returns a list of (id, doc, [reply docs]) for every messages doc with
    kind == "request" that no messages doc's replyTo points at, newest first
    by the enriched t (the same rule the page uses).
    """
    return _requests(docs)


def inbox_for(docs: Dict, pid: str) -> List[Tuple[str, dict, List[dict]]]:
    """The open requests addressed to pid (to contains pid, or to absent), newest first."""
    return _requests(docs, pid=pid)


def all_requests_for(docs: Dict, pid: str) -> List[Tuple[str, dict, List[dict]]]:
    """Every request addressed to pid (to contains pid, or to absent), newest first, answered included."""
    return _requests(docs, pid=pid, open_only=False)
