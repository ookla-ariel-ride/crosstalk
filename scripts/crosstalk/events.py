"""The rows added to the log since a mark: what the push channel (/api/events) sends.

A mark holds, for each log file `log/<pid>.jsonl`, the number of complete
lines (ending in a newline) it held. since() reads only what came after, and
returns each new line as a row of ids: {"from", "seq", "collection", "id"}.
A row never carries the line's data, so the stream sends no message body; the
page reads the rows it needs through the other routes.

- A last line with no newline yet is not complete: the next call reads it.
- A file with fewer complete lines than its mark (it shrank, or was written
  again) starts again from line 0.
- A file that went away drops out of the mark; when it comes back it starts from 0.
- A line that is not a JSON object counts as a line but gives no row.
"""
import json
from pathlib import Path
from typing import Dict, List, Tuple


def _files(root) -> Dict[str, Path]:
    log_dir = Path(root) / "log"
    if not log_dir.is_dir():
        return {}
    return {p.stem: p for p in sorted(log_dir.glob("*.jsonl"))}


def _complete_lines(path: Path) -> List[bytes]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    return raw.split(b"\n")[:-1]  # the part after the last newline is not complete


def stamp(root) -> tuple:
    """Each log file's name, size and mtime: when this is the same, since() finds nothing new."""
    out = []
    for name, path in _files(root).items():
        try:
            st = path.stat()
        except OSError:
            continue
        out.append((name, st.st_size, st.st_mtime_ns))
    return tuple(out)


def mark(root) -> Dict[str, int]:
    """The number of complete lines in each log file, by participant id."""
    return {name: len(_complete_lines(path)) for name, path in _files(root).items()}


def _row(name: str, raw: bytes):
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    return {"from": d.get("from", name), "seq": d.get("seq"),
            "collection": d.get("collection"), "id": d.get("id")}


def since(root, old: Dict[str, int]) -> Tuple[List[dict], Dict[str, int]]:
    """(rows added after the mark `old`, the new mark); see the module docstring."""
    rows = []  # type: List[dict]
    new = {}  # type: Dict[str, int]
    for name, path in _files(root).items():
        lines = _complete_lines(path)
        start = old.get(name, 0)
        if start > len(lines):
            start = 0
        for raw in lines[start:]:
            row = _row(name, raw)
            if row is not None:
                rows.append(row)
        new[name] = len(lines)
    return rows, new
