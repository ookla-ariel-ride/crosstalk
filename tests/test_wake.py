"""Per-participant wake config, inbox items, and the Waker."""
import copy
import json
import os
import shutil
import stat
import time
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line
from test_inbox import rep, req

from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.wake import (WakeConfig, Waker, actionable_items, load_wake, render_command,
                            render_prompt, wake_config, wake_problem)
from unittest import mock

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


FAKE = str(Path(__file__).resolve().parent / "fake_agent.py")
T0 = 1793016000.0  # 2026-10-26T12:00:00Z


def owner_msg(id_, **extra):
    data = {"from": "owner", "kind": "owner", "summary": "hi " + id_}
    data.update(extra)
    return line(1, "owner", id_=id_, data=data)


class WakerTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-waker-test-"))
        self.cwd = self.tmp / "work"
        self.cwd.mkdir()
        self.out = self.tmp / "calls.jsonl"
        self.state = self.tmp / "state.json"
        self.logs = self.tmp / "logs"
        self.now = [T0]
        self.wakers = []
        env = mock.patch.dict(os.environ, {"FAKE_AGENT_OUT": str(self.out),
                                           "FAKE_AGENT_MODE": "ok"})
        env.start()
        self.addCleanup(env.stop)
        self.owner_lines = []
        self.roster({"vectorcraft": {}})

    def tearDown(self):
        for w in self.wakers:
            w.kill_grace = 0.5
            w.stop()
        self.r.__exit__(None, None, None)
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    # helpers

    def roster(self, wakes):
        """wakes: pid -> dict of wake overrides (merged onto the fake-agent default)."""
        data = copy.deepcopy(SPEC_ROSTER)
        for p in data["participants"]:
            if p["id"] in wakes:
                w = {"command": [sys.executable, FAKE, "{pid}", "{ids}"], "cwd": str(self.cwd)}
                w.update(wakes[p["id"]])
                p["wake"] = w
        self.r.write_roster(data)

    def owner(self, *msgs):
        for m in msgs:
            m["seq"] = len(self.owner_lines) + 1
            self.owner_lines.append(m)
        self.r.write_log("owner", self.owner_lines)

    def waker(self, **kw):
        def snapshot():
            roster = load_roster(self.r.root / "participants.json")
            lines = load_all_lines(self.r.root, roster)
            return enrich_messages(replay(lines), lines), {}
        kw.setdefault("kill_grace", 0.5)
        w = Waker(self.r.root, snapshot, self.state, self.logs,
                  clock=lambda: self.now[0], **kw)
        self.wakers.append(w)
        return w

    def calls(self):
        if not self.out.exists():
            return []
        return [json.loads(x) for x in self.out.read_text().splitlines() if x]

    def wait_reaped(self, w):
        deadline = time.time() + 10
        while time.time() < deadline:
            w.poll_once()
            if not any(s["status"] == "running" for s in w.status().values()):
                return
            time.sleep(0.05)
        self.fail("child still running after 10 s")

    def wait_calls(self, n):
        deadline = time.time() + 10
        while time.time() < deadline and len(self.calls()) < n:
            time.sleep(0.05)
        self.assertEqual(len(self.calls()), n)

    def tick(self, sec):
        self.now[0] += sec

    # tests

    def test_first_sight_seeds_without_running(self):
        self.owner(owner_msg("m-old", to=["vectorcraft"]))
        w = self.waker()
        w.poll_once()
        self.wait_reaped(w)
        self.assertEqual(self.calls(), [])
        self.assertEqual(w.status()["vectorcraft"]["status"], "idle")

    def test_new_item_rings_once(self):
        self.owner(owner_msg("m-old", to=["vectorcraft"]))
        w = self.waker()
        w.poll_once()
        self.owner(owner_msg("m-new", to=["vectorcraft"]))
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "running")
        self.wait_reaped(w)
        for _ in range(3):
            self.tick(1000)
            w.poll_once()
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["env"]["CROSSTALK_IDS"], "m-new")
        self.assertEqual(calls[0]["env"]["CROSSTALK_PID"], "vectorcraft")
        self.assertEqual(calls[0]["env"]["CROSSTALK_WAKE"], "1")
        self.assertEqual(calls[0]["argv"], ["vectorcraft", "m-new"])
        self.assertEqual(os.path.realpath(calls[0]["cwd"]), os.path.realpath(str(self.cwd)))
        st = w.status()["vectorcraft"]
        self.assertEqual(st["status"], "ok")
        self.assertEqual(st["ids"], ["m-new"])
        self.assertTrue(Path(st["log"]).name.startswith("vectorcraft-20261026T12"))
        self.assertTrue(Path(st["log"]).is_file())

    def test_reply_rings_nobody(self):
        self.roster({"vectorcraft": {}, "bewareilive": {}})
        self.r.write_log("bewareilive", [req(1, "bewareilive", "m-1", to=["vectorcraft"])])
        w = self.waker()
        w.poll_once()
        self.r.write_log("vectorcraft", [rep(1, "vectorcraft", "m-2", "m-1")])
        self.tick(1000)
        w.poll_once()
        self.wait_reaped(w)
        self.assertEqual(self.calls(), [])

    def test_everyone_rings_each_wakeable(self):
        self.roster({"vectorcraft": {}, "bewareilive": {}})
        w = self.waker()
        w.poll_once()
        self.owner(owner_msg("m-all", summary="@Everyone ship it"))
        w.poll_once()
        self.wait_reaped(w)
        pids = sorted(c["env"]["CROSSTALK_PID"] for c in self.calls())
        self.assertEqual(pids, ["bewareilive", "vectorcraft"])
        self.assertNotIn("codex", w.status())
        self.assertNotIn("owner", w.status())

    def test_cooldown(self):
        w = self.waker()
        w.poll_once()
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_reaped(w)
        self.tick(10)
        self.owner(owner_msg("m-2", to=["vectorcraft"]))
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "cooling-down")
        self.assertEqual(len(self.calls()), 1)
        self.tick(300)
        w.poll_once()
        self.wait_reaped(w)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["env"]["CROSSTALK_IDS"], "m-2")

    def test_daily_cap(self):
        self.roster({"vectorcraft": {"dailyCap": 1, "cooldownSec": 1}})
        w = self.waker()
        w.poll_once()
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_reaped(w)
        self.tick(60)
        self.owner(owner_msg("m-2", to=["vectorcraft"]))
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "capped")
        self.assertEqual(len(self.calls()), 1)
        self.now[0] = T0 + 12 * 3600 + 1  # 2026-10-27T00:00:01Z
        w.poll_once()
        self.wait_reaped(w)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.calls()[1]["env"]["CROSSTALK_IDS"], "m-2")

    def test_one_at_a_time(self):
        self.roster({"vectorcraft": {"cooldownSec": 1}})
        os.environ["FAKE_AGENT_MODE"] = "sleep:2"
        w = self.waker()
        w.poll_once()
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_calls(1)
        self.tick(60)
        self.owner(owner_msg("m-2", to=["vectorcraft"]))
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "running")
        self.assertEqual(w.status()["vectorcraft"]["ids"], ["m-1"])
        self.assertEqual(len(self.calls()), 1)
        os.environ["FAKE_AGENT_MODE"] = "ok"
        self.wait_reaped(w)  # first exits; the same pass may start the second
        w.poll_once()
        self.wait_reaped(w)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["env"]["CROSSTALK_IDS"], "m-2")

    def test_timeout_kills_group(self):
        self.roster({"vectorcraft": {"timeoutSec": 1}})
        os.environ["FAKE_AGENT_MODE"] = "hang"
        w = self.waker(kill_grace=0.5)
        w.poll_once()
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_calls(1)
        child = w._running["vectorcraft"]["proc"].pid
        self.tick(2)
        w.poll_once()  # SIGTERM, ignored
        self.assertEqual(w.status()["vectorcraft"]["status"], "running")
        self.tick(1)
        w.poll_once()  # SIGKILL
        self.wait_reaped(w)
        self.assertEqual(w.status()["vectorcraft"]["status"], "timed-out")
        with self.assertRaises(OSError):
            os.kill(child, 0)

    def test_failure_and_missing_command(self):
        self.roster({"vectorcraft": {}, "bewareilive": {"command": ["/no/such/bin"]}})
        os.environ["FAKE_AGENT_MODE"] = "fail"
        w = self.waker()
        w.poll_once()
        self.owner(owner_msg("m-all", summary="@Everyone ship it"))
        w.poll_once()
        self.wait_reaped(w)
        st = w.status()
        self.assertEqual(st["vectorcraft"]["status"], "failed")
        self.assertEqual(st["bewareilive"]["status"], "failed")
        self.assertEqual([c["env"]["CROSSTALK_PID"] for c in self.calls()], ["vectorcraft"])
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["vectorcraft"]["last"]["exit"], 3)
        self.assertIsNone(saved["bewareilive"]["last"]["exit"])
        self.assertIn("/no/such/bin", Path(st["bewareilive"]["log"]).read_text())
        self.assertEqual(saved["bewareilive"]["rung"], ["m-all"])

    def test_state_survives_restart(self):
        w = self.waker()
        w.poll_once()
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_reaped(w)
        self.tick(1000)
        w2 = self.waker()
        w2.poll_once()
        self.wait_reaped(w2)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(w2.status()["vectorcraft"]["status"], "ok")

    def test_stop_interrupts_running(self):
        os.environ["FAKE_AGENT_MODE"] = "sleep:30"
        w = self.waker()
        w.poll_once()
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_calls(1)
        child = w._running["vectorcraft"]["proc"].pid
        w.stop()
        self.assertEqual(w.status()["vectorcraft"]["status"], "interrupted")
        with self.assertRaises(OSError):
            os.kill(child, 0)
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["vectorcraft"]["last"]["status"], "interrupted")
        self.assertEqual(saved["vectorcraft"]["rung"], ["m-1"])
        self.tick(1000)
        w2 = self.waker()
        w2.poll_once()
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(w2.status()["vectorcraft"]["status"], "interrupted")

    def test_running_after_crash_becomes_interrupted(self):
        self.state.write_text(json.dumps({"vectorcraft": {
            "rung": [], "runs": [], "last": {"status": "running", "started": "2026-10-26T11:00:00Z",
                                             "ended": None, "exit": None, "ids": ["m-0"],
                                             "log": None}}}))
        w = self.waker()
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "interrupted")

    def test_invalid_roster_skips_pass(self):
        w = self.waker()
        w.poll_once()
        good = (self.r.root / "participants.json").read_text()
        (self.r.root / "participants.json").write_text("{")
        w.poll_once()
        (self.r.root / "participants.json").write_text(good)
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_reaped(w)
        self.assertEqual([c["env"]["CROSSTALK_IDS"] for c in self.calls()], ["m-1"])

    def test_wake_added_live_seeds_backlog(self):
        self.r.write_log("bewareilive", [req(1, "bewareilive", "m-b", to=["vectorcraft"])])
        self.roster({"bewareilive": {}})
        w = self.waker()
        w.poll_once()
        self.assertNotIn("vectorcraft", w.status())
        self.roster({"bewareilive": {}, "vectorcraft": {}})
        w.poll_once()
        self.wait_reaped(w)
        self.assertEqual(self.calls(), [])
        self.assertEqual(w.status()["vectorcraft"]["status"], "idle")
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_reaped(w)
        self.assertEqual([c["env"]["CROSSTALK_IDS"] for c in self.calls()], ["m-1"])

    def test_misconfigured(self):
        self.roster({"vectorcraft": {"cwd": str(self.tmp / "nope")}})
        w = self.waker()
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "misconfigured")

    def test_fixed_config_and_answered_items_clear_transient_status(self):
        w = self.waker()
        w.poll_once()
        self.roster({"vectorcraft": {"cwd": str(self.tmp / "nope")}})
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "misconfigured")
        self.roster({"vectorcraft": {}})
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "idle")
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_reaped(w)
        self.tick(10)
        self.r.write_log("bewareilive", [req(1, "bewareilive", "m-2", to=["vectorcraft"])])
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "cooling-down")
        self.r.write_log("vectorcraft", [rep(1, "vectorcraft", "m-3", "m-2")])
        w.poll_once()
        self.assertEqual(w.status()["vectorcraft"]["status"], "ok")

    def test_state_file_mode(self):
        w = self.waker()
        w.poll_once()
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)

    def test_memory_only_state(self):
        self.state = None
        w = self.waker()
        w.poll_once()
        self.owner(owner_msg("m-1", to=["vectorcraft"]))
        w.poll_once()
        self.wait_reaped(w)
        self.assertEqual(w.status()["vectorcraft"]["status"], "ok")

    def test_thread_start_stop(self):
        w = self.waker(poll_sec=0.05)
        w.start()
        deadline = time.time() + 5
        while time.time() < deadline and "vectorcraft" not in w.status():
            time.sleep(0.05)
        w.stop()
        self.assertEqual(w.status()["vectorcraft"]["status"], "idle")


if __name__ == "__main__":
    unittest.main()
