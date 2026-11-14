"""Log lines: read, check and append."""
import fcntl
import hashlib
import json
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .marks import MARKS
from .participants import HARNESS_MAX, ID_RE, Roster, harness_problem

LEGACY_PREFIXES = ("m-", "vc-", "own-", "a-", "d-", "w-", "l-", "p-", "i-", "lesson-")
COLLECTIONS = ("messages", "agreements", "waiting", "locks", "lessons",
               "decisions", "progress", "incidents", "schedules", "adoptions", "acks", "state")
RUN_RESULTS = ("ok", "failed", "skipped")
RUNS_KEPT = 10
OWNER_COLLECTIONS = COLLECTIONS + ("participants",)
OPS = ("set", "update")
KINDS = ("check-in", "checkin", "sync", "reply", "request", "review", "agreement", "alert",
         "disagreement", "lesson", "owner", "question", "status", "withdrawn")
# Who wrote a message, what it is, and what it answers: the fields the request and
# question rules read. Only its writer (or the owner) may change them.
MESSAGE_IDENTITY = ("from", "kind", "replyTo")
# Who spoke: a message may name the harness (the roster's harness rule) and the model that wrote it.
SPEAKER_FIELDS = ("harness", "model")
MODEL_MAX = 64
MODEL_BAD_RE = re.compile(r"[\x00-\x1f\x7f]")
# A question may carry choices for the owner and the index (from 0) of the recommended one.
OPTIONS_MIN, OPTIONS_MAX, OPTION_LEN_MAX = 2, 6, 200
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
    # The first 12 hex digits of the SHA-256 of the line's exact text in its
    # file, when it was read from or written to one (inboxstate.fingerprint).
    fp: Optional[str] = field(default=None, compare=False, repr=False)

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

    With a roster, the trust rules (_trust_problem) apply too; without one they are skipped.
    """
    path = Path(path)
    owner = path.stem
    name = _rel(path)
    lines = []  # type: List[Line]
    data = path.read_bytes()
    torn = None  # type: Optional[Problem]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        # A writer that died mid-line can cut a character in two: keep the
        # complete lines before that part line, as for any other bad last line.
        start = data.rfind(b"\n") + 1
        try:
            if data.endswith(b"\n") or e.start < start:
                raise e
            text = data[:start].decode("utf-8")
        except UnicodeDecodeError:
            return lines, [Problem(name, 1, "not valid UTF-8: %s" % e, True)]
        torn = Problem(name, text.count("\n") + 1,
                       "an unterminated last line that is not valid UTF-8", True)
    expected = 1
    for n, raw in enumerate(text.split("\n"), start=1):
        if raw == "" and n == len(text.split("\n")):
            break  # trailing newline
        msg = _validate(raw, owner, expected, lines, roster)
        if isinstance(msg, str):
            return lines, [Problem(name, n, msg, True)]
        lines.append(msg)
        expected += 1
    return lines, [torn] if torn else []


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
    if d["collection"] == "acks":
        msg = ack_problem(d["op"], d["id"], owner, d["data"])
        if msg:
            return msg
    if d["collection"] == "state":
        msg = state_problem(d["op"], d["id"], owner, d["t"], d["data"])
        if msg:
            return msg
    if d["collection"] == "messages":
        for name in SPEAKER_FIELDS:
            if name in d["data"]:
                msg = speaker_problem(name, d["data"][name])
                if msg:
                    return msg
        msg = options_problem(d["data"])
        if msg:
            return msg
    if roster is not None:
        msg = _trust_problem(d, owner, roster)
        if msg:
            return msg
    return Line(seq=seq, t=d["t"], from_=d["from"], op=d["op"], collection=d["collection"],
                id=d["id"], data=d["data"], via=via,
                fp=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12])


def ack_problem(op: str, doc_id: str, owner: str, data: dict) -> Optional[str]:
    """The reason an `acks` line is invalid, or None. Any approved writer may `update` any id,
    so the id rule holds for every op: a reader trusts that `<writer>--` names the writer.
    A writer whose id holds `--` cannot ack: its ids could equal another writer's."""
    if "--" in owner:
        return "participant %r holds '--', so it cannot ack; post a reply instead" % owner
    if not doc_id.startswith(owner + "--"):
        return "an ack id must start with %r" % (owner + "--")
    if "from" in data and data["from"] != owner:
        return "ack from %r is not its writer %r" % (data["from"], owner)
    if op == "set":
        item = data.get("item")
        if "from" not in data or not isinstance(item, str) or not item:
            return "an ack set needs from and a string item"
        if doc_id != owner + "--" + item:
            return "ack id %r must be %r" % (doc_id, owner + "--" + item)
        if "mark" in data and not (isinstance(data["mark"], str) and data["mark"] in MARKS):
            return "ack mark %r is not one of %s" % (data["mark"], ", ".join(MARKS))
    return None


def state_problem(op: str, doc_id: str, owner: str, t: str, data: dict) -> Optional[str]:
    """The reason a `state` line is invalid, or None. For every op the id is exactly
    `<writer>-wind-down` (state.py), so no writer can set or change another's record,
    and a prefix such as `relay` of `relay-bot` cannot reach `relay-bot-wind-down`.
    The record in force is chosen by its `t`, so a `set` carries the line's own time,
    and an `update` cannot change `step`, `started` or `t`: a new step is a new `set`."""
    if doc_id != owner + "-wind-down":
        return "state id %r must be %r: only its writer may set or change it" % (doc_id, owner + "-wind-down")
    if "from" in data and data["from"] != owner:
        return "state from %r is not its writer %r" % (data["from"], owner)
    if op == "set" and data.get("t") != t:
        return "state t %r must be the line t %r" % (data.get("t"), t)
    if op == "update" and any(k in data for k in ("step", "started", "t")):
        return "an update cannot change step, started or t; run a new state wind-down"
    return None


def speaker_problem(name: str, value) -> Optional[str]:
    """The reason a message's `harness` or `model` value is invalid, or None when it is acceptable."""
    if name == "harness":
        if value is None or harness_problem("", value):
            return ("message harness %r is not a lowercase id like claude-code of at most %d chars"
                    % (value, HARNESS_MAX))
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= MODEL_MAX or MODEL_BAD_RE.search(value):
        return "message model %r must be 1 to %d chars with no control character" % (value, MODEL_MAX)
    return None


def options_problem(data: dict) -> Optional[str]:
    """The reason a message's `options` or `recommend` is invalid, or None when it is acceptable.

    Only a question carries them: 2 to 6 distinct one-line options of 1 to 200 chars
    (after trimming), and `recommend` an index into them from 0.
    """
    if "options" not in data and "recommend" not in data:
        return None
    if data.get("kind") != "question":
        return "options and recommend belong only on a message of kind 'question'"
    if "options" not in data:
        return "recommend needs options"
    options = data["options"]
    if not isinstance(options, list) or not OPTIONS_MIN <= len(options) <= OPTIONS_MAX:
        return "options must be a list of %d to %d strings" % (OPTIONS_MIN, OPTIONS_MAX)
    seen = set()
    for o in options:
        if not isinstance(o, str) or not 1 <= len(o.strip()) <= OPTION_LEN_MAX \
                or "\n" in o or "\r" in o:
            return "each of the options must be 1 to %d chars on one line: %r" % (OPTION_LEN_MAX, o)
        if o.strip() in seen:
            return "options must differ: %r is there twice" % o.strip()
        seen.add(o.strip())
    if "recommend" in data:
        rec = data["recommend"]
        if isinstance(rec, bool) or not isinstance(rec, int) or not 0 <= rec < len(options):
            return "recommend %r must be an index into options, from 0 to %d" % (rec, len(options) - 1)
    return None


def check_speaker(speaker: Optional[Dict[str, str]]) -> None:
    """Raise ValueError when a post's speaker fields are invalid: for posters that write other rows first."""
    for name, value in (speaker or {}).items():
        msg = speaker_problem(name, value)
        if msg:
            raise ValueError(msg)


def _owns_id(doc_id: str, owner: str) -> bool:
    return doc_id.startswith(owner + "-") or any(
        doc_id.startswith(p + owner + "-") for p in ("l-", "a-", "i-", "p-")
    )


def _trust_problem(d: dict, owner: str, roster: Roster) -> Optional[str]:
    """The trust rules. Only an approved participant may write ids outside its own prefix,
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


def _identity_problem(root: Path, from_: str, op: str, collection: str, doc_id: str,
                      data: dict, via: Optional[str], roster: Roster) -> Optional[str]:
    """A new line that changes MESSAGE_IDENTITY on another participant's message.

    The writer is whoever made the `set` the replay shows, or the first line if no
    `set` exists. The owner keeps its powers and imported lines are exempt. This
    judges new lines only: read_log leaves it out, so `check` accepts lines already
    in the log and a stray one can't cut off the rest of its file.
    """
    from .replay import _is_delete, load_all_lines, order_key, replay  # replay imports this module

    if collection != "messages" or from_ == "owner" or via == "import":
        return None
    if op == "update" and not any(name in data for name in MESSAGE_IDENTITY):
        return None
    history = sorted((ln for ln in load_all_lines(root, roster)
                      if ln.collection == "messages" and ln.id == doc_id), key=order_key)
    if not history:
        return None
    sets = [ln for ln in history if ln.op == "set"]
    writer = sets[-1].from_ if sets else history[0].from_
    if writer == from_:
        return None
    doc = replay(history)[("messages", doc_id)]
    doc.setdefault("from", writer)
    changed = []  # type: List[str]
    for name in MESSAGE_IDENTITY:
        if op == "update" and name not in data:
            continue
        new = None if _is_delete(data.get(name)) else data.get(name)
        if new != doc.get(name):
            changed.append(name)
    if changed:
        return ("message %r was written by %r; only its writer or the owner may change %s"
                % (doc_id, writer, ", ".join(changed)))
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
    elif op == "update":
        for key in ("kind", "summary"):
            if key in data and (not isinstance(data.get(key), str) or not data[key].strip()):
                errors.append("message %s must be a non-empty string" % key)
    if isinstance(data.get("summary"), str) and ("\n" in data["summary"] or "\r" in data["summary"]):
        errors.append("summary must be one line (put the rest in body)")
    kind = data.get("kind")
    if isinstance(kind, str) and kind and kind not in KINDS:
        warnings.append("unknown kind %r (known: %s)" % (kind, ", ".join(KINDS)))
    if kind == "owner" and data.get("from") not in (None, "owner"):
        errors.append("only 'owner' may post kind 'owner'")
    msg = options_problem(data)
    if msg:
        errors.append(msg)
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


def parse_utc(text) -> Optional[datetime]:
    """A UTC yyyy-mm-ddThh:mm:ssZ time, or None if it isn't one."""
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def utc_text(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _one_line(value, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit \
        and "\n" not in value and "\r" not in value


def adoption_problems(collection: str, doc_id: str, from_: str, data: dict) -> List[str]:
    """Only a project's own record: the id carries its writer, and the state is a known word."""
    if collection != "adoptions":
        return []
    errors = []  # type: List[str]
    if from_ != "owner" and not doc_id.startswith(from_ + "-adopt-"):
        errors.append("an adoption id must start with %r" % (from_ + "-adopt-"))
    if "state" in data and data["state"] not in ("adopted", "planned", "not-applicable", "set-aside"):
        errors.append("adoption state must be adopted, planned, not-applicable or set-aside")
    return errors


def schedule_problems(collection: str, op: str, doc_id: str, from_: str, data: dict,
                      now: Optional[float] = None) -> List[str]:
    """Shape rules for a `schedules` row an appender writes now.

    Like message_problems, these judge new lines only, so a stray row already in
    a log can't cut off the rest of its file.
    """
    if collection != "schedules":
        return []
    errors = []  # type: List[str]
    if from_ != "owner" and not doc_id.startswith(from_ + "-sched-"):
        errors.append("a schedule id must start with %r" % (from_ + "-sched-"))
    required = op == "set"
    for key, limit in (("name", 80), ("cron", 120)):
        if (required or key in data) and not _one_line(data.get(key), limit):
            errors.append("schedule %s must be one line of at most %d characters" % (key, limit))
    if (required or "from" in data) and data.get("from") != from_:
        errors.append("schedule from must be its writer %r" % from_)
    if "next" in data and data["next"] is not None and parse_utc(data["next"]) is None:
        errors.append("schedule next must be null or UTC ISO 8601 (yyyy-mm-ddThh:mm:ssZ)")
    for key in ("reported", "removed"):
        if key in data and parse_utc(data[key]) is None:
            errors.append("schedule %s must be UTC ISO 8601 (yyyy-mm-ddThh:mm:ssZ)" % key)
    reported = parse_utc(data.get("reported"))
    if reported and (reported.timestamp() - (time.time() if now is None else now)) / 60 > SKEW_MIN:
        errors.append("schedule reported is ahead of the clock")  # a future time would hide staleness
    for key in ("periodMin", "everyMin"):
        v = data.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
            errors.append("schedule %s must be a positive whole number or null" % key)
    runs = data.get("runs")
    if "runs" in data or required:
        if not isinstance(runs, list) or len(runs) > RUNS_KEPT:
            errors.append("schedule runs must be a list of at most %d" % RUNS_KEPT)
        else:
            for r in runs:
                if not isinstance(r, dict) or parse_utc(r.get("at")) is None \
                        or r.get("result") not in RUN_RESULTS \
                        or ("note" in r and not _one_line(r["note"], 200)):
                    errors.append("schedule run needs a UTC at, a result (%s), and a one-line note"
                                  % "|".join(RUN_RESULTS))
                    break
    return errors


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


STATE_LOCK = ".state.lock"
_state_lock_depth = threading.local()


def state_lock_path(root: Path) -> Path:
    """The lock file that orders `state` rows against the granter's GRANTED (in the git-ignored log/)."""
    return Path(root) / "log" / STATE_LOCK


STATE_LOCK_WAIT = 5.0  # seconds a caller waits for the state lock
STATE_LOCK_TRY = 0.05
# The longest a `hold` holder (the granter) may keep the state lock and still
# append. It is under STATE_LOCK_WAIT, so a holder that a forced writer gave up
# on appends nothing after that writer's row.
STATE_HOLD_LIMIT = 3.0


class StateLockBusy(OSError):
    """The state lock stayed held for STATE_LOCK_WAIT seconds."""


class StateHoldExpired(StateLockBusy):
    """A `hold` holder kept the state lock past STATE_HOLD_LIMIT seconds; its append is refused."""


def _check_hold() -> None:
    """Refuse an append of a `hold` holder past STATE_HOLD_LIMIT (called inside the file lock)."""
    since = getattr(_state_lock_depth, "hold_since", None)
    if since is not None and time.monotonic() - since > STATE_HOLD_LIMIT:
        raise StateHoldExpired("held the state lock for more than %g s" % STATE_HOLD_LIMIT)


@contextmanager
def state_lock(root: Path, force: bool = False, hold: bool = False):
    """Hold the exclusive state lock of `root`; again in the same thread, it is a no-op.

    A row of the `state` collection takes it around its append, and the granter
    takes it from its last read of the log to its GRANTED append. A process
    that dies drops its flock, so a crashed holder blocks nobody. A live holder
    that is stuck blocks a caller for at most STATE_LOCK_WAIT seconds. Then a
    `force` caller (a state row: the owner's command must win) goes on without
    the lock and says so on stderr; any other caller gets StateLockBusy.

    With `hold`, each append_line of this thread under the lock checks, inside
    its file lock and just before it writes, that the lock was got at most
    STATE_HOLD_LIMIT seconds ago; later it raises StateHoldExpired and writes
    nothing. A forced writer waits longer than that, so it gives up on such a
    holder only after the holder can no longer append.
    """
    depth = getattr(_state_lock_depth, "n", 0)
    if depth:
        _state_lock_depth.n = depth + 1
        try:
            yield
        finally:
            _state_lock_depth.n -= 1
        return
    path = state_lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "a") as f:
        deadline = time.monotonic() + STATE_LOCK_WAIT
        held = False
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(STATE_LOCK_TRY)
        if not held and not force:
            raise StateLockBusy("the state lock %s stayed busy for %g s" % (path, STATE_LOCK_WAIT))
        if not held:
            print("crosstalk: %s stayed busy for %g s; writing the state row without the state lock."
                  " A GRANTED in flight can still appear; the server then withdraws it"
                  " (GRANT WITHDRAWN)." % (path, STATE_LOCK_WAIT), file=sys.stderr)
        _state_lock_depth.n = 1
        _state_lock_depth.hold_since = time.monotonic() if (held and hold) else None
        try:
            yield
        finally:
            _state_lock_depth.n = 0
            _state_lock_depth.hold_since = None
            if held:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def intent_path(root: Path, pid: str) -> Path:
    """The append intent of log/<pid>.jsonl, shared by every writer of that log."""
    return Path(root) / "log" / (pid + ".inflight.json")


def _sync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _load_intent(root: Path, pid: str) -> Optional[Tuple[int, bytes]]:
    """(offset, intended bytes), or None when no readable intent exists."""
    try:
        d = json.loads(intent_path(root, pid).read_text(encoding="utf-8"))
        offset, intended = int(d["offset"]), bytes.fromhex(d["intended_hex"])
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        # OverflowError: an offset of Infinity, which json.loads accepts.
        return None
    if offset < 0 or not intended:
        return None
    return offset, intended


def _write_intent(root: Path, pid: str, offset: int, record: bytes) -> None:
    """Make the intent durable before the line is written (the caller holds the log's flock).

    The temp name is fixed per log: the flock lets one writer at a time use it,
    and a temp left by a crash is overwritten by the next append, not piled up.
    """
    path = intent_path(root, pid)
    tmp = "%s.tmp" % path
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"offset": offset, "intended_hex": record.hex()}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, str(path))
    _sync_dir(path.parent)


def _clear_intent(root: Path, pid: str) -> None:
    """Remove the intent; call it only after the log's fsync.

    No fsync of log/ follows: an unlink lost to a power cut leaves a stale
    intent, and the next append clears it without touching a complete line.
    """
    try:
        os.unlink(str(intent_path(root, pid)))
    except FileNotFoundError:
        pass


def _recover(root: Path, pid: str, path: Path, f, data: bytes) -> bytes:
    """Settle a writer that died mid-append; return the log's bytes after it.

    The caller holds the log's flock. A last line with no trailing newline is
    cut back only when the intent names its exact start and its bytes are a
    prefix of the intended line: that is the dead writer's own write and
    nobody else's. Any other unterminated tail raises ValueError and nothing
    is cut. With a clean end, the intent goes: after an fsync of the log when
    its line is already there (published), at once when it is not (the write
    never started, or the intent is stale). A complete line is never cut.
    """
    intent = _load_intent(root, pid)
    if data and not data.endswith(b"\n"):
        start = data.rfind(b"\n") + 1
        if intent is None or intent[0] != start or not intent[1].startswith(data[start:]):
            raise ValueError("%s has an unterminated record at byte %d with no matching append"
                             " intent in %s; repair the log by hand"
                             % (path, start, intent_path(root, pid).name))
        f.truncate(start)
        f.flush()
        os.fsync(f.fileno())
        _clear_intent(root, pid)
        return data[:start]
    if intent is not None:
        offset, intended = intent
        if data[offset:offset + len(intended)] == intended:
            f.flush()
            os.fsync(f.fileno())
        _clear_intent(root, pid)
    return data


def append_line(root: Path, from_: str, op: str, collection: str, id: str, data: dict,
                t: Optional[str] = None, via: Optional[str] = None,
                roster: Optional[Roster] = None) -> Line:
    """Append one line to log/<from_>.jsonl under an exclusive lock; returns the Line written.

    With a roster, a line that breaks the trust rules or the message shape rules is
    refused before the file is opened. A `state` row also holds the state lock
    (state_lock), so it never lands between the granter's last read and its GRANTED.
    """
    if collection == "state":
        with state_lock(root, force=True):
            return _append_line(root, from_, op, collection, id, data, t, via, roster)
    return _append_line(root, from_, op, collection, id, data, t, via, roster)


def _append_line(root: Path, from_: str, op: str, collection: str, id: str, data: dict,
                 t: Optional[str], via: Optional[str], roster: Optional[Roster]) -> Line:
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
        errors = message_problems(collection, op, data, roster)[0] \
            + schedule_problems(collection, op, id, from_, data) \
            + adoption_problems(collection, id, from_, data)
        if errors:
            raise ValueError("; ".join(errors))
        msg = _identity_problem(root, from_, op, collection, id, data, via, roster)
        if msg:
            raise ValueError(msg)
    if t is None:
        t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log_dir = Path(root) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / (from_ + ".jsonl")
    with open(str(path), "a+b") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            body = _recover(root, from_, path, f, f.read())
            last = 0
            for raw in body.decode("utf-8").split("\n"):
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
            record = (text + "\n").encode("utf-8")
            f.seek(0, 2)
            _write_intent(root, from_, f.tell(), record)
            try:
                _check_hold()  # last, so no slow step sits between it and the write
            except StateHoldExpired:
                _clear_intent(root, from_)
                raise
            f.write(record)
            f.flush()
            os.fsync(f.fileno())
            _clear_intent(root, from_)
            return result
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)