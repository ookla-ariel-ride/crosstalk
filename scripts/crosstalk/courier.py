"""Courier state: log/relay-state.json (bookmarks, versions, participants hash per page)."""
import json
import os
import tempfile
from pathlib import Path

STATE_FILE = "relay-state.json"


def _path(root: Path) -> Path:
    return Path(root) / "log" / STATE_FILE


def load_state(root: Path) -> dict:
    """The whole state, keyed by page URL. A missing file means {}."""
    path = _path(root)
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("%s: expected a JSON object" % path)
    return state


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to path atomically: temp file in the same dir, fsync, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save_state(root: Path, state: dict) -> None:
    """Write the state atomically."""
    atomic_write_text(_path(root), json.dumps(state, indent=2, sort_keys=True) + "\n")
