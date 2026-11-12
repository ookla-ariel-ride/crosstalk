"""Marks on a post: the word an `acks` row carries, and the reader of those words.

An agent puts one mark on a post with `relay.py ack` (consults.ack_item). The mark is
the field `mark` of the row `<agent>--<item>` in `acks`. The log holds the word; the
emoji exist only where text is shown.
"""
from typing import Dict, Optional, Tuple

from .requests import ACK_SEP, _is_approved

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
