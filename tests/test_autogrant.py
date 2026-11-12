"""The auto-granter: GRANTED for the next land turn when queue, usage and load allow."""
import calendar
import contextlib
import copy
import fcntl
import io
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

import helpers  # noqa: F401  (puts scripts/ on sys.path)

from crosstalk.autogrant import Granter, grant_text, read_usage
from crosstalk import logs, state
from crosstalk.consults import post_consult, post_reply
from crosstalk.landqueue import land_queue
from crosstalk.logs import state_lock_path
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.requests import thread_replies_for

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
        self.during_probe = None

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, did, doc):
        self.docs[("messages", did)] = doc

    def granter(self):
        def probe():
            self.probes += 1
            if self.during_probe is not None:
                self.during_probe()
            return self.probe

        def post(root, pid, to_id, summary, body=None):
            self.posts.append((pid, to_id, summary, body))
            # what the real post does: the GRANTED row joins the log
            self.add("relay-bot-g%d" % len(self.posts),
                     {"from": pid, "kind": "reply", "replyTo": to_id, "summary": summary,
                      "t": "2026-11-08T10:09:00Z"})
        return Granter(Path(self.tmp.name), lambda: (dict(self.docs), PROJECTS), probe=probe,
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

    def test_wind_down_set_during_the_probe_stops_the_grant(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        self.during_probe = lambda: self.wind_down("owner", 2, 4)
        g = self.granter()
        self.assertIsNone(g.poll_once())
        self.assertEqual((self.posts, self.probes), ([], 1))
        self.assertEqual(g._said, "not granting gam-1: wind-down step 2")

    def test_usage_over_the_limit_during_the_probe_stops_the_grant(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        self.during_probe = lambda: setattr(self, "usage", (97.0, ""))
        self.assertIsNone(self.granter().poll_once())
        self.assertEqual(self.posts, [])

    def test_ask_granted_by_hand_during_the_probe_gets_no_second_grant(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        self.during_probe = lambda: self.add("relay-bot-h1", {
            "from": "relay-bot", "kind": "reply", "replyTo": "gam-1",
            "summary": "GRANTED gamma grav-1110", "t": "2026-11-08T10:04:00Z"})
        self.assertIsNone(self.granter().poll_once())
        self.assertEqual(self.posts, [])

    def test_grant_is_appended_under_the_state_lock(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        held = []

        def post(root, pid, to_id, summary, body=None):
            with open(str(state_lock_path(root)), "a") as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    held.append(True)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        g = self.granter()
        g.post = post
        self.assertEqual(g.poll_once(), "gam-1")
        self.assertEqual(held, [True])

    def test_pause_set_during_the_probe_stops_the_grant(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        self.during_probe = lambda: self.pause.write_text("")
        self.assertIsNone(self.granter().poll_once())
        self.assertEqual(self.posts, [])

    def test_busy_state_lock_means_no_grant_in_this_poll(self):
        self.add("gam-1", msg(3, "gamma", "TURN ASK gamma grav-1110"))
        g = self.granter()
        path = state_lock_path(self.tmp.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "a") as f, mock.patch.object(logs, "STATE_LOCK_WAIT", 0.3):
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            self.assertIsNone(g.poll_once())
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        self.assertEqual(self.posts, [])
        self.assertIn("state lock", g._said)
        self.assertEqual(g.poll_once(), "gam-1")

    def test_grant_text_without_holders(self):
        s, b = grant_text({"project": "gamma", "branches": []}, [], 3.0, 90)
        self.assertEqual(s, "GRANTED gamma its branches")
        self.assertIn("other holders: none", b)


class RealLogTests(unittest.TestCase):
    """The granter over a real log: the state lock, the hold limit and GRANT WITHDRAWN."""

    def setUp(self):
        self.r = helpers.TempRepo()
        self.r.__enter__()
        data = copy.deepcopy(helpers.SPEC_ROSTER)
        data["relay"] = "relay-bot"
        data["participants"].append(
            {"id": "relay-bot", "name": "Relay Bot", "kind": "project", "color": "#2e7d32",
             "colorDark": "#81c784", "checkInMin": 30, "trust": "approved"})
        self.r.write_roster(data)
        self.ask = post_consult(self.r.root, "alpha", "TURN ASK alpha feat-1112",
                                to=["relay-bot"], roster=self.roster()).id

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def roster(self):
        return load_roster(self.r.root / "participants.json")

    def docs(self):
        return enrich_messages(replay(load_all_lines(self.r.root, self.roster())),
                               load_all_lines(self.r.root, self.roster()))

    def granter(self, post=post_reply):
        entries = {pid: {} for pid in self.roster().participants}
        return Granter(self.r.root, lambda: (self.docs(), entries),
                       probe=lambda: {"reachable": True, "load": [1.0, 1.0, 1.0]},
                       post=post, pause_path=None)

    def coordinator_posts(self, word):
        return [(did, d) for (c, did), d in self.docs().items()
                if c == "messages" and d.get("from") == "relay-bot"
                and d.get("summary", "").startswith(word)]

    def queue(self):
        return land_queue(self.docs(), projects=self.roster().participants, now=time.time(),
                          coordinator="relay-bot")

    def test_granter_paused_after_its_last_check_posts_nothing_after_the_owner_timeout(self):
        entered, resume, out = threading.Event(), threading.Event(), []

        def post(*args, **kwargs):
            entered.set()
            resume.wait(10)
            return post_reply(*args, **kwargs)
        g = self.granter(post)
        err = io.StringIO()
        with mock.patch.object(logs, "STATE_LOCK_WAIT", 0.6), \
                mock.patch.object(logs, "STATE_HOLD_LIMIT", 0.3), contextlib.redirect_stderr(err):
            t = threading.Thread(target=lambda: out.append(g.poll_once()))
            t.start()
            self.assertTrue(entered.wait(10))
            started = time.monotonic()
            state.set_wind_down(self.r.root, "owner", 1, roster=self.roster())
            self.assertLess(time.monotonic() - started, 5)
            resume.set()
            t.join(10)
        self.assertEqual(out, [None])
        self.assertEqual(self.coordinator_posts("GRANTED"), [])
        self.assertEqual(state.wind_down(self.docs(), self.roster())["step"], 1)
        self.assertIn("withdraw", err.getvalue())

    def wind_down_first(self):
        """Force the order: the owner's record lands, then the GRANTED."""
        def post(root, pid, to_id, summary, body=None):
            state.set_wind_down(root, "owner", 1, roster=self.roster())
            return post_reply(root, pid, to_id, summary, body=body)
        return self.granter(post)

    def test_grant_that_lands_after_a_wind_down_is_withdrawn(self):
        self.assertIsNone(self.wind_down_first().poll_once())
        [(grant, _)] = self.coordinator_posts("GRANTED")
        [(void, doc)] = self.coordinator_posts("GRANT WITHDRAWN")
        self.assertEqual((doc["replyTo"], doc["to"]), (grant, ["alpha"]))
        self.assertTrue(doc["summary"].startswith("GRANT WITHDRAWN alpha feat-1112"))
        q = self.queue()
        self.assertEqual(q["holders"], [])
        self.assertEqual([(w["id"], "granted" in w) for w in q["waiting"]], [(self.ask, False)])
        self.assertEqual(q["next"], "alpha")
        self.assertIn(void, [did for did, _d, _r in thread_replies_for(self.docs(), "alpha",
                                                                       roster=self.roster())])

    def test_withdrawn_grant_gets_a_new_grant_after_step_0(self):
        g = self.wind_down_first()
        self.assertIsNone(g.poll_once())
        g.post = post_reply
        self.assertIsNone(g.poll_once())  # still step 1
        state.set_wind_down(self.r.root, "owner", 0, roster=self.roster())
        self.assertEqual(g.poll_once(), self.ask)
        grants = self.coordinator_posts("GRANTED")
        self.assertEqual(len(grants), 2)
        self.assertEqual(len(self.coordinator_posts("GRANT WITHDRAWN")), 1)
        [w] = self.queue()["waiting"]
        self.assertEqual(w["granted"], max(did for did, _d in grants))


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
