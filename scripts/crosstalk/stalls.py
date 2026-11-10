"""Find agents stuck on a tool call, from Claude Code's transcripts.

A stall: the last transcript row is an assistant tool
call with no result, and the file has not moved for the given minutes. Only each
file's tail is read, and only the tool's name is reported, never its input.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

TAIL_BYTES = 262144
RECENT_HOURS = 6  # older than this the agent is gone, not stalled


def _last_row(path: Path) -> Optional[dict]:
    """The file's last whole JSON row, or None."""
    with path.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - TAIL_BYTES))
        lines = f.read().split(b"\n")
    for raw in reversed(lines):
        if raw.strip():
            try:
                row = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(row, dict):
                return row
    return None


def _open_tool(row: dict) -> Optional[str]:
    """The name of the tool call this row ends on, if it is an assistant row."""
    message = row.get("message")
    if row.get("type") != "assistant" or not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    calls = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    return str(calls[-1].get("name", "?")) if calls else None


def _project_name(folder, ids=()):
    """Name the project from a transcript folder (its path, with - for each /).

    The longest roster id the folder ends on wins, so an id with a dash is read
    whole; with no match it is the text after the last dash.
    """
    known = [i for i in ids if folder == i or folder.endswith("-" + i)]
    if known:
        return max(known, key=len)
    return folder.rsplit("-", 1)[-1] if folder else "?"


def find_stalls(root: Path, minutes: int, now: float, ids=()) -> List[Dict]:
    """Every transcript under root that ends on an unanswered tool call. `ids` are the roster's ids."""
    found = []  # type: List[Dict]
    for path in sorted(root.glob("*/*.jsonl")) + sorted(root.glob("*/*/subagents/*.jsonl")):
        try:
            age = now - path.stat().st_mtime
            if not minutes * 60 <= age <= RECENT_HOURS * 3600:
                continue
            row = _last_row(path)
        except OSError:
            continue
        tool = _open_tool(row) if row else None
        if tool:
            sub = path.parent.name == "subagents"
            project = (path.parents[2] if sub else path.parent).name
            found.append({"project": _project_name(project, ids),
                          "session": (path.parents[1].name if sub else path.stem)[:8],
                          "agent": path.stem[:24] if sub else "main",
                          "tool": tool, "minutes": int(age // 60)})
    return found
