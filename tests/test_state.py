"""The wind-down record: only the owner and the coordinator set it, and the newest of theirs counts."""
import copy
import fcntl
import json
import os
import subprocess
import sys
import threading
import unittest

from helpers import SPEC_ROSTER, TempRepo, line, relay_in

from crosstalk import state
from crosstalk.logs import append_line, state_lock_path
from crosstalk.participants import load_roster
from crosstalk.replay import load_all_lines, replay


def roster_data(relay="relay-bot"):
    data = copy.deepcopy(SPEC_ROSTER)
    data["relay"] = relay
    for pid, color, dark in (("relay-bot", "#2e7d32", "#81c784"), ("relay", "#c77700", "#f0a73a"),
                             ("owner-x", "#5d4037", "#a1887f")):
        data["participants"].append(
            {"id": pid, "name": pid.title(), "kind": "project", "color": color,
             "colorDark": dark, "checkInMin": 30, "trust": "approved"})
    return data


def row(frm, step, t, **extra):
    data = {"from": frm, "step": step, "started": None, "by": frm, "reason": None, "t": t}
    data.update(extra)
    return data


NONE = {"step": 0, "started": None, "by": None, "reason": None}


class WindDownTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(roster_data())
        self.script = str(relay_in(self.r.root))

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def roster(self):
        return load_roster(self.r.root / "participants.json")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def docs(self):
        return replay(load_all_lines(self.r.root, self.roster()))

    def current(self):
        return state.wind_down(self.docs(), self.roster())

    def test_no_record_is_step_0(self):
        self.assertEqual(state.wind_down({}, self.roster()), NONE)
        self.assertEqual(self.current(), NONE)

    def test_owner_sets_and_coordinator_sets(self):
        p = self.run_cli("state", "wind-down", "--from", "owner", "--step", "1",
                         "--reason", "owner request")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "owner-wind-down"), p.stderr)
        p = self.run_cli("state", "wind-down", "--from", "relay-bot", "--step", "2")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "relay-bot-wind-down"), p.stderr)
        docs = self.docs()
        own = docs[("state", "owner-wind-down")]
        self.assertEqual((own["from"], own["by"], own["step"], own["reason"]),
                         ("owner", "owner", 1, "owner request"))
        self.assertIsNotNone(own["started"])
        coord = docs[("state", "relay-bot-wind-down")]
        self.assertEqual((coord["from"], coord["by"], coord["step"], coord["reason"]),
                         ("relay-bot", "relay-bot", 2, None))
        self.assertEqual(coord["started"], own["started"])  # the record in force was at step 1
        # the later command takes force, also within the same second
        self.assertEqual(self.current(), {"step": 2, "started": own["started"], "by": "relay-bot",
                                          "reason": None})
        self.assertGreater(coord["t"], own["t"])

    def test_a_later_command_always_takes_force(self):
        roster = self.roster()
        for pid, step in (("owner", 1), ("relay-bot", 0), ("owner", 2), ("relay-bot", 1), ("owner", 0)):
            ln = state.set_wind_down(self.r.root, pid, step, roster=roster)
            self.assertEqual(ln.t, ln.data["t"])
            self.assertEqual((self.current()["step"], self.current()["by"]), (step, pid))

    def test_future_t_through_append_is_refused(self):
        state.set_wind_down(self.r.root, "owner", 1, roster=self.roster())
        for pid in ("owner", "relay-bot"):
            for t in ("2099-01-01T00:00:00Z", None):
                data = row(pid, 0, t)
                if t is None:
                    del data["t"]
                p = self.run_cli("append", "--from", pid, "--op", "set", "--collection", "state",
                                 "--id", pid + "-wind-down", "--data", json.dumps(data))
                self.assertEqual(p.returncode, 1, (pid, t))
                self.assertIn("line t", p.stderr)
        self.assertEqual(self.current()["step"], 1)
        state.set_wind_down(self.r.root, "relay-bot", 0, roster=self.roster())
        self.assertEqual(self.current()["by"], "relay-bot")

    def test_a_state_row_waits_for_the_state_lock(self):
        # The granter holds this lock from its last read of the log to its GRANTED.
        with open(str(state_lock_path(self.r.root)), "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            worker = threading.Thread(target=state.set_wind_down,
                                      args=(self.r.root, "owner", 1), kwargs={"roster": self.roster()})
            worker.start()
            worker.join(0.5)
            self.assertTrue(worker.is_alive())
            self.assertEqual(self.current()["step"], 0)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.current()["step"], 1)

    def test_update_cannot_change_the_step(self):
        state.set_wind_down(self.r.root, "owner", 1, reason="stop", roster=self.roster())
        for data in ({"step": 0}, {"t": "2099-01-01T00:00:00Z"}, {"started": None}):
            p = self.run_cli("append", "--from", "owner", "--op", "update", "--collection", "state",
                             "--id", "owner-wind-down", "--data", json.dumps(data))
            self.assertEqual(p.returncode, 1, data)
            self.assertIn("new state wind-down", p.stderr)
        self.assertEqual(self.current()["step"], 1)

    def test_newest_of_owner_and_coordinator_wins(self):
        roster = self.roster()
        docs = {("state", "owner-wind-down"): row("owner", 1, "2026-11-11T10:00:00Z",
                                                  started="2026-11-11T10:00:00Z", reason="stop"),
                ("state", "relay-bot-wind-down"): row("relay-bot", 0, "2026-11-11T10:05:00Z")}
        self.assertEqual(state.wind_down(docs, roster),
                         {"step": 0, "started": None, "by": "relay-bot", "reason": None})
        docs[("state", "owner-wind-down")]["t"] = "2026-11-11T10:06:00Z"
        self.assertEqual(state.wind_down(docs, roster),
                         {"step": 1, "started": "2026-11-11T10:00:00Z", "by": "owner",
                          "reason": "stop"})
        # by comes from the id, not from the data
        docs[("state", "owner-wind-down")]["by"] = "beta"
        self.assertEqual(state.wind_down(docs, roster)["by"], "owner")
        # a row without a log time, or with a step outside WIND_DOWN_STEPS, counts for nothing
        docs[("state", "owner-wind-down")]["t"] = None
        self.assertEqual(state.wind_down(docs, roster)["by"], "relay-bot")
        docs[("state", "owner-wind-down")].update(t="2026-11-11T10:06:00Z", step=3)
        self.assertEqual(state.wind_down(docs, roster)["by"], "relay-bot")
        docs[("state", "owner-wind-down")]["step"] = True
        self.assertEqual(state.wind_down(docs, roster)["by"], "relay-bot")

    def test_other_writer_is_refused_by_the_command(self):
        p = self.run_cli("state", "wind-down", "--from", "beta", "--step", "1")
        self.assertEqual(p.returncode, 1)
        self.assertIn("owner or the coordinator", p.stderr)
        p = self.run_cli("state", "wind-down", "--from", "owner", "--step", "3")
        self.assertNotEqual(p.returncode, 0)
        with self.assertRaises(ValueError):
            state.set_wind_down(self.r.root, "owner", 3, roster=self.roster())
        self.assertEqual(self.docs(), {})

    def test_other_writer_row_counts_for_nothing(self):
        t = "2099-01-01T00:00:00Z"  # newer than any record; append_line is what relay.py append runs
        append_line(self.r.root, "beta", "set", "state", "beta-wind-down", row("beta", 2, t), t=t,
                    roster=self.roster())
        self.assertIn(("state", "beta-wind-down"), self.docs())
        self.assertEqual(self.current(), NONE)

    def test_prefix_id_is_refused(self):
        roster = self.roster()
        data = row("relay", 2, "2026-11-11T10:00:00Z")
        for op in ("set", "update"):
            with self.assertRaises(ValueError):
                append_line(self.r.root, "relay", op, "state", "relay-bot-wind-down", data, roster=roster)
            # the owner against a participant named owner-x, both ways
            with self.assertRaises(ValueError):
                append_line(self.r.root, "owner", op, "state", "owner-x-wind-down",
                            row("owner", 2, "2026-11-11T10:00:00Z"), roster=roster)
            with self.assertRaises(ValueError):
                append_line(self.r.root, "owner-x", op, "state", "owner-wind-down",
                            row("owner-x", 2, "2026-11-11T10:00:00Z"), roster=roster)
        # any other id in state, and a data.from that is not the writer
        for did in ("relay-wind-downs", "relay-other", "relay-wind-down-2"):
            with self.assertRaises(ValueError):
                append_line(self.r.root, "relay", "set", "state", did, data, roster=roster)
        with self.assertRaises(ValueError):
            append_line(self.r.root, "relay", "set", "state", "relay-wind-down",
                        row("relay-bot", 2, "2026-11-11T10:00:00Z"), roster=roster)
        append_line(self.r.root, "relay", "set", "state", "relay-wind-down", data, t=data["t"],
                    roster=roster)
        self.assertEqual(self.current(), NONE)

    def test_update_by_another_writer_is_refused(self):
        state.set_wind_down(self.r.root, "owner", 1, reason="stop", roster=self.roster())
        for pid in ("beta", "relay-bot", "owner-x"):
            p = self.run_cli("append", "--from", pid, "--op", "update", "--collection", "state",
                             "--id", "owner-wind-down", "--data", json.dumps({"step": 0}))
            self.assertEqual(p.returncode, 1, pid)
            self.assertIn("owner-wind-down", p.stderr)
        self.assertEqual(self.current()["step"], 1)

    def test_no_coordinator_only_owner_counts(self):
        self.r.write_roster(roster_data(relay=""))
        roster = self.roster()
        docs = {("state", "owner-wind-down"): row("owner", 1, "2026-11-11T10:00:00Z"),
                ("state", "relay-bot-wind-down"): row("relay-bot", 0, "2026-11-11T10:05:00Z")}
        self.assertEqual(state.wind_down(docs, roster)["by"], "owner")
        with self.assertRaises(ValueError):
            state.set_wind_down(self.r.root, "relay-bot", 1, roster=roster)
        self.assertEqual(state.set_wind_down(self.r.root, "owner", 2, roster=roster).id,
                         "owner-wind-down")

    def test_started_carries_from_step_1_to_step_2(self):
        self.r.write_log("owner", [line(1, "owner", collection="state", id_="owner-wind-down",
                                        data=row("owner", 1, "2020-01-01T09:00:00Z",
                                                 started="2020-01-01T09:00:00Z"),
                                        t="2020-01-01T09:00:00Z")])
        ln = state.set_wind_down(self.r.root, "relay-bot", 2, reason="queue drained", roster=self.roster())
        self.assertEqual(ln.data["started"], "2020-01-01T09:00:00Z")
        self.assertEqual(self.current(), {"step": 2, "started": "2020-01-01T09:00:00Z",
                                          "by": "relay-bot", "reason": "queue drained"})
        ln = state.set_wind_down(self.r.root, "owner", 0, roster=self.roster())
        self.assertIsNone(ln.data["started"])
        self.assertEqual(self.current()["step"], 0)
        ln = state.set_wind_down(self.r.root, "owner", 1, roster=self.roster())
        self.assertEqual(ln.data["started"], ln.data["t"])  # a new wind-down starts now


VITALS = {"disk_free_gb": 149, "usage": 43, "usage_limit": 96,
          "build_host": {"reachable": True, "load": 1.5, "t": "2026-11-11T23:45:00Z"}}
LAND = {"holders": [], "waiting": [], "max_holders": 2, "next": None, "slots_free": 2,
        "may_start": True, "notes": []}
T = "2026-11-11T23:50:00Z"


def wind(step):
    return {"step": step, "started": T if step else None, "by": "owner" if step else None,
            "reason": "owner request" if step else None}


class StateRowTests(unittest.TestCase):
    def test_state_row_readings_null_and_not_null(self):
        out = state.state_row(VITALS, LAND, NONE, T)
        self.assertEqual(sorted(out), ["build_host", "disk_free_gb", "land", "may_start", "may_start_why",
                                       "t", "usage", "usage_limit", "wind_down"])
        self.assertEqual((out["t"], out["disk_free_gb"], out["usage"], out["usage_limit"]), (T, 149, 43, 96))
        self.assertEqual(out["build_host"], VITALS["build_host"])
        self.assertEqual(out["wind_down"], NONE)
        self.assertEqual((out["may_start"], out["may_start_why"]), (True, []))
        nulls = dict(VITALS, usage=None, usage_limit=None, build_host=None)
        out = state.state_row(nulls, LAND, NONE, T)
        self.assertIsNone(out["usage"])
        self.assertIsNone(out["usage_limit"])
        self.assertIsNone(out["build_host"])
        self.assertEqual((out["may_start"], out["may_start_why"]), (True, []))

    def test_may_start_false_for_each_reason(self):
        for step in (1, 2):
            out = state.state_row(VITALS, LAND, wind(step), T)
            self.assertEqual((out["may_start"], out["may_start_why"]), (False, ["wind-down step %d" % step]))
        for usage in (96, 97):
            out = state.state_row(dict(VITALS, usage=usage), LAND, NONE, T)
            self.assertEqual((out["may_start"], out["may_start_why"]),
                             (False, ["usage %d at or over the limit 96" % usage]))
        down = dict(VITALS, build_host={"reachable": False, "load": None, "t": None})
        out = state.state_row(down, LAND, NONE, T)
        self.assertEqual((out["may_start"], out["may_start_why"]), (False, ["build host not reachable"]))
        out = state.state_row(dict(down, usage=99), LAND, wind(2), T)
        self.assertFalse(out["may_start"])
        self.assertEqual(out["may_start_why"], ["wind-down step 2", "usage 99 at or over the limit 96",
                                                "build host not reachable"])

    def test_null_reading_does_not_block(self):
        for vit in (dict(VITALS, usage=None), dict(VITALS, usage=200, usage_limit=None),
                    dict(VITALS, build_host=None)):
            out = state.state_row(vit, LAND, NONE, T)
            self.assertEqual((out["may_start"], out["may_start_why"]), (True, []), vit)

    def test_land_does_not_block(self):
        full = dict(LAND, may_start=False, slots_free=0)
        out = state.state_row(VITALS, full, NONE, T)
        self.assertIs(out["land"], full)
        self.assertTrue(out["may_start"])

    def test_get_dotted_key(self):
        row = state.state_row(VITALS, LAND, wind(1), T)
        self.assertEqual(state.get(row, "wind_down.step"), 1)
        self.assertIs(state.get(row, "build_host.reachable"), True)
        self.assertEqual(state.get(row, "land.max_holders"), 2)
        for key in ("nope", "wind_down.nope", "t.x", "wind_down.step.x", "", "wind_down."):
            with self.assertRaises(KeyError, msg=key):
                state.get(row, key)
        self.assertIsNone(state.get(dict(row, build_host=None), "build_host.reachable"))
        with self.assertRaises(KeyError):
            state.get(dict(row, build_host=None), "build_host.nope")


class StateCommandTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(roster_data())
        self.script = str(relay_in(self.r.root))
        self.env = dict(os.environ, CROSSTALK_BUILDHOST_STATE=str(self.r.root / "buildhost-state.json"))

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/", env=self.env)

    def files(self):
        return {str(p.relative_to(self.r.root)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in self.r.root.rglob("*") if p.is_file() and "scripts" not in p.parts}

    def test_state_and_wind_down_in_one_log(self):
        p = self.run_cli("state")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["wind_down"], NONE)
        p = self.run_cli("state", "wind-down", "--from", "owner", "--step", "1", "--reason", "owner request")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "owner-wind-down"), p.stderr)
        p = self.run_cli("state")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual((out["wind_down"]["step"], out["wind_down"]["by"]), (1, "owner"))
        self.assertEqual((out["may_start"], out["may_start_why"]), (False, ["wind-down step 1"]))

    def test_state_only_reads(self):
        self.r.write_log("owner", [line(1, "owner", data={"from": "owner", "kind": "sync", "summary": "hi"})])
        before = self.files()
        for args in (("state",), ("state", "--get", "wind_down.step"), ("state", "--get", "usage")):
            self.run_cli(*args)
        self.assertEqual(self.files(), before)

    def test_land_is_the_queue_output(self):
        self.r.write_log("alpha", [line(1, "alpha", t="2026-11-11T10:00:00Z",
                                        data={"from": "alpha", "kind": "sync",
                                              "summary": "TURN ASK alpha a-1111"})])
        q = self.run_cli("queue")
        s = self.run_cli("state")
        self.assertEqual((q.returncode, s.returncode), (0, 0), q.stderr + s.stderr)
        land = json.loads(s.stdout)["land"]
        self.assertEqual(land, json.loads(q.stdout))
        self.assertEqual([w["project"] for w in land["waiting"]], ["alpha"])

    def test_get_prints_a_value_exit_0(self):
        self.r.write_config({"usage": {"glob": str(self.r.root / "usage-*.json"), "limit": 90}})
        for key, want in (("wind_down.step", "0"), ("usage_limit", "90"), ("may_start", "true"),
                          ("may_start_why", "[]")):
            p = self.run_cli("state", "--get", key)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, want + "\n", ""), key)
        self.run_cli("state", "wind-down", "--from", "owner", "--step", "2", "--reason", "owner request")
        p = self.run_cli("state", "--get", "wind_down.reason")
        self.assertEqual((p.returncode, p.stdout), (0, "owner request\n"))  # a string as it is
        p = self.run_cli("state", "--get", "t")
        self.assertRegex(p.stdout, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n$")

    def test_get_null_exits_1(self):
        # A key under a null reading is null too: build_host is null with no build host set.
        for key in ("usage", "build_host", "build_host.reachable", "wind_down.by"):
            p = self.run_cli("state", "--get", key)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (1, "", ""), key)

    def test_get_unknown_key_exits_2(self):
        for key in ("nope", "wind_down.nope", "wind_down.step.x"):
            p = self.run_cli("state", "--get", key)
            self.assertEqual((p.returncode, p.stdout), (2, ""), key)
            self.assertIn(key, p.stderr)


if __name__ == "__main__":
    unittest.main()
