"""Marks on a post: the word an `acks` row carries, and the reader of those words.

An agent puts one mark on a post with `relay.py ack` (consults.ack_item). The mark is
the field `mark` of the row `<agent>--<item>` in `acks`. The log holds the word; the
emoji exist only where text is shown.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as config_mod
from .requests import ACK_SEP, _is_approved, _parse_iso

MARKS = ("seen", "ack", "applies", "none", "unclear")
# The marks that say "I read it and understood it".
ACKNOWLEDGED = ("ack", "applies", "none")
EMOJI = {"seen": "\U0001F440", "ack": "✅", "applies": "\U0001F527", "none": "➖", "unclear": "❓"}
LABEL = {"seen": "seen", "ack": "acknowledged", "applies": "applies", "none": "no change", "unclear": "not clear"}

# The collections a mark may name, in the order an item id is looked up.
MARKABLE = ("messages", "decisions", "lessons")
LESSON_PREFIX = "lesson-"


def mark_of(row) -> Optional[str]:
    """The word of one `acks` row: `mark` when it is one of MARKS, else `none` for a row
    with `none: true` (the form before marks), else None."""
    if not isinstance(row, dict):
        return None
    mark = row.get("mark")
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


def marks_by_item(docs: Dict, roster=None) -> Dict[Tuple[str, str], Dict[str, str]]:
    """item_ref to {participant: word}.

    A row counts when its id is from + ACK_SEP + item, `from` is approved and holds no
    ACK_SEP, the item exists, `from` did not write the item, and the row has a word.
    When two rows of one participant name the same item (a message and its lesson),
    the row with the later `t` wins. Pass the docs from replay.enrich_messages: a plain
    dict has no `first_writer`, so the own-post rule then misses decisions and lessons.
    """
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
        rows.append((t if isinstance(t, str) else "", did, ref, frm, word))
    out = {}  # type: Dict[Tuple[str, str], Dict[str, str]]
    for _t, _did, ref, frm, word in sorted(rows):
        out.setdefault(ref, {})[frm] = word
    return out


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


def _since_t(config) -> Optional[float]:
    if isinstance(config, _ParsedConfig):
        return config.since_t
    since = marks_since(config)
    return _parse_iso(since) if since is not None else None


def _post_time_ok(doc, config) -> bool:
    since = _since_t(config)
    if since is None:
        return False
    t = _parse_iso(doc.get("t"))
    return t is not None and t >= since


def _folded(docs: Dict, coll: str, did: str) -> bool:
    """True for a `lessons` row whose message exists: the message is the item."""
    return coll == "lessons" and item_ref(docs, did) != (coll, did)


def expects_mark(docs: Dict, coll: str, did: str, roster, config) -> bool:
    """True when the post (coll, did) expects a mark (PROTOCOL.md, "Which posts expect a
    mark"). A message with `must_read: true` always does, whatever marks_since holds.
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
        if doc.get("must_read") is True:
            return True
        lesson = docs.get(("lessons", LESSON_PREFIX + did))
        if isinstance(lesson, dict) and _post_time_ok(lesson, config):
            return True
        if not _post_time_ok(doc, config):
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
    if not _post_time_ok(doc, config):
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
    "expects": bool}. A `lessons` row that names its message shows the marks of that
    message and expects none itself. Pass the docs from replay.enrich_messages;
    by_item as in owed()."""
    if not isinstance(docs.get((coll, did)), dict):
        return {"marks": {}, "owed": [], "expects": False}
    if by_item is None:
        by_item = marks_by_item(docs, roster)
    return {"marks": _marks_of(docs, coll, did, roster, by_item),
            "owed": owed(docs, coll, did, roster, config, by_item),
            "expects": expects_mark(docs, coll, did, roster, config)}


def page_views(docs: Dict, roster, config) -> Dict[Tuple[str, str], dict]:
    """(collection, id) to view() for each message, decision and lesson that has a mark
    or expects one: the `marks` key of that row in the data the pages load. A row that
    is not here gets no key. Pass the docs from replay.enrich_messages."""
    by_item = marks_by_item(docs, roster)
    config = _ParsedConfig(config)
    out = {}
    for (coll, did), doc in docs.items():
        if coll not in MARKABLE or not isinstance(doc, dict):
            continue
        expects = expects_mark(docs, coll, did, roster, config)
        have = _marks_of(docs, coll, did, roster, by_item)
        if not have and not expects:
            continue
        owe = [pid for pid in markers(docs, coll, did, roster) if have.get(pid) not in ACKNOWLEDGED] if expects else []
        out[(coll, did)] = {"marks": have, "owed": owe, "expects": expects}
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
            t = doc.get("t")
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
