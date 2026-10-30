"""Load and validate participants.json."""
import itertools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

ID_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*$")
# A name is shown as text on the page, so it must be plain: no angle brackets and no control characters.
NAME_BAD_RE = re.compile(r"[\x00-\x1f\x7f<>]")
# A repo is a single-line owner/repo string: no control characters (which
# includes newlines), no angle brackets, and a sane length.
REPO_BAD_RE = re.compile(r"[\x00-\x1f\x7f<>]")
REPO_MAX = 120
# A harness is the tool a participant's sessions run in (claude-code, codex, pi, ...): a short lowercase id. The
# page knows icons for some; any other id gets a letter badge.
HARNESS_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HARNESS_MAX = 32
# An alias is a short extra @handle for a participant (@bil for BewareILive). These handles are taken by the page.
ALIAS_RESERVED = ("everyone", "all", "channel", "here", "projects", "teams", "reviewers", "owner")
# itemNames maps a progress row's raw item name (lowercase) to the name shown, e.g. centiped -> Centipede.
ITEM_NAME_MAX = 60
# reviewDir is the folder where a project's Codex review hook keeps its reports (reviews.py reads it):
# an absolute path or one starting with ~/, on one line, without control characters.
REVIEW_DIR_MAX = 300
KINDS = ("project", "agent", "reviewer", "researcher", "tool", "person")
TRUSTS = ("approved", "quarantined")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# The page's panel backgrounds in light and dark mode. A participant's color must stand out from them.
PANEL_LIGHT = "#fbfaf8"
PANEL_DARK = "#1d1c1a"
MIN_CONTRAST = 3.0
# Two participants' colors closer than this (CIE76 delta E) read as the same color.
MIN_DELTA_E = 20.0


def repo_problem(pid: str, repo) -> Optional[str]:
    """The reason a repo value is invalid, or None when it is acceptable.

    A repo, when given, must be a non-empty single-line string without control
    characters or angle brackets, and at most REPO_MAX characters.
    """
    if repo is None:
        return None
    if not isinstance(repo, str) or not repo:
        return "participant %s: repo must be a non-empty string" % pid
    if REPO_BAD_RE.search(repo):
        return "participant %s: bad repo %r" % (pid, repo)
    if len(repo) > REPO_MAX:
        return "participant %s: repo too long (%d chars, max %d)" % (pid, len(repo), REPO_MAX)
    return None


def harness_problem(pid: str, harness) -> Optional[str]:
    """The reason a harness value is invalid, or None when it is acceptable (or absent)."""
    if harness is None:
        return None
    if not isinstance(harness, str) or len(harness) > HARNESS_MAX or not HARNESS_RE.fullmatch(harness):
        return "participant %s: bad harness %r (a lowercase id like claude-code, at most %d chars)" % (
            pid, harness, HARNESS_MAX)
    return None


def review_dir_problem(pid: str, value) -> Optional[str]:
    """The reason a reviewDir value is invalid, or None when it is acceptable (or absent)."""
    if value is None:
        return None
    if (not isinstance(value, str) or not value or len(value) > REVIEW_DIR_MAX
            or NAME_BAD_RE.search(value) or not (value.startswith("/") or value.startswith("~/"))):
        return ("participant %s: bad reviewDir %r (an absolute path or one starting with ~/, "
                "at most %d chars)" % (pid, value, REVIEW_DIR_MAX))
    return None


@dataclass
class Participant:
    id: str
    name: str
    kind: str
    color: Optional[str] = None
    colorDark: Optional[str] = None
    repo: Optional[str] = None
    parent: Optional[str] = None
    checkInMin: Optional[int] = None
    trust: str = "quarantined"
    harness: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    itemNames: Dict[str, str] = field(default_factory=dict)
    reviewDir: Optional[str] = None


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


def _linear(c: int) -> float:
    v = c / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _rgb(color: str):
    return [_linear(int(color[i:i + 2], 16)) for i in (1, 3, 5)]


def _luminance(color: str) -> float:
    r, g, b = _rgb(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two #rrggbb colors."""
    lo, hi = sorted((_luminance(a), _luminance(b)))
    return (hi + 0.05) / (lo + 0.05)


def _lab(color: str):
    r, g, b = _rgb(color)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1.0 / 3) if t > 0.008856 else 7.787 * t + 16.0 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def delta_e(a: str, b: str) -> float:
    """CIE76 color difference between two #rrggbb colors."""
    return sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b))) ** 0.5


def _check_color(pid: str, field: str, value, panel: str) -> str:
    if not isinstance(value, str) or not COLOR_RE.fullmatch(value):
        raise ValueError("participant %s: %s must be #rrggbb, got %r" % (pid, field, value))
    ratio = contrast(value, panel)
    if ratio < MIN_CONTRAST:
        raise ValueError("participant %s: %s %s has contrast %.1f against %s, needs %.1f"
                         % (pid, field, value, ratio, panel, MIN_CONTRAST))
    return value.lower()


def _aliases(pid: str, value) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("participant %s: aliases must be a list" % pid)
    out = []  # type: List[str]
    for a in value:
        if not isinstance(a, str) or not ID_RE.fullmatch(a.lower()):
            raise ValueError("participant %s: bad alias %r" % (pid, a))
        a = a.lower()
        if a in ALIAS_RESERVED or a == pid:
            raise ValueError("participant %s: alias %r is reserved" % (pid, a))
        if a not in out:
            out.append(a)
    return out


def _item_names(pid: str, value) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("participant %s: itemNames must be an object" % pid)
    out = {}  # type: Dict[str, str]
    for raw, shown in value.items():
        ok = (isinstance(raw, str) and raw.strip() and isinstance(shown, str) and shown.strip()
              and len(shown) <= ITEM_NAME_MAX and not NAME_BAD_RE.search(shown))
        if not ok:
            raise ValueError("participant %s: bad itemNames entry %r: %r" % (pid, raw, shown))
        out[raw.strip().lower()] = shown
    return out


def _parse(entry: dict, index: int) -> Participant:
    if not isinstance(entry, dict):
        raise ValueError("participants[%d]: not an object" % index)
    pid = entry.get("id")
    if not isinstance(pid, str) or not ID_RE.fullmatch(pid):
        raise ValueError("participants[%d]: bad id %r" % (index, pid))
    if not isinstance(entry.get("name"), str) or not entry["name"]:
        raise ValueError("participant %s: missing name" % pid)
    if NAME_BAD_RE.search(entry["name"]):
        raise ValueError("participant %s: bad name %r" % (pid, entry["name"]))
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
    problem = (repo_problem(pid, entry.get("repo")) or harness_problem(pid, entry.get("harness"))
               or review_dir_problem(pid, entry.get("reviewDir")))
    if problem:
        raise ValueError(problem)
    color = _check_color(pid, "color", entry.get("color"), PANEL_LIGHT)
    color_dark = _check_color(pid, "colorDark", entry.get("colorDark"), PANEL_DARK)
    return Participant(id=pid, name=entry["name"], kind=kind, color=color, colorDark=color_dark,
                       repo=entry.get("repo"), parent=parent, checkInMin=check, trust=trust,
                       harness=entry.get("harness"), aliases=_aliases(pid, entry.get("aliases")),
                       itemNames=_item_names(pid, entry.get("itemNames")),
                       reviewDir=entry.get("reviewDir"))


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
    # An alias must name one participant only, and never another participant's id.
    taken = {}  # type: Dict[str, str]
    for p in people.values():
        for a in p.aliases:
            if a in people or a in taken:
                raise ValueError("participant %s: alias %r already names %s" % (p.id, a, taken.get(a, a)))
            taken[a] = p.id
    # Every participant's chats need their own color, in both themes.
    for a, b in itertools.combinations(people.values(), 2):
        for field in ("color", "colorDark"):
            d = delta_e(getattr(a, field), getattr(b, field))
            if d < MIN_DELTA_E:
                raise ValueError("participants %s and %s: %s too close (%s, %s; delta E %.0f, needs %.0f)"
                                 % (a.id, b.id, field, getattr(a, field), getattr(b, field), d, MIN_DELTA_E))
    relay = raw.get("relay", "")
    if relay and relay not in people:
        raise ValueError("relay %s is not a participant" % relay)
    return Roster(page=raw.get("page", ""), relay=relay, participants=people)
