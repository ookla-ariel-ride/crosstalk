"""Replay log lines into documents."""
import copy
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .logs import Line, read_log
from .participants import Roster

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


def load_all_lines(root: Path, roster: Optional[Roster] = None) -> List[Line]:
    """Good lines from every log/*.jsonl under root.

    With a roster, each file also stops at its first trust violation (see read_log).
    """
    out = []  # type: List[Line]
    for path in sorted((Path(root) / "log").glob("*.jsonl")):
        out.extend(read_log(path, roster)[0])
    return out


def last_line_map(lines: Iterable[Line]) -> Dict[DocKey, Line]:
    """The highest-order line that touched each (collection, id) pair."""
    last = {}  # type: Dict[DocKey, Line]
    for ln in lines:
        key = (ln.collection, ln.id)
        cur = last.get(key)
        if cur is None or order_key(ln) > order_key(cur):
            last[key] = ln
    return last


def enrich_messages(docs: Dict[DocKey, dict], lines: Iterable[Line]) -> Dict[DocKey, dict]:
    """A copy of `docs` where every message a line wrote shows its writer and time.

    The protocol tells participants to leave `data.t` out (the line's own `t` is the
    clock) and posters sometimes omit `from`; the page sorts by and shows both, so
    the replay is enriched with the writing line's `from` and `t` when the doc lacks
    them. The log itself is never touched.
    """
    docs = {k: copy.deepcopy(v) for k, v in docs.items()}
    last = last_line_map(lines)
    for (coll, did), doc in docs.items():
        if coll != "messages":
            continue
        ln = last.get((coll, did))
        if ln is None:
            continue
        if not isinstance(doc.get("from"), str) or not doc["from"].strip():
            doc["from"] = ln.from_
        if not isinstance(doc.get("t"), str):
            doc["t"] = ln.t
    return docs
