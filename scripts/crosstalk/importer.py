"""One-time import of exported page snapshots into the logs.

Two modes:

- `import_snapshots` writes every snapshot row into an *empty* log: the
  foundation's one-time rebuild.
- `merge_snapshots` folds the old pages' history into a *live* log: rows the
  log already has are skipped with a warning, the rest are appended with the
  row's own time so the replay sorts them back into history.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .logs import append_line
from .participants import load_roster
from .replay import load_all_lines, replay

EPOCH = "1970-01-01T00:00:00Z"
PREFIX_WRITERS = (("m-", "bewareilive"), ("vc-", "vectorcraft"), ("own-", "owner"))


def _writer(doc_id: str, fields: dict, projects, people) -> str:
    for pid in people:
        if doc_id.startswith(pid + "-"):
            return pid
    for prefix, writer in PREFIX_WRITERS:
        if doc_id.startswith(prefix):
            return writer
    for key in ("project", "by", "builder"):
        value = fields.get(key)
        if isinstance(value, str) and value in projects:
            return value
    return "bewareilive"


def _time(fields: dict) -> str:
    for key in ("t", "since", "start"):
        value = fields.get(key)
        if isinstance(value, str) and value:
            return value
    return EPOCH


def _read_rows(snapshot_dirs: List[Path]) -> Dict[Tuple[str, str], Tuple[dict, Path]]:
    """Every row of every snapshot dir: (collection, id) -> (fields, source dir)."""
    rows = {}  # type: Dict[Tuple[str, str], Tuple[dict, Path]]
    for snap in snapshot_dirs:
        snap = Path(snap)
        for coll_dir in sorted(p for p in snap.iterdir()
                               if p.is_dir() and not p.is_symlink()):
            for f in sorted(coll_dir.glob("*.json")):
                if f.is_symlink():
                    # Never follow a symlink: a snapshot dir must not read
                    # arbitrary files outside it.
                    continue
                key = (coll_dir.name, f.stem)
                if key in rows:
                    raise RuntimeError("%s/%s appears in both %s and %s"
                                       % (key[0], key[1], rows[key][1], snap))
                fields = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(fields, dict):
                    raise RuntimeError("%s is not a JSON object" % f)
                rows[key] = (fields, snap)
    return rows


def import_snapshots(root: Path, snapshot_dirs: List[Path]) -> Dict[str, int]:
    """Write every snapshot row as a `set` line; returns lines written per participant."""
    root = Path(root)
    for path in sorted((root / "log").glob("*.jsonl")):
        if path.stat().st_size > 0:
            raise RuntimeError("log/ already has lines (%s); import runs once, on an empty log"
                               % path.name)
    roster = load_roster(root / "participants.json")
    projects = {pid for pid, p in roster.participants.items() if p.kind == "project"}

    rows = _read_rows(snapshot_dirs)

    plan = []
    for (coll, did), (fields, _src) in rows.items():
        plan.append((_time(fields), _writer(did, fields, projects, roster.participants),
                     coll, did, fields))
    plan.sort(key=lambda r: r[:4])

    counts = {}  # type: Dict[str, int]
    for t, writer, coll, did, fields in plan:
        # The old page's rows were written by one session for both sides, so a row's
        # own "from" need not match the log it lands in; via="import" marks that.
        append_line(root, writer, "set", coll, did, fields, t=t, via="import")
        counts[writer] = counts.get(writer, 0) + 1
    return counts


def merge_snapshots(root: Path, snapshot_dirs: List[Path]) -> Dict[str, Any]:
    """Fold snapshot rows into a live log.

    A row whose (collection, id) is already in the replay is skipped (the log's
    own row wins) and reported; every other row is appended with the row's own
    time and via="import", so the replay puts it back in history.
    """
    root = Path(root)
    roster = load_roster(root / "participants.json")
    projects = {pid for pid, p in roster.participants.items() if p.kind == "project"}

    rows = _read_rows(snapshot_dirs)
    have = set(replay(load_all_lines(root, roster)).keys())

    plan = []
    skipped = []  # type: List[str]
    for (coll, did), (fields, _src) in rows.items():
        if (coll, did) in have:
            skipped.append("%s/%s" % (coll, did))
            continue
        plan.append((_time(fields), _writer(did, fields, projects, roster.participants),
                     coll, did, fields))
    plan.sort(key=lambda r: r[:4])

    counts = {}  # type: Dict[str, int]
    for t, writer, coll, did, fields in plan:
        append_line(root, writer, "set", coll, did, fields, t=t, via="import")
        counts[writer] = counts.get(writer, 0) + 1
    skipped.sort()
    return {"counts": counts, "skipped": skipped}
