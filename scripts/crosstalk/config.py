"""An install's own settings: `config.json` beside participants.json.

The file is local and git-ignored, like the roster. A missing file, a missing
key or a value of the wrong type reads as the default below, so a new clone
runs with no config at all. `config.example.json` shows every key. The file
holds no secret.

    repoUrl             This code's repo page, such as https://example.com/me/crosstalk.
                        The page links its version stamp to <repoUrl>/commit/<hash>;
                        empty shows the hash as plain text.
    rulingsSince        A decision for all projects is a standard to adopt when its
                        time is at or after this (adoption.py); empty counts all.
    broadcastWakesFrom  A peer's request with no `to` and no mention goes to every
                        approved project when its time is at or after this
                        (requests.py); empty wakes for all.
    marks_since         A post expects a mark only when its time is at or after this
                        ISO time (marks.py); empty, or a value that is not a time,
                        means only `must_read` posts expect one. `relay.py check`
                        warns on the latter.
    buildHost.ssh       The build host's ssh alias (buildhost.py); empty means no
                        build host, and nothing probes.
    buildHost.loadHigh, loadClear, diskLowGiB, diskClearGiB
                        The thresholds of the build host's busy and disk-low flags.
    autogrant.loadLimit The auto-granter posts no GRANTED at this 1-minute load.
    usage.glob          The files that hold weekly usage as {"weeklyUsage": n}
                        (~ is the home folder); empty turns the usage check off,
                        and the page's health line (vitals.py) shows no usage.
    usage.limit         No GRANTED at this usage or higher; the usage-guard job
                        winds down here.
    usage.stopLimit     The usage-guard job stops all work here.
    usage.graceMin      The usage-guard job also stops this many minutes after its
                        wind-down began; 0 means no such clock.
    usage.maxAgeMin     A usage file older than this is not trusted.
    reviews.reviewer    The key of the reviewing tool's position in an override
                        (reviews.py).
    modelFamilies       [[pattern, family], ...]: extra rows for the page's model
                        family table. A value that is not a list of two-string
                        pairs reads as the empty list.

The coordinator is not here: it is the roster's `relay` field (participants.py).
"""
import copy
import json
from pathlib import Path

FILE = "config.json"

DEFAULTS = {
    "repoUrl": "",
    "rulingsSince": "",
    "broadcastWakesFrom": "",
    "marks_since": "",
    "buildHost": {"ssh": "", "loadHigh": 12.0, "loadClear": 10.0, "diskLowGiB": 50.0, "diskClearGiB": 55.0},
    "autogrant": {"loadLimit": 12.0},
    "usage": {"glob": "", "limit": 90.0, "stopLimit": 95.0, "graceMin": 0.0, "maxAgeMin": 45.0},
    "reviews": {"reviewer": "codex"},
    "modelFamilies": [],  # a list of [pattern, family] string pairs
}


def _pairs(value) -> bool:
    return isinstance(value, list) and all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in value)


def _merge(defaults: dict, raw) -> dict:
    out = copy.deepcopy(defaults)
    if not isinstance(raw, dict):
        return out
    for key, default in defaults.items():
        value = raw.get(key)
        if isinstance(default, dict):
            out[key] = _merge(default, value)
        elif isinstance(default, float):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[key] = float(value)
        elif isinstance(default, list):
            if _pairs(value):
                out[key] = copy.deepcopy(value)
        elif isinstance(value, str):
            out[key] = value.strip()
    return out


def defaults() -> dict:
    return copy.deepcopy(DEFAULTS)


def load(root) -> dict:
    """The settings of the install at root; the defaults when config.json is missing or unreadable."""
    try:
        raw = json.loads((Path(root) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    return _merge(DEFAULTS, raw)


def of(roster) -> dict:
    """The settings a roster carries (load_roster reads them); the defaults for any other object."""
    cfg = getattr(roster, "config", None)
    return cfg if isinstance(cfg, dict) else DEFAULTS


def coordinator(roster) -> str:
    """The coordinator's participant id: the roster's `relay`, or "" when none is set.

    The coordinator gets the TURN ASK messages and the land reports, grants land
    turns, and posts the reviews and the build host's state. With none set those
    features are off: see docs/operations.md, "The coordinator".
    """
    relay = getattr(roster, "relay", "")
    return relay if isinstance(relay, str) else ""
