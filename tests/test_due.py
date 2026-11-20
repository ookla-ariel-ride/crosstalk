"""consult --due: a proposal with a due time, the votes it waits for, and its state at that time."""
import calendar
import copy
import json
import shutil
import subprocess
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk import marks
from crosstalk.logs import message_problems, read_log
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.reports import inbox_report

REPO = Path(__file__).resolve().parent.parent

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["participants"].append(
    {"id": "gamma", "name": "Gamma", "kind": "project", "color": "#2e7d32", "colorDark": "#81c784",
     "checkInMin": 30, "trust": "approved"})

DUE = "2026-11-20T12:00:00Z"
BEFORE = calendar.timegm((2026, 11, 20, 11, 0, 0))
AFTER = calendar.timegm((2026, 11, 20, 13, 0, 0))


def msg(seq, frm, id_, t="2026-11-19T10:00:00Z", **data):
    return line(seq, frm, id_=id_, data=dict(data, **{"from": frm}), t=t)


def vote(seq, frm, item, choice, t="2026-11-19T11:00:00Z"):
    return line(seq, frm, "acks", id_="%s--%s" % (frm, item),
                data={"from": frm, "item": item, "mark": "vote", "vote": choice, "t": t}, t=t)


class ParseDueTests(unittest.TestCase):
    NOW = calendar.timegm((2026, 11, 19, 10, 0, 0))

    def test_iso_with_z_or_offset_is_kept_in_utc(self):
        self.assertEqual(marks.parse_due("2026-11-20T12:00:00Z", self.NOW), DUE)
        self.assertEqual(marks.parse_due("2026-11-20T12:00Z", self.NOW), DUE)
        self.assertEqual(marks.parse_due(" 2026-11-20T14:00:00+02:00 ", self.NOW), DUE)
        self.assertEqual(marks.parse_due("2026-11-20T07:00-05:00", self.NOW), DUE)

    def test_local_hhmm_is_the_next_such_time(self):
        for text in ("09:30", "23:59", "7:05"):
            got = marks.due_utc(marks.parse_due(text, self.NOW)).timestamp()
            self.assertGreater(got, self.NOW)
            self.assertLessEqual(got, self.NOW + 86400 + 3600)
            local = datetime.fromtimestamp(got)
            self.assertEqual((local.hour, local.minute), tuple(int(x) for x in text.split(":")))

    def test_refusals(self):
        for text in ("tomorrow", "2026-11-20", "2026-11-20T12:00:00", "2026-11-20 12:00Z", "24:00", "12:60",
                     "2026-13-01T00:00Z", "", None):
            with self.subTest(text=text), self.assertRaises(ValueError):
                marks.parse_due(text, self.NOW)
        with self.assertRaisesRegex(ValueError, "in the past"):
            marks.parse_due("2026-11-19T09:00:00Z", self.NOW)
        with self.assertRaisesRegex(ValueError, "in the past"):
            marks.parse_due("2026-11-19T10:00:00Z", self.NOW)

    def test_log_checks_the_field(self):
        roster = load_roster_from(ROSTER)
        base = {"from": "alpha", "kind": "request", "summary": "s"}
        self.assertEqual(message_problems("messages", "set", dict(base, due=DUE), roster, now=BEFORE)[0], [])
        errors = message_problems("messages", "set", dict(base, due=DUE), roster, now=AFTER)[0]
        self.assertIn("due %s is in the past" % DUE, errors)
        for bad in ("2026-11-20T12:00Z", "soon", 7, None, []):
            with self.subTest(bad=bad):
                self.assertTrue(any("not UTC ISO 8601" in e for e in
                                    message_problems("messages", "set", dict(base, due=bad), roster, now=BEFORE)[0]))


def load_roster_from(data):
    with TempRepo() as r:
        r.write_roster(data)
        return load_roster(r.root / "participants.json")


class DueTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        self.r.write_log("beta", [
            msg(1, "beta", "beta-p1", kind="request", summary="adopt the plan?", to=["alpha", "gamma"], due=DUE),
            msg(2, "beta", "beta-p2", kind="request", summary="no due", to=["alpha"]),
        ])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def docs(self):
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root, roster)
        return enrich_messages(replay(lines), lines), roster

    def tally(self, now):
        docs, roster = self.docs()
        return marks.tally(docs, "messages", "beta-p1", roster, now=now)

    def test_read_refuses_a_due_that_is_not_utc(self):
        path = self.r.write_log("gamma", [msg(1, "gamma", "gamma-x", kind="request", summary="x", due="12:00")])
        problems = read_log(path, load_roster(self.r.root / "participants.json"))[1]
        self.assertTrue(problems and problems[0].fatal and "due" in problems[0].message)

    def test_inbox_lists_the_owed_vote_and_flags_it_overdue(self):
        docs, roster = self.docs()
        before = inbox_report(docs, "alpha", roster, now_iso="2026-11-20T11:00:00Z")["due"]
        self.assertEqual([(d["id"], d["due"], d["overdue"], d["state"]) for d in before],
                         [("beta-p1", DUE, False, "open")])
        after = inbox_report(docs, "alpha", roster, now_iso="2026-11-20T13:00:00Z")["due"]
        self.assertEqual([(d["id"], d["overdue"], d["state"]) for d in after], [("beta-p1", True, "passed")])
        self.assertEqual(inbox_report(docs, "beta", roster)["due"], [])

    def test_a_vote_or_a_reply_clears_the_owed_vote(self):
        self.r.write_log("alpha", [vote(1, "alpha", "beta-p1", "ADOPT")])
        self.r.write_log("gamma", [msg(1, "gamma", "gamma-r", kind="reply", summary="ok", replyTo="beta-p1")])
        docs, roster = self.docs()
        self.assertEqual(marks.due_owed(docs, roster, AFTER), [])

    def test_retracted_or_no_answer_proposal_owes_nothing(self):
        self.r.write_log("beta", [
            msg(1, "beta", "beta-p1", kind="request", summary="x", to=["alpha"], due=DUE, needs_answer=False),
            msg(2, "beta", "beta-p3", kind="request", summary="y", to=["alpha"], due=DUE, retracted=True),
        ])
        docs, roster = self.docs()
        self.assertEqual(marks.due_owed(docs, roster, AFTER), [])
        self.assertEqual(marks.tally(docs, "messages", "beta-p3", roster, now=AFTER)["state"], "retracted")

    def test_tally_state_without_object(self):
        self.r.write_log("alpha", [vote(1, "alpha", "beta-p1", "ADOPT")])
        v = self.tally(BEFORE)
        self.assertEqual((v["due"], v["state"], v["pending"]), (DUE, "open", ["gamma"]))
        self.assertEqual(self.tally(AFTER)["state"], "passed")

    def test_tally_state_with_object(self):
        self.r.write_log("gamma", [vote(1, "gamma", "beta-p1", "OBJECT")])
        self.assertEqual(self.tally(BEFORE)["state"], "open")
        self.assertEqual(self.tally(AFTER)["state"], "objected")

    def test_an_object_after_the_due_time_does_not_count(self):
        self.r.write_log("gamma", [vote(1, "gamma", "beta-p1", "OBJECT", t="2026-11-20T12:30:00Z")])
        self.assertEqual(self.tally(AFTER)["state"], "passed")

    def test_a_post_without_due_has_no_state(self):
        docs, roster = self.docs()
        v = marks.tally(docs, "messages", "beta-p2", roster, now=AFTER)
        self.assertEqual((v["due"], v["state"]), (None, None))

    def test_the_page_views_carry_the_votes(self):
        """page_views puts `votes` on a post with a vote or a due time: the v2 page shows it."""
        self.r.write_log("alpha", [vote(1, "alpha", "beta-p1", "ADOPT"), vote(2, "alpha", "beta-p2", "ADOPT")])
        self.r.write_log("gamma", [line(1, "gamma", "acks", id_="gamma--beta-p1",
                                        data={"from": "gamma", "item": "beta-p1", "mark": "vote", "vote": "OBJECT",
                                              "note": "too soon", "t": "2026-11-19T11:30:00Z"})])
        docs, roster = self.docs()
        views = marks.page_views(docs, roster, {})
        v = views[("messages", "beta-p1")]["votes"]
        self.assertEqual(v["counts"], {"ADOPT": 1, "OBJECT": 1})
        self.assertEqual([(x["from"], x["vote"], x["note"]) for x in v["votes"]],
                         [("alpha", "ADOPT", None), ("gamma", "OBJECT", "too soon")])
        self.assertEqual((v["due"], v["outcome"]), (DUE, "objected"))
        self.assertEqual(views[("messages", "beta-p1")]["marks"], {"alpha": "vote", "gamma": "vote"})
        no_due = views[("messages", "beta-p2")]["votes"]
        self.assertEqual((no_due["counts"], no_due["due"], no_due["state"], no_due["outcome"]),
                         ({"ADOPT": 1}, None, None, None))
        self.assertEqual(marks.view(docs, "messages", "beta-p1", roster, {})["votes"], v)

    def test_a_due_post_with_no_vote_has_votes_and_a_post_with_neither_has_none(self):
        docs, roster = self.docs()
        views = marks.page_views(docs, roster, {})
        self.assertEqual(views[("messages", "beta-p1")]["votes"],
                         {"counts": {}, "votes": [], "due": DUE, "state": marks.due_state(docs[("messages", "beta-p1")], []),
                          "outcome": "passed"})
        self.assertNotIn(("messages", "beta-p2"), views)
        self.assertEqual(marks.vote_view(docs, "messages", "beta-p1", {}, now=BEFORE)["state"], "open")
        self.assertNotIn("votes", marks.view(docs, "messages", "beta-p2", roster, {}))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, cwd="/")

    def test_consult_stores_due_and_refuses_bad_ones(self):
        future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 7200))
        p = self.run_cli("consult", "--from", "beta", "--to", "alpha", "--summary", "plan?", "--due", future)
        self.assertEqual(p.returncode, 0, p.stderr)
        mid = p.stdout.strip()
        rows = [json.loads(x) for x in (self.r.root / "log" / "beta.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["data"]["due"], future)
        for bad in ("2020-01-01T00:00:00Z", "soon"):
            p = self.run_cli("consult", "--from", "beta", "--to", "alpha", "--summary", "x", "--due", bad)
            self.assertEqual(p.returncode, 1, bad)
        self.assertEqual(len((self.r.root / "log" / "beta.jsonl").read_text().splitlines()), 1)
        out = self.run_cli("tally", mid, "--brief").stdout.splitlines()
        self.assertEqual(out[-1], "due %s: open" % future)
        brief = self.run_cli("inbox", "alpha", "--brief").stdout
        self.assertIn("due beta %s: plan?" % mid, brief)

    def test_append_refuses_a_past_due(self):
        data = json.dumps({"from": "beta", "kind": "request", "summary": "x", "due": "2020-01-01T00:00:00Z"})
        p = self.run_cli("append", "--from", "beta", "--op", "set", "--collection", "messages",
                         "--id", "beta-x", "--data", data)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("in the past", p.stderr)

    def test_stalls_lists_overdue_votes(self):
        self.r.write_log("beta", [msg(1, "beta", "beta-p1", kind="request", summary="adopt?",
                                      to=["alpha"], due="2026-11-01T00:00:00Z")])
        p = self.run_cli("stalls", "--root", str(self.r.root / "none"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.splitlines()[1:],
                         ["alpha owes a vote on beta-p1 (due 2026-11-01T00:00:00Z, passed): adopt?",
                          "1 votes overdue"])


if __name__ == "__main__":
    unittest.main()
