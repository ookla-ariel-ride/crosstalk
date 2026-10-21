"""Courier: state (log/relay-state.json), pending batches, sent and restore.

Row contents are data. Nothing here acts on what a row says.
"""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .participants import load_roster
from .replay import load_all_lines, order_key, replay

STATE_FILE = "relay-state.json"


def _path(root: Path) -> Path:
    return Path(root) / "log" / STATE_FILE


def load_state(root: Path) -> dict:
    """The whole state, keyed by page URL. A missing file means {}."""
    path = _path(root)
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("%s: expected a JSON object" % path)
    return state


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to path atomically: temp file in the same dir, fsync, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
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


def save_state(root: Path, state: dict) -> None:
    """Write the state atomically."""
    atomic_write_text(_path(root), json.dumps(state, indent=2, sort_keys=True) + "\n")


BATCH_SIZE = 50
PARTICIPANTS = "participants"
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _doc_file_name(batch_no: int, coll: str, did: str) -> str:
    safe = _SAFE_RE.sub("_", did)
    if safe != did or safe.startswith("."):
        safe = "%s-%s" % (safe, hashlib.sha256(did.encode("utf-8")).hexdigest()[:12])
    return "%03d-%s-%s.json" % (batch_no, coll, safe)


def _clear_out(out: Path) -> None:
    """Remove batch and doc files left by an earlier run, so the dir holds only this one."""
    for p in out.glob("batch-*.json"):
        p.unlink()
    docs = out / "docs"
    if docs.is_dir():
        for p in docs.glob("*.json"):
            p.unlink()


def _build(root: Path, out: Path, bookmarks: Dict[str, int], versions: Dict[str, int],
           known_hash: Optional[str]) -> List[Path]:
    root, out = Path(root), Path(out)
    roster_path = root / "participants.json"
    roster = load_roster(roster_path)
    roster_bytes = roster_path.read_bytes()
    roster_hash = hashlib.sha256(roster_bytes).hexdigest()

    lines = load_all_lines(root)
    unsent = [ln for ln in lines if ln.seq > bookmarks.get(ln.from_, 0)]
    all_docs = replay(lines)

    # Each doc touched by an unsent (non-participants) line, ordered by its last unsent line.
    last = {}  # type: Dict[Tuple[str, str], tuple]
    for ln in unsent:
        if ln.collection == PARTICIPANTS:
            continue
        key = (ln.collection, ln.id)
        k = order_key(ln)
        if key not in last or k > last[key]:
            last[key] = k
    doc_keys = sorted(last, key=lambda key: last[key])

    items = []  # (collection, id, fields)
    mirror = known_hash != roster_hash
    if mirror:
        raw = json.loads(roster_bytes.decode("utf-8"))
        for entry in raw["participants"]:
            items.append((PARTICIPANTS, entry["id"], entry))
    # The batch that finishes the mirror (the first, unless the roster exceeds 50 entries).
    mirror_batch = max(0, (len(items) - 1) // BATCH_SIZE)
    for key in doc_keys:
        items.append((key[0], key[1], all_docs[key]))

    _clear_out(out)
    if not items:
        return []

    chunks = [items[i:i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    batch_of = {}  # type: Dict[Tuple[str, str], int]
    for k, chunk in enumerate(chunks):
        for coll, did, _fields in chunk:
            if coll != PARTICIPANTS:
                batch_of[(coll, did)] = k

    # Per participant, unsent lines in seq order with the batch that covers each.
    per_pid = {}  # type: Dict[str, List[Tuple[int, int]]]
    for ln in unsent:
        b = mirror_batch if ln.collection == PARTICIPANTS else batch_of[(ln.collection, ln.id)]
        per_pid.setdefault(ln.from_, []).append((ln.seq, b))
    for seqs in per_pid.values():
        seqs.sort()
    advanced = dict((pid, bookmarks.get(pid, 0)) for pid in per_pid)

    docs_dir = out / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    paths = []  # type: List[Path]
    for k, chunk in enumerate(chunks):
        n = k + 1
        writes = []
        for coll, did, fields in chunk:
            doc_path = (docs_dir / _doc_file_name(n, coll, did)).resolve()
            atomic_write_text(doc_path, json.dumps(fields, ensure_ascii=False, indent=2) + "\n")
            w = {"op": "set", "collection": coll, "doc_id": did, "file_path": str(doc_path)}
            v = versions.get("%s/%s" % (coll, did))
            if v is not None:
                w["if_version"] = v
            writes.append(w)
        advance = {}
        for pid in sorted(per_pid):
            top = None
            for seq, b in per_pid[pid]:
                if b > k:
                    break
                top = seq
            if top is not None and top > advanced[pid]:
                advance[pid] = top
                advanced[pid] = top
        batch = {"page": roster.page, "advance": advance, "writes": writes}
        if mirror and k == mirror_batch:
            batch["participantsHash"] = roster_hash
        path = out / ("batch-%03d.json" % n)
        atomic_write_text(path, json.dumps(batch, indent=2, ensure_ascii=False) + "\n")
        paths.append(path)
    return paths


def pending(root: Path, out: Path) -> List[Path]:
    """Write batch files for every line past the current page's bookmarks; returns them in order."""
    roster = load_roster(Path(root) / "participants.json")
    page_state = load_state(root).get(roster.page, {})
    return _build(root, out, page_state.get("bookmarks", {}), page_state.get("versions", {}),
                  page_state.get("participantsHash"))


def restore(root: Path, out: Path) -> List[Path]:
    """pending for a fresh page: every line, the participants mirror, no if_version.

    Does not touch the saved state; bookmarks move only when `sent` runs.
    """
    return _build(root, out, {}, {}, None)


def sent(root: Path, batch: Path) -> None:
    """Record that a batch went through: move bookmarks, and the participants hash if carried."""
    data = json.loads(Path(batch).read_text(encoding="utf-8"))
    roster = load_roster(Path(root) / "participants.json")
    page = data.get("page")
    if page != roster.page:
        raise RuntimeError("batch is for page %r, but participants.json page is %r"
                           % (page, roster.page))
    state = load_state(root)
    page_state = state.setdefault(page, {})
    bookmarks = page_state.setdefault("bookmarks", {})
    for pid, seq in data.get("advance", {}).items():
        bookmarks[pid] = max(bookmarks.get(pid, 0), seq)
    if "participantsHash" in data:
        page_state["participantsHash"] = data["participantsHash"]
    save_state(root, state)
