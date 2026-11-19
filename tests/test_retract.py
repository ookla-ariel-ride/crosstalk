"""relay.py retract: a sender takes back its own post; it stays in the log and owes nothing."""
import copy
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk.consults import retract_post
from crosstalk.landqueue import land_queue
from crosstalk.logs import append_line
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay

REPO = Path(__file__).resolve().parent.parent
T0 = "2026-10-21T18:00:00Z"

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["relay"] = "relay-bot"
ROSTER["participants"].append(
    {"id": "relay-bot", "name": "Relay", "kind": "project", "color": "#2e7d32",
     "colorDark": "#81c784", "trust": "approved"})


def msg(seq, frm, id_, t=T0, **data):
    return line(seq, frm, id_=id_, data=dict(data, **{"from": frm}), t=t)


class RetractTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")
        self.r.write_log("alpha", [msg(1, "alpha", "alpha-m1", kind="sync", summary="status")])
        self.r.write_log("beta", [
            msg(1, "beta", "beta-r1", kind="request", summary="please review", to=["alpha"]),
            msg(2, "beta", "beta-t1", kind="reply", summary="a reply on your thread",
                replyTo="alpha-m1", t="2026-10-21T18:01:00Z"),
            msg(3, "beta", "beta-mr", kind="sync", summary="read this", must_read=True,
                t="2026-10-21T18:02:00Z"),
            msg(4, "beta", "beta-n1", kind="sync", summary="the new plan", t="2026-10-21T18:03:00Z"),
            msg(5, "beta", "beta-ask", kind="request", summary="TURN ASK beta parser-fix-1119",
                to=["relay-bot"], t="2026-10-21T18:04:00Z"),
            msg(6, "beta", "beta-ask2", kind="request", summary="TURN ASK beta lexer-fix-1119",
                to=["relay-bot"], t="2026-10-21T18:05:00Z"),
            msg(7, "beta", "beta-ls", kind="sync", summary="LAND START beta parser-fix-1119",
                t="2026-10-21T18:06:00Z"),
            msg(8, "beta", "beta-q", kind="question", summary="Which one?", to=["owner"],
                t="2026-10-21T18:07:00Z"),
        ])
        self.r.write_log("owner", [msg(1, "owner", "owner-1", kind="owner", summary="a ruling")])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def retract(self, frm, did, *extra, reason="wrong numbers"):
        args = ["retract", "--from", frm, "--id", did] + list(extra)
        if reason is not None:
            args += ["--reason", reason]
        return self.run_cli(*args)

    def docs(self):
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root, roster)
        return enrich_messages(replay(lines), lines), roster

    def refused(self, p, words):
        self.assertEqual(p.returncode, 1 if words else 2, p.stdout)
        if words:
            self.assertIn(words, p.stderr)
        docs, _roster = self.docs()
        self.assertFalse(any(d.get("retracted") for d in docs.values()))

    def test_refusals(self):
        self.refused(self.retract("alpha", "beta-n1"), "only the sender (beta)")
        self.refused(self.retract("owner", "owner-1"), "cannot be retracted")
        self.refused(self.retract("beta", "beta-ls"), "land word")
        self.refused(self.retract("beta", "beta-n1", "--superseded-by", "beta-nope"), "names no other message")
        self.refused(self.retract("beta", "beta-n1", reason=None), None)
        self.refused(self.retract("beta", "beta-n1", reason="two\nlines"), "single-line")
        self.refused(self.retract("beta", "beta-nope"), "no such message")
        self.refused(self.retract("beta", "beta-q"), "relay.py withdraw")

    def test_granted_turn_ask_refuses(self):
        self.r.write_log("relay-bot", [msg(1, "relay-bot", "relay-bot-g1", kind="reply",
                                           summary="GRANTED beta parser-fix-1119", replyTo="beta-ask",
                                           to=["beta"], t="2026-10-21T18:04:30Z")])
        self.refused(self.retract("beta", "beta-ask"), "has a GRANTED")
        self.refused(self.retract("relay-bot", "relay-bot-g1"), "land word")

    def test_success_sets_fields_and_keeps_the_post(self):
        p = self.retract("beta", "beta-n1", "--superseded-by", "beta-mr")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "beta-n1")
        docs, _roster = self.docs()
        doc = docs[("messages", "beta-n1")]
        self.assertIs(doc["retracted"], True)
        self.assertEqual(doc["retract_reason"], "wrong numbers")
        self.assertEqual(doc["superseded_by"], "beta-mr")
        self.assertRegex(doc["retracted_t"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(doc["summary"], "the new plan")
        self.assertEqual(doc["t"], "2026-10-21T18:03:00Z")  # the post keeps its place
        again = self.retract("beta", "beta-n1")
        self.assertIn("already retracted", again.stderr)

    def test_only_the_writer_may_write_the_fields(self):
        roster = load_roster(self.r.root / "participants.json")
        with self.assertRaisesRegex(ValueError, "only its writer or the owner"):
            append_line(self.r.root, "alpha", "update", "messages", "beta-n1",
                        {"retracted": True}, roster=roster)

    def test_inbox_and_marks_skip_it(self):
        before = json.loads(self.run_cli("inbox", "alpha").stdout)
        self.assertEqual([r["request"]["id"] for r in before["requests"]], ["beta-r1"])
        self.assertIn("beta-t1", [m["message"]["id"] for m in before["thread_replies"]])
        self.assertIn("beta-mr", [i["id"] for i in before["to_read"]])
        for did in ("beta-r1", "beta-t1", "beta-mr"):
            self.assertEqual(self.retract("beta", did).returncode, 0)
        after = json.loads(self.run_cli("inbox", "alpha").stdout)
        self.assertEqual(after["requests"], [])
        self.assertEqual(after["thread_replies"], [])
        self.assertEqual(after["to_read"], [])
        missing = json.loads(self.run_cli("marks", "--missing", "--json").stdout)
        self.assertNotIn("beta-mr", json.dumps(missing))

    def test_reply_to_a_retracted_post_stays(self):
        self.r.write_log("alpha", [
            msg(1, "alpha", "alpha-m1", kind="sync", summary="status"),
            msg(2, "alpha", "alpha-a1", kind="reply", summary="looks fine", replyTo="beta-r1",
                t="2026-10-21T18:10:00Z"),
        ])
        self.assertEqual(self.retract("beta", "beta-r1").returncode, 0)
        docs, _roster = self.docs()
        self.assertEqual(docs[("messages", "alpha-a1")]["replyTo"], "beta-r1")

    def test_ungranted_turn_ask_leaves_the_queue(self):
        docs, _roster = self.docs()
        q = land_queue(docs, coordinator="relay-bot")
        self.assertEqual([h["branches"] for h in q["holders"]], [["parser-fix-1119"]])
        self.assertEqual(self.retract("beta", "beta-ask2").returncode, 0)
        docs, _roster = self.docs()
        q = land_queue(docs, coordinator="relay-bot")
        self.assertEqual([h["branches"] for h in q["holders"]], [["parser-fix-1119"]])
        self.assertEqual(q["waiting"], [])
        self.assertIs(docs[("messages", "beta-ask2")]["retracted"], True)

    def test_retracted_turn_ask_alone_leaves_waiting(self):
        self.r.write_log("alpha", [
            msg(1, "alpha", "alpha-m1", kind="sync", summary="status"),
            msg(2, "alpha", "alpha-ask", kind="request", summary="TURN ASK alpha docs-fix-1119",
                to=["relay-bot"], t="2026-10-21T18:10:00Z"),
        ])
        docs, _roster = self.docs()
        self.assertIn("alpha", [w["project"] for w in land_queue(docs, coordinator="relay-bot")["waiting"]])
        roster = load_roster(self.r.root / "participants.json")
        retract_post(self.r.root, "alpha", "alpha-ask", "asked too early", roster=roster)
        docs, _roster = self.docs()
        self.assertNotIn("alpha", [w["project"] for w in land_queue(docs, coordinator="relay-bot")["waiting"]])


if __name__ == "__main__":
    unittest.main()
