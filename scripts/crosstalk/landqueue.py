"""The land queue of the build host, derived from the replayed messages.

Projects take turns landing branches on one build host. Their land traffic
starts with fixed words:

    TURN ASK <project> <branch> ...    ask for a land turn
    TURN ASK WITHDRAWN <project> ...   the asker drops its waiting ask
    LAND START <project> <branch> ...  the sender's ask becomes a holder
    LAND END <project> <branch> ...    the holder frees its slot
    Turn free ...                      as LAND END, in any case: a stopped run frees its slot
    LANE START / LANE END              a gate run on the build host: holds and frees a slot
                                       only when the sender has a waiting ask

Only the sender of a message counts: a project asks, starts, ends and
withdraws for itself. Asks wait in post order; a second TURN ASK from a
project that is already waiting adds its branches to that ask and keeps its
place. To drop a branch, withdraw the ask and ask again.
A start that names a branch its sender still holds replaces that run, which
stopped with no LAND END. A LAND END from a sender that holds nothing closes
its granted ask when it names one of the ask's branches.

The coordinator's GRANTED reply to an ask (summary starting GRANTED, replyTo
the ask) marks it granted: it reserves a slot until its LAND START or LANE START,
or until GRANT_TTL_SEC passes with `now` given (the grant lapses). A re-ask
clears the grant. A start takes the whole ask, except the branches that
neither its GRANTED nor the start names: those stay waiting. `next` is the
first waiting ask, not granted, from a project that is not holding. The queue
is derived, never stored, so it cannot drift from the log.

The coordinator is the roster's `relay`. It grants turns and holds none, so a
reply of its that opens with a land word claims nothing. With no coordinator
no message is a GRANTED, and the queue is the asks in post order.
"""
import calendar
import re
import time
from typing import Dict, Iterable, List, Optional

MAX_HOLDERS = 2
GRANT_TTL_SEC = 30 * 60
WORDS = ("TURN ASK", "LAND START", "LAND END", "LANE START", "LANE END")
_WORD_RE = re.compile(r"^(%s)\b" % "|".join(WORDS))
_HEAD_END_RE = re.compile(r":(?=\s|$)")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
# A message id cited in the head of a summary, as in "(answers alpha-20260102T030405Z)".
_MESSAGE_ID_RE = re.compile(r"-\d{8}T\d+Z?$")
# What a project's land tool posts when it gives up its turn, landed or stopped.
_TOOL_END_RE = re.compile(r"^land(?: --queue)?\b.*\bturn released\b")
# What a project posts when its run stopped and it gives the slot back.
_TURN_FREE_RE = re.compile(r"^turn free\b", re.I)
# The lookarounds keep "tool-fix-1110" out of the longer name "tool-fix-1110-min".
_DATED_BRANCH_RE = re.compile(r"(?<![\w.-])([a-z][a-z0-9.]*(?:-[a-z0-9.]+)*-\d{4})(?![\w-])")


def land_word(summary) -> Optional[str]:
    """The fixed word a summary starts with, or None. Case matters: the words are the contract."""
    if not isinstance(summary, str):
        return None
    m = _WORD_RE.match(summary.strip())
    if m:
        return m.group(1)
    s = summary.strip()
    return "LAND END" if _TOOL_END_RE.search(s) or _TURN_FREE_RE.match(s) else None


def _rest(summary: str, word: str) -> str:
    return summary.strip()[len(word):].strip()


def is_withdrawal(summary) -> bool:
    """True for a summary starting TURN ASK WITHDRAWN (the last word in any case)."""
    if land_word(summary) != "TURN ASK":
        return False
    rest = _rest(summary, "TURN ASK")
    return rest[:9].lower() == "withdrawn" and rest[9:10] in ("", " ", ":", ",", ".")


def branches_of(summary: str, word: str, skip: Iterable[str] = ()) -> List[str]:
    """Branch names in the head of a summary: the text after the fixed word up to
    the first ':' that ends a word (so 05:41 stays in), taking each name that holds a '-' (branch names here do;
    times, shas and short project names don't) and is not a project id."""
    head = _HEAD_END_RE.split(_rest(summary, word), 1)[0]
    skip = set(skip)
    out = []  # type: List[str]
    for tok in _TOKEN_RE.findall(head):
        tok = tok.rstrip(".")
        if "-" in tok and tok not in skip and tok not in out and not _MESSAGE_ID_RE.search(tok):
            out.append(tok)
    return out


def _land_messages(docs: Dict, coordinator: str = "") -> List[tuple]:
    rows = []
    for (coll, did), doc in docs.items():
        if coll != "messages" or not isinstance(doc, dict):
            continue
        sender = doc.get("from")
        if not isinstance(sender, str) or not sender:
            continue
        if coordinator and sender == coordinator:
            s = doc.get("summary")
            if not (isinstance(s, str) and s.strip().startswith("GRANTED")
                    and isinstance(doc.get("replyTo"), str)):
                continue
            word = "GRANTED"
        else:
            word = land_word(doc.get("summary"))
            if word is None:
                continue
        t = doc.get("t") if isinstance(doc.get("t"), str) else ""
        rows.append((t, did, sender, word, doc["summary"], doc.get("replyTo")))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def _epoch(t: str) -> Optional[float]:
    try:
        return float(calendar.timegm(time.strptime(t, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def land_queue(docs: Dict, max_holders: int = MAX_HOLDERS,
               projects: Iterable[str] = (), now: Optional[float] = None,
               coordinator: str = "") -> dict:
    """The queue from replayed docs: holders, waiting asks in post order, `next`,
    `slots_free` and `may_start`. `projects` (the roster ids) are left out of
    branch names. A granted ask carries `granted` (the GRANTED id) and, with
    `now` given and GRANT_TTL_SEC past, `grant_lapsed`. `notes` names traffic
    that breaks the rules. `coordinator` is the participant whose GRANTED counts
    (config.coordinator)."""
    skip = set(projects)
    holders = []  # type: List[dict]
    waiting = []  # type: List[dict]
    notes = []  # type: List[str]

    message_ids = {did for (coll, did) in docs if coll == "messages"}

    def find(rows, project):
        return next((r for r in rows if r["project"] == project), None)

    def start(did, t, sender, names, ask, word):
        held = find(holders, sender)
        if held is not None:
            # A start of a branch the sender already holds: its last run stopped
            # with no LAND END, so this run replaces it.
            mine = names or (ask["branches"] if ask else [])
            if not set(mine) & set(held["branches"]):
                notes.append("%s: %s from %s, already holding" % (did, word, sender))
                return
            holders.remove(held)
            notes.append("%s: %s from %s replaces %s, which had no LAND END"
                         % (did, word, sender, held["id"]))
        if len(holders) >= max_holders:
            notes.append("%s: %s from %s with %d holders already"
                         % (did, word, sender, len(holders)))
        covered = ask.get("granted_branches") if ask else None
        if ask is not None:
            # Branches a later TURN ASK added after the GRANTED went out are not
            # part of this run: they stay waiting, in the ask's place.
            rest = [b for b in ask["branches"] if b not in covered and b not in names] if covered else []
            if rest:
                held = [b for b in ask["branches"] if b not in rest]
                ask["branches"] = rest
                for key in ("granted", "granted_t", "granted_branches"):
                    ask.pop(key, None)
            else:
                held = ask["branches"]
                waiting.remove(ask)
        holders.append({"project": sender, "id": did, "since": t, "word": word,
                        "branches": names or (held if ask else []),
                        "ask": ask["id"] if ask else None})

    for t, did, sender, word, summary, reply_to in _land_messages(docs, coordinator):
        if word == "GRANTED":
            ask = next((w for w in waiting if w["id"] == reply_to), None)
            if ask is not None:
                ask["granted"] = did
                ask["granted_t"] = t
                named = branches_of(summary, "GRANTED", skip | {ask["project"]})
                ask["granted_branches"] = [b for b in named if b in ask["branches"]]
            continue
        # A message id in the head ("TURN ASK <ask id> now covers ...") is not a branch.
        names = [n for n in branches_of(summary, word, skip | {sender}) if n not in message_ids]
        if word == "TURN ASK":
            # "TURN ASK alpha: one land run of parser-fixes-1109 91f5b81b": a
            # branch can sit after the colon; take dated names (ending -MMDD) from anywhere.
            names += [n for n in dict.fromkeys(_DATED_BRANCH_RE.findall(summary))
                      if n not in skip and n != sender and n not in names and n not in message_ids]
        ask = find(waiting, sender)
        if word == "TURN ASK":
            if is_withdrawal(summary):
                if ask is not None:
                    waiting.remove(ask)
            elif ask is not None:
                ask["branches"] += [n for n in names if n not in ask["branches"]]
                ask["updated"] = did
                # granted_branches stays: a GRANTED the server posted before it saw
                # this ask still names what the coming start covers.
                ask.pop("granted", None)
                ask.pop("granted_t", None)
            else:
                waiting.append({"project": sender, "id": did, "t": t, "branches": names})
        elif word == "LAND START":
            start(did, t, sender, names, ask, word)
        elif word == "LANE START":
            # A lane counts only as the run of a waiting (granted) ask; other lanes are tests.
            if ask is not None:
                start(did, t, sender, names, ask, word)
        elif word in ("LAND END", "LANE END"):
            held = find(holders, sender)
            if held is None:
                if word != "LAND END":
                    continue
                # A granted ask whose branch landed (its start missed) is over too.
                said = {tok.rstrip(".") for tok in _TOKEN_RE.findall(summary)}
                if ask is not None and "granted" in ask and said & set(ask["branches"]):
                    waiting.remove(ask)
                    notes.append("%s: LAND END from %s closes granted ask %s" % (did, sender, ask["id"]))
                else:
                    notes.append("%s: LAND END from %s, not holding" % (did, sender))
            else:
                holders.remove(held)
    holding = {h["project"] for h in holders}
    reserved = 0
    for w in waiting:
        w["holding"] = w["project"] in holding
        if "granted" in w:
            granted_at = _epoch(w.get("granted_t", ""))
            if now is not None and granted_at is not None and now - granted_at > GRANT_TTL_SEC:
                w["grant_lapsed"] = True
            elif not w["holding"]:
                reserved += 1
    nxt = next((w["project"] for w in waiting
                if not w["holding"] and "granted" not in w), None)
    slots_free = max(0, max_holders - len(holders) - reserved)
    return {"holders": holders, "waiting": waiting, "next": nxt,
            "slots_free": slots_free, "may_start": nxt is not None and slots_free > 0,
            "max_holders": max_holders, "notes": notes}
