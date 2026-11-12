"""The wind-down record: only the owner and the coordinator set it, and the newest of theirs counts."""
import copy
import json
import subprocess
import sys
import unittest

from helpers import SPEC_ROSTER, TempRepo, line, relay_in

from crosstalk import state
from crosstalk.logs import append_line
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
        self.assertIn(self.current()["step"], (1, 2))

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
        p = self.run_cli("append", "--from", "beta", "--op", "set", "--collection", "state",
                         "--id", "beta-wind-down",
                         "--data", json.dumps(row("beta", 2, "2026-11-11T10:00:00Z")))
        self.assertEqual(p.returncode, 0, p.stderr)
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
        append_line(self.r.root, "relay", "set", "state", "relay-wind-down", data, roster=roster)
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


if __name__ == "__main__":
    unittest.main()
