"""Machine posters for file claims, land-sync, and incidents."""
import re
import time
from pathlib import Path
from typing import Optional

from .logs import Line, append_line
from .participants import Roster, load_roster, repo_problem
from .replay import load_all_lines, replay

SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
SLUG_MAX = 48


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
