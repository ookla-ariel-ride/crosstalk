import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from crosstalk.logs import append_line  # noqa: E402


def worker(root, n):
    for i in range(n):
        append_line(Path(root), "rev", "set", "messages", "rev-%d-%d" % (_pid(), i), {"i": i})


def _pid():
    import os
    return os.getpid()
