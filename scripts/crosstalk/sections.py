"""The rows of one section of the v2 page (`/api/section/<name>`), open ones first.

Each section reads one collection of the replay; nothing here is stored.
"Open" follows what the present page (index.html) and commands treat as open:

  agreements  a status other than landed-both and dropped (the Open agreements tile)
  locks       not released (the File claims tile)
  incidents   no end (the Incidents tab's "open" pill and Resolve button)
  schedules   not removed (the jobs the Schedules tab lists, `relay.py schedule remove`)
  reviews     an override with no resolution whose status is not agreed
              (reviews.open_overrides, the Reviews tab); a review row is never open
  waiting     not done (the present page's Waiting on you tab); a row whose id is
              also a message id is left out: that message's thread shows it
  decisions, lessons, progress, adoption: no open state, so never open

`q` searches the fields each section shows (FIELDS), case ignored, as plain text.

The reviews section holds the coordinator's Codex review rows: messages of kind
review or disagreement with source codex-review and a project (reviews.py).
"""
from typing import Callable, Dict, List, Optional, Tuple

from . import config
from . import marks as marks_mod
from . import reviews as reviews_mod

SECTIONS = ("agreements", "decisions", "lessons", "incidents", "locks", "progress",
            "reviews", "schedules", "adoption", "waiting")

# The collection each section reads.
COLLECTION = {name: name for name in SECTIONS}
COLLECTION.update(reviews="messages", adoption="adoptions")


def _never(_doc: dict) -> bool:
    return False


OPEN = {
    "agreements": lambda d: d.get("status") not in ("landed-both", "dropped"),
    "locks": lambda d: not d.get("released"),
    "incidents": lambda d: not d.get("end"),
    "schedules": lambda d: not d.get("removed"),
    "waiting": lambda d: not d.get("done"),
    "reviews": lambda d: d.get("kind") == "disagreement" and not d.get("resolution")
    and d.get("status") != "agreed",
}


def _is_review_row(doc: dict, coordinator: str) -> bool:
    """The page's isReviewRow: a review or override the coordinator posted."""
    return (bool(coordinator) and doc.get("from") == coordinator
            and doc.get("source") == reviews_mod.SOURCE and isinstance(doc.get("project"), str)
            and doc.get("kind") in ("review", "disagreement"))


# The fields `q` reads in each section: the text its rows show (web/src/features/sections).
_TEXT = ("summary", "body")
FIELDS = {name: _TEXT for name in SECTIONS}
FIELDS.update(
    agreements=("title", "builder", "owner", "adopter") + _TEXT,
    decisions=("title", "project", "source") + _TEXT,
    lessons=("title", "fix") + _TEXT,
    incidents=("title", "fix", "project") + _TEXT,
    reviews=("reason", "project", "verdict") + _TEXT,
    locks=("path", "note", "repo", "project") + _TEXT,
    schedules=("name", "about", "cron") + _TEXT,
    adoption=("item", "note", "ref") + _TEXT,
    progress=("item", "game", "note") + _TEXT,
    waiting=("project",) + _TEXT,
)


def _matches(doc: dict, needle: str, fields) -> bool:
    for name in fields:
        value = doc.get(name)
        if isinstance(value, str) and needle in value.casefold():
            return True
    return False


def rows(name: str, docs: Dict[Tuple[str, str], dict], roster,
         changed: Optional[Callable[[Tuple[str, str]], Optional[str]]] = None,
         q: str = "", views: Optional[Dict[Tuple[str, str], dict]] = None) -> Tuple[List[dict], int]:
    """(the section's rows, how many are open): open rows first, then newest change first.

    Each row is a copy of its doc with "id" set. `changed` gives the time of
    the last line that touched a (collection, id) pair; without it, or when it
    gives None, the doc's own `t` stands in. Ties go to the higher id. `q`
    keeps the docs where one of the section's FIELDS holds it, case ignored,
    as plain text. A row of a message, a decision or a lesson gets its `marks` from
    `views` (marks.page_views) and never keeps a `marks` field of the log.
    An unknown name is a KeyError.
    """
    if name not in SECTIONS:
        raise KeyError(name)
    coll = COLLECTION[name]
    is_open = OPEN.get(name, _never)
    coordinator = config.coordinator(roster) if name == "reviews" else ""
    needle = q.strip().casefold()
    found = []  # type: List[Tuple[bool, str, str, dict]]
    for (c, did), doc in docs.items():
        if c != coll or not isinstance(doc, dict):
            continue
        if name == "reviews" and not _is_review_row(doc, coordinator):
            continue
        if name == "waiting" and ("messages", did) in docs:
            continue
        if needle and not _matches(doc, needle, FIELDS[name]):
            continue
        when = changed((c, did)) if changed is not None else None
        if not isinstance(when, str):
            when = doc.get("t") if isinstance(doc.get("t"), str) else ""
        found.append((bool(is_open(doc)), when, did, doc))
    found.sort(key=lambda r: (r[1], r[2]), reverse=True)
    found.sort(key=lambda r: not r[0])  # stable: open first, each part newest first
    out = [dict(doc, id=did) for _open, _when, did, doc in found]
    if coll in marks_mod.MARKABLE:
        views = views or {}
        out = [marks_mod.on_row(row, views.get((coll, row["id"]))) for row in out]
    return out, sum(1 for r in found if r[0])
