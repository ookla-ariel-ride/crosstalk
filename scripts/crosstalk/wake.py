"""Per-participant wake config: the command that wakes a project's agent."""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULTS = {"timeoutSec": 900, "cooldownSec": 300, "dailyCap": 30}
KEYS = ("command", "cwd") + tuple(DEFAULTS)


@dataclass
class WakeConfig:
    command: List[str]
    cwd: str
    timeoutSec: int
    cooldownSec: int
    dailyCap: int


def wake_problem(pid: str, raw) -> Optional[str]:
    """The reason a wake value is invalid, or None when it is acceptable.

    A wake is an object with a non-empty command (list of non-empty strings)
    and a cwd naming an existing directory. timeoutSec, cooldownSec and
    dailyCap are optional positive ints. Unknown keys are rejected.
    """
    if not isinstance(raw, dict):
        return "participant %s: wake must be an object" % pid
    unknown = sorted(k for k in raw if k not in KEYS)
    if unknown:
        return "participant %s: wake has unknown key %s" % (pid, ", ".join(repr(k) for k in unknown))
    command = raw.get("command")
    if (not isinstance(command, list) or not command
            or not all(isinstance(a, str) and a for a in command)):
        return "participant %s: wake command must be a non-empty list of non-empty strings" % pid
    cwd = raw.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return "participant %s: wake cwd must be a directory path" % pid
    if not Path(cwd).is_dir():
        return "participant %s: wake cwd %r is not an existing directory" % (pid, cwd)
    for key in DEFAULTS:
        v = raw.get(key)
        if key in raw and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            return "participant %s: wake %s must be a positive integer, got %r" % (pid, key, v)
    return None


def wake_config(pid: str, raw) -> Optional[WakeConfig]:
    """The WakeConfig for a raw wake value, or None when absent or invalid."""
    if raw is None or wake_problem(pid, raw) is not None:
        return None
    opts = {k: raw.get(k, d) for k, d in DEFAULTS.items()}
    return WakeConfig(command=list(raw["command"]), cwd=raw["cwd"], **opts)


def load_wake(root: Path) -> Tuple[Dict[str, WakeConfig], Dict[str, str]]:
    """Read root/participants.json: (valid configs by pid, problems by pid).

    Raises ValueError or OSError when the file is unreadable.
    """
    path = Path(root) / "participants.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError("%s: invalid JSON: %s" % (path, e))
    entries = raw.get("participants") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ValueError("%s: expected an object with a participants list" % path)
    configs = {}  # type: Dict[str, WakeConfig]
    problems = {}  # type: Dict[str, str]
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        pid = entry["id"]
        if entry.get("wake") is None:
            continue
        problem = wake_problem(pid, entry["wake"])
        if problem:
            problems[pid] = problem
        else:
            configs[pid] = wake_config(pid, entry["wake"])
    return configs, problems
