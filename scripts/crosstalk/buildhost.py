"""The build host's state, posted when it changes.

The build host is the machine the projects land on. Its ssh alias is
`buildHost.ssh` in config.json (config.py); with none set nothing probes.

probe() runs one ssh (`uptime; df -Pk "$HOME"`) and returns whether the host
answered, its load averages and the free space of its home volume in GiB.

The Poller compares a probe with the last posted state and posts one
`kind: "alert"` message as the coordinator (the roster's `relay`; id
`<coordinator>-buildhost-<UTC time>`, no `to`, so it notifies nobody) only when
one of these changes:

- reachable <-> unreachable;
- busy: the 1-minute load reaches `loadHigh`, and stays busy until it falls
  under `loadClear` (hysteresis, so a load near the limit does not flap);
- disk low: free space under `diskLowGiB`, clear again at `diskClearGiB`.

The four thresholds are `buildHost` keys in config.json.

While unreachable the busy and disk flags keep their last values; the
recovery post carries the fresh ones. The state before the first poll counts
as reachable, not busy, disk fine, so a first poll posts only when something
is wrong. Unreachable and disk-low posts are `important`; the others are not.

State (default ~/.crosstalk-buildhost-state.json, 0600, `.lock` beside it;
CROSSTALK_BUILDHOST_STATE moves it):
    {"reachable": bool, "busy": bool, "disk_low": bool, "posted": "<id>", "load": float}
`load` is the 1-minute load of the last post while reachable; vitals.py reads it.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

from . import config
from .files import atomic_write_text
from .logs import append_line
from .reviews import _locked, _one_line

SOURCE = "build-host"
TIMEOUT = 8
ERROR_MAX = 160
NO_HOST = "no build host set (buildHost.ssh in config.json)"
DEFAULT_STATE = Path.home() / ".crosstalk-buildhost-state.json"
REMOTE = 'uptime; df -Pk "$HOME"'
LOAD_RE = re.compile(r"load averages?:\s*([\d.]+),?\s+([\d.]+),?\s+([\d.]+)")

Runner = Callable[[Sequence[str], float], Tuple[int, str, str]]


def state_path_from_env() -> Path:
    env = os.environ.get("CROSSTALK_BUILDHOST_STATE")
    return Path(env) if env else DEFAULT_STATE


def run_ssh(argv: Sequence[str], timeout: float) -> Tuple[int, str, str]:
    """(returncode, stdout, stderr); 255 with the reason when ssh cannot run or hangs."""
    try:
        p = subprocess.run(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 255, "", "timed out after %ds" % timeout
    except OSError as e:
        return 255, "", str(e)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def probe(host: str, timeout: int = TIMEOUT, runner: Runner = run_ssh) -> dict:
    """{host, reachable, load: [1, 5, 15] | None, free_gib | None, error | None}.

    With an empty host it runs no ssh and reports unreachable with NO_HOST.
    """
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=%d" % timeout, host, REMOTE]
    out = {"host": host, "reachable": False, "load": None, "free_gib": None, "error": None}
    if not host:
        out["error"] = NO_HOST
        return out
    code, stdout, stderr = runner(argv, timeout + 10)
    if code == 255:  # ssh's own failure; another code is the remote command's
        lines = [x for x in stderr.splitlines() if x.strip()]
        out["error"] = _one_line(lines[-1] if lines else "ssh exited %d" % code, ERROR_MAX)
        return out
    out["reachable"] = True
    m = LOAD_RE.search(stdout)
    if m:
        out["load"] = [float(x) for x in m.groups()]
    rows = [x.split() for x in stdout.splitlines() if x.strip()]
    if rows and len(rows[-1]) > 3 and rows[-1][3].isdigit():
        out["free_gib"] = round(int(rows[-1][3]) / (1024.0 * 1024.0), 1)
    if out["load"] is None or out["free_gib"] is None:
        out["error"] = "could not read uptime or df output"
    return out


def _flags(p: dict, prev: dict, limits: dict) -> dict:
    """The state a probe implies, given the previous one (for the hysteresis)."""
    if not p["reachable"]:
        return dict(prev, reachable=False)
    busy, low = prev["busy"], prev["disk_low"]
    if p["load"] is not None:
        busy = p["load"][0] >= limits["loadClear" if busy else "loadHigh"]
    if p["free_gib"] is not None:
        low = p["free_gib"] < limits["diskClearGiB" if low else "diskLowGiB"]
    return {"reachable": True, "busy": busy, "disk_low": low}


def _summary(p: dict, prev: dict, new: dict, limits: dict) -> str:
    if not new["reachable"]:
        return "Build host unreachable: %s" % (p["error"] or "no answer")
    parts = []
    if p["load"] is not None:
        load = "load %.1f" % p["load"][0]
        if new["busy"]:
            load += " (over %g)" % limits["loadHigh"]
        elif prev["busy"]:
            load += " (back under %g)" % limits["loadClear"]
        parts.append(load)
    if p["free_gib"] is not None:
        disk = "%.0f GiB free" % p["free_gib"]
        if new["disk_low"]:
            disk += " (under %g)" % limits["diskLowGiB"]
        parts.append(disk)
    head = "Build host reachable again" if not prev["reachable"] else "Build host"
    return "%s: %s" % (head, ", ".join(parts) or "no figures")


def _load(path: Optional[Path]) -> dict:
    state = {"reachable": True, "busy": False, "disk_low": False}
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        raw = None
    if isinstance(raw, dict):
        state.update({k: raw[k] for k in state if isinstance(raw.get(k), bool)})
        if isinstance(raw.get("posted"), str):
            state["posted"] = raw["posted"]
    return state


class Poller:
    """Probes the build host and posts its state on a change; see the module docstring.

    `host` replaces the config's ssh alias. `prober` returns a probe dict
    (probe()'s shape); `append` writes one line (logs.append_line's signature).
    """

    def __init__(self, root, state_path, host: Optional[str] = None, timeout: int = TIMEOUT,
                 prober: Optional[Callable[[], dict]] = None, clock: Callable[[], float] = time.time,
                 append: Callable[..., object] = append_line):
        self.root = Path(root)
        self.state_path = Path(state_path) if state_path is not None else None
        self.host = host
        self.timeout = timeout
        self.prober = prober
        self.clock = clock
        self.append = append

    def poll(self, roster, dry_run: bool = False) -> dict:
        """One probe. Returns {dry_run, probe, changed, writes}; with dry_run nothing is written.

        ValueError when the roster names no coordinator to post as, or no build host is set.
        """
        if not config.coordinator(roster):
            raise ValueError("no coordinator to post as (relay in participants.json)")
        host = self.host or config.of(roster)["buildHost"]["ssh"]
        if self.prober is None and not host:
            raise ValueError(NO_HOST)
        p = self.prober() if self.prober is not None else probe(host, self.timeout)
        if dry_run or self.state_path is None:
            return self._step(p, _load(self.state_path), roster, dry_run=True)
        with _locked(self.state_path):
            return self._step(p, _load(self.state_path), roster, dry_run=False)

    def _step(self, p: dict, prev: dict, roster, dry_run: bool) -> dict:
        limits = config.of(roster)["buildHost"]
        poster = config.coordinator(roster)
        new = _flags(p, prev, limits)
        changed = sorted(k for k in ("reachable", "busy", "disk_low") if new[k] != prev[k])
        plan = {"dry_run": dry_run, "probe": p, "changed": changed, "writes": []}
        if not changed:
            return plan
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
        did = "%s-buildhost-%s" % (poster, stamp)
        data = {"from": poster, "kind": "alert", "source": SOURCE, "host": p["host"],
                "reachable": p["reachable"], "important": not new["reachable"] or new["disk_low"],
                "summary": _summary(p, prev, new, limits)}
        for key in ("load", "free_gib", "error"):
            if p.get(key) is not None:
                data["freeGiB" if key == "free_gib" else key] = p[key]
        plan["writes"].append({"op": "set", "collection": "messages", "id": did, "data": data})
        if dry_run:
            return plan
        self.append(self.root, poster, "set", "messages", did, data, roster=roster)
        new["posted"] = did
        if p["reachable"] and p["load"] is not None:
            new["load"] = p["load"][0]  # the page's health line (vitals.py) shows it
        atomic_write_text(self.state_path, json.dumps(new, sort_keys=True) + "\n")
        os.chmod(str(self.state_path), 0o600)
        return plan
