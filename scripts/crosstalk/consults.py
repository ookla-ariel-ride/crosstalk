"""Ergonomic replies, cross-model peer consults, questions for the owner, and agreement adoption."""
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .logs import Line, append_line, check_speaker
from .marks import MARKS, item_ref, writer_of
from .participants import Roster, load_roster
from .posters import next_stamp
from .replay import enrich_messages, load_all_lines, replay
from .requests import ACK_SEP, _is_approved, extract_mentions, no_answer as _no_answer, owner_questions

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


def _require_pid(pid: str, roster: Roster) -> None:
    if not isinstance(pid, str) or pid not in roster.participants:
        raise ValueError("unknown participant %r" % (pid,))


def _validate_msg_id(msg_id: Optional[str], pid: str, docs: Dict) -> Optional[str]:
    if msg_id is None:
        return None
    if not isinstance(msg_id, str) or not msg_id.startswith(pid + "-"):
        raise ValueError("message id %r must start with %r" % (msg_id, pid + "-"))
    if ("messages", msg_id) in docs:
        raise ValueError("message %r already exists" % msg_id)
    return msg_id


def _clean_summary(summary: str) -> str:
    if not isinstance(summary, str) or "\n" in summary or "\r" in summary or not summary.strip():
        raise ValueError("summary must be a non-empty single-line string")
    return summary.strip()


def _str_list(items: Optional[Iterable[str]]) -> list:
    if items is None:
        return []
    if isinstance(items, str):
        return [items] if items else []
    return list(items)


def post_reply(
    root: Path,
    pid: str,
    to_id: str,
    summary: str,
    body: Optional[str] = None,
    refs: Optional[Iterable[str]] = None,
    msg_id: Optional[str] = None,
    roster: Optional[Roster] = None,
    speaker: Optional[Dict[str, str]] = None,
    no_answer: bool = False,
    to: Optional[Iterable[str]] = None,
    docs: Optional[Dict] = None,
) -> Line:
    """Reply to an existing message `to_id`, auto-populating `replyTo` and `to`.

    `no_answer` marks the reply as asking nothing (requests.no_answer). `to`
    replaces the default recipient (the sender of `to_id`). `docs` are replayed
    documents the caller just read; without them the log is read here.
    """
    root = Path(root)
    roster = _ensure_roster(root, roster)
    _require_pid(pid, roster)
    summary = _clean_summary(summary)
    if docs is None:
        lines = load_all_lines(root, roster)
        docs = enrich_messages(replay(lines), lines)
    target_doc = docs.get(("messages", to_id))
    if target_doc is None:
        raise ValueError("no such message %r" % to_id)
    mid = _validate_msg_id(msg_id, pid, docs) or ("%s-%s" % (pid, microsecond_utc_stamp()))
    target_from = target_doc.get("from")
    data = {
        "from": pid,
        "kind": "reply",
        "summary": summary,
        "replyTo": to_id,
    }  # type: Dict[str, object]
    if to is not None:
        data["to"] = _str_list(to)
    elif isinstance(target_from, str) and target_from in roster.participants:
        data["to"] = [target_from]
    if body is not None:
        data["body"] = body
    refs_list = _str_list(refs)
    if refs_list:
        data["refs"] = refs_list
    data.update(speaker or {})
    if no_answer:
        _no_answer(data)
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
    speaker: Optional[Dict[str, str]] = None,
    no_answer: bool = False,
) -> Line:
    """Post a `kind: request` peer consultation message.

    `no_answer` marks it as asking nothing (requests.no_answer): it then waits for nobody.
    """
    root = Path(root)
    roster = _ensure_roster(root, roster)
    _require_pid(pid, roster)
    summary = _clean_summary(summary)
    lines = load_all_lines(root, roster)
    docs = replay(lines)
    mid = _validate_msg_id(msg_id, pid, docs) or ("%s-%s" % (pid, microsecond_utc_stamp()))
    data = {
        "from": pid,
        "kind": "request",
        "summary": summary,
    }  # type: Dict[str, object]
    if body is not None:
        data["body"] = body
    refs_list = _str_list(refs)
    if refs_list:
        data["refs"] = refs_list
    to_list = _str_list(to)
    if to_list:
        # A request addressed to its own sender would wait for the sender to answer itself.
        to_list = [p for p in to_list if p != pid]
        if not to_list:
            raise ValueError("--to names only yourself; name who should answer")
        data["to"] = to_list
    else:
        mentioned = [
            m for m in extract_mentions({"summary": summary, "body": body or ""}, roster=roster)
            if m != pid and _is_approved(m, roster)
        ]
        if mentioned:
            data["to"] = mentioned
    data.update(speaker or {})
    if no_answer:
        _no_answer(data)
    return append_line(root, pid, "set", "messages", mid, data, roster=roster)


def post_question(
    root: Path,
    pid: str,
    summary: str,
    body: Optional[str] = None,
    refs: Optional[Iterable[str]] = None,
    msg_id: Optional[str] = None,
    roster: Optional[Roster] = None,
    speaker: Optional[Dict[str, str]] = None,
    options: Optional[List[str]] = None,
    recommend: Optional[int] = None,
) -> Line:
    """Post a `kind: question` for the owner: one question per row, summary = the question.

    The owner answers on the page's Questions tab; the answer (an owner row with
    `replyTo` this id) comes back in the asker's inbox as an owner message.
    `options` (trimmed) are choices the page can show as buttons; `recommend` is
    the index of one of them, from 0. The log validator checks both.
    """
    if pid == "owner":
        raise ValueError("the owner answers questions; only participants ask them")
    root = Path(root)
    roster = _ensure_roster(root, roster)
    _require_pid(pid, roster)
    summary = _clean_summary(summary)
    docs = replay(load_all_lines(root, roster))
    mid = _validate_msg_id(msg_id, pid, docs) or ("%s-%s" % (pid, microsecond_utc_stamp()))
    data = {
        "from": pid,
        "kind": "question",
        "summary": summary,
    }  # type: Dict[str, object]
    if "owner" in roster.participants:
        data["to"] = ["owner"]
    if body is not None:
        data["body"] = body
    refs_list = _str_list(refs)
    if refs_list:
        data["refs"] = refs_list
    if options is not None:
        data["options"] = ([o.strip() if isinstance(o, str) else o for o in options]
                           if isinstance(options, list) else options)
    if recommend is not None:
        data["recommend"] = recommend
    data.update(speaker or {})
    return append_line(root, pid, "set", "messages", mid, data, roster=roster)


def question_warning(root: Path, pid: str, roster: Optional[Roster] = None) -> Optional[str]:
    """What to tell an asker whose question the owner won't see, or None if the owner will."""
    roster = _ensure_roster(Path(root), roster)
    if _is_approved(pid, roster):
        return None
    return ("%s is quarantined, so this question is logged but the owner won't see it "
            "until the owner approves %s" % (pid, pid))


def withdraw_question(
    root: Path,
    pid: str,
    question_id: str,
    summary: Optional[str] = None,
    msg_id: Optional[str] = None,
    roster: Optional[Roster] = None,
    speaker: Optional[Dict[str, str]] = None,
) -> Line:
    """Withdraw one of pid's own open questions: a `kind: withdrawn` row with `replyTo` it."""
    root = Path(root)
    roster = _ensure_roster(root, roster)
    _require_pid(pid, roster)
    if summary is not None:
        summary = _clean_summary(summary)
    lines = load_all_lines(root, roster)
    docs = enrich_messages(replay(lines), lines)
    q = docs.get(("messages", question_id))
    if q is None or q.get("kind") != "question":
        raise ValueError("no such question %r" % (question_id,))
    if q.get("from") != pid:
        raise ValueError("only the question's author (%s) may withdraw it" % (q.get("from"),))
    for did, _doc, status, _closer in owner_questions(docs):
        if did == question_id and status != "open":
            raise ValueError("question %r is already %s" % (question_id, status))
    mid = _validate_msg_id(msg_id, pid, docs) or ("%s-%s" % (pid, microsecond_utc_stamp()))
    data = {
        "from": pid,
        "kind": "withdrawn",
        "summary": summary or ("Withdrawn: %s" % q.get("summary", question_id)),
        "replyTo": question_id,
    }  # type: Dict[str, object]
    data.update(speaker or {})
    return append_line(root, pid, "set", "messages", mid, data, roster=roster)


def ack_item(root: Path, pid: str, item_id: str, mark: str = "none",
             roster: Optional[Roster] = None) -> Line:
    """Put pid's mark on the item item_id: one `acks` row, id `<pid>--<item_id>`, that a
    later mark replaces. It is not a message.

    `none` says pid read a request and has nothing to do or say: it counts as pid's reply
    (requests.acks_by_item), so it takes only a request and never a question for the
    owner. Any other mark (marks.MARKS) takes a message, a decision or a lesson that pid
    did not write, and answers nothing."""
    if mark not in MARKS:
        raise ValueError("unknown mark %r (expected one of %s)" % (mark, ", ".join(MARKS)))
    root = Path(root)
    roster = _ensure_roster(root, roster)
    _require_pid(pid, roster)
    if ACK_SEP in pid:
        raise ValueError("participant %r holds '--', so it cannot ack; post a reply instead" % (pid,))
    lines = load_all_lines(root, roster)
    docs = enrich_messages(replay(lines), lines)
    data = {"from": pid, "item": item_id, "mark": mark, "t": now_iso()}
    if mark == "none":
        item = docs.get(("messages", item_id))
        if item is None:
            raise ValueError("no such message %r" % (item_id,))
        if item.get("kind") == "question":
            raise ValueError("a question to the owner gets an answer, not an ack")
        if item.get("kind") != "request":
            raise ValueError("message %r is not a request; only a request takes an ack" % (item_id,))
        data["none"] = True
    else:
        ref = item_ref(docs, item_id)
        if ref is None:
            raise ValueError("no such message, decision or lesson %r" % (item_id,))
        if writer_of(docs, ref[0], ref[1]) == pid:
            raise ValueError("%r is your own post; a mark is for another participant's post" % (item_id,))
    return append_line(root, pid, "set", "acks", pid + ACK_SEP + item_id, data, roster=roster)


def adopt_agreement(
    root: Path,
    pid: str,
    agreement_id: str,
    commit: str,
    summary: Optional[str] = None,
    roster: Optional[Roster] = None,
    speaker: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Record a project's adoption commit on an agreement and post a sync message."""
    check_speaker(speaker)
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
    }  # type: Dict[str, object]
    msg_data.update(speaker or {})
    msg_ln = append_line(root, pid, "set", "messages", msg_id, msg_data, t=stamp, roster=roster)
    return {
        "agreement_id": agreement_id,
        "status": new_status,
        "message_id": msg_ln.id,
    }
