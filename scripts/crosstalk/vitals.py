"""The health numbers the page shows at the foot of its left column (/api/vitals).

read() returns:

    disk_free_gb  The free space of the volume that holds the data root, in
                  whole GB (10^9 bytes), from shutil.disk_usage.
    usage         The `weeklyUsage` of the newest file that matches the config
                  key `usage.glob`, rounded to a whole number. It is read by
                  autogrant.read_usage, the same rule the auto-granter uses, so
                  a file older than `usage.maxAgeMin` gives null. null too when
                  `usage.glob` is empty or no file can be read.
    usage_limit   `usage.limit` as a whole number; null when `usage.glob` is empty.
    build_host    {"reachable", "load", "t"} from the state that buildhost.py
                  stored on its last post, or null when no build host is set
                  (`buildHost.ssh`) or no state is stored yet. `load` and `t`
                  are null when the state holds no value for them (a state
                  written before they were stored).
"""
import json
import shutil
from pathlib import Path
from typing import Optional

from . import buildhost
from .autogrant import read_usage

GB = 10 ** 9


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _usage(cfg: dict, now: Optional[float]) -> Optional[int]:
    if not cfg["glob"]:
        return None
    value, _why = read_usage(cfg["glob"], now=now, max_age=cfg["maxAgeMin"] * 60)
    return int(round(value)) if value is not None else None


def _build_host(cfg: dict, state_path) -> Optional[dict]:
    if not cfg["buildHost"]["ssh"] or state_path is None:
        return None
    try:
        raw = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("reachable"), bool):
        return None
    load = raw.get("load")
    t = raw.get("t")
    return {"reachable": raw["reachable"], "load": float(load) if _number(load) else None,
            "t": t if isinstance(t, str) else None}


def read(root, cfg: dict, buildhost_state=None, now: Optional[float] = None) -> dict:
    """The health numbers; see the module docstring. `buildhost_state` is the
    build host's state file (default: buildhost.state_path_from_env())."""
    if buildhost_state is None:
        buildhost_state = buildhost.state_path_from_env()
    usage = cfg["usage"]
    return {"disk_free_gb": int(shutil.disk_usage(str(root)).free // GB),
            "usage": _usage(usage, now),
            "usage_limit": int(round(usage["limit"])) if usage["glob"] else None,
            "build_host": _build_host(cfg, buildhost_state)}
