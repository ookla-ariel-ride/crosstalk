"""The auto-granter: GRANTED for the next land turn when queue, usage and load allow."""
import calendar
import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts scripts/ on sys.path)

from crosstalk.autogrant import Granter, grant_text, read_usage

T = calendar.timegm((2026, 11, 8, 10, 10, 0))
PROJECTS = {"gamma": {}, "beta": {}, "alpha": {}, "epsilon-kit": {}}


def msg(minute, sender, summary, **extra):
    doc = {"from": sender, "kind": "request", "summary": summary,
           "t": "2026-11-08T10:%02d:00Z" % minute}
    doc.update(extra)
    return doc


class GranterTests(unittest.TestCase):
    def setUp(self):
        self.docs = {}
        self.posts = []
        self.usage = (80.0, "")
        self.probe = {"reachable": True, "load": [4.0, 4.0, 4.0]}
        self.probes = 0
        self.tmp = tempfile.TemporaryDirectory()
        self.pause = Path(self.tmp.name) / "off"
        self.relay = "relay-bot"

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, did, doc):
        self.docs[("messages", did)] = doc

    def granter(self):
        def probe():
            self.probes += 1
            return self.probe

        def post(root, pid, to_id, summary, body=None):
            self.posts.append((pid, to_id, summary, body))
            # what the real post does: the GRANTED row joins the log
            self.add("relay-bot-g%d" % len(self.posts),
                     {"from": pid, "kind": "reply", "replyTo": to_id, "summary": summary,
                      "t": "2026-11-08T10:09:00Z"})
        return Granter(Path(self.tmp.name), lambda: (self.docs, PROJECTS), probe=probe,
                       usage=lambda: self.usage, post=post, clock=lambda: T,
                       pause_path=self.pause,
                       roster=lambda: helpers.plain_roster(self.relay, list(PROJECTS) + ["relay-bot"]))

    def test_grants_next_once_with_branches_and_holders(self):
        self.add("bet-1", msg(1, "beta", "TURN ASK beta def-1110"))
        self.add("bet-2", msg(2, "beta", "LAND START beta def-1110", kind="sync"))
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110, docs-1110"))
        g = self.granter()
        self.assertEqual(g.poll_once(), "gam-1")
        pid, to_id, summary, body = self.posts[0]
        self.assertEqual((pid, to_id, summary), ("relay-bot", "gam-1",
                                                 "GRANTED gamma grav-1110, docs-1110"))
        self.assertIn("other holders: beta def-1110", body)
        self.assertIn("load 4.0, weekly usage 80", body)
        self.assertTrue(body.endswith("No answer needed."))
        self.assertIsNone(g.poll_once())  # granted: no second GRANTED
        self.assertEqual(len(self.posts), 1)

    def test_no_grant_when_slots_are_full(self):
        self.add("bet-2", msg(2, "beta", "LAND START beta def-1110"))
        self.add("alp-2", msg(2, "alpha", "LAND START alpha c-1110"))
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        self.assertIsNone(self.granter().poll_once())
        self.assertEqual((self.posts, self.probes), ([], 0))

    def test_usage_stale_or_high_blocks(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        for usage in [(None, "usage file is 50 minutes old"), (96.0, "")]:
            self.usage = usage
            self.assertIsNone(self.granter().poll_once())
        self.assertEqual(self.posts, [])

    def test_high_load_or_unreachable_blocks(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        for p in [{"reachable": True, "load": [12.0, 1, 1]}, {"reachable": False, "load": None}]:
            self.probe = p
            self.assertIsNone(self.granter().poll_once())
        self.assertEqual(self.posts, [])

    def test_pause_file_stops_it(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        self.pause.write_text("")
        self.assertIsNone(self.granter().poll_once())
        self.assertEqual(self.posts, [])

    def wind_down(self, pid, step, minute):
        self.docs[("state", pid + "-wind-down")] = {
            "from": pid, "step": step, "started": None, "by": pid, "reason": None,
            "t": "2026-11-08T10:%02d:00Z" % minute}

    def test_no_grant_during_wind_down(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        g = self.granter()
        for step, minute in ((1, 4), (2, 5)):
            self.wind_down("relay-bot", step, minute)
            self.assertIsNone(g.poll_once())
            self.assertEqual(g._said, "not granting gam-1: wind-down step %d" % step)
        self.assertEqual((self.posts, self.probes), ([], 0))

    def test_waiting_ask_gets_its_grant_after_step_0(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        g = self.granter()
        self.wind_down("relay-bot", 1, 4)
        self.assertIsNone(g.poll_once())
        self.wind_down("beta", 0, 5)  # another writer's record counts for nothing
        self.assertIsNone(g.poll_once())
        self.wind_down("relay-bot", 0, 6)
        self.assertEqual(g.poll_once(), "gam-1")
        self.assertEqual(len(self.posts), 1)

    def test_grant_text_without_holders(self):
        s, b = grant_text({"project": "gamma", "branches": []}, [], 3.0, 90)
        self.assertEqual(s, "GRANTED gamma its branches")
        self.assertIn("other holders: none", b)


class UsageTests(unittest.TestCase):
    def test_newest_fresh_file_wins_and_stale_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d, "usage-a.json"), Path(d, "usage-b.json")
            old.write_text(json.dumps({"weeklyUsage": 50}))
            new.write_text(json.dumps({"weeklyUsage": 91}))
            os.utime(old, (T - 600, T - 600))
            os.utime(new, (T - 60, T - 60))
            pattern = str(Path(d, "usage*.json"))
            self.assertEqual(read_usage(pattern, now=T), (91.0, ""))
            value, why = read_usage(pattern, now=T + 3600)
            self.assertIsNone(value)
            self.assertIn("minutes old", why)
            self.assertEqual(read_usage(str(Path(d, "none*.json")), now=T), (None, "no usage file"))


if __name__ == "__main__":
    unittest.main()
