"""ack --vote and tally: a vote is a mark with a choice; tally counts the votes and names who has not answered."""
import copy
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk import marks
from crosstalk.logs import read_log
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.requests import acks_by_item

REPO = Path(__file__).resolve().parent.parent

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["participants"] += [
    {"id": "gamma", "name": "Gamma", "kind": "project", "color": "#2e7d32", "colorDark": "#81c784",
     "checkInMin": 30, "trust": "approved"},
    {"id": "delta", "name": "Delta", "kind": "project", "color": "#5d4037", "colorDark": "#a1887f",
     "checkInMin": 30, "trust": "quarantined"},
]


def msg(seq, frm, id_, t="2026-10-21T18:00:00Z", **data):
    return line(seq, frm, id_=id_, data=dict(data, **{"from": frm}), t=t)


class VoteTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")
        self.r.write_log("beta", [
            msg(1, "beta", "beta-p1", kind="request", summary="adopt the plan?", to=["alpha", "gamma", "delta"]),
            msg(2, "beta", "beta-s1", kind="sync", summary="status"),
        ])
        self.r.write_log("alpha", [msg(1, "alpha", "alpha-q1", kind="question", summary="Which?", to=["owner"])])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def ok(self, *args):
        p = self.run_cli(*args)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def refused(self, code, *args):
        before = self.log_lines()
        p = self.run_cli(*args)
        self.assertEqual(p.returncode, code, p.stdout + p.stderr)
        self.assertEqual(self.log_lines(), before)
        return p.stderr

    def log_lines(self):
        out = []
        for f in sorted((self.r.root / "log").glob("*.jsonl")):
            out.extend(ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip())
        return out

    def docs(self):
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root, roster)
        return enrich_messages(replay(lines), lines), roster

    def row(self, did):
        docs, _roster = self.docs()
        return docs[("acks", did)]

    def tally(self, item):
        return json.loads(self.ok("tally", item))

    def test_vote_sets_the_mark_the_choice_and_the_note(self):
        out = self.ok("ack", "--from", "alpha", "beta-p1", "--vote", " adopt ", "--note", "fine by me")
        self.assertEqual(out.strip(), "alpha--beta-p1")
        row = self.row("alpha--beta-p1")
        self.assertEqual((row["mark"], row["vote"], row["note"], row["item"]), ("vote", "ADOPT", "fine by me", "beta-p1"))
        self.assertNotIn("none", row)
        self.ok("ack", "--from", "gamma", "beta-p1", "--vote", "2")
        self.assertNotIn("note", self.row("gamma--beta-p1"))
        self.assertEqual(self.row("gamma--beta-p1")["vote"], "2")

    def test_a_later_vote_replaces_the_earlier_one(self):
        self.ok("ack", "--from", "alpha", "beta-p1", "--vote", "OBJECT", "--note", "too soon")
        self.ok("ack", "--from", "alpha", "beta-p1", "--vote", "ADOPT")
        t = self.tally("beta-p1")
        self.assertEqual(t["counts"], {"ADOPT": 1})
        self.assertEqual([(v["from"], v["vote"], v["note"]) for v in t["votes"]], [("alpha", "ADOPT", None)])
        # A plain mark after a vote takes the vote back.
        self.ok("ack", "--from", "alpha", "beta-p1", "--seen")
        self.assertEqual(self.tally("beta-p1")["counts"], {})

    def test_a_vote_counts_as_the_answer_on_a_request(self):
        self.ok("ack", "--from", "alpha", "beta-p1", "--vote", "ADOPT")
        docs, roster = self.docs()
        self.assertEqual(acks_by_item(docs, roster=roster), {"beta-p1": {"alpha"}})
        hits = [r for r in json.loads(self.ok("inbox", "gamma"))["requests"] if r["request"]["id"] == "beta-p1"]
        self.assertEqual(hits[0]["pending_from"], ["gamma"])
        self.assertEqual(hits[0]["acked"], ["alpha"])
        config = marks.parsed({})
        self.assertNotIn("alpha", marks.owed(docs, "messages", "beta-p1", roster, config))

    def test_refusals(self):
        self.assertIn("own post", self.refused(1, "ack", "--from", "beta", "beta-p1", "--vote", "ADOPT"))
        for bad in ("", "two words", "x" * 33, "-lead", "a.b"):
            self.assertIn("bad vote", self.refused(1, "ack", "--from", "alpha", "beta-p1", "--vote=" + bad), bad)
        self.assertIn("--note goes only with --vote",
                      self.refused(2, "ack", "--from", "alpha", "beta-p1", "--none", "--note", "x"))
        self.assertIn("not allowed with",
                      self.refused(2, "ack", "--from", "alpha", "beta-p1", "--none", "--vote", "ADOPT"))
        for note in ("x" * (marks.NOTE_MAX + 1), "two\nlines", "  "):
            self.assertIn("one line", self.refused(1, "ack", "--from", "alpha", "beta-p1", "--vote", "ADOPT",
                                                   "--note", note))
        self.assertIn("gets an answer", self.refused(1, "ack", "--from", "gamma", "alpha-q1", "--vote", "ADOPT"))
        self.assertIn("no message", self.refused(1, "tally", "beta-gone"))

    def test_the_log_refuses_a_vote_row_with_a_bad_choice(self):
        good = {"from": "alpha", "item": "beta-p1", "mark": "vote", "t": "2026-10-21T18:05:00Z"}
        for bad in ({}, {"vote": "adopt"}, {"vote": 7}, {"vote": "ADOPT", "note": "a\nb"}, {"vote": "ADOPT", "note": 7}):
            self.r.write_log("alpha", [line(1, "alpha", "acks", id_="alpha--beta-p1", data=dict(good, **bad))])
            self.assertTrue(read_log(self.r.root / "log" / "alpha.jsonl")[1], bad)
        self.r.write_log("alpha", [line(1, "alpha", "acks", id_="alpha--beta-p1", data=dict(good, vote="ADOPT"))])
        self.assertFalse(read_log(self.r.root / "log" / "alpha.jsonl")[1])

    def test_tally_counts_and_pending(self):
        self.ok("ack", "--from", "alpha", "beta-p1", "--vote", "ADOPT", "--note", "yes")
        self.ok("ack", "--from", "delta", "beta-p1", "--vote", "OBJECT")  # quarantined: counts for nothing
        t = self.tally("beta-p1")
        self.assertEqual((t["id"], t["collection"], t["summary"]), ("beta-p1", "messages", "adopt the plan?"))
        self.assertEqual(t["counts"], {"ADOPT": 1})
        self.assertEqual(t["votes"][0]["from"], "alpha")
        self.assertIsInstance(t["votes"][0]["t"], str)
        self.assertEqual(t["pending"], ["gamma"])
        # A reply answers too: gamma leaves pending without a vote.
        self.ok("reply", "--from", "gamma", "--to-id", "beta-p1", "--summary", "new numbers")
        self.assertEqual(self.tally("beta-p1")["pending"], [])
        self.ok("ack", "--from", "gamma", "beta-p1", "--vote", "adopt")
        self.assertEqual(self.tally("beta-p1")["counts"], {"ADOPT": 2})
        self.assertEqual(self.ok("tally", "beta-p1", "--brief").splitlines(),
                         ["ADOPT 2: alpha, gamma", "pending: nobody"])

    def test_tally_brief_with_no_votes(self):
        self.assertEqual(self.ok("tally", "beta-p1", "--brief").splitlines(),
                         ["no votes", "pending: alpha, gamma"])
        self.assertEqual(self.tally("beta-s1")["pending"], [])


if __name__ == "__main__":
    unittest.main()
