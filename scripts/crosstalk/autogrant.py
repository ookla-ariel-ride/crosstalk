"""The auto-granter: posts the coordinator's GRANTED for the next land turn.

The server grants a free slot on the build host itself instead of waiting for
the coordinator's session. The coordinator is the roster's `relay`; with none
set the granter does nothing. One pass (Granter.poll_once):

1. The land queue (landqueue.land_queue, with `now`) must have `may_start`:
   a waiting ask that is not granted, from a project that is not holding,
   and a slot that no holder or granted ask uses.
2. Weekly usage, from the newest file that `usage.glob` matches (config.py),
   must be readable, fresh (`usage.maxAgeMin`) and under `usage.limit`. With no
   glob set this check is skipped.
3. The build host must answer a probe (buildhost.probe) with a 1-minute load
   under `autogrant.loadLimit`. A probe result is reused for PROBE_SEC. With
   no build host set (`buildHost.ssh`) this check is skipped.

Then it replies to the ask as the coordinator with a GRANTED row that names
the branches, the load, the usage and the other holders. Anything it cannot
decide (a project asking to go to the front, a lapsed grant) stays with the
coordinator's session. A file at PAUSE_PATH (~/.crosstalk-autogrant-off)
pauses it; each pass checks for the file.
"""
import glob
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional, Tuple

from . import buildhost, config
from .consults import post_reply
from .landqueue import land_queue
from .participants import load_roster

PROBE_SEC = 60.0
POLL_SEC = 10.0
PAUSE_PATH = Path.home() / ".crosstalk-autogrant-off"


def _warn(msg: str) -> None:
    print("crosstalk autogrant: " + msg, file=sys.stderr)


def read_usage(pattern: str, now: Optional[float] = None,
               max_age: float = 45 * 60) -> Tuple[Optional[float], str]:
    """(weeklyUsage, why) from the newest matching file; None when missing, stale or unreadable."""
    now = time.time() if now is None else now
    files = []
    for p in glob.glob(os.path.expanduser(pattern)):
        try:
            files.append((os.path.getmtime(p), p))
        except OSError:
            continue
    if not files:
        return None, "no usage file"
    mtime, path = max(files)
    if now - mtime > max_age:
        return None, "usage file is %d minutes old" % ((now - mtime) // 60)
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8")).get("weeklyUsage")
    except (OSError, ValueError, AttributeError) as e:
        return None, "usage file unreadable (%s)" % e.__class__.__name__
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "usage file has no weeklyUsage"
    return float(value), ""


def grant_text(ask: dict, holders: list, load: Optional[float], usage: Optional[float]) -> Tuple[str, str]:
    """(summary, body) of the GRANTED reply. A load or usage of None was not checked and is left out."""
    branches = ", ".join(ask.get("branches") or []) or "its branches"
    summary = "GRANTED %s %s" % (ask["project"], branches)
    others = "; ".join("%s %s" % (h["project"], ", ".join(h.get("branches") or []))
                       for h in holders) or "none"
    facts = []
    if load is not None:
        facts.append("Build host load %.1f" % load)
    if usage is not None:
        facts.append("weekly usage %g" % usage)
    facts.append("other holders: %s" % others)
    line = ", ".join(facts)
    body = ("GRANTED %s %s: one land run on the build host (auto-granted by the Crosstalk server). "
            "%s. Post LAND START and LAND END. No answer needed."
            % (ask["project"], branches, line[:1].upper() + line[1:]))
    return summary, body


class Granter:
    """Grants the next land turn when the queue, usage and the build host's load allow it.

    `roster` returns the current Roster (the default reads participants.json).
    `probe` and `usage` replace the configured checks; left as None, each check
    runs only when config.json sets its source.
    """

    def __init__(self, root: Path, snapshot: Callable[[], Tuple[dict, dict]],
                 probe: Optional[Callable[[], dict]] = None,
                 usage: Optional[Callable[[], Tuple[Optional[float], str]]] = None,
                 post: Callable[..., object] = post_reply,
                 clock: Callable[[], float] = time.time,
                 pause_path: Optional[Path] = PAUSE_PATH, poll_sec: float = POLL_SEC,
                 roster: Optional[Callable[[], object]] = None):
        self.root = Path(root)
        self.snapshot = snapshot
        self.probe = probe
        self.usage = usage
        self.post = post
        self.clock = clock
        self.pause_path = pause_path
        self.poll_sec = poll_sec
        self.roster = roster or (lambda: load_roster(self.root / "participants.json"))
        self._probed = None  # type: Optional[Tuple[float, dict]]
        self._said = ""
        self._stop = threading.Event()
        self._thread = None  # type: Optional[threading.Thread]

    def _say(self, msg: str) -> None:
        """Warn once per distinct reason, so a long wait logs one line."""
        if msg != self._said:
            self._said = msg
            _warn(msg)

    def _load(self, now: float, host: str) -> Optional[dict]:
        """The probe to judge by, or None when no build host is set and no probe was given."""
        if self.probe is None and not host:
            return None
        if self._probed is None or now - self._probed[0] >= PROBE_SEC:
            self._probed = (now, self.probe() if self.probe is not None else buildhost.probe(host))
        return self._probed[1]

    def _usage(self, cfg: dict) -> Tuple[Optional[float], str, bool]:
        """(usage, why, checked)."""
        if self.usage is not None:
            return self.usage() + (True,)
        if not cfg["glob"]:
            return None, "", False
        return read_usage(cfg["glob"], max_age=cfg["maxAgeMin"] * 60) + (True,)

    def poll_once(self) -> Optional[str]:
        """One pass; returns the ask id it granted, or None."""
        if self.pause_path is not None and self.pause_path.exists():
            self._say("paused (%s exists)" % self.pause_path)
            return None
        roster = self.roster()
        poster = config.coordinator(roster)
        if not poster:
            self._say("off: the roster names no coordinator (relay in participants.json)")
            return None
        cfg = config.of(roster)
        now = self.clock()
        docs, entries = self.snapshot()
        q = land_queue(docs, projects=entries, now=now, coordinator=poster)
        if not q["may_start"]:
            self._said = ""
            return None
        ask = next(w for w in q["waiting"] if w["project"] == q["next"])
        usage, why, checked = self._usage(cfg["usage"])
        limit = cfg["usage"]["limit"]
        if checked and usage is None:
            self._say("not granting %s: %s" % (ask["id"], why))
            return None
        if checked and usage >= limit:
            self._say("not granting %s: weekly usage %g (limit %g)" % (ask["id"], usage, limit))
            return None
        load = None
        p = self._load(now, cfg["buildHost"]["ssh"])
        if p is not None:
            load = (p.get("load") or [None])[0] if p.get("reachable") else None
            load_limit = cfg["autogrant"]["loadLimit"]
            if load is None:
                self._say("not granting %s: build host not reachable or load unknown" % ask["id"])
                return None
            if load >= load_limit:
                self._say("not granting %s: build host load %.1f (limit %g)" % (ask["id"], load, load_limit))
                return None
        summary, body = grant_text(ask, q["holders"], load, usage)
        self.post(self.root, poster, ask["id"], summary, body=body)
        self._probed = None  # the next grant probes afresh
        self._said = ""
        _warn("granted %s (%s)" % (ask["id"], summary))
        return ask["id"]

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                traceback.print_exc(file=sys.stderr)
            self._stop.wait(self.poll_sec)

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="crosstalk-autogrant",
                                            daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
            self._thread = None
