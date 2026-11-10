"""relay.py read --brief and --since, inbox --brief, and the stall finder."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import TempRepo
from test_inbox import req

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from crosstalk.stalls import find_stalls  # noqa: E402


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")
        self.r.write_log("beta", [req(1, "beta", "beta-r1", to=["alpha"]),
                                  req(2, "beta", "beta-r2", to=["alpha"], t="2026-10-22T18:00:00Z")])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def cli(self, *args):
        p = subprocess.run([sys.executable, self.script] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, cwd="/")
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_inbox_brief_one_line_per_request(self):
        lines = self.cli("inbox", "alpha", "--brief").splitlines()
        self.assertEqual(len([ln for ln in lines if " request beta " in ln]), 2)
        self.assertIn("help beta-r2", lines[0])
        self.assertEqual(lines[-1], "adopt 0, stale locks 0")

    def test_read_brief_oldest_first(self):
        lines = self.cli("read", "--brief").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("beta-r1", lines[0])
        self.assertIn("beta-r2", lines[1])

    def test_read_since_keeps_later_rows_in_json(self):
        out = json.loads(self.cli("read", "--since", "2026-10-22T00:00"))
        self.assertEqual(sorted(out["collections"]["messages"]), ["beta-r2"])


def transcript(path, rows, age):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    then = time.time() - age
    os.utime(str(path), (then, then))


CALL = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": "secret-looking text"}}]}}
RESULT = {"type": "user", "message": {"content": [{"type": "tool_result"}]}}


class StallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hung_subagent_found_without_its_input(self):
        transcript(self.root / "-a-gamma/s1/subagents/agent-1.jsonl", [RESULT, CALL], 3600)
        found = find_stalls(self.root, 30, time.time())
        self.assertEqual([(s["project"], s["tool"]) for s in found], [("gamma", "Bash")])
        self.assertEqual(found[0]["minutes"], 60)
        self.assertNotIn("secret", json.dumps(found))

    def test_answered_call_is_not_a_stall(self):
        transcript(self.root / "-a-b/s1.jsonl", [CALL, RESULT], 3600)
        self.assertEqual(find_stalls(self.root, 30, time.time()), [])

    def test_recent_call_is_not_a_stall(self):
        transcript(self.root / "-a-b/s1.jsonl", [CALL], 60)
        self.assertEqual(find_stalls(self.root, 30, time.time()), [])

    def test_old_files_are_left_out(self):
        transcript(self.root / "-a-b/s1.jsonl", [CALL], 86400)
        self.assertEqual(find_stalls(self.root, 30, time.time()), [])

    def test_dashed_project_keeps_its_whole_name(self):
        transcript(self.root / "-a-GitHub-epsilon-kit/s1.jsonl", [CALL], 3600)
        found = find_stalls(self.root, 30, time.time(), ids=["kit", "epsilon-kit"])
        self.assertEqual([s["project"] for s in found], ["epsilon-kit"])
        # without the roster's ids the name is the text after the last dash
        self.assertEqual([s["project"] for s in find_stalls(self.root, 30, time.time())], ["kit"])

    def test_torn_last_row_falls_back_to_the_row_before(self):
        path = self.root / "-a-b/s1.jsonl"
        transcript(path, [CALL], 3600)
        with path.open("a") as f:
            f.write("{torn")
        then = time.time() - 3600
        os.utime(str(path), (then, then))
        self.assertEqual(len(find_stalls(self.root, 30, time.time())), 1)
