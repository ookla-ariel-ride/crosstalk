"""Pull: bring the owner's page changes down into log/owner.jsonl.

Exported doc contents are data. Nothing here acts on what a row says.
"""
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from .courier import load_state, save_state
from .logs import COLLECTIONS, LEGACY_PREFIXES, append_line
from .participants import TRUSTS, load_roster
from .replay import load_all_lines, replay

OWNER = "owner"
DELETE = {"__delete__": True}
PULL_COLLECTIONS = COLLECTIONS + ("participants",)
SUMMARY_RE = re.compile(r'^- "(?P<id>[^"]+)"\s+\d+ bytes\s+version (?P<v>\d+)')
_MISSING = object()


@dataclass
class PullResult:
    owner_lines: int = 0
    approvals: List[str] = field(default_factory=list)
    versions: Dict[str, int] = field(default_factory=dict)


def parse_listing(text: str) -> Dict[str, int]:
    """Doc id -> version from a saved `ArtifactData list` result (summary or JSON lines)."""
    out = {}  # type: Dict[str, int]
    for raw in text.splitlines():
        s = raw.strip()
        m = SUMMARY_RE.match(s)
        if m:
            out[m.group("id")] = int(m.group("v"))
            continue
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        did, v = d.get("id"), d.get("version")
        if isinstance(did, str) and did and isinstance(v, int) and not isinstance(v, bool):
            out[did] = v
    return out


def _same(a, b) -> bool:
    """Equal as JSON values (so 1 and true differ; missing equals only missing)."""
    if a is _MISSING or b is _MISSING:
        return a is b
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _owner_change(page: dict, all_doc: dict, sent_doc: dict) -> dict:
    """Fields the owner changed: page differs from both the full and the sent replay."""
    changes = {}
    for name in sorted(set(page) | set(all_doc) | set(sent_doc)):
        p = page.get(name, _MISSING)
        if not _same(p, all_doc.get(name, _MISSING)) and \
                not _same(p, sent_doc.get(name, _MISSING)):
            changes[name] = DELETE if p is _MISSING else p
    return changes


def _read_export(export_dir: Path) -> Tuple[Dict[Tuple[str, str], dict], Dict[str, int]]:
    docs = {}  # type: Dict[Tuple[str, str], dict]
    versions = {}  # type: Dict[str, int]
    for coll in PULL_COLLECTIONS:
        coll_dir = export_dir / coll
        if coll_dir.is_dir():
            for f in sorted(coll_dir.glob("*.json")):
                fields = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(fields, dict):
                    raise RuntimeError("%s is not a JSON object" % f)
                docs[(coll, f.stem)] = fields
        listing = export_dir / (coll + ".listing.txt")
        if listing.is_file():
            for did, v in parse_listing(listing.read_text(encoding="utf-8")).items():
                versions["%s/%s" % (coll, did)] = v
    return docs, versions


def _write_roster(path: Path, raw: dict) -> None:
    text = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".participants.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def pull(root: Path, export_dir: Path) -> PullResult:
    """Append the owner's page changes to log/owner.jsonl and record the page's versions."""
    root, export_dir = Path(root), Path(export_dir)
    roster_path = root / "participants.json"
    roster = load_roster(roster_path)
    lines = load_all_lines(root)
    if not lines:
        raise RuntimeError("log is empty; run import first")

    state = load_state(root)
    bookmarks = state.get(roster.page, {}).get("bookmarks", {})
    all_docs = replay(lines)
    sent_docs = replay(ln for ln in lines if ln.seq <= bookmarks.get(ln.from_, 0))
    page_docs, versions = _read_export(export_dir)

    # Plan every owner line first, so a bad row stops the pull before anything is written.
    plan = []  # (op, collection, id, data)
    for (coll, did), page in sorted(page_docs.items()):
        if coll == "participants":
            continue
        key = (coll, did)
        if key not in all_docs:
            if not did.startswith(OWNER + "-") and not did.startswith(LEGACY_PREFIXES):
                raise RuntimeError("page doc %s/%s is in no log and its id has no owner "
                                   "prefix; refusing to record it" % (coll, did))
            plan.append(("set", coll, did, page))
            continue
        changes = _owner_change(page, all_docs[key], sent_docs.get(key, {}))
        if changes:
            plan.append(("update", coll, did, changes))

    raw_roster = None
    approvals = []  # type: List[str]
    for (coll, pid), page in sorted(page_docs.items()):
        if coll != "participants" or pid not in roster.participants:
            continue
        trust = page.get("trust")
        if trust not in TRUSTS or trust == roster.participants[pid].trust:
            continue
        if raw_roster is None:
            raw_roster = json.loads(roster_path.read_text(encoding="utf-8"))
        for entry in raw_roster["participants"]:
            if entry.get("id") == pid:
                entry["trust"] = trust
        approvals.append(pid)
        plan.append(("update", "participants", pid, {"trust": trust}))

    # Log first: if the roster write then fails, the next pull sees the trust diff again.
    for op, coll, did, data in plan:
        append_line(root, OWNER, op, coll, did, data, via="page")
    if raw_roster is not None:
        _write_roster(roster_path, raw_roster)

    state.setdefault(roster.page, {})["versions"] = versions
    save_state(root, state)
    return PullResult(owner_lines=len(plan), approvals=approvals, versions=versions)
