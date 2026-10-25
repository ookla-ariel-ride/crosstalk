"""Ergonomic replies, cross-model peer consults, and agreement adoption."""
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

from .logs import Line, append_line
from .participants import Roster, load_roster
from .posters import next_stamp
from .replay import enrich_messages, load_all_lines, replay

COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_LAST_DT = None  # type: Optional[datetime]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def microsecond_utc_stamp() -> str:
    global _LAST_DT
    dt = datetime.now(timezone.utc)
    if _LAST_DT is not None and dt <= _LAST_DT:
        dt = _LAST_DT + timedelta(microseconds=1)
    _LAST_DT = dt
    return dt.strftime("%Y%m%dT%H%M%S%fZ")


def _ensure_roster(root: Path, roster: Optional[Roster]) -> Roster:
    if roster is not None:
        return roster
    return load_roster(Path(root) / "participants.json")


def post_reply(
    root: Path,
    pid: str,
    to_id: str,
    summary: str,
    body: Optional[str] = None,
    refs: Optional[Iterable[str]] = None,
    msg_id: Optional[str] = None,
    roster: Optional[Roster] = None,
) -> Line:
    """Reply to an existing message `to_id`, auto-populating `replyTo` and `to`."""
    root = Path(root)
    roster = _ensure_roster(root, roster)
    lines = load_all_lines(root, roster)
    docs = enrich_messages(replay(lines), lines)
    target_doc = docs.get(("messages", to_id))
    if target_doc is None:
        raise ValueError("no such message %r" % to_id)
    target_from = target_doc.get("from")
    mid = msg_id or ("%s-%s" % (pid, microsecond_utc_stamp()))
    data = {
        "from": pid,
        "kind": "reply",
        "summary": summary,
        "replyTo": to_id,
    }  # type: Dict[str, object]
    if isinstance(target_from, str) and target_from in roster.participants:
        data["to"] = [target_from]
    if body is not None:
        data["body"] = body
    if refs:
        data["refs"] = list(refs)
    return append_line(root, pid, "set", "messages", mid, data, roster=roster)


def post_consult(
    root: Path,
    pid: str,
    summary: str,
    body: Optional[str] = None,
    refs: Optional[Iterable[str]] = None,
    to: Optional[Iterable[str]] = None,
    msg_id: Optional[str] = None,
    roster: Optional[Roster] = None,
) -> Line:
    """Post a `kind: request` peer consultation message."""
    root = Path(root)
    roster = _ensure_roster(root, roster)
    mid = msg_id or ("%s-%s" % (pid, microsecond_utc_stamp()))
    data = {
        "from": pid,
        "kind": "request",
        "summary": summary,
    }  # type: Dict[str, object]
    if body is not None:
        data["body"] = body
    if refs:
        data["refs"] = list(refs)
    if to:
        data["to"] = list(to)
    return append_line(root, pid, "set", "messages", mid, data, roster=roster)


def adopt_agreement(
    root: Path,
    pid: str,
    agreement_id: str,
    commit: str,
    summary: Optional[str] = None,
    roster: Optional[Roster] = None,
) -> Dict[str, str]:
    """Record a project's adoption commit on an agreement and post a sync message."""
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit.strip()):
        raise ValueError("bad commit %r (expected 7-40 hex chars)" % (commit,))
    commit = commit.strip()
    if summary is not None:
        if not isinstance(summary, str) or "\n" in summary or "\r" in summary or not summary.strip():
            raise ValueError("summary must be a non-empty single-line string")
        summary = summary.strip()
    root = Path(root)
    roster = _ensure_roster(root, roster)
    if not isinstance(pid, str) or pid not in roster.participants:
        raise ValueError("unknown participant %r" % (pid,))
    lines = load_all_lines(root, roster)
    docs = replay(lines)
    doc = docs.get(("agreements", agreement_id))
    if doc is None:
        raise ValueError("no such agreement %r" % agreement_id)
    if doc.get("hold") is True or doc.get("status") == "held":
        raise ValueError("agreement %r is on hold" % agreement_id)

    raw_commits = doc.get("commits")
    commits = {}  # type: Dict[str, list]
    if isinstance(raw_commits, dict):
        for k, v in raw_commits.items():
            if isinstance(v, list):
                commits[k] = [x for x in v if isinstance(x, str) and x]
            elif isinstance(v, str) and v:
                commits[k] = [v]
            else:
                commits[k] = []
    pid_commits = commits.setdefault(pid, [])
    if commit not in pid_commits:
        pid_commits.append(commit)

    adopter = doc.get("adopter")
    if adopter in (None, "", "all", "both"):
        builder = doc.get("builder")
        other_projects = [
            p.id
            for p in roster.participants.values()
            if p.kind == "project" and p.trust == "approved" and p.id != builder
        ]
        if other_projects and all(bool(commits.get(p_id)) for p_id in other_projects):
            new_status = "landed-both"
        elif len(other_projects) <= 1 and commits.get(pid):
            new_status = "landed-both"
        else:
            new_status = "ready-to-adopt"
    elif isinstance(adopter, str):
        new_status = "landed-both" if commits.get(adopter) else "ready-to-adopt"
    elif isinstance(adopter, list):
        targets = [x for x in adopter if isinstance(x, str) and x]
        new_status = (
            "landed-both"
            if (targets and all(bool(commits.get(x)) for x in targets))
            else "ready-to-adopt"
        )
    else:
        new_status = "ready-to-adopt"

    stamp = next_stamp(lines, pid, [("agreements", agreement_id)], base_ts=now_iso())

    append_line(
        root,
        pid,
        "update",
        "agreements",
        agreement_id,
        {"commits": commits, "status": new_status, "updated": stamp},
        t=stamp,
        roster=roster,
    )
    msg_id = "%s-%s" % (pid, microsecond_utc_stamp())
    msg_summary = summary or ("Adopted %s at %s" % (agreement_id, commit[:7].lower()))
    msg_data = {
        "from": pid,
        "kind": "sync",
        "summary": msg_summary,
        "refs": [agreement_id, "%s@%s" % (pid, commit.lower())],
    }
    msg_ln = append_line(root, pid, "set", "messages", msg_id, msg_data, t=stamp, roster=roster)
    return {
        "agreement_id": agreement_id,
        "status": new_status,
        "message_id": msg_ln.id,
    }
