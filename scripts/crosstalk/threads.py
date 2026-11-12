"""Threads built from the replayed messages, four sort orders, and paged reads.

A thread is a root message and every message whose `replyTo` chain leads to
it. A `replyTo` naming no known message makes the message a root. A chain
that comes back to a message already seen is a cycle: the walk ends there and
the oldest message of the cycle (by `t`, then id) is the root.

Whether a thread needs the owner, and whether it holds an open request, comes
from the rules in requests.py. Nothing here is stored; the server rebuilds the
threads when a log file changes (serve.ReplayCache.threads).
"""
import bisect
from datetime import datetime, timedelta, timezone
import threading
from typing import Dict, List, Optional, Set, Tuple

from . import requests as requests_mod

ORDERS = ("needs", "new", "hot", "unanswered")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
HOT_WINDOW = timedelta(minutes=60)
ROUTINE_KINDS = ("check-in", "checkin", "sync")
ROUTINE_PREFIXES = ("LAND START", "LAND END", "GRANTED", "TURN ASK")
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


# The fields the index uses as keys or compares as text (_flags, roots, requests.py).
_TEXT_FIELDS = ("kind", "from", "replyTo", "t")


def readable(doc) -> Optional[dict]:
    """The row as the index reads it: a field of _TEXT_FIELDS that holds no
    text is left out, so {"kind": []} reads as no kind. None for a row that is
    not an object. The log check (logs._validate) leaves the types inside a
    row's data free, so a row can hold any JSON value in any field."""
    if not isinstance(doc, dict):
        return None
    bad = [name for name in _TEXT_FIELDS if name in doc and not isinstance(doc[name], str)]
    return {k: v for k, v in doc.items() if k not in bad} if bad else doc


def _t(doc: dict) -> str:
    t = doc.get("t")
    return t if isinstance(t, str) else ""


def _summary(doc: dict) -> str:
    s = doc.get("summary")
    return s if isinstance(s, str) else ""


def roots(messages: Dict[str, dict], when=None) -> Dict[str, str]:
    """Each message id mapped to the id of its thread's root.

    `when` gives a message's time (its id in, a time out); the default reads its `t`.
    """
    when = when or (lambda did: _t(messages[did]))
    root_of = {}  # type: Dict[str, str]
    for start in messages:
        if start in root_of:
            continue
        path = []  # type: List[str]
        on_path = {}  # type: Dict[str, int]
        cur = start
        while True:
            if cur in root_of:
                root = root_of[cur]
                break
            on_path[cur] = len(path)
            path.append(cur)
            parent = messages[cur].get("replyTo")
            if not isinstance(parent, str) or parent not in messages:
                root = cur
                break
            if parent in on_path:
                cycle = path[on_path[parent]:]
                root = min(cycle, key=lambda d: (when(d), d))
                break
            cur = parent
        for did in path:
            root_of[did] = root
    return root_of


def _routine(root_doc: dict, authors: List[str]) -> bool:
    if len(authors) > 1:
        return False
    if root_doc.get("kind") in ROUTINE_KINDS:
        return True
    return _summary(root_doc).lstrip().startswith(ROUTINE_PREFIXES)


def _flags(messages: Dict[str, dict], roster,
           acks: Optional[Dict[str, object]] = None) -> Tuple[set, set, set, Dict[str, Set[str]]]:
    """(ids that need the owner, ids of open requests, ids of the requests that
    wait for the owner, request id -> the ids that acked it), by the rules of requests.py.

    `acks` (ack id -> doc) closes a recipient's part of a request as in requests.py:
    acks_by_item reads them, so a row it does not count changes nothing.
    """
    # Each helper reads only its own kind of message and the replies to it: hand it no more.
    by_kind = {"question": {}, "request": {}}  # type: Dict[str, Dict]
    kind_of = {}  # type: Dict[str, Dict]
    replies = []  # type: List[Tuple[str, str, dict]]
    for did, doc in messages.items():
        kind = doc.get("kind")
        if kind in by_kind:
            by_kind[kind][("messages", did)] = doc
            kind_of[did] = by_kind[kind]
        parent = doc.get("replyTo")
        if isinstance(parent, str):
            replies.append((parent, did, doc))
    for parent, did, doc in replies:
        docs = kind_of.get(parent)
        if docs is not None:
            docs[("messages", did)] = doc
    questions, asks = by_kind["question"], by_kind["request"]
    for aid, doc in (acks or {}).items():
        asks[("acks", aid)] = doc
    acked = requests_mod.acks_by_item(asks, roster=roster)
    needs = {did for did, _doc in requests_mod.open_owner_questions(questions, roster=roster)}
    # inbox_for also lists a broadcast request; the owner needs it only when named.
    owner_asks = {did for did, doc, replies in requests_mod.inbox_for(asks, "owner", roster=roster)
                  if "owner" in requests_mod.pending_recipients(doc, replies, roster=roster,
                                                                 acked=acked.get(did, ()))}
    needs |= owner_asks
    open_ = {did for did, _doc, _replies in requests_mod.open_requests(asks, roster=roster)}
    return needs, open_, owner_asks, acked


def _hot_cutoff(now: str) -> str:
    try:
        when = datetime.strptime(now, _TIME_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        when = datetime.now(timezone.utc)
    return (when - HOT_WINDOW).strftime(_TIME_FORMAT)


class ThreadIndex:
    """The threads of one replay: built once, with `hot` recomputed for each `now`.

    `hot` counts the messages of the last hour when two or more participants
    wrote in it, and is 0 for a routine thread.

    The log's `t` is always `%Y-%m-%dT%H:%M:%SZ` (logs.py checks it), so times
    compare as strings and the last hour is a bisect over the sorted times.

    One time for each message drives the member order, `last_t`, `hot` and the
    `t` that thread() sends: its created time from `created` (message id ->
    time, see serve.ReplayCache), else its replayed `t`. So a later `update` of
    an old message does not move it. `waiting` holds the ids of the open
    `waiting` rows: the thread of a message with one of those ids needs the owner.
    `acks` holds the `acks` rows (id -> doc): an ack closes a recipient's part of a
    request, as in requests.py.
    """

    def __init__(self, messages: Dict[str, dict], roster=None,
                 created: Optional[Dict[str, str]] = None, waiting=(), acks: Optional[Dict[str, object]] = None):
        messages = {did: row for did, row in ((did, readable(doc)) for did, doc in messages.items())
                    if row is not None}
        self.messages = messages
        self._lock = threading.Lock()
        created = created or {}
        self.when = {did: created.get(did) or _t(doc) for did, doc in messages.items()}
        self.root_of = root_of = roots(messages, self.when.__getitem__)
        timed = sorted((when, did) for did, when in self.when.items())
        self.members = {}  # type: Dict[str, List[str]]
        for _when, did in timed:
            self.members.setdefault(root_of[did], []).append(did)
        self._times = [when for when, _did in timed]
        self._recent = [(messages[did].get("from"), root_of[did]) for _when, did in timed]
        needs, open_, owner_asks, self._acked = _flags(messages, roster, acks)
        needs = {root_of[did] for did in needs | (set(waiting) & set(messages))}
        open_ = {root_of[did] for did in open_}
        self.by_root = {}  # type: Dict[str, dict]
        for root, ids in self.members.items():
            root_doc = messages[root]
            last = ids[-1]
            authors = []  # type: List[str]
            for did in ids:
                frm = messages[did].get("from")
                if isinstance(frm, str) and frm not in authors:
                    authors.append(frm)
            self.by_root[root] = {
                "root": root,
                "title": _summary(root_doc),
                "preview": _summary(messages[last]),
                "projects": authors,
                "count": len(ids),
                "last_t": self.when[last],
                "last_id": last,
                "needs_owner": root in needs,
                # The requests of the thread that wait for the owner, oldest first.
                "owner_requests": [did for did in ids if did in owner_asks],
                "unanswered": root in open_,
                "routine": _routine(root_doc, authors),
                "hot": 0,
                "kind": root_doc.get("kind") if isinstance(root_doc.get("kind"), str) else "",
            }
        # The spec: a routine line never counts as hot.
        self._routine = {root for root, t in self.by_root.items() if t["routine"]}
        self._hot = {}  # type: Dict[str, int]
        self._sorted = {}  # type: Dict[str, List[str]]

    def _hot_counts(self, now: str) -> Dict[str, int]:
        lo = bisect.bisect_right(self._times, _hot_cutoff(now))
        hi = bisect.bisect_right(self._times, now)
        counts = {}  # type: Dict[str, int]
        authors = {}  # type: Dict[str, set]
        for frm, root in self._recent[lo:hi]:
            counts[root] = counts.get(root, 0) + 1
            authors.setdefault(root, set()).add(frm)
        return {root: n for root, n in counts.items()
                if len(authors[root]) >= 2 and root not in self._routine}

    def _refresh_hot(self, now: str) -> None:
        """Set `hot` as of `now`. Only the threads whose `hot` changed are copied; none is changed in place."""
        hot = self._hot_counts(now)
        if hot != self._hot:
            for root in set(hot) | set(self._hot):
                self.by_root[root] = dict(self.by_root[root], hot=hot.get(root, 0))
            self._hot = hot
            self._sorted.pop("hot", None)

    def threads_at(self, now: str) -> List[dict]:
        """Every thread, with `hot` as of `now`."""
        with self._lock:
            self._refresh_hot(now)
            return list(self.by_root.values())

    def row(self, root: str, now: str) -> Optional[dict]:
        """The list row of the thread `root`, with `hot` as of `now`, or None."""
        with self._lock:
            self._refresh_hot(now)
            return self.by_root.get(root)

    def page(self, order: str, now: str, cursor: Optional[str],
             limit: Optional[int] = DEFAULT_LIMIT, all_rows: bool = False) -> Tuple[List[dict], Optional[str]]:
        """One page of the threads in `order` as of `now` (see page()).

        Each order is sorted once per replay, and `hot` again when a count
        changes, so a read with no new row only slices a list of ids.
        """
        if order not in ORDERS:
            raise ValueError("unknown sort %r: use one of %s" % (order, ", ".join(ORDERS)))
        with self._lock:
            self._refresh_hot(now)
            roots_ = self._sorted.get(order)
            if roots_ is None:
                roots_ = [t["root"] for t in sort_threads(list(self.by_root.values()), order)]
                self._sorted[order] = roots_
            if all_rows:
                return [self.by_root[r] for r in roots_], None
            items, nxt = page(roots_, cursor, limit)
            return [self.by_root[r] for r in items], nxt

    def ordered(self, order: str, now: str) -> List[dict]:
        """Every thread in `order` as of `now`, from the same sorted ids as page()."""
        rows, _nxt = self.page(order, now, None, None, all_rows=True)
        return rows

    def thread(self, root: str) -> Optional[List[dict]]:
        """The messages of one thread in created order with `id`, `depth`, `quote` and the created `t`, or None.

        A request that a participant acked with `none: true` also holds `acked_by`, their ids.
        """
        ids = self.members.get(root)
        if ids is None:
            return None
        out = []  # type: List[dict]
        for did in ids:
            doc = self.messages[did]
            parent = doc.get("replyTo")
            if did == root:
                depth, quote = 0, None
            elif parent == root:
                depth, quote = 1, None
            else:
                depth, quote = 2, _summary(self.messages[parent])
            row = dict(doc, id=did, depth=depth, quote=quote)
            if did in self._acked:
                # Who said this request needs nothing from them (`ack --none`).
                row["acked_by"] = sorted(self._acked[did])
            if self.when[did]:
                row["t"] = self.when[did]
            out.append(row)
        return out


def build_threads(messages: Dict[str, dict], now: str, roster=None,
                  acks: Optional[Dict[str, object]] = None) -> List[dict]:
    """The threads of `messages` (message id -> replayed doc), `hot` as of `now`.

    `roster` (a Roster) decides trust and who a broadcast reaches, as in
    requests.py; None treats every sender as approved.
    """
    return ThreadIndex(messages, roster, acks=acks).threads_at(now)


def _newest_first(t: dict) -> Tuple[str, str]:
    return (t["last_t"], t["last_id"])


def sort_threads(threads: List[dict], order: str) -> List[dict]:
    """`threads` in one of the ORDERS; `unanswered` keeps only the unanswered ones.

    needs: the threads that need the owner, oldest first, then the rest newest
    first. new: newest first. hot: by `hot`, then newest first. unanswered:
    oldest first. "Oldest" and "newest" read `last_t`, then `last_id`.
    """
    if order == "needs":
        needs = sorted((t for t in threads if t["needs_owner"]), key=_newest_first)
        rest = sorted((t for t in threads if not t["needs_owner"]), key=_newest_first, reverse=True)
        return needs + rest
    if order == "new":
        return sorted(threads, key=_newest_first, reverse=True)
    if order == "hot":
        return sorted(threads, key=lambda t: (t["hot"], t["last_t"], t["last_id"]), reverse=True)
    if order == "unanswered":
        return sorted((t for t in threads if t["unanswered"]), key=_newest_first)
    raise ValueError("unknown sort %r: use one of %s" % (order, ", ".join(ORDERS)))


def page(items: List, cursor: Optional[str], limit: Optional[int] = DEFAULT_LIMIT) -> Tuple[List, Optional[str]]:
    """(one page of `items`, the cursor of the next page or None at the end).

    The cursor is the decimal offset of the page's first item. `limit` is 50
    when None and at most 200; a limit below 1 or a cursor that is not a
    whole number of 0 or more is a ValueError.
    """
    if cursor is None or cursor == "":
        offset = 0
    elif isinstance(cursor, str) and cursor.isdigit():
        offset = int(cursor)
    else:
        raise ValueError("the cursor must be a whole number of 0 or more")
    if limit is None:
        limit = DEFAULT_LIMIT
    if limit < 1:
        raise ValueError("the limit must be 1 or more")
    limit = min(limit, MAX_LIMIT)
    end = offset + limit
    return items[offset:end], (str(end) if end < len(items) else None)
