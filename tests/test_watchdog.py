"""relay.py watchdog: one alert when the coordinator's check-in is late (crosstalk/watchdog.py)."""
import copy
import json
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone

from helpers import SPEC_ROSTER, TempRepo, line, relay_in

from crosstalk import watchdog
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["relay"] = "alpha"
ON = {"watchdog": {"enabled": True}}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_in(seq, id_, minutes_ago, frm="alpha", **extra):
    t = iso(time.time() - minutes_ago * 60)
    return line(seq, frm, id_=id_, data=dict({"from": frm, "kind": "check-in", "summary": "status"}, **extra), t=t)


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_relay(self, *args):
        return subprocess.run([sys.executable, str(relay_in(self.r.root))] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)

    def reading(self, late_min=None):
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root, roster)
        return watchdog.reading(enrich_messages(replay(lines), lines), roster, time.time(), late_min)

    def alerts(self):
        out = []
        for path in (self.r.root / "log").glob("*.jsonl"):
            for raw in path.read_text().splitlines():
                row = json.loads(raw)
                if row["data"].get("kind") == "alert":
                    out.append(row)
        return out

    def test_not_late(self):
        self.r.write_config(ON)
        self.r.write_log("alpha", [check_in(1, "alpha-c1", 60)])  # due 30 min ago: 30 min late
        r = self.reading()
        self.assertEqual((r["state"], r["last_id"], r["period_min"]), ("on time", "alpha-c1", 30.0))
        self.assertAlmostEqual(r["late_min"], 30.0, delta=1)
        p = self.run_relay("watchdog", "--post")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("on time", p.stdout)
        self.assertEqual(self.alerts(), [])

    def test_late_posts_once_then_no_op_then_new_check_in_resets(self):
        self.r.write_config(ON)
        self.r.write_log("alpha", [check_in(1, "alpha-c0", 300), check_in(2, "alpha-c1", 100),
                                   check_in(3, "alpha-c2", 50, retracted=True)])
        p = self.run_relay("watchdog", "--post")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.splitlines()[-1], "beta-watchdog-alpha-alpha-c1")
        [row] = self.alerts()
        data = row["data"]
        self.assertEqual((row["from"], data["from"]), ("beta", "beta"))  # not the coordinator, not the owner
        self.assertEqual(data["to"], ["alpha", "owner"])
        self.assertEqual((data["kind"], data["source"], data["important"]), ("alert", "watchdog", True))
        self.assertIn("alpha-c1", data["body"])
        self.assertIn("70 min late", data["body"])
        self.assertNotIn("check-in", data["summary"].lower())  # the alert is no check-in of beta's
        # A second run posts nothing.
        p = self.run_relay("watchdog", "--post")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("posted", p.stdout)
        self.assertEqual(len(self.alerts()), 1)
        # Another sender sees the same gap as posted.
        self.assertIn("posted", self.run_relay("watchdog", "--post", "--from", "rev").stdout)
        self.assertEqual(len(self.alerts()), 1)
        # A new check-in ends the gap; the next late one gets its own alert.
        self.r.write_log("alpha", [check_in(1, "alpha-c1", 100), check_in(2, "alpha-c3", 10)])
        self.assertEqual(self.reading()["state"], "on time")
        self.r.write_log("alpha", [check_in(1, "alpha-c1", 300), check_in(2, "alpha-c3", 200)])
        p = self.run_relay("watchdog", "--post")
        self.assertEqual(p.stdout.splitlines()[-1], "beta-watchdog-alpha-alpha-c3")
        self.assertEqual(len(self.alerts()), 2)

    def test_schedule_period_and_late_min(self):
        self.r.write_log("alpha", [
            check_in(1, "alpha-c1", 100),
            line(2, "alpha", "schedules", id_="alpha-sched-check-in", t=iso(time.time() - 6000),
                 data={"from": "alpha", "name": "check-in", "cron": "0 * * * *", "periodMin": 60}),
        ])
        r = self.reading()
        self.assertEqual((r["period_min"], r["state"]), (60.0, "on time"))
        self.assertEqual(self.reading(late_min=30)["state"], "late")

    def test_wind_down_quiet(self):
        self.r.write_config(ON)
        self.r.write_log("alpha", [check_in(1, "alpha-c1", 300)])
        p = self.run_relay("state", "wind-down", "--from", "owner", "--step", "1", "--reason", "usage")
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.run_relay("watchdog", "--post")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("wind-down", p.stdout)
        self.assertEqual(self.alerts(), [])

    def test_disabled_quiet(self):
        self.r.write_log("alpha", [check_in(1, "alpha-c1", 300)])
        p = self.run_relay("watchdog", "--post")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("late", p.stdout)
        self.assertIn("watchdog.enabled", p.stdout)
        self.assertEqual(self.alerts(), [])

    def test_sender_rules_and_no_check_in(self):
        self.r.write_config(ON)
        self.assertEqual(self.reading()["state"], "no check-in")
        self.r.write_log("alpha", [check_in(1, "alpha-c1", 300)])
        for bad in ("alpha", "owner", "nobody"):
            p = self.run_relay("watchdog", "--post", "--from", bad)
            self.assertEqual(p.returncode, 1, bad)
        self.assertEqual(self.alerts(), [])
        p = self.run_relay("watchdog", "--post", "--from", "rev")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.alerts()[0]["data"]["to"], ["alpha", "beta", "owner"])


if __name__ == "__main__":
    unittest.main()
