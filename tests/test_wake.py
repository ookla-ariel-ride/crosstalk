"""Per-participant wake config: parsing and validation."""
import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line
from test_inbox import rep, req

from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.wake import (WakeConfig, actionable_items, load_wake, render_command,
                            render_prompt, wake_config, wake_problem)

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


class ItemTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def items(self, pid, roster_data=None):
        self.r.write_roster(roster_data)
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root)
        docs = enrich_messages(replay(lines), lines)
        return actionable_items(docs, pid, roster)

    def owner_msg(self, id_, **extra):
        data = {"from": "owner", "kind": "owner", "summary": "hi " + id_}
        data.update(extra)
        return line(1, "owner", id_=id_, data=data)

    def test_owner_message_to_project(self):
        self.r.write_log("owner", [self.owner_msg("m-1", to=["vectorcraft"])])
        self.assertEqual(self.items("vectorcraft"), ["m-1"])

    def test_reply_closes(self):
        self.r.write_log("bewareilive", [req(1, "bewareilive", "m-1", to=["vectorcraft"])])
        self.r.write_log("vectorcraft", [rep(1, "vectorcraft", "m-2", "m-1")])
        self.assertEqual(self.items("vectorcraft"), [])

    def test_reply_to_other_does_not_ring_requester(self):
        self.r.write_log("bewareilive", [req(1, "bewareilive", "m-1", to=["vectorcraft"])])
        self.r.write_log("vectorcraft", [rep(1, "vectorcraft", "m-2", "m-1")])
        self.assertEqual(self.items("bewareilive"), [])

    def test_everyone(self):
        self.r.write_log("owner", [self.owner_msg("m-1", summary="@Everyone ship it")])
        self.assertEqual(self.items("vectorcraft"), ["m-1"])
        self.assertEqual(self.items("bewareilive"), ["m-1"])

    def test_quarantined_sender_ignored(self):
        data = copy.deepcopy(SPEC_ROSTER)
        for p in data["participants"]:
            if p["id"] == "codex":
                p["trust"] = "quarantined"
        self.r.write_log("codex", [req(1, "codex", "m-1", to=["vectorcraft"])])
        self.assertEqual(self.items("vectorcraft", data), [])

    def test_prompt_and_command(self):
        cfg = WakeConfig(command=["hermes", "-p", "vc", "-z", "{prompt}", "--ids={ids}"],
                         cwd="/", timeoutSec=1, cooldownSec=1, dailyCap=1)
        self.assertEqual(render_command(cfg, "P", "vectorcraft", ["m-1", "m-2"]),
                         ["hermes", "-p", "vc", "-z", "P", "--ids=m-1,m-2"])
        text = render_prompt("VectorCraft", "vectorcraft", ["m-1", "m-2"],
                             "http://127.0.0.1:8484/", Path("/srv/crosstalk"))
        self.assertIn("New items for you: m-1, m-2.", text)
        self.assertIn("relay.py inbox vectorcraft", text)
        self.assertIn("/srv/crosstalk/scripts/relay.py", text)


if __name__ == "__main__":
    unittest.main()
