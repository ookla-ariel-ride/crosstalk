"""Quiet wake: a live session wakes at once only for posts that need it (notify.wakes_at_once)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402,F401  (puts scripts/ on sys.path)
from helpers import TempRepo, line  # noqa: E402

from crosstalk import config  # noqa: E402
from crosstalk.notify import actionable_items, wakes_at_once  # noqa: E402
from crosstalk.participants import load_roster  # noqa: E402
from crosstalk.replay import enrich_messages, load_all_lines, replay  # noqa: E402

NO = "Done.\n\nNo answer needed."


def post(seq, frm, id_, kind, summary, reply_to=None, t="2026-10-21T19:00:00Z", **extra):
    data = {"from": frm, "kind": kind, "summary": summary}
    if reply_to:
        data["replyTo"] = reply_to
    data.update(extra)
    return line(seq, frm, id_=id_, data=data, t=t)


class RuleTests(unittest.TestCase):
    def test_kinds(self):
        cases = [
            ({"from": "owner", "kind": "owner", "body": NO}, True),
            ({"from": "alpha", "kind": "alert", "body": NO}, True),
            ({"from": "alpha", "kind": "question", "needs_answer": False}, True),
            ({"from": "alpha", "kind": "request"}, True),
            ({"from": "alpha", "kind": "request", "needs_answer": False}, False),
            ({"from": "alpha", "kind": "reply"}, True),
            ({"from": "alpha", "kind": "reply", "body": NO}, False),
            ({"from": "alpha", "kind": "reply", "needs_answer": False}, False),
            ({"from": "alpha", "kind": "review", "body": NO}, False),
            ({"from": "alpha", "kind": "sync"}, False),
            ({"from": "alpha", "kind": "status"}, False),
            ({"from": "alpha", "kind": "check-in"}, False),
            ({"from": "alpha", "kind": "reply", "retracted": True}, False),
        ]
        for doc, want in cases:
            self.assertIs(wakes_at_once(doc), want, doc)

    def test_grant_words_always_wake(self):
        for summary in ("GRANTED beta x-0101", "GRANT WITHDRAWN beta x-0101: step 1"):
            self.assertIs(wakes_at_once({"from": "relay-bot", "kind": "reply", "summary": summary,
                                         "body": NO, "needs_answer": False}), True)
        self.assertIs(wakes_at_once({"from": "alpha", "kind": "reply", "summary": "LAND START x",
                                     "body": NO}), False)


class ActionableTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def items(self, pid="beta"):
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root, roster)
        return actionable_items(enrich_messages(replay(lines), lines), pid, roster)

    def write(self):
        self.r.write_log("beta", [
            post(1, "beta", "beta-root", "request", "TURN ASK beta x-0101", to=["alpha"],
                 t="2026-10-21T18:00:00Z"),
        ])
        self.r.write_log("alpha", [
            post(1, "alpha", "a-ask", "reply", "why?", "beta-root"),
            post(2, "alpha", "a-fyi", "reply", "seen", "beta-root", body=NO),
            post(3, "alpha", "a-flag", "reply", "noted", "beta-root", needs_answer=False),
            post(4, "alpha", "a-sync", "sync", "status", "beta-root"),
            post(5, "alpha", "a-grant", "reply", "GRANTED beta x-0101", "beta-root", body=NO),
            post(6, "alpha", "a-alert", "alert", "fire", "beta-root", body=NO),
            post(7, "alpha", "a-req", "request", "help", to=["beta"]),
            post(8, "alpha", "a-req-fyi", "request", "fyi", to=["beta"], needs_answer=False),
        ])
        self.r.write_log("owner", [post(1, "owner", "own-1", "owner", "hi", "beta-root", body=NO)])

    def test_only_posts_that_need_it_wake(self):
        self.write()
        self.assertEqual(self.items(), ["a-alert", "a-ask", "a-grant", "a-req", "own-1"])

    def test_config_switch_restores_every_thread_reply(self):
        self.r.write_config({"quietWake": False})
        self.write()
        self.assertEqual(self.items(), ["a-alert", "a-ask", "a-flag", "a-fyi", "a-grant", "a-req",
                                        "a-sync", "own-1"])

    def test_config_default_and_bad_value(self):
        self.assertIs(config.DEFAULTS["quietWake"], True)
        self.r.write_config({"quietWake": "no"})
        self.assertIs(config.load(self.r.root)["quietWake"], True)

    def test_land_report_still_wakes_the_coordinator(self):
        self.r.write_roster(dict(helpers.SPEC_ROSTER, relay="beta"))
        self.r.write_log("alpha", [
            post(1, "alpha", "a-start", "request", "LAND START x 1234", to=["alpha"],
                 t="2026-10-21T18:00:00Z"),
            post(2, "alpha", "a-end", "reply", "LAND END x: landed", "a-start", to=["alpha"], body=NO),
        ])
        self.assertEqual(self.items(), ["a-end"])


if __name__ == "__main__":
    unittest.main()
