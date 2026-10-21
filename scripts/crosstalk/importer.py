"""One-time import of exported page snapshots into the logs."""
import json
from pathlib import Path
from typing import Dict, List

from .logs import append_line
from .participants import load_roster

EPOCH = "1970-01-01T00:00:00Z"
PREFIX_WRITERS = (("m-", "bewareilive"), ("vc-", "vectorcraft"), ("own-", "owner"))


def _writer(doc_id: str, fields: dict, projects) -> str:
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


def import_snapshots(root: Path, snapshot_dirs: List[Path]) -> Dict[str, int]:
    """Write every snapshot row as a `set` line; returns lines written per participant."""
    root = Path(root)
    for path in sorted((root / "log").glob("*.jsonl")):
        if path.stat().st_size > 0:
            raise RuntimeError("log/ already has lines (%s); import runs once, on an empty log"
                               % path.name)
    roster = load_roster(root / "participants.json")
    projects = {pid for pid, p in roster.participants.items() if p.kind == "project"}

    rows = {}  # (collection, id) -> (fields, source)
    for snap in snapshot_dirs:
        snap = Path(snap)
        for coll_dir in sorted(p for p in snap.iterdir() if p.is_dir()):
            for f in sorted(coll_dir.glob("*.json")):
                key = (coll_dir.name, f.stem)
                if key in rows:
                    raise RuntimeError("%s/%s appears in both %s and %s"
                                       % (key[0], key[1], rows[key][1], snap))
                fields = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(fields, dict):
                    raise RuntimeError("%s is not a JSON object" % f)
                rows[key] = (fields, snap)

    plan = []
    for (coll, did), (fields, _src) in rows.items():
        plan.append((_time(fields), _writer(did, fields, projects), coll, did, fields))
    plan.sort(key=lambda r: r[:4])

    counts = {}  # type: Dict[str, int]
    for t, writer, coll, did, fields in plan:
        append_line(root, writer, "set", coll, did, fields, t=t)
        counts[writer] = counts.get(writer, 0) + 1
    return counts
