"""Marks on a post: the word an `acks` row carries, and the reader of those words.

An agent puts one mark on a post with `relay.py ack` (consults.ack_item). The mark is
the field `mark` of the row `<agent>--<item>` in `acks`. The log holds the word; the
emoji exist only where text is shown.
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as config_mod
from .requests import ACK_SEP, _is_approved, _parse_iso, is_retracted

MARKS = ("seen", "ack", "applies", "none", "unclear", "vote")
# The marks that say "I read it and understood it".
ACKNOWLEDGED = ("ack", "applies", "none", "vote")
# The marks that count as the marker's reply on a request (requests.acks_by_item).
ANSWERS = ("none", "vote")
EMOJI = {"seen": "\U0001F440", "ack": "✅", "applies": "\U0001F527", "none": "➖", "unclear": "❓",
         "vote": "\U0001F5F3\uFE0F"}
LABEL = {"seen": "seen", "ack": "acknowledged", "applies": "applies", "none": "no change", "unclear": "not clear",
         "vote": "voted"}

# A vote (`ack --vote`): one word of 1 to 32 letters, digits, `_` or `-`, kept in upper
# case (ADOPT, OBJECT, ABSTAIN, 2), and an optional one-line note.
VOTE_RE = re.compile(r"[A-Z0-9][A-Z0-9_-]{0,31}")
NOTE_MAX = 200

# The collections a mark may name, in the order an item id is looked up.
MARKABLE = ("messages", "decisions", "lessons")
LESSON_PREFIX = "lesson-"


def vote_choice(raw) -> str:
    """raw as a vote choice in upper case; ValueError when it is not one word of VOTE_RE."""
    choice = raw.strip().upper() if isinstance(raw, str) else None
    if not choice or not VOTE_RE.fullmatch(choice):
        raise ValueError("bad vote %r (one word of 1 to 32 letters, digits, '_' or '-', "
                         "such as ADOPT, OBJECT, ABSTAIN or an option number)" % (raw,))
    return choice


def vote_note(raw) -> str:
    """raw as a vote note: one line of 1 to NOTE_MAX characters; ValueError otherwise."""
    note = raw.strip() if isinstance(raw, str) else ""
    if not note or "\n" in note or "\r" in note or len(note) > NOTE_MAX:
        raise ValueError("a vote note is one line of 1 to %d characters" % NOTE_MAX)
    return note


def vote_of(row) -> Optional[str]:
    """The choice of a vote row: its `vote` when that is a word of VOTE_RE, else None."""
    vote = row.get("vote") if isinstance(row, dict) else None
    return vote if isinstance(vote, str) and VOTE_RE.fullmatch(vote) else None


def mark_of(row) -> Optional[str]:
    """The word of one `acks` row: `mark` when it is one of MARKS, else `none` for a row
    with `none: true` (the form before marks), else None. A `vote` row counts only with
    a good choice (vote_of)."""
    if not isinstance(row, dict):
        return None
    mark = row.get("mark")
    if mark == "vote":
        return mark if vote_of(row) is not None else None
    if isinstance(mark, str) and mark in MARKS:
        return mark
    return "none" if row.get("none") is True else None


def item_ref(docs: Dict, item_id) -> Optional[Tuple[str, str]]:
    """The (collection, id) that item_id names: a message, a decision, then a lesson.
    A lesson `lesson-<m>` whose message <m> exists names that message."""
    if not isinstance(item_id, str) or not item_id:
        return None
    for coll in MARKABLE:
        if isinstance(docs.get((coll, item_id)), dict):
            if coll == "lessons" and item_id.startswith(LESSON_PREFIX):
                mid = item_id[len(LESSON_PREFIX):]
                if isinstance(docs.get(("messages", mid)), dict):
                    return ("messages", mid)
            return (coll, item_id)
    return None


def writer_of(docs: Dict, coll: str, did: str) -> Optional[str]:
    """Who wrote the item: `from` of a message; for any other row the writer of its first
    log line (replay.Docs.first_writer, filled by enrich_messages). None when unknown."""
    if coll == "messages":
        doc = docs.get((coll, did))
        frm = doc.get("from") if isinstance(doc, dict) else None
        return frm if isinstance(frm, str) and frm else None
    return getattr(docs, "first_writer", {}).get((coll, did))


def marks_by_item(docs: Dict, roster=None, rows=None) -> Dict[Tuple[str, str], Dict[str, str]]:
    """item_ref to {participant: word}.

    A row counts when its id is from + ACK_SEP + item, `from` is approved and holds no
    ACK_SEP, the item exists, `from` did not write the item, and the row has a word.
    When two rows of one participant name the same item (a message and its lesson),
    the row with the later `t` wins. Pass the docs from replay.enrich_messages: a plain
    dict has no `first_writer`, so the own-post rule then misses decisions and lessons.
    rows is _mark_rows(docs, roster), passed to save its work.
    """
    out = {}  # type: Dict[Tuple[str, str], Dict[str, str]]
    for ref, frm, word, _doc in _mark_rows(docs, roster) if rows is None else rows:
        out.setdefault(ref, {})[frm] = word
    return out


def _vote_list(latest: Dict[str, dict]) -> List[dict]:
    """{from, vote, note, t} of each latest row (participant to row) that is a vote, by name."""
    votes = []
    for frm in sorted(latest):
        doc = latest[frm]
        if mark_of(doc) != "vote":
            continue
        note, t = doc.get("note"), doc.get("t")
        votes.append({"from": frm, "vote": doc["vote"], "note": note if isinstance(note, str) else None,
                      "t": t if isinstance(t, str) else None})
    return votes


def _vote_counts(votes: List[dict]) -> Dict[str, int]:
    counts = {}  # type: Dict[str, int]
    for v in votes:
        counts[v["vote"]] = counts.get(v["vote"], 0) + 1
    return dict(sorted(counts.items()))


def votes_by_item(docs: Dict, roster=None, rows=None) -> Dict[Tuple[str, str], List[dict]]:
    """item_ref to the votes on it (_vote_list): each participant whose latest mark on
    the item is a vote. rows is _mark_rows(docs, roster), passed to save its work."""
    latest = {}  # type: Dict[Tuple[str, str], Dict[str, dict]]
    for ref, frm, _word, doc in _mark_rows(docs, roster) if rows is None else rows:
        latest.setdefault(ref, {})[frm] = doc
    out = {}
    for ref, by_frm in latest.items():
        votes = _vote_list(by_frm)
        if votes:
            out[ref] = votes
    return out


def _mark_rows(docs: Dict, roster=None) -> List[Tuple[Tuple[str, str], str, str, dict]]:
    """(item_ref, from, word, row) of each `acks` row that counts (marks_by_item), oldest `t` first."""
    rows = []
    for (coll, did), doc in docs.items():
        if coll != "acks" or not isinstance(doc, dict):
            continue
        word = mark_of(doc)
        frm, item = doc.get("from"), doc.get("item")
        if word is None or not isinstance(frm, str) or ACK_SEP in frm or not isinstance(item, str) \
                or did != frm + ACK_SEP + item or not _is_approved(frm, roster):
            continue
        ref = item_ref(docs, item)
        if ref is None or writer_of(docs, ref[0], ref[1]) == frm:
            continue
        t = doc.get("t")
        rows.append((t if isinstance(t, str) else "", did, ref, frm, word, doc))
    rows.sort(key=lambda r: r[:2])
    return [(ref, frm, word, doc) for _t, _did, ref, frm, word, doc in rows]


# A due time (`consult --due`, a message's `due`): the log keeps it as UTC ISO 8601.
DUE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# What `consult --due` accepts: an ISO time with Z or an offset (seconds optional),
# or a local HH:MM, the next such time on the machine's clock.
_DUE_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})")
_DUE_HHMM_RE = re.compile(r"(\d{1,2}):(\d{2})")
# The vote that stops a proposal from passing at its due time (lazy consensus).
OBJECT = "OBJECT"


def due_utc(value) -> Optional[datetime]:
    """A message's `due` as an aware UTC datetime, or None when it is not yyyy-mm-ddThh:mm:ssZ."""
    try:
        return datetime.strptime(value, DUE_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_due(raw, now: Optional[float] = None) -> str:
    """`consult --due` text as the `due` the log keeps (yyyy-mm-ddThh:mm:ssZ).

    Takes yyyy-mm-ddThh:mm[:ss] with Z or +hh:mm/-hh:mm, or a local hh:mm: today at that
    time on this machine's clock, or tomorrow when that time has passed. Raises
    ValueError for any other text and for a time that is not after `now`."""
    text = raw.strip() if isinstance(raw, str) else ""
    now = time.time() if now is None else now
    hhmm = _DUE_HHMM_RE.fullmatch(text)
    if hhmm:
        hour, minute = int(hhmm.group(1)), int(hhmm.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("bad due %r: hh:mm runs from 00:00 to 23:59" % (raw,))
        day = datetime.fromtimestamp(now).date()
        when = time.mktime((day.year, day.month, day.day, hour, minute, 0, 0, 0, -1))
        if when <= now:
            day += timedelta(days=1)
            when = time.mktime((day.year, day.month, day.day, hour, minute, 0, 0, 0, -1))
        stamp = datetime.fromtimestamp(int(when), timezone.utc)
    elif _DUE_ISO_RE.fullmatch(text):
        try:
            stamp = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError:
            raise ValueError("bad due %r: not a real date and time" % (raw,))
        stamp = stamp.astimezone(timezone.utc)
    else:
        raise ValueError("bad due %r: give yyyy-mm-ddThh:mm[:ss] with Z or an offset such as "
                         "+02:00, or a local hh:mm" % (raw,))
    if stamp.timestamp() <= now:
        raise ValueError("due %s is in the past" % stamp.strftime(DUE_FORMAT))
    return stamp.strftime(DUE_FORMAT)


def due_state(post: dict, votes: List[dict], now: Optional[float] = None) -> Optional[str]:
    """Where a proposal with a `due` stands: None without a good `due`; `retracted`;
    `open` before the due time; at or after it `objected` when a vote OBJECT cast by
    then holds, else `passed` (lazy consensus)."""
    due = due_utc(post.get("due")) if isinstance(post, dict) else None
    if due is None:
        return None
    if is_retracted(post):
        return "retracted"
    if (time.time() if now is None else now) < due.timestamp():
        return "open"
    for v in votes:
        cast = _parse_iso(v.get("t"))
        if v.get("vote") == OBJECT and (cast is None or cast <= due.timestamp()):
            return "objected"
    return "passed"


def due_owed(docs: Dict, roster=None, now: Optional[float] = None, pid: Optional[str] = None) -> List[dict]:
    """The votes owed on messages with a `due`: one row {pid, id, from, summary, due,
    overdue, state} per name in the message's `tally` pending, oldest due first. Leaves
    out a retracted message and one that asks nothing (`needs_answer: false`). With
    `pid`, only that name's rows."""
    now = time.time() if now is None else now
    rows = []
    for (coll, did), doc in docs.items():
        if coll != "messages" or not isinstance(doc, dict) or is_retracted(doc) \
                or doc.get("needs_answer") is False:
            continue
        due = due_utc(doc.get("due"))
        if due is None:
            continue
        v = tally(docs, coll, did, roster, now=now)
        summary = doc.get("summary")
        for name in v["pending"]:
            if pid is None or name == pid:
                rows.append({"pid": name, "id": did, "from": doc.get("from"),
                             "summary": summary if isinstance(summary, str) else None,
                             "due": doc["due"], "overdue": now >= due.timestamp(), "state": v["state"]})
    rows.sort(key=lambda r: (r["due"], r["id"], r["pid"]))
    return rows


def tally(docs: Dict, coll: str, did: str, roster=None, now: Optional[float] = None) -> dict:
    """The votes on one post (`relay.py tally`): each voter whose latest mark on it is a
    vote, with its choice, note and time; the count per choice; `pending`, the names
    in the post's `to` (not its writer) that hold no vote and posted no reply to it;
    and `due` and `state` (due_state), both None when the post has no due time.

    Pass the docs from replay.enrich_messages, as marks_by_item asks."""
    latest = {}  # type: Dict[str, dict]
    for ref, frm, _word, doc in _mark_rows(docs, roster):
        if ref == (coll, did):
            latest[frm] = doc
    votes = _vote_list(latest)
    post = docs.get((coll, did))
    post = post if isinstance(post, dict) else {}
    summary = post.get("summary") if isinstance(post.get("summary"), str) else post.get("title")
    writer = writer_of(docs, coll, did)
    answered = {v["from"] for v in votes}
    if coll == "messages":
        answered |= {m.get("from") for (c, _i), m in docs.items()
                     if c == "messages" and isinstance(m, dict) and m.get("replyTo") == did
                     and not is_retracted(m) and _is_approved(m.get("from"), roster)}
    pending = []  # type: List[str]
    for pid in _to_names(post) or []:
        if pid != writer and pid not in answered and pid not in pending and _is_approved(pid, roster):
            pending.append(pid)
    state = due_state(post, votes, now)
    return {"id": did, "collection": coll, "summary": summary if isinstance(summary, str) else None,
            "counts": _vote_counts(votes), "votes": votes, "pending": pending,
            "due": post["due"] if state is not None else None, "state": state}


# The kinds of participant that owe a mark (participants.KINDS); the owner owes none.
MARKER_KINDS = ("project", "agent")
OWNER = "owner"
SINCE_KEY = "marks_since"


def marks_since(config) -> Optional[str]:
    """The ISO time in `marks_since` of config.json; None when the key is absent, empty
    or not a time. Only a post at or after it expects a mark."""
    value = config.get(SINCE_KEY) if isinstance(config, dict) else None
    if isinstance(value, str) and value.strip() and _parse_iso(value) is not None:
        return value.strip()
    return None


def since_problem(root) -> Optional[str]:
    """A warning for `relay.py check` when config.json holds a `marks_since` that is not a
    time; None when the key is fine or absent. It never fails the check."""
    try:
        raw = json.loads((Path(root) / config_mod.FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or SINCE_KEY not in raw or raw[SINCE_KEY] in ("", None):
        return None
    if marks_since(raw) is None:
        return "%s: %s is not an ISO time (%r); no post expects a mark" % (config_mod.FILE, SINCE_KEY, raw[SINCE_KEY])
    return None


def _participants(roster) -> Dict:
    participants = getattr(roster, "participants", roster)
    return participants if isinstance(participants, dict) else {}


def _kind(entry) -> Optional[str]:
    return entry.get("kind") if isinstance(entry, dict) else getattr(entry, "kind", None)


def _to_names(doc) -> Optional[List[str]]:
    """The names in `to`; None when the row has no `to`. A `to` of a wrong type names nobody."""
    to = doc.get("to")
    if to is None:
        return None
    if isinstance(to, str):
        return [to] if to else []
    if isinstance(to, list):
        return [x for x in to if isinstance(x, str) and x]
    return []


class _ParsedConfig(dict):
    """A config whose marks_since is parsed once, for a caller that asks about many posts."""

    def __init__(self, config):
        dict.__init__(self, config if isinstance(config, dict) else {})
        since = marks_since(self)
        self.since_t = _parse_iso(since) if since is not None else None


def parsed(config) -> "_ParsedConfig":
    """config with marks_since parsed once, for a caller that asks about many posts."""
    return config if isinstance(config, _ParsedConfig) else _ParsedConfig(config)


def _since_t(config) -> Optional[float]:
    if isinstance(config, _ParsedConfig):
        return config.since_t
    since = marks_since(config)
    return _parse_iso(since) if since is not None else None


def post_time(docs: Dict, coll: str, did: str):
    """The time of a post: for a message its created time (replay.Docs.created, the first
    `set` line), so that an edit does not make an old post new; else the row's `t`, which
    enrich_messages takes from the first `set` line when the row has none."""
    created = getattr(docs, "created", {}).get((coll, did))
    if created is not None:
        return created
    doc = docs.get((coll, did))
    return doc.get("t") if isinstance(doc, dict) else None


def _post_time_ok(docs: Dict, coll: str, did: str, config) -> bool:
    since = _since_t(config)
    if since is None:
        return False
    t = _parse_iso(post_time(docs, coll, did))
    return t is not None and t >= since


def _folded(docs: Dict, coll: str, did: str) -> bool:
    """True for a `lessons` row whose message exists: the message is the item."""
    return coll == "lessons" and item_ref(docs, did) != (coll, did)


def expects_mark(docs: Dict, coll: str, did: str, roster, config) -> bool:
    """True when the post (coll, did) expects a mark (PROTOCOL.md, "Which posts expect a
    mark"). A retracted message never does. A message with `must_read: true` always does,
    whatever marks_since holds.
    Any other post needs a time at or after marks_since(config), and one of these: a
    message of kind `lesson`, or a message that a `lessons` row `lesson-<id>` names, or
    a `lessons` row with no message; a `decisions` row written by the coordinator or
    the owner; a message from the owner with no `replyTo` and no `to`, or a `to` that
    holds every approved project.

    Pass the docs from replay.enrich_messages: they hold the time of each row and
    `first_writer`. A decision whose writer is unknown expects no mark.
    """
    doc = docs.get((coll, did))
    if not isinstance(doc, dict):
        return False
    if coll == "messages":
        if is_retracted(doc):
            return False
        if doc.get("must_read") is True:
            return True
        lesson = docs.get(("lessons", LESSON_PREFIX + did))
        if isinstance(lesson, dict) and _post_time_ok(docs, "lessons", LESSON_PREFIX + did, config):
            return True
        if not _post_time_ok(docs, coll, did, config):
            return False
        if doc.get("kind") == "lesson":
            return True
        if writer_of(docs, coll, did) != OWNER or doc.get("replyTo") not in (None, ""):
            return False
        to = _to_names(doc)
        if to is None:
            return True
        return all(pid in to for pid, entry in _participants(roster).items()
                   if _kind(entry) == "project" and _is_approved(pid, roster))
    if not _post_time_ok(docs, coll, did, config):
        return False
    if coll == "lessons":
        return not _folded(docs, coll, did)
    if coll == "decisions":
        writer = writer_of(docs, coll, did)
        coordinator = config_mod.coordinator(roster)
        return writer is not None and (writer == OWNER or (bool(coordinator) and writer == coordinator))
    return False


def markers(docs: Dict, coll: str, did: str, roster) -> List[str]:
    """The participants that must mark the post, sorted: approved, of kind `project` or
    `agent`, not its writer and not the owner; only the names in `to` when it has one.
    Pass the docs from replay.enrich_messages (writer_of needs `first_writer`)."""
    doc = docs.get((coll, did))
    if not isinstance(doc, dict):
        return []
    writer = writer_of(docs, coll, did)
    to = _to_names(doc)
    return sorted(pid for pid, entry in _participants(roster).items()
                  if _kind(entry) in MARKER_KINDS and _is_approved(pid, roster)
                  and pid != writer and pid != OWNER and (to is None or pid in to))


def _marks_of(docs: Dict, coll: str, did: str, roster, by_item) -> Dict[str, str]:
    if by_item is None:
        by_item = marks_by_item(docs, roster)
    ref = item_ref(docs, did) if coll == "lessons" else (coll, did)
    return dict(by_item.get(ref, {})) if ref is not None else {}


def owed(docs: Dict, coll: str, did: str, roster, config, by_item=None) -> List[str]:
    """markers() that hold no acknowledged mark (ACKNOWLEDGED); [] when the post expects
    none. by_item is marks_by_item(docs, roster), passed to save its work across posts.
    Pass the docs from replay.enrich_messages."""
    if not expects_mark(docs, coll, did, roster, config):
        return []
    have = _marks_of(docs, coll, did, roster, by_item)
    return [pid for pid in markers(docs, coll, did, roster) if have.get(pid) not in ACKNOWLEDGED]


def view(docs: Dict, coll: str, did: str, roster, config, by_item=None) -> dict:
    """What a page shows for one post: {"marks": {participant: word}, "owed": [names],
    "expects": bool}, plus "votes" (vote_view) when the post has a vote or a due
    time. A `lessons` row that names its message shows the marks of that
    message and expects none itself. Pass the docs from replay.enrich_messages;
    by_item as in owed()."""
    if not isinstance(docs.get((coll, did)), dict):
        return {"marks": {}, "owed": [], "expects": False}
    if by_item is None:
        by_item = marks_by_item(docs, roster)
    out = {"marks": _marks_of(docs, coll, did, roster, by_item),
           "owed": owed(docs, coll, did, roster, config, by_item),
           "expects": expects_mark(docs, coll, did, roster, config)}
    votes = vote_view(docs, coll, did, votes_by_item(docs, roster))
    if votes is not None:
        out["votes"] = votes
    return out


def vote_view(docs: Dict, coll: str, did: str, by_item: Dict[Tuple[str, str], List[dict]],
              now: Optional[float] = None) -> Optional[dict]:
    """The `votes` key of a post's view: {"counts", "votes", "due", "state", "outcome"},
    or None when the post has no vote and no due time. counts and votes are as in
    tally(); due and state too (None without a due time). outcome is the state from
    the due time on (`objected`, `passed` or `retracted`), so that a page shows the
    result once the due time passes without a new build. by_item is votes_by_item()."""
    ref = item_ref(docs, did) if coll == "lessons" else (coll, did)
    if ref is None:
        return None
    post = docs.get(ref)
    post = post if isinstance(post, dict) else {}
    votes = by_item.get(ref, [])
    due = due_utc(post.get("due"))
    if not votes and due is None:
        return None
    state = due_state(post, votes, now)
    return {"counts": _vote_counts(votes), "votes": votes,
            "due": post["due"] if state is not None else None, "state": state,
            "outcome": due_state(post, votes, due.timestamp()) if due is not None else None}


def page_views(docs: Dict, roster, config) -> Dict[Tuple[str, str], dict]:
    """(collection, id) to view() for each message, decision and lesson that has a mark,
    expects one, or has a due time (vote_view): the `marks` key of that row in the
    data the pages load. A row that is not here gets no key. Pass the docs from
    replay.enrich_messages."""
    rows = _mark_rows(docs, roster)
    by_item = marks_by_item(docs, roster, rows)
    votes_by = votes_by_item(docs, roster, rows)
    config = parsed(config)
    out = {}
    for (coll, did), doc in docs.items():
        if coll not in MARKABLE or not isinstance(doc, dict):
            continue
        expects = expects_mark(docs, coll, did, roster, config)
        have = _marks_of(docs, coll, did, roster, by_item)
        votes = vote_view(docs, coll, did, votes_by)
        if not have and not expects and votes is None:
            continue
        owe = [pid for pid in markers(docs, coll, did, roster) if have.get(pid) not in ACKNOWLEDGED] if expects else []
        out[(coll, did)] = {"marks": have, "owed": owe, "expects": expects}
        if votes is not None:
            out[(coll, did)]["votes"] = votes
    return out


def with_marks(collections: Dict[str, Dict[str, dict]], views: Dict[Tuple[str, str], dict]) -> None:
    """Put each view of page_views() on a copy of its row in collections, as `marks`, and
    drop the `marks` field that a writer put on any other message, decision or lesson:
    marks come only from `acks` rows. The rows are copied so that a cached replay stays
    as it was."""
    for coll in MARKABLE:
        rows = collections.get(coll)
        if not isinstance(rows, dict):
            continue
        for did, row in list(rows.items()):
            if not isinstance(row, dict):
                continue
            rows[did] = on_row(row, views.get((coll, did)))


def counts(docs: Dict, roster, views: Dict[Tuple[str, str], dict]) -> Dict[Tuple[str, str], dict]:
    """(collection, id) to {"acknowledged": n, "expected": m} for each post of views
    (page_views) that expects a mark: m names owe a mark (markers), n of them hold an
    acknowledged one. The v2 page shows it as "n of m acknowledged" on a thread row.
    Pass the docs from replay.enrich_messages."""
    out = {}
    for (coll, did), v in views.items():
        if v.get("expects") is not True:
            continue
        names = markers(docs, coll, did, roster)
        out[(coll, did)] = {"acknowledged": len(names) - len(v.get("owed", ())), "expected": len(names)}
    return out


def on_row(row: dict, view: Optional[dict]) -> dict:
    """row with `marks` set to view, or with no `marks` field when view is None: marks
    come only from `acks` rows. The row is copied when it changes."""
    if view is not None:
        return dict(row, marks=view)
    if "marks" in row:
        return {k: val for k, val in row.items() if k != "marks"}
    return row


ROW_KIND = {"decisions": "decision", "lessons": "lesson"}


def _posts_that_expect(docs: Dict, roster, config):
    """(time, collection, id, doc) of each post that expects a mark, oldest first."""
    out = []
    for (coll, did), doc in docs.items():
        if coll in MARKABLE and isinstance(doc, dict) and expects_mark(docs, coll, did, roster, config):
            t = post_time(docs, coll, did)
            out.append((t if isinstance(t, str) else "", coll, did, doc))
    out.sort(key=lambda r: (_parse_iso(r[0]) or 0.0, r[0], r[1], r[2]))
    return out


def item_of(docs: Dict, coll: str, did: str, doc: dict) -> dict:
    """One post as the inbox lists it: id, collection, from (its writer), t, kind, summary, body."""
    kind = doc.get("kind") if coll == "messages" else ROW_KIND.get(coll)
    return {"id": did, "collection": coll, "from": writer_of(docs, coll, did), "t": doc.get("t"),
            "kind": kind, "summary": doc.get("summary"), "body": doc.get("body")}


def to_read(docs: Dict, pid: str, roster, config, by_item=None) -> List[dict]:
    """The posts that owe pid's acknowledged mark, newest first, each as item_of gives it.
    The inbox leaves out the ones it shows in another section (reports.inbox_report).
    Pass the docs from replay.enrich_messages; by_item as in owed()."""
    if by_item is None:
        by_item = marks_by_item(docs, roster)
    config = parsed(config)
    out = []
    for _t, coll, did, doc in reversed(_posts_that_expect(docs, roster, config)):
        if pid in owed(docs, coll, did, roster, config, by_item):
            out.append(item_of(docs, coll, did, doc))
    return out


def missing(docs: Dict, roster, config, before: Optional[float] = None, by_item=None) -> List[dict]:
    """Each post that expects a mark and has names that owe one, oldest first: view()
    plus `id` and `collection`. With `before` (a POSIX time), only posts older than it,
    and the posts whose time cannot be read: they miss marks, and their age is unknown.
    Pass the docs from replay.enrich_messages."""
    if by_item is None:
        by_item = marks_by_item(docs, roster)
    config = parsed(config)
    out = []
    for t, coll, did, _doc in _posts_that_expect(docs, roster, config):
        if before is not None:
            when = _parse_iso(t)
            if when is not None and when >= before:
                continue
        v = view(docs, coll, did, roster, config, by_item)
        if v["owed"]:
            out.append(dict(v, id=did, collection=coll))
    return out
