"""Open help requests derived from the replayed messages.

A help request is a `messages` doc with `kind: "request"`. It is open while no
messages doc's `replyTo` points at its id. `to` lists the asked participants;
omitted means everyone (a broadcast). Open-ness is derived, never stored, so
it cannot drift or race.
"""
from datetime import datetime, timezone
import re
import time
from typing import Dict, List, Optional, Tuple

_MENTION_RE = re.compile(r"(?:^|[\s(\[,:;])@([A-Za-z0-9_.-]+)")
_BROADCAST_HANDLES = ("everyone", "all", "channel", "here")
_ALIASES = {"bil": "bewareilive", "vc": "vectorcraft", "amb": "avoidmissingball"}
_DEFAULT_PROJECTS = ("avoidmissingball", "bewareilive", "vectorcraft")
_DEFAULT_REVIEWERS = ("codex",)
_OWNER_MESSAGE_KINDS = ("owner", "alert", "agreement", "lesson", "decision")


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


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def extract_mentions(doc: dict, roster=None) -> List[str]:
    """Resolve @mentions in summary/body to participant ids (empty list if broadcast or none)."""
    s = doc.get("summary")
    b = doc.get("body")
    text = "%s\n%s" % (s if isinstance(s, str) else "", b if isinstance(b, str) else "")
    raw_tokens = []  # type: List[str]
    for m in _MENTION_RE.finditer(text):
        tok = m.group(1).rstrip(".,;:!?)]")
        if tok:
            raw_tokens.append(tok)
    if not raw_tokens:
        return []
    if any(_norm(t) in _BROADCAST_HANDLES or t.lower() in _BROADCAST_HANDLES for t in raw_tokens):
        return []
    participants = getattr(roster, "participants", roster) if roster is not None else None
    by_norm = dict(_ALIASES)
    projects = []  # type: List[str]
    reviewers = []  # type: List[str]
    if isinstance(participants, dict) and participants:
        valid_pids = set(participants.keys()) - {"owner"}
        by_norm = {k: v for k, v in _ALIASES.items() if v in valid_pids}
        all_projects = []  # type: List[str]
        approved_projects = []  # type: List[str]
        all_reviewers = []  # type: List[str]
        approved_reviewers = []  # type: List[str]
        for pid, p in participants.items():
            if pid == "owner":
                continue
            by_norm[pid.lower()] = pid
            by_norm[_norm(pid)] = pid
            name = p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
            if isinstance(name, str) and name:
                by_norm[name.lower()] = pid
                by_norm[_norm(name)] = pid
            kind = p.get("kind") if isinstance(p, dict) else getattr(p, "kind", None)
            if kind == "project":
                all_projects.append(pid)
                if _is_approved(pid, roster):
                    approved_projects.append(pid)
            elif kind in ("reviewer", "researcher"):
                all_reviewers.append(pid)
                if _is_approved(pid, roster):
                    approved_reviewers.append(pid)
        projects = approved_projects if approved_projects else all_projects
        reviewers = approved_reviewers if approved_reviewers else all_reviewers
    else:
        for pid in _DEFAULT_PROJECTS + _DEFAULT_REVIEWERS:
            by_norm[pid.lower()] = pid
            by_norm[_norm(pid)] = pid
        projects = list(_DEFAULT_PROJECTS)
        reviewers = list(_DEFAULT_REVIEWERS)
    out = []  # type: List[str]
    seen = set()
    for raw_t in raw_tokens:
        low = raw_t.lower()
        t = _norm(raw_t)
        matched = []  # type: List[str]
        if t in ("projects", "teams"):
            matched = projects
        elif t == "reviewers":
            matched = reviewers
        elif low in by_norm:
            matched = [by_norm[low]]
        elif t in by_norm:
            matched = [by_norm[t]]
        for pid in matched:
            if pid != "owner" and pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def _addressed(doc: dict, pid: str, roster=None) -> bool:
    to = doc.get("to")
    if to is None:
        mentioned = extract_mentions(doc, roster=roster)
        return not mentioned or pid in mentioned
    if isinstance(to, str):
        return pid == to
    if isinstance(to, list):
        return pid in to
    return False


def _recipients(doc: dict, roster=None) -> List[str]:
    to = doc.get("to")
    if to is None:
        mentioned = extract_mentions(doc, roster=roster)
        if mentioned:
            return mentioned
        if doc.get("kind") == "request" and doc.get("from") == "owner" and roster is not None:
            participants = getattr(roster, "participants", roster)
            if isinstance(participants, dict):
                approved_projects = sorted(
                    pid for pid in participants if _is_approved_project(pid, roster)
                )
                if approved_projects:
                    return approved_projects
        return []
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


def _is_approved(pid, roster) -> bool:
    if roster is None:
        return True
    if not isinstance(pid, str) or not pid:
        return False
    participants = getattr(roster, "participants", roster)
    if isinstance(participants, dict):
        p = participants.get(pid)
        if p is None:
            return False
        trust = p.get("trust") if isinstance(p, dict) else getattr(p, "trust", None)
        return trust == "approved"
    return False


def _is_approved_project(pid: str, roster) -> bool:
    if roster is None:
        return True
    participants = getattr(roster, "participants", roster)
    if isinstance(participants, dict):
        p = participants.get(pid)
        if p is None:
            return False
        kind = p.get("kind") if isinstance(p, dict) else getattr(p, "kind", None)
        trust = p.get("trust") if isinstance(p, dict) else getattr(p, "trust", None)
        return kind == "project" and trust == "approved"
    return False


def pending_recipients(doc: dict, replies: List[dict], roster=None) -> List[str]:
    """Recipient pids in `to` (preserving order, deduplicated) that have not replied."""
    replied = {
        r.get("from")
        for r in replies
        if isinstance(r.get("from"), str) and _is_approved(r.get("from"), roster)
    }
    return [
        pid for pid in _recipients(doc, roster=roster)
        if _is_approved(pid, roster) and pid not in replied
    ]


def _requests(docs: Dict, pid=None, open_only: bool = True,
              roster=None) -> List[Tuple[str, dict, List[dict]]]:
    messages = _messages(docs)
    replies = _replies_by_request(messages)
    out = []
    for did, doc in messages.items():
        if doc.get("kind") != "request":
            continue
        doc_replies = replies.get(did, [])
        recipients = _recipients(doc, roster=roster)
        if pid is not None and not _addressed(doc, pid, roster=roster):
            continue
        if open_only:
            trusted_replies = [
                r for r in doc_replies if _is_approved(r.get("from"), roster)
            ]
            if pid is None:
                if len(recipients) >= 2:
                    if not pending_recipients(doc, doc_replies, roster=roster):
                        continue
                elif len(recipients) == 1:
                    if any(r.get("from") in (recipients[0], "owner") for r in trusted_replies):
                        continue
                elif trusted_replies:
                    continue
            else:
                if doc.get("from") == pid and pid not in recipients:
                    continue
                if len(recipients) == 1:
                    if any(
                        r.get("from") == pid
                        or (_is_approved(r.get("from"), roster) and r.get("from") in (recipients[0], "owner"))
                        for r in doc_replies
                    ):
                        continue
                elif any(
                    r.get("from") == pid and _is_approved(r.get("from"), roster)
                    for r in doc_replies
                ):
                    continue
        out.append((did, doc, doc_replies))
    out.sort(key=lambda item: _t(item[1]), reverse=True)
    return out


def open_requests(docs: Dict, roster=None) -> List[Tuple[str, dict, List[dict]]]:
    """Open requests over the replayed docs, newest first.

    Returns a list of (id, doc, [reply docs]) for every messages doc with
    kind == "request" that no messages doc's replyTo points at, newest first
    by the enriched t (the same rule the page uses).
    """
    return _requests(docs, roster=roster)


def inbox_for(docs: Dict, pid: str, roster=None) -> List[Tuple[str, dict, List[dict]]]:
    """The open requests addressed to pid (to contains pid, or to absent), newest first."""
    return _requests(docs, pid=pid, roster=roster)


def all_requests_for(docs: Dict, pid: str, roster=None) -> List[Tuple[str, dict, List[dict]]]:
    """Every request addressed to pid (to contains pid, or to absent), newest first, answered included."""
    return _requests(docs, pid=pid, open_only=False, roster=roster)


def unanswered_owner_messages(docs: Dict, pid: str, roster=None) -> List[Tuple[str, dict, List[dict]]]:
    """Owner messages addressed to pid that pid has not replied to yet, newest first."""
    messages = _messages(docs)
    replies = _replies_by_request(messages)
    out = []  # type: List[Tuple[str, dict, List[dict]]]
    for did, doc in messages.items():
        if doc.get("from") != "owner" or doc.get("replyTo"):
            continue
        if doc.get("kind") not in _OWNER_MESSAGE_KINDS:
            continue
        if not _addressed(doc, pid, roster=roster):
            continue
        if pid == "owner" and "owner" not in _recipients(doc, roster=roster):
            continue
        doc_replies = replies.get(did, [])
        if any(r.get("from") == pid for r in doc_replies):
            continue
        out.append((did, doc, doc_replies))
    out.sort(key=lambda item: _t(item[1]), reverse=True)
    return out


def _agreement_time(doc: dict) -> str:
    u = doc.get("updated")
    if isinstance(u, str) and u:
        return u
    t = doc.get("t")
    return t if isinstance(t, str) else ""


def agreements_to_adopt_for(docs: Dict, pid: str, roster=None) -> List[dict]:
    """Unheld agreements ready for pid to adopt with no commits from pid yet, newest first."""
    out = []  # type: List[dict]
    for (coll, did), doc in docs.items():
        if coll != "agreements" or not isinstance(doc, dict):
            continue
        if doc.get("hold") or doc.get("status") == "held":
            continue
        if doc.get("status") not in ("landed-builder", "ready-to-adopt"):
            continue
        adopter = doc.get("adopter")
        is_adopter = (
            adopter == pid
            or (isinstance(adopter, list) and pid in adopter)
            or (
                adopter in (None, "", "all", "both")
                and doc.get("builder") != pid
                and _is_approved_project(pid, roster)
            )
        )
        if not is_adopter:
            continue
        commits = doc.get("commits")
        if isinstance(commits, dict) and commits.get(pid):
            continue
        out.append(dict(doc, id=did))
    out.sort(key=_agreement_time, reverse=True)
    return out


def _parse_iso(ts) -> Optional[float]:
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(ts.strip(), "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(timezone.utc).timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def stale_locks_for(docs: Dict, pid: str, now_iso: Optional[str] = None,
                    stale_hours: float = 4.0) -> List[dict]:
    """Unreleased locks for project == pid older than stale_hours, oldest since first."""
    now_ts = _parse_iso(now_iso) if now_iso is not None else time.time()
    if now_ts is None:
        now_ts = time.time()
    threshold = float(stale_hours) * 3600.0
    items = []  # type: List[Tuple[float, str, str, dict]]
    for (coll, did), doc in docs.items():
        if coll != "locks" or not isinstance(doc, dict):
            continue
        if doc.get("released"):
            continue
        if doc.get("project") != pid:
            continue
        since_ts = _parse_iso(doc.get("since"))
        if since_ts is None:
            continue
        if (now_ts - since_ts) > threshold:
            items.append((since_ts, str(doc.get("since") or ""), did, dict(doc, id=did)))
    items.sort(key=lambda x: (x[0], x[1], x[2]))
    return [x[3] for x in items]
