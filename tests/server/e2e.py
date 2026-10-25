#!/usr/bin/env python3
"""End-to-end page harness: a real serve process over a temp log, plus the page.

Seeds a temp repo (roster + a few messages, one from a quarantined sender),
starts the server on an ephemeral port, and prints the URL and the one-time
login PIN. The page scenarios are then run in a browser against that URL (see
run.md). The server keeps running until this process is stopped.

Python 3.9, standard library only.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from crosstalk.serve import start  # noqa: E402

ROSTER = {
    "page": "http://127.0.0.1:8484/",
    "relay": "",
    "participants": [
        {"id": "bewareilive", "name": "BewareILive", "kind": "project",
         "repo": "ookla-ariel-ride/bewareilive", "color": "#b3261e", "colorDark": "#ef6a5f",
         "checkInMin": 30, "trust": "approved"},
        {"id": "vectorcraft", "name": "VectorCraft", "kind": "project",
         "repo": "ookla-ariel-ride/vectorcraft", "color": "#1f5f8b", "colorDark": "#6fb2e2",
         "checkInMin": 30, "trust": "approved"},
        {"id": "codex", "name": "Codex", "kind": "reviewer",
         "color": "#00727a", "colorDark": "#45c2bf", "trust": "approved"},
        {"id": "owner", "name": "Owner", "kind": "person",
         "color": "#6b3fa0", "colorDark": "#b892e6", "trust": "approved"},
        {"id": "qbot", "name": "Q bot", "kind": "tool",
         "color": "#555555", "colorDark": "#888888", "trust": "quarantined"},
    ],
}


def line(seq, frm, collection="messages", id_=None, op="set", data=None,
         t="2026-10-24T18:00:00Z"):
    return {"seq": seq, "t": t, "from": frm, "op": op, "collection": collection,
            "id": id_ if id_ is not None else "%s-%04d" % (frm, seq),
            "data": data if data is not None else {}}


def seed(root):
    (root / "log").mkdir()
    (root / "participants.json").write_text(json.dumps(ROSTER), encoding="utf-8")
    msgs = [
        line(1, "codex", data={"kind": "sync", "from": "codex", "t": "2026-10-24T18:00:00Z",
                               "summary": "Codex checked in"}),
        line(2, "codex", data={"kind": "sync", "from": "codex", "t": "2026-10-24T18:05:00Z",
                               "summary": "Codex second note"}),
        line(1, "bewareilive", data={"kind": "check-in", "from": "bewareilive",
                                     "t": "2026-10-24T18:01:00Z", "summary": "BewareILive check-in"}),
        line(1, "qbot", data={"kind": "sync", "from": "qbot", "t": "2026-10-24T18:02:00Z",
                              "summary": "From a quarantined sender"}),
    ]
    for m in msgs:
        with open(root / "log" / (m["from"] + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(m) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-pin", action="store_true")
    args = ap.parse_args()
    root = Path(tempfile.mkdtemp(prefix="crosstalk-e2e-"))
    seed(root)
    # The server serves index.html from the checkout, so copy it into the temp root.
    shutil.copy(SCRIPTS.parent / "index.html", root / "index.html")
    srv = start(root, port=args.port, host="127.0.0.1", no_pin=args.no_pin)
    print("ROOT=%s" % root, flush=True)
    print("URL=%s" % srv.url, flush=True)
    if srv.pin:
        print("PIN=%s" % srv.pin, flush=True)
    print("READY", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == "__main__":
    main()
