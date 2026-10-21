"""Load and validate participants.json."""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

ID_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*$")
KINDS = ("project", "agent", "reviewer", "researcher", "tool", "person")
TRUSTS = ("approved", "quarantined")


@dataclass
class Participant:
    id: str
    name: str
    kind: str
    color: Optional[str] = None
    repo: Optional[str] = None
    parent: Optional[str] = None
    checkInMin: Optional[int] = None
    trust: str = "quarantined"


@dataclass
class Roster:
    page: str
    relay: str
    participants: Dict[str, Participant]

    def repo_for(self, pid: str) -> Optional[str]:
        p = self.participants.get(pid)
        if p is None:
            return None
        if p.repo:
            return p.repo
        if p.parent:
            return self.repo_for(p.parent)
        return None


def _parse(entry: dict, index: int) -> Participant:
    if not isinstance(entry, dict):
        raise ValueError("participants[%d]: not an object" % index)
    pid = entry.get("id")
    if not isinstance(pid, str) or not ID_RE.fullmatch(pid):
        raise ValueError("participants[%d]: bad id %r" % (index, pid))
    if not isinstance(entry.get("name"), str) or not entry["name"]:
        raise ValueError("participant %s: missing name" % pid)
    kind = entry.get("kind")
    if kind not in KINDS:
        raise ValueError("participant %s: bad kind %r" % (pid, kind))
    trust = entry.get("trust", "quarantined")
    if trust not in TRUSTS:
        raise ValueError("participant %s: bad trust %r" % (pid, trust))
    parent = entry.get("parent")
    if kind == "agent" and not parent:
        raise ValueError("participant %s: kind agent needs parent" % pid)
    check = entry.get("checkInMin")
    if check is not None and (isinstance(check, bool) or not isinstance(check, int) or check <= 0):
        raise ValueError("participant %s: bad checkInMin %r" % (pid, check))
    return Participant(id=pid, name=entry["name"], kind=kind, color=entry.get("color"),
                       repo=entry.get("repo"), parent=parent, checkInMin=check, trust=trust)


def load_roster(path: Path) -> Roster:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError("%s: invalid JSON: %s" % (path, e))
    if not isinstance(raw, dict) or not isinstance(raw.get("participants"), list):
        raise ValueError("%s: expected an object with a participants list" % path)
    people = {}  # type: Dict[str, Participant]
    for i, entry in enumerate(raw["participants"]):
        p = _parse(entry, i)
        if p.id in people:
            raise ValueError("participant %s: duplicate id" % p.id)
        people[p.id] = p
    for p in people.values():
        if p.parent and p.parent not in people:
            raise ValueError("participant %s: parent %s not in roster" % (p.id, p.parent))
    relay = raw.get("relay", "")
    if relay and relay not in people:
        raise ValueError("relay %s is not a participant" % relay)
    return Roster(page=raw.get("page", ""), relay=relay, participants=people)
