"""relay.py scorecard: posts, check-ins, gaps, answered requests and owner questions over a window."""
import calendar
import copy
import json
import subprocess
import sys
import unittest

from helpers import SPEC_ROSTER, TempRepo, line, relay_in

from crosstalk import scorecard
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["relay"] = "alpha"
AT = "2026-11-19T12:00:00Z"
END = calendar.timegm((2026, 11, 19, 12, 0, 0))


def msg(seq, frm, id_, t, **data):
    return line(seq, frm, id_=id_, data=dict(data, **{"from": frm}), t=t)


def mark(seq, frm, item, t, word, **extra):
    return line(seq, frm, "acks", id_="%s--%s" % (frm, item),
                data=dict({"from": frm, "item": item, "mark": word, "t": t}, **extra), t=t)


def request(seq, frm, id_, t, to, **extra):
    return msg(seq, frm, id_, t, kind="request", summary="please look", to=to, **extra)


def question(seq, frm, id_, t, **extra):
    return msg(seq, frm, id_, t, kind="question", summary="which one?", **extra)


class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        self.r.write_log("alpha", [
            msg(1, "alpha", "alpha-0", "2026-11-10T00:00:00Z", kind="check-in", summary="old"),
            msg(2, "alpha", "alpha-1", "2026-11-17T13:00:00Z", kind="check-in", summary="first"),
            msg(3, "alpha", "alpha-2", "2026-11-18T13:00:00Z", kind="sync", summary="Check-in: two commits"),
            msg(4, "alpha", "alpha-4", "2026-11-18T14:00:00Z", kind="reply", summary="done", replyTo="beta-r1"),
            mark(5, "alpha", "beta-r2", "2026-11-18T15:10:00Z", "none", none=True),
            mark(6, "alpha", "rev-r3", "2026-11-18T16:04:00Z", "vote", vote="ADOPT"),
            msg(7, "alpha", "alpha-3", "2026-11-19T10:00:00Z", kind="checkin", summary="third"),
            msg(8, "alpha", "alpha-5", "2026-11-19T11:00:00Z", kind="check-in", summary="gone", retracted=True),
            msg(9, "alpha", "alpha-6", "2026-11-19T12:30:00Z", kind="reply", summary="late", replyTo="beta-r7"),
        ])
        self.r.write_log("beta", [
            request(1, "beta", "beta-r0", "2026-11-16T00:00:00Z", ["alpha"]),
            request(2, "beta", "beta-r1", "2026-11-18T13:58:00Z", ["alpha"]),
            request(3, "beta", "beta-r2", "2026-11-18T15:00:00Z", ["alpha"]),
            request(4, "beta", "beta-r4", "2026-11-18T17:00:00Z", ["alpha"], body="FYI.\n\nNo answer needed."),
            request(5, "beta", "beta-r5", "2026-11-18T18:00:00Z", ["alpha"], retracted=True),
            request(6, "beta", "beta-r6", "2026-11-19T11:00:00Z", ["alpha"]),
            request(7, "beta", "beta-r7", "2026-11-19T11:30:00Z", ["alpha"]),
            question(8, "beta", "beta-q1", "2026-11-18T09:00:00Z"),
            question(9, "beta", "beta-q2", "2026-11-18T10:00:00Z"),
        ])
        self.r.write_log("rev", [
            question(1, "rev", "rev-q0", "2026-11-01T00:00:00Z"),
            request(2, "rev", "rev-r3", "2026-11-18T16:00:00Z", ["alpha", "beta"]),
            question(3, "rev", "rev-q1", "2026-11-18T11:00:00Z"),
            question(4, "rev", "rev-q2", "2026-11-18T12:00:00Z"),
            question(5, "rev", "rev-q3", "2026-11-18T13:00:00Z", retracted=True),
        ])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def card(self, end=END, hours=48.0):
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root, roster)
        return scorecard.scorecard(enrich_messages(replay(lines), lines), roster, end, hours)

    def row(self, card, pid):
        return next(r for r in card["participants"] if r["id"] == pid)

    def test_counts_gaps_and_answers(self):
        card = self.card()
        self.assertEqual([r["id"] for r in card["participants"]], ["alpha", "beta", "rev"])
        alpha = self.row(card, "alpha")
        # alpha-5 is retracted; alpha-0 and alpha-6 are outside the window.
        self.assertEqual((alpha["posts"], alpha["check_ins"]), (4, 3))
        # window start 17T12:00 -> 13:00 (60), -> 18T13:00 (1440), -> 19T10:00 (1260), -> end (120).
        self.assertEqual(alpha["longest_gap_min"], 1440.0)
        # r1 by reply (2 min), r2 by a none mark (10), r3 by a vote (4); r6 open; r7 answered after the end.
        # r0 is before the window, r4 needs no answer, r5 is retracted.
        self.assertEqual((alpha["requests"], alpha["answered"]), (5, 3))
        self.assertEqual(alpha["unanswered"], ["beta-r6", "beta-r7"])
        self.assertEqual(alpha["median_answer_min"], 4.0)

    def test_no_check_in_is_a_gap_of_the_whole_window_and_a_late_start_shortens_it(self):
        card = self.card()
        beta = self.row(card, "beta")
        self.assertEqual((beta["posts"], beta["check_ins"], beta["longest_gap_min"]), (7, 0, 2880.0))
        self.assertEqual((beta["requests"], beta["answered"], beta["unanswered"]), (1, 0, ["rev-r3"]))
        self.assertIsNone(beta["median_answer_min"])
        # A window that starts before alpha's first row measures from that row.
        early = self.card(end=calendar.timegm((2026, 11, 10, 1, 0, 0)), hours=48)
        self.assertEqual(self.row(early, "alpha")["longest_gap_min"], 60.0)
        self.assertIsNone(self.row(early, "beta")["longest_gap_min"])

    def test_owner_questions_by_asker(self):
        q = self.card()["owner_questions"]
        self.assertEqual(q, {"count": 4, "by_asker": {"beta": 2, "rev": 2}})

    def test_lines(self):
        self.assertEqual(scorecard.lines(self.card()), [
            "alpha: 4 posts, 3 check-ins, longest gap 1440 min. 5 requests to it, 2 unanswered, median 4 min.",
            "beta: 7 posts, 0 check-ins, longest gap 2880 min. 1 request to it, 1 unanswered.",
            "rev: 3 posts, 0 check-ins, longest gap 2880 min. 0 requests to it.",
            "Owner questions: 4 in 48 hours, 2 each from beta and rev.",
        ])
        card = {"hours": 24.0, "participants": [
            {"id": "gamma", "posts": 9, "check_ins": 2, "longest_gap_min": 61.4, "requests": 2, "answered": 2,
             "unanswered": [], "median_answer_min": 1.0}],
            "owner_questions": {"count": 5, "by_asker": {"alpha": 1, "gamma": 4}}}
        self.assertEqual(scorecard.lines(card), [
            "gamma: 9 posts, 2 check-ins, longest gap 61 min. 2 requests to it, all answered, median 1 min.",
            "Owner questions: 5 in 24 hours, 4 from gamma, 1 from alpha.",
        ])

    def test_daily_slot(self):
        on = {"scorecard": {"daily": True, "at": "09:00", "zone": "America/Los_Angeles", "hours": 48.0}}
        before = calendar.timegm((2026, 11, 19, 16, 59, 0))  # 08:59 PST
        after = calendar.timegm((2026, 11, 19, 17, 0, 0))
        self.assertIsNone(scorecard.daily_slot(on, before))
        self.assertEqual(scorecard.daily_slot(on, after), ("2026-11-19", float(after)))
        self.assertEqual(scorecard.daily_slot(on, after + 3600)[1], float(after))
        self.assertIsNone(scorecard.daily_slot({"scorecard": dict(on["scorecard"], daily=False)}, after))

    def run_relay(self, *args):
        return subprocess.run([sys.executable, str(relay_in(self.r.root))] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)

    def test_cli_plain_and_json(self):
        p = self.run_relay("scorecard", "--at", AT)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.splitlines(), scorecard.lines(self.card()))
        p = self.run_relay("scorecard", "--at", AT, "--json", "--hours", "24")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(sorted(data), ["end", "hours", "owner_questions", "participants", "start"])
        self.assertEqual((data["start"], data["end"], data["hours"]), ("2026-11-18T12:00:00Z", AT, 24.0))
        self.assertEqual(sorted(data["participants"][0]), [
            "answered", "check_ins", "id", "longest_gap_min", "median_answer_min", "posts", "requests",
            "unanswered"])
        self.assertNotEqual(self.run_relay("scorecard", "--at", "yesterday").returncode, 0)

    def test_cli_post_and_daily(self):
        p = self.run_relay("scorecard", "--at", AT, "--post", "--from", "rev")
        self.assertEqual(p.returncode, 0, p.stderr)
        mid = p.stdout.strip()
        rows = [json.loads(x) for x in (self.r.root / "log" / "rev.jsonl").read_text().splitlines()]
        data = next(x["data"] for x in rows if x["id"] == mid)
        self.assertEqual((data["kind"], data["to"], data["needs_answer"]), ("status", ["alpha", "beta"], False))
        self.assertTrue(data["body"].startswith("alpha: 4 posts"))
        self.assertTrue(data["body"].endswith("No answer needed."))
        # Off by default: --daily posts nothing.
        self.assertIn("no daily post due", self.run_relay("scorecard", "--daily").stdout)
        self.r.write_config({"scorecard": {"daily": True, "at": "00:00", "zone": "UTC"}})
        p = self.run_relay("scorecard", "--daily")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertRegex(p.stdout.strip(), r"^alpha-scorecard-\d{4}-\d{2}-\d{2}$")
        p = self.run_relay("scorecard", "--daily")
        self.assertIn("already posted", p.stdout)


if __name__ == "__main__":
    unittest.main()
