"""Machine posters for file claims, land-sync, and incidents."""
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .logs import Line, append_line
from .participants import Roster, load_roster, repo_problem
from .replay import last_line_map, load_all_lines, replay

SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
SLUG_MAX = 48
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _plus_one_second(ts: str) -> str:
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return now_iso()
    return (dt + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_stamp(lines: List[Line], pid: str, target_keys: List[Tuple[str, str]],
               base_ts: Optional[str] = None) -> str:
    stamp = base_ts or now_iso()
    for ln in lines:
        if ln.from_ == pid and ln.t > stamp:
            stamp = ln.t
    last_map = last_line_map(lines)
    for key in target_keys:
        last_ln = last_map.get(key)
        if last_ln is not None:
            while (stamp, pid) <= (last_ln.t, last_ln.from_):
                base = stamp if stamp >= last_ln.t else last_ln.t
                bumped = _plus_one_second(base)
                if bumped <= base:
                    break
                stamp = bumped
    return stamp


def _ensure_roster(root: Path, roster: Optional[Roster]) -> Roster:
    if roster is not None:
        return roster
    return load_roster(Path(root) / "participants.json")


def _require_pid(pid: str, roster: Roster) -> None:
    if not isinstance(pid, str) or pid not in roster.participants:
        raise ValueError("unknown participant %r" % (pid,))


def slugify_path(path: str) -> str:
    if not isinstance(path, str):
        raise ValueError("bad path %r" % (path,))
    slug = SLUG_NON_ALNUM_RE.sub("-", path.lower()).strip("-")[:SLUG_MAX].strip("-")
    if not slug:
        raise ValueError("empty path slug for %r" % (path,))
    return slug


def claim_file(root: Path, pid: str, path: str, repo: Optional[str] = None,
               holder: Optional[str] = None, note: Optional[str] = None,
               roster: Optional[Roster] = None) -> Line:
    root = Path(root)
    roster = _ensure_roster(root, roster)
    _require_pid(pid, roster)
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    doc_id = "l-%s-%s" % (pid, slugify_path(path))
    resolved_repo = repo if repo is not None else roster.repo_for(pid)
    if resolved_repo is not None:
        prob = repo_problem(pid, resolved_repo)
        if prob:
            raise ValueError(prob)
    lines = load_all_lines(root, roster)
    ts = next_stamp(lines, pid, [("locks", doc_id)], base_ts=now_iso())
    data = {
        "path": path,
        "project": pid,
        "since": ts,
        "released": None,
    }
    if resolved_repo:
        data["repo"] = resolved_repo
    if holder:
        data["holder"] = holder
    if note:
        data["note"] = note
    return append_line(root, pid, "set", "locks", doc_id, data, t=ts, roster=roster)


def release_file(root: Path, pid: str, path: Optional[str] = None,
                 lock_id: Optional[str] = None,
                 roster: Optional[Roster] = None) -> Line:
    root = Path(root)
    roster = _ensure_roster(root, roster)
    _require_pid(pid, roster)
    if not lock_id and not path:
        raise ValueError("release requires path or lock_id")
    doc_id = lock_id or ("l-%s-%s" % (pid, slugify_path(path)))
    lines = load_all_lines(root, roster)
    docs = replay(lines)
    lock_doc = docs.get(("locks", doc_id))
    if lock_doc is None:
        raise ValueError("no such lock %r" % doc_id)
    if lock_doc.get("project") != pid:
        raise ValueError("lock %r belongs to %r, not %r" % (doc_id, lock_doc.get("project"), pid))
    ts = next_stamp(lines, pid, [("locks", doc_id)], base_ts=now_iso())
    return append_line(root, pid, "update", "locks", doc_id, {"released": ts},
                       t=ts, roster=roster)


def land_sync(root: Path, pid: str, commit: str, summary: str,
              files: Optional[List[str]] = None, adopter: Optional[str] = None,
              agreement_id: Optional[str] = None, body: Optional[str] = None,
              roster: Optional[Roster] = None) -> dict:
    root = Path(root)
    roster = _ensure_roster(root, roster)
    _require_pid(pid, roster)
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit.strip()):
        raise ValueError("bad commit sha %r" % (commit,))
    commit = commit.strip()
    if not isinstance(summary, str) or "\n" in summary or "\r" in summary or not summary.strip():
        raise ValueError("summary must be a non-empty single-line string")
    summary = summary.strip()
    if adopter is not None:
        if isinstance(adopter, str):
            if adopter not in ("all", "both") and adopter not in roster.participants:
                raise ValueError("unknown adopter %r" % (adopter,))
        elif isinstance(adopter, list):
            if not adopter or not all(isinstance(x, str) and x in roster.participants for x in adopter):
                raise ValueError("invalid adopter %r" % (adopter,))
        else:
            raise ValueError("invalid adopter %r" % (adopter,))
    file_slugs = [(f, "l-%s-%s" % (pid, slugify_path(f))) for f in (files or [])]

    short_sha = commit[:7].lower()
    agr_id = agreement_id or ("a-%s-%s" % (pid, short_sha))
    msg_id = "%s-sync-%s" % (pid, short_sha)
    lines = load_all_lines(root, roster)
    docs = replay(lines)

    existing_agr = docs.get(("agreements", agr_id))
    if existing_agr is not None:
        if existing_agr.get("hold") is True or existing_agr.get("status") == "held":
            raise ValueError("agreement %r is on hold" % agr_id)

    locks_to_release = []  # type: List[str]
    if file_slugs:
        seen = set()
        for f, slug_id in file_slugs:
            for (coll, did), doc in sorted(docs.items()):
                if coll != "locks" or did in seen:
                    continue
                if doc.get("released"):
                    continue
                if did == slug_id or (doc.get("project") == pid and doc.get("path") == f):
                    seen.add(did)
                    locks_to_release.append(did)

    target_keys = [("agreements", agr_id), ("messages", msg_id)] + [
        ("locks", did) for did in locks_to_release
    ]
    ts = next_stamp(lines, pid, target_keys, base_ts=now_iso())

    commits = {}  # type: Dict[str, List[str]]
    if existing_agr and isinstance(existing_agr.get("commits"), dict):
        for k, v in existing_agr["commits"].items():
            if isinstance(v, list):
                commits[k] = list(v)
            elif isinstance(v, str) and v:
                commits[k] = [v]
    pid_commits = commits.setdefault(pid, [])
    if commit not in pid_commits:
        pid_commits.append(commit)

    resolved_adopter = adopter
    if resolved_adopter is None:
        if existing_agr and existing_agr.get("adopter"):
            resolved_adopter = existing_agr["adopter"]
        else:
            resolved_adopter = "all"
    resolved_builder = (
        existing_agr["builder"]
        if (existing_agr and isinstance(existing_agr.get("builder"), str) and existing_agr.get("builder"))
        else pid
    )

    agr_data = {
        "status": "ready-to-adopt",
        "builder": resolved_builder,
        "adopter": resolved_adopter,
        "summary": summary,
        "updated": ts,
        "hold": False,
        "commits": commits,
    }
    if existing_agr is None:
        agr_data["t"] = ts
        agr_op = "set"
    else:
        agr_op = "update"
    append_line(root, pid, agr_op, "agreements", agr_id, agr_data, t=ts, roster=roster)

    msg_data = {
        "from": pid,
        "kind": "sync",
        "summary": summary,
        "refs": ["%s@%s" % (pid, commit.lower())],
    }
    if body:
        msg_data["body"] = body
    append_line(root, pid, "set", "messages", msg_id, msg_data, t=ts, roster=roster)

    released_locks = []  # type: List[str]
    for did in locks_to_release:
        append_line(root, pid, "update", "locks", did, {"released": ts},
                    t=ts, roster=roster)
        released_locks.append(did)

    return {
        "message_id": msg_id,
        "agreement_id": agr_id,
        "released_locks": released_locks,
    }


def post_incident(root: Path, pid: str, slug: str, summary: Optional[str] = None,
                  body: Optional[str] = None, resolve: bool = False,
                  fix: Optional[str] = None, fix_refs: Optional[List[str]] = None,
                  roster: Optional[Roster] = None) -> Line:
    root = Path(root)
    roster = _ensure_roster(root, roster)
    _require_pid(pid, roster)
    doc_id = "i-%s-%s" % (pid, slugify_path(slug))
    lines = load_all_lines(root, roster)
    ts = next_stamp(lines, pid, [("incidents", doc_id)], base_ts=now_iso())
    if resolve:
        docs = replay(lines)
        if ("incidents", doc_id) not in docs:
            raise ValueError("no such incident %r" % doc_id)
        data = {"end": ts}  # type: Dict[str, Any]
        if fix:
            data["fix"] = fix
        if fix_refs:
            data["fixRefs"] = list(fix_refs)
        return append_line(root, pid, "update", "incidents", doc_id, data, t=ts, roster=roster)

    if not isinstance(summary, str) or "\n" in summary or "\r" in summary or not summary.strip():
        raise ValueError("incident requires a non-empty single-line summary")
    data = {
        "project": pid,
        "start": ts,
        "end": None,
        "summary": summary.strip(),
    }
    if body:
        data["body"] = body
    return append_line(root, pid, "set", "incidents", doc_id, data, t=ts, roster=roster)
