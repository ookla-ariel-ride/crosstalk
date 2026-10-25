"""Machine posters for file claims, land-sync, and incidents."""
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from .logs import Line, append_line
from .participants import Roster, load_roster, repo_problem
from .replay import load_all_lines, replay

SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
SLUG_MAX = 48
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
    ts = now_iso()
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
    docs = replay(load_all_lines(root, roster))
    if ("locks", doc_id) not in docs:
        raise ValueError("no such lock %r" % doc_id)
    ts = now_iso()
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
    if not isinstance(summary, str) or not summary.strip() or "\n" in summary.strip():
        raise ValueError("summary must be a non-empty single-line string")
    summary = summary.strip()

    short_sha = commit[:7].lower()
    agr_id = agreement_id or ("a-%s-%s" % (pid, short_sha))
    docs = replay(load_all_lines(root, roster))
    ts = now_iso()

    existing_agr = docs.get(("agreements", agr_id))
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

    agr_data = {
        "status": "ready-to-adopt",
        "builder": pid,
        "adopter": adopter or "all",
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

    msg_id = "%s-sync-%s" % (pid, short_sha)
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
    if files:
        seen = set()
        for f in files:
            slug_id = "l-%s-%s" % (pid, slugify_path(f))
            for (coll, did), doc in sorted(docs.items()):
                if coll != "locks" or did in seen:
                    continue
                if doc.get("released"):
                    continue
                if did == slug_id or (doc.get("project") == pid and doc.get("path") == f):
                    append_line(root, pid, "update", "locks", did, {"released": ts},
                                t=ts, roster=roster)
                    seen.add(did)
                    released_locks.append(did)

    return {
        "message_id": msg_id,
        "agreement_id": agr_id,
        "released_locks": released_locks,
    }
