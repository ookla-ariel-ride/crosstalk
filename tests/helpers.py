import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from crosstalk.serve import RunningServer as _RunningServer  # noqa: E402


class RunningServer(_RunningServer):
    """A Crosstalk server on 127.0.0.1 with an ephemeral port, over a temp repo.

    `with RunningServer(root) as srv:` starts the server and shuts it down
    (and its thread) on the way out.
    """

    def __init__(self, root, **kwargs):
        kwargs.setdefault("port", 0)
        kwargs.setdefault("host", "127.0.0.1")
        _RunningServer.__init__(self, root, **kwargs)


SPEC_ROSTER = {
    "page": "http://127.0.0.1:8484/",
    "relay": "bewareilive",
    "participants": [
        {"id": "bewareilive", "name": "BewareILive", "kind": "project", "repo": "ookla-ariel-ride/bewareilive", "color": "#b3261e", "colorDark": "#ef6a5f", "checkInMin": 30, "trust": "approved"},
        {"id": "vectorcraft", "name": "VectorCraft", "kind": "project", "repo": "ookla-ariel-ride/vectorcraft", "color": "#1f5f8b", "colorDark": "#6fb2e2", "checkInMin": 30, "trust": "approved"},
        {"id": "codex", "name": "Codex", "kind": "reviewer", "color": "#00727a", "colorDark": "#45c2bf", "trust": "approved"},
        {"id": "owner", "name": "Owner", "kind": "person", "color": "#6b3fa0", "colorDark": "#b892e6", "trust": "approved"},
    ],
}


class TempRepo:
    """A throwaway repo root with participants.json and log/."""

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="crosstalk-test-"))
        (self.root / "log").mkdir()
        return self

    def __exit__(self, *exc):
        shutil.rmtree(str(self.root), ignore_errors=True)
        return False

    def write_roster(self, data=None):
        path = self.root / "participants.json"
        path.write_text(json.dumps(SPEC_ROSTER if data is None else data), encoding="utf-8")
        return path

    def write_log(self, pid, lines):
        """lines: list of dicts (JSON-encoded) or str (written raw)."""
        path = self.root / "log" / (pid + ".jsonl")
        out = []
        for ln in lines:
            out.append(ln if isinstance(ln, str) else json.dumps(ln))
        path.write_text("".join(x + "\n" for x in out), encoding="utf-8")
        return path


def line(seq, frm, collection="messages", id_=None, op="set", data=None, t="2026-10-21T18:00:00Z"):
    return {"seq": seq, "t": t, "from": frm, "op": op, "collection": collection,
            "id": id_ if id_ is not None else "%s-%04d" % (frm, seq), "data": data if data is not None else {}}
