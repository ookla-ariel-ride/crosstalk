"""Log lines: read and check (append is added in a later task)."""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .participants import Roster

LEGACY_PREFIXES = ("m-", "vc-", "own-", "a-", "d-", "w-", "l-", "p-", "i-", "lesson-")
COLLECTIONS = ("messages", "agreements", "waiting", "locks", "lessons",
               "decisions", "progress", "incidents")
OWNER_COLLECTIONS = COLLECTIONS + ("participants",)
OPS = ("set", "update")


@dataclass
class Line:
    seq: int
    t: str
    from_: str
    op: str
    collection: str
    id: str
    data: dict = field(default_factory=dict)
    via: Optional[str] = None

    def to_json(self) -> str:
        d = {"seq": self.seq, "t": self.t, "from": self.from_, "op": self.op,
             "collection": self.collection, "id": self.id, "data": self.data}
        if self.via is not None:
            d["via"] = self.via
        return json.dumps(d, ensure_ascii=False, separators=(", ", ": "))


@dataclass
class Problem:
    file: str
    line: int
    message: str
    fatal: bool


def _rel(path: Path) -> str:
    return "log/" + Path(path).name


def read_log(path: Path) -> Tuple[List[Line], List[Problem]]:
    """Read one log file. Stops at the first fatal problem; returns the good lines before it."""
    path = Path(path)
    owner = path.stem
    name = _rel(path)
    lines = []  # type: List[Line]
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        return lines, [Problem(name, 1, "not valid UTF-8: %s" % e, True)]
    expected = 1
    for n, raw in enumerate(text.split("\n"), start=1):
        if raw == "" and n == len(text.split("\n")):
            break  # trailing newline
        msg = _validate(raw, owner, expected, lines)
        if isinstance(msg, str):
            return lines, [Problem(name, n, msg, True)]
        lines.append(msg)
        expected += 1
    return lines, []


def _validate(raw: str, owner: str, expected: int, _prior: List[Line]):
    try:
        d = json.loads(raw)
    except ValueError as e:
        return "malformed JSON: %s" % e
    if not isinstance(d, dict):
        return "line is not a JSON object"
    for key in ("seq", "t", "from", "op", "collection", "id", "data"):
        if key not in d:
            return "missing key %r" % key
    seq = d["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int):
        return "seq must be an integer"
    if seq != expected:
        return "seq %d, expected %d (gap or repeat)" % (seq, expected)
    for key in ("t", "from", "op", "collection", "id"):
        if not isinstance(d[key], str) or not d[key]:
            return "%s must be a non-empty string" % key
    if not isinstance(d["data"], dict):
        return "data must be an object"
    via = d.get("via")
    if via is not None and not isinstance(via, str):
        return "via must be a string"
    if d["from"] != owner:
        return "from %r does not match file participant %r" % (d["from"], owner)
    if d["op"] not in OPS:
        return "unknown op %r" % d["op"]
    allowed = OWNER_COLLECTIONS if owner == "owner" else COLLECTIONS
    if d["collection"] not in allowed:
        return "collection %r not allowed here" % d["collection"]
    if d["op"] == "set" and not d["id"].startswith(owner + "-") \
            and not d["id"].startswith(LEGACY_PREFIXES):
        return "set of id %r must start with %r" % (d["id"], owner + "-")
    return Line(seq=seq, t=d["t"], from_=d["from"], op=d["op"], collection=d["collection"],
                id=d["id"], data=d["data"], via=via)


def check_all(root: Path, roster: Roster) -> List[Problem]:
    """Check every log/*.jsonl under root."""
    problems = []  # type: List[Problem]
    for path in sorted((Path(root) / "log").glob("*.jsonl")):
        if path.stem not in roster.participants:
            problems.append(Problem(_rel(path), 0,
                                    "participant %r not in participants.json; "
                                    "lines are carried as quarantined" % path.stem, False))
        problems.extend(read_log(path)[1])
    return problems
