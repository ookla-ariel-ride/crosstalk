"""Which project adopted which shared standard (docs/adoption.md).

A standard is an agreement ready for other projects, or a ruling for all
projects (from the install's `rulingsSince` on, when config.json sets one).
Each project records its own state; no record is a gap. The coordinator (the
roster's `relay`) issues the standards and adopts none.
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from . import config

STATES = ("adopted", "planned", "not-applicable", "set-aside")
OPEN_AGREEMENT = ("ready-to-adopt", "landed-builder")


def projects(roster) -> List[str]:
    return sorted(pid for pid, p in roster.participants.items()
                  if p.kind == "project" and p.trust == "approved" and pid != config.coordinator(roster))


def adoption_id(from_: str, item: str) -> str:
    return "%s-adopt-%s" % (from_, re.sub(r"[^a-z0-9]+", "-", item.lower()).strip("-"))


def standards(docs, roster) -> dict:
    """{item id: {type, summary, needs: [project ids]}} for every standard that projects must adopt."""
    everyone = projects(roster)
    out = {}
    for (coll, did), doc in docs.items():
        if not isinstance(doc, dict):
            continue
        if coll == "agreements" and doc.get("status") in OPEN_AGREEMENT and not doc.get("hold"):
            adopter = doc.get("adopter")
            needs = [p for p in everyone if p != doc.get("builder")] if adopter == "all" else \
                [adopter] if adopter in everyone and adopter != doc.get("builder") else []
        elif coll == "decisions" and doc.get("project") in ("all", "both") \
                and str(doc.get("t") or doc.get("date") or "") >= config.of(roster)["rulingsSince"]:
            needs = everyone
        else:
            continue
        if needs:
            out[did] = {"type": coll[:-1], "summary": doc.get("summary"), "needs": needs}
    return out


def _states(docs) -> dict:
    """Commits that `relay.py adopt` put on an agreement count as adopted; an explicit record replaces them."""
    out = {}
    for (coll, did), d in docs.items():
        if coll == "agreements" and isinstance(d, dict) and isinstance(d.get("commits"), dict):
            for pid, shas in d["commits"].items():
                if shas and pid != d.get("builder"):
                    out[(pid, did)] = {"from": pid, "item": did, "state": "adopted", "ref": shas[-1] if isinstance(shas, list) else shas}
    out.update({(d["from"], d["item"]): d for (coll, _), d in docs.items()
                if coll == "adoptions" and isinstance(d, dict) and d.get("from") and d.get("item")})
    return out


def gaps(docs, roster, pid: str) -> list:
    """Standards that `pid` must adopt and has no record for, oldest ids first."""
    states = _states(docs)
    return [{"item": i, "type": s["type"], "summary": s["summary"]}
            for i, s in sorted(standards(docs, roster).items())
            if pid in s["needs"] and (pid, i) not in states]


def summary(docs, roster) -> dict:
    """Counts per project: one per state, plus `open` for standards with no record."""
    states, std = _states(docs), standards(docs, roster)
    out = {}
    for pid in projects(roster):
        row = dict.fromkeys(STATES + ("open",), 0)
        for item, s in std.items():
            if pid in s["needs"]:
                row[states[(pid, item)]["state"] if (pid, item) in states else "open"] += 1
        out[pid] = row
    return out


def record(root: Path, from_: str, items: Iterable[str], state: str, ref: Optional[str] = None,
           note: Optional[str] = None, roster=None, docs=None) -> list:
    """Record one state for each item; refuses every item before writing any if one is unknown."""
    from .logs import append_line
    if state not in STATES:
        raise ValueError("--state must be one of %s" % ", ".join(STATES))
    items = list(items)
    known = {did for (coll, did) in docs if coll in ("agreements", "decisions")}
    unknown = [i for i in items if i not in known]
    if unknown:
        raise ValueError("no agreement or decision with id: %s" % ", ".join(unknown))
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for item in items:
        doc = {"from": from_, "item": item, "state": state, "at": at}
        if ref:
            doc["ref"] = ref
        if note:
            doc["note"] = note
        out.append(append_line(root, from_, "set", "adoptions", adoption_id(from_, item), doc, roster=roster))
    return out
