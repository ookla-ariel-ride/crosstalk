"""Log lines: read, check and append."""
import fcntl
import json
import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .participants import ID_RE, Roster

LEGACY_PREFIXES = ("m-", "vc-", "own-", "a-", "d-", "w-", "l-", "p-", "i-", "lesson-")
COLLECTIONS = ("messages", "agreements", "waiting", "locks", "lessons",
               "decisions", "progress", "incidents")
OWNER_COLLECTIONS = COLLECTIONS + ("participants",)
OPS = ("set", "update")
KINDS = ("check-in", "checkin", "sync", "reply", "request", "review", "agreement", "alert",
         "disagreement", "lesson", "owner")
SKEW_MIN = 2


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


def read_log(path: Path, roster: Optional[Roster] = None) -> Tuple[List[Line], List[Problem]]:
    """Read one log file. Stops at the first fatal problem; returns the good lines before it.

    With a roster, the trust rules (ruling R9) apply too; without one they are skipped.
    """
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
        msg = _validate(raw, owner, expected, lines, roster)
        if isinstance(msg, str):
            return lines, [Problem(name, n, msg, True)]
        lines.append(msg)
        expected += 1
    return lines, []


def _validate(raw: str, owner: str, expected: int, _prior: List[Line],
              roster: Optional[Roster] = None):
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
    if d["collection"] == "messages" and d["data"].get("kind") == "owner" \
            and owner != "owner" and d.get("via") != "import":
        return "only 'owner' may post kind 'owner'"
    if roster is not None:
        msg = _trust_problem(d, owner, roster)
        if msg:
            return msg
    return Line(seq=seq, t=d["t"], from_=d["from"], op=d["op"], collection=d["collection"],
                id=d["id"], data=d["data"], via=via)


def _owns_id(doc_id: str, owner: str) -> bool:
    return doc_id.startswith(owner + "-") or any(
        doc_id.startswith(p + owner + "-") for p in ("l-", "a-", "i-", "p-")
    )


def _trust_problem(d: dict, owner: str, roster: Roster) -> Optional[str]:
    """Ruling R9. Only an approved participant may write ids outside its own prefix,
    and a message's data.from must be its writer (imported lines excepted)."""
    p = roster.participants.get(owner)
    if (p is None or p.trust != "approved") and not _owns_id(d["id"], owner):
        return ("%s participant %r may only write ids starting with %r, not %r"
                % ("unknown" if p is None else p.trust, owner, owner + "-", d["id"]))
    if d["collection"] == "messages" and "from" in d["data"] \
            and d["data"]["from"] != owner and d.get("via") != "import":
        return "message data.from %r is not its writer %r" % (d["data"]["from"], owner)
    if d["collection"] == "messages" and d["data"].get("kind") == "owner" \
            and owner != "owner" and d.get("via") != "import":
        return "only 'owner' may post kind 'owner'"
    return None


def message_problems(collection: str, op: str, data: dict, roster: Roster,
                     now: Optional[float] = None) -> Tuple[List[str], List[str]]:
    """Shape rules for a message an appender writes now: (errors, warnings).

    A new message needs a kind and a one-line summary, and every `to` must be a
    participant. An unknown kind, or a `t` more than SKEW_MIN minutes from the
    clock, is only a warning.
    """
    errors, warnings = [], []  # type: List[str], List[str]
    if collection != "messages":
        return errors, warnings
    if op == "set":
        for key in ("kind", "summary"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                errors.append("a new message needs a %s" % key)
        if not isinstance(data.get("from"), str) or not data["from"].strip():
            errors.append("a new message needs a from (your participant id)")
    if isinstance(data.get("summary"), str) and ("\n" in data["summary"] or "\r" in data["summary"]):
        errors.append("summary must be one line (put the rest in body)")
    kind = data.get("kind")
    if isinstance(kind, str) and kind and kind not in KINDS:
        warnings.append("unknown kind %r (known: %s)" % (kind, ", ".join(KINDS)))
    if kind == "owner" and data.get("from") not in (None, "owner"):
        errors.append("only 'owner' may post kind 'owner'")
    if "to" in data:
        to = data["to"]
        if not isinstance(to, list) or not all(isinstance(x, str) for x in to):
            errors.append("to must be a list of participant ids")
        else:
            for x in to:
                if x not in roster.participants:
                    errors.append("to %r is not a participant" % x)
    t = data.get("t")
    if isinstance(t, str):
        try:
            when = datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            errors.append("t %r is not UTC ISO 8601 (yyyy-mm-ddThh:mm:ssZ)" % t)
        else:
            off = (when.timestamp() - (time.time() if now is None else now)) / 60
            if off > SKEW_MIN:
                # A future t would pin the message at the top of the page and
                # spoof check-in freshness, so it is refused, not just warned.
                errors.append("t %s is %d min ahead of the clock; leave t out to use the clock"
                              % (t, round(off)))
            elif off < -SKEW_MIN:
                warnings.append("t %s is %d min behind the clock; leave t out to use the clock"
                                % (t, round(-off)))
    return errors, warnings


def check_all(root: Path, roster: Roster) -> List[Problem]:
    """Check every log/*.jsonl under root."""
    problems = []  # type: List[Problem]
    for path in sorted((Path(root) / "log").glob("*.jsonl")):
        if path.stem not in roster.participants:
            problems.append(Problem(_rel(path), 0,
                                    "participant %r not in participants.json; "
                                    "lines are carried as quarantined" % path.stem, False))
        problems.extend(read_log(path, roster)[1])
    return problems


def append_line(root: Path, from_: str, op: str, collection: str, id: str, data: dict,
                t: Optional[str] = None, via: Optional[str] = None,
                roster: Optional[Roster] = None) -> Line:
    """Append one line to log/<from_>.jsonl under an exclusive lock; returns the Line written.

    With a roster, a line that breaks the trust rules or the message shape rules is
    refused before the file is opened.
    """
    if not isinstance(from_, str) or not ID_RE.fullmatch(from_):
        # Checked before any path is built: on a case-insensitive disk "Codex" would
        # open codex.jsonl.
        raise ValueError("bad participant id %r" % (from_,))
    if not isinstance(id, str) or not id:
        # Checked before the trust rules run: _trust_problem calls id.startswith,
        # which would raise AttributeError (and kill the request thread) on a
        # null/number/array/object id.
        raise ValueError("id must be a non-empty string")
    if op not in OPS:
        raise ValueError("unknown op %r" % (op,))
    if roster is not None:
        msg = _trust_problem({"collection": collection, "id": id, "data": data, "via": via},
                             from_, roster)
        if msg:
            raise ValueError(msg)
        errors = message_problems(collection, op, data, roster)[0]
        if errors:
            raise ValueError("; ".join(errors))
    if t is None:
        t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log_dir = Path(root) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / (from_ + ".jsonl")
    with open(str(path), "a+b") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            last = 0
            for raw in f.read().decode("utf-8").split("\n"):
                if raw.strip():
                    last = json.loads(raw)["seq"]
            d = {"seq": last + 1, "t": t, "from": from_, "op": op,
                 "collection": collection, "id": id, "data": data}
            if via is not None:
                d["via"] = via
            text = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
            result = _validate(text, from_, last + 1, [])
            if isinstance(result, str):
                raise ValueError(result)
            f.seek(0, 2)
            f.write((text + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
            return result
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)