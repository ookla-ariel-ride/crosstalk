"""Per-participant wake config: parsing and validation."""
import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo

from crosstalk.participants import load_roster
from crosstalk.wake import WakeConfig, load_wake, wake_config, wake_problem

REPO = Path(__file__).resolve().parent.parent


class WakeConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="crosstalk-wake-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_minimal(self):
        raw = {"command": ["echo", "{prompt}"], "cwd": self.tmp}
        self.assertIsNone(wake_problem("vectorcraft", raw))
        self.assertEqual(wake_config("vectorcraft", raw),
                         WakeConfig(command=["echo", "{prompt}"], cwd=self.tmp,
                                    timeoutSec=900, cooldownSec=300, dailyCap=30))

    def test_overrides(self):
        raw = {"command": ["echo"], "cwd": self.tmp,
               "timeoutSec": 60, "cooldownSec": 1, "dailyCap": 2}
        cfg = wake_config("vectorcraft", raw)
        self.assertEqual((cfg.timeoutSec, cfg.cooldownSec, cfg.dailyCap), (60, 1, 2))

    def test_none_is_not_configured(self):
        self.assertIsNone(wake_config("vectorcraft", None))

    def test_problems(self):
        ok = {"command": ["echo"], "cwd": self.tmp}
        bad = [
            [],
            {"command": [], "cwd": self.tmp},
            {"command": "echo", "cwd": self.tmp},
            {"command": ["echo", 3], "cwd": self.tmp},
            {"command": ["echo", ""], "cwd": self.tmp},
            {"command": ["echo"]},
            {"command": ["echo"], "cwd": self.tmp + "/nope"},
            dict(ok, timeoutSec=0),
            dict(ok, dailyCap=True),
            dict(ok, cooldownSec="5"),
            dict(ok, shell=True),
        ]
        for raw in bad:
            msg = wake_problem("vectorcraft", raw)
            self.assertIsInstance(msg, str, raw)
            self.assertIn("vectorcraft", msg)
            self.assertIsNone(wake_config("vectorcraft", raw), raw)

    def test_load_roster_tolerates_bad_wake(self):
        data = copy.deepcopy(SPEC_ROSTER)
        for p in data["participants"]:
            if p["id"] == "vectorcraft":
                p["wake"] = {"command": []}
            if p["id"] == "codex":
                p["wake"] = {"command": ["echo"], "cwd": self.tmp}
        with TempRepo() as r:
            r.write_roster(data)
            roster = load_roster(r.root / "participants.json")
            self.assertEqual(roster.participants["vectorcraft"].wake, {"command": []})
            configs, problems = load_wake(r.root)
            self.assertEqual(list(problems), ["vectorcraft"])
            self.assertIn("vectorcraft", problems["vectorcraft"])
            self.assertEqual(list(configs), ["codex"])

    def test_load_wake_unreadable(self):
        with TempRepo() as r:
            with self.assertRaises((ValueError, OSError)):
                load_wake(r.root)

    def test_check_reports_wake_problem(self):
        data = copy.deepcopy(SPEC_ROSTER)
        for p in data["participants"]:
            if p["id"] == "vectorcraft":
                p["wake"] = {"command": []}
        with TempRepo() as r:
            r.write_roster(data)
            shutil.copytree(str(REPO / "scripts"), str(r.root / "scripts"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            p = subprocess.run([sys.executable, str(r.root / "scripts" / "relay.py"), "check"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, cwd="/")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("vectorcraft", p.stderr)
            self.assertIn("wake", p.stderr)


if __name__ == "__main__":
    unittest.main()
