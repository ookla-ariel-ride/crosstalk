"""Replay log lines into documents."""
import copy
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .logs import Line, read_log

DocKey = Tuple[str, str]


def order_key(line: Line) -> tuple:
    return (line.t, line.from_, line.seq)


def _is_delete(value) -> bool:
    return isinstance(value, dict) and value == {"__delete__": True} \
        and value["__delete__"] is True


def replay(lines: Iterable[Line]) -> Dict[DocKey, dict]:
    """Apply lines in (t, from, seq) order. Never mutates the input lines."""
    docs = {}  # type: Dict[DocKey, dict]
    for ln in sorted(lines, key=order_key):
        key = (ln.collection, ln.id)
        data = copy.deepcopy(ln.data)
        if ln.op == "set":
            docs[key] = data
        else:
            doc = docs.setdefault(key, {})
            for name, value in data.items():
                if _is_delete(value):
                    doc.pop(name, None)
                else:
                    doc[name] = value
    return docs


def load_all_lines(root: Path) -> List[Line]:
    """Good lines from every log/*.jsonl under root."""
    out = []  # type: List[Line]
    for path in sorted((Path(root) / "log").glob("*.jsonl")):
        out.extend(read_log(path)[0])
    return out
