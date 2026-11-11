"""The health numbers the page shows at the foot of its left column (/api/vitals).

read() returns:

    disk_free_gb  The free space of the volume that holds the data root, in
                  whole GB (10^9 bytes), from shutil.disk_usage.
    usage         The `weeklyUsage` field of the newest file that matches the
                  config key `usageFile` (a glob; ~ is the home folder),
                  rounded to a whole number. null when the key is empty, no
                  file matches, the newest is older than 10 minutes, or the
                  field is not a number.
    usage_limit   The config key `usageLimit`, a whole number, or null.
    build_host    {"reachable", "load"} from the state that buildhost.py stored
                  on its last post, or null when no build host is set
                  (`buildHost.ssh`) or no state is stored yet. `load` is null
                  when the state holds no number for it.
"""
import glob
import json
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from . import buildhost

USAGE_MAX_AGE_SEC = 10 * 60
GB = 10 ** 9


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _usage(pattern: str, now: float) -> Optional[int]:
    if not pattern:
        return None
    newest, newest_mtime = None, None
    for name in glob.glob(os.path.expanduser(pattern)):
        try:
            mtime = os.stat(name).st_mtime
        except OSError:
            continue
        if newest_mtime is None or mtime > newest_mtime:
            newest, newest_mtime = name, mtime
    if newest is None or now - newest_mtime > USAGE_MAX_AGE_SEC:
        return None
    try:
        raw = json.loads(Path(newest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = raw.get("weeklyUsage") if isinstance(raw, dict) else None
    return int(round(value)) if _number(value) else None


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
    return {"reachable": raw["reachable"], "load": float(load) if _number(load) else None}


def read(root, cfg: dict, buildhost_state=None, now: Optional[float] = None) -> dict:
    """The health numbers; see the module docstring. `buildhost_state` is the
    build host's state file (default: buildhost.state_path_from_env())."""
    if buildhost_state is None:
        buildhost_state = buildhost.state_path_from_env()
    limit = cfg.get("usageLimit")
    return {"disk_free_gb": int(shutil.disk_usage(str(root)).free // GB),
            "usage": _usage(cfg.get("usageFile", ""), time.time() if now is None else now),
            "usage_limit": int(round(limit)) if _number(limit) else None,
            "build_host": _build_host(cfg, buildhost_state)}
