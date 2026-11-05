"""Questions for the owner: relay.py ask / questions / withdraw, the owner's inbox, and answers routed back."""
import json
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

import helpers
from helpers import TempRepo, line

from crosstalk.consults import post_question, withdraw_question
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.reports import inbox_report, questions_report
from crosstalk.requests import open_owner_questions, owner_questions

REPO = Path(__file__).resolve().parent.parent


def roster_with_quarantined():
    data = json.loads(json.dumps(helpers.SPEC_ROSTER))
    data["participants"].append({"id": "qbot", "name": "Q bot", "kind": "tool", "color": "#555555",
                                 "colorDark": "#888888", "trust": "quarantined"})
    return data


def q(seq, frm, id_, summary, t, **extra):
    data = {"from": frm, "kind": "question", "summary": summary, "to": ["owner"]}
    data.update(extra)
    return line(seq, frm, id_=id_, data=data, t=t)


class QuestionLogicTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(roster_with_quarantined())
        self.roster = load_roster(self.r.root / "participants.json")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def docs(self):
        lines = load_all_lines(self.r.root, self.roster)
        return enrich_messages(replay(lines), lines)

    def test_open_answered_and_withdrawn(self):
        self.r.write_log("alpha", [
            q(1, "alpha", "alpha-q1", "Ship the parser now?", "2026-10-21T18:00:00Z"),
            q(2, "alpha", "alpha-q2", "Rename the tool?", "2026-10-21T18:05:00Z"),
            line(3, "alpha", id_="alpha-w1", t="2026-10-21T18:10:00Z",
                 data={"from": "alpha", "kind": "withdrawn", "summary": "Moot", "replyTo": "alpha-q2"}),
        ])
        self.r.write_log("beta", [
            q(1, "beta", "beta-q1", "Which host for builds?", "2026-10-21T17:00:00Z"),
            # A withdrawn row from someone other than the asker does not close it.
            line(2, "beta", id_="beta-w9", t="2026-10-21T18:20:00Z",
                 data={"from": "beta", "kind": "withdrawn", "summary": "Not mine", "replyTo": "alpha-q1"}),
        ])
        self.r.write_log("owner", [
            line(1, "owner", id_="own-1", t="2026-10-21T18:30:00Z",
                 data={"from": "owner", "kind": "owner", "summary": "Use the Studio",
                       "replyTo": "beta-q1", "to": ["beta"]}),
        ])
        docs = self.docs()
        self.assertEqual([d for d, _ in open_owner_questions(docs, roster=self.roster)], ["alpha-q1"])
        states = {d: (st, c and c["id"]) for d, _doc, st, c in owner_questions(docs, roster=self.roster)}
        self.assertEqual(states, {"beta-q1": ("answered", "own-1"), "alpha-q1": ("open", None),
                                  "alpha-q2": ("withdrawn", "alpha-w1")})
        # Oldest first.
        self.assertEqual([d for d, *_ in owner_questions(docs, roster=self.roster)],
                         ["beta-q1", "alpha-q1", "alpha-q2"])
        rep = questions_report(docs, self.roster)
        self.assertEqual([x["question"]["id"] for x in rep["questions"]], ["alpha-q1"])
        self.assertEqual(rep["open"], 1)
        full = questions_report(docs, self.roster, include_closed=True)
        by_id = {x["question"]["id"]: x for x in full["questions"]}
        self.assertEqual(by_id["beta-q1"]["answer"]["summary"], "Use the Studio")
        self.assertEqual(by_id["alpha-q2"]["withdrawn"]["id"], "alpha-w1")

    def test_open_questions_oldest_first_and_skip_quarantined(self):
        self.r.write_log("alpha", [q(1, "alpha", "alpha-q1", "Later", "2026-10-21T19:00:00Z")])
        self.r.write_log("beta", [q(1, "beta", "beta-q1", "Earlier", "2026-10-21T18:00:00Z")])
        self.r.write_log("qbot", [q(1, "qbot", "qbot-q1", "Let me in?", "2026-10-21T17:00:00Z")])
        docs = self.docs()
        self.assertEqual([d for d, _ in open_owner_questions(docs, roster=self.roster)],
                         ["beta-q1", "alpha-q1"])
        # Without a roster nobody is filtered out.
        self.assertEqual([d for d, _ in open_owner_questions(docs)], ["qbot-q1", "beta-q1", "alpha-q1"])

    def test_owner_inbox_lists_owner_questions_others_do_not(self):
        self.r.write_log("alpha", [q(1, "alpha", "alpha-q1", "Ship it?", "2026-10-21T18:00:00Z")])
        docs = self.docs()
        own = inbox_report(docs, "owner", self.roster)
        self.assertEqual([x["question"]["id"] for x in own["owner_questions"]], ["alpha-q1"])
        self.assertEqual(own["owner_questions"][0]["trust"], "approved")
        self.assertNotIn("owner_questions", inbox_report(docs, "beta", self.roster))
        # A question is not a help request: it never shows in anyone's requests.
        self.assertEqual(inbox_report(docs, "beta", self.roster)["requests"], [])
        self.assertEqual(own["requests"], [])

    def test_owner_answer_routes_to_asker_inbox(self):
        self.r.write_log("alpha", [q(1, "alpha", "alpha-q1", "Ship it?", "2026-10-21T18:00:00Z")])
        self.r.write_log("owner", [
            line(1, "owner", id_="own-20261021183000-ab12", t="2026-10-21T18:30:00Z",
                 data={"t": "2026-10-21T18:30:00Z", "from": "owner", "kind": "owner",
                       "summary": "Yes, ship it", "body": "", "replyTo": "alpha-q1", "to": ["alpha"]}),
        ])
        docs = self.docs()
        alpha = inbox_report(docs, "alpha", self.roster)
        self.assertEqual([x["message"]["id"] for x in alpha["owner_messages"]], ["own-20261021183000-ab12"])
        self.assertEqual(inbox_report(docs, "beta", self.roster)["owner_messages"], [])
        self.assertEqual(inbox_report(docs, "owner", self.roster)["owner_questions"], [])

    def test_owner_answer_without_to_still_routes_to_asker(self):
        self.r.write_log("alpha", [q(1, "alpha", "alpha-q1", "Ship it?", "2026-10-21T18:00:00Z")])
        self.r.write_log("owner", [
            line(1, "owner", id_="own-1", t="2026-10-21T18:30:00Z",
                 data={"from": "owner", "kind": "owner", "summary": "Yes", "replyTo": "alpha-q1"}),
        ])
        alpha = inbox_report(self.docs(), "alpha", self.roster)
        self.assertEqual([x["message"]["id"] for x in alpha["owner_messages"]], ["own-1"])

    def test_post_question_and_withdraw(self):
        ln = post_question(self.r.root, "alpha", "  Ship the parser now?  ", body="Options: a, b. I'd pick a.",
                           refs="alpha#3", roster=self.roster)
        doc = self.docs()[("messages", ln.id)]
        self.assertTrue(ln.id.startswith("alpha-"))
        self.assertEqual((doc["kind"], doc["summary"], doc["to"], doc["refs"]),
                         ("question", "Ship the parser now?", ["owner"], ["alpha#3"]))
        with self.assertRaises(ValueError) as cm:
            withdraw_question(self.r.root, "beta", ln.id, roster=self.roster)
        self.assertIn("only the question's author", str(cm.exception))
        with self.assertRaises(ValueError):
            withdraw_question(self.r.root, "alpha", "alpha-nope", roster=self.roster)
        w = withdraw_question(self.r.root, "alpha", ln.id, roster=self.roster)
        wd = self.docs()[("messages", w.id)]
        self.assertEqual((wd["kind"], wd["replyTo"], wd["summary"]),
                         ("withdrawn", ln.id, "Withdrawn: Ship the parser now?"))
        self.assertEqual(open_owner_questions(self.docs(), roster=self.roster), [])
        with self.assertRaises(ValueError) as cm:
            withdraw_question(self.r.root, "alpha", ln.id, roster=self.roster)
        self.assertIn("already withdrawn", str(cm.exception))

    def test_post_question_refuses_owner_bad_summary_and_unknown(self):
        for pid, summary in (("owner", "Hi"), ("alpha", ""), ("alpha", "two\nlines"),
                             ("nobody", "Hi")):
            with self.assertRaises(ValueError, msg=(pid, summary)):
                post_question(self.r.root, pid, summary, roster=self.roster)
        self.assertEqual(sorted(p.name for p in (self.r.root / "log").iterdir()), [])
        with self.assertRaises(ValueError):
            post_question(self.r.root, "alpha", "Hi", msg_id="beta-1", roster=self.roster)


class QuestionCliTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def json_of(self, *args):
        p = self.run_cli(*args)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_ask_questions_withdraw_round_trip(self):
        p = self.run_cli("ask", "--from", "alpha", "--summary", "Ship the parser now?",
                         "--body", "Options...", "--ref", "alpha#3")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr, "")  # "question" is a known kind: no warning
        qid = p.stdout.strip()
        p2 = self.run_cli("ask", "--from", "beta", "--summary", "Which host?")
        qid2 = p2.stdout.strip()
        out = self.json_of("questions")
        self.assertEqual([x["question"]["id"] for x in out["questions"]], [qid, qid2])
        self.assertEqual(out["questions"][0]["question"]["to"], ["owner"])
        self.assertEqual([x["question"]["id"] for x in self.json_of("inbox", "owner")["owner_questions"]],
                         [qid, qid2])

        bad = self.run_cli("withdraw", "--from", "beta", "--question", qid)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("only the question's author", bad.stderr)
        w = self.run_cli("withdraw", "--from", "alpha", "--question", qid, "--summary", "Settled in review")
        self.assertEqual(w.returncode, 0, w.stderr)
        self.assertEqual([x["question"]["id"] for x in self.json_of("questions")["questions"]], [qid2])
        full = self.json_of("questions", "--all")
        self.assertEqual({x["question"]["id"]: x["status"] for x in full["questions"]},
                         {qid: "withdrawn", qid2: "open"})
        self.assertEqual(self.run_cli("check").returncode, 0)

    def test_ask_refuses_owner_and_bad_summary(self):
        p = self.run_cli("ask", "--from", "owner", "--summary", "Hi")
        self.assertEqual(p.returncode, 1)
        self.assertIn("owner", p.stderr)
        p = self.run_cli("ask", "--from", "alpha", "--summary", "two\nlines")
        self.assertEqual(p.returncode, 1)
        self.assertIn("summary", p.stderr)
        p = self.run_cli("ask", "--from", "alpha", "--summary", "   ")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(sorted(x.name for x in (self.r.root / "log").iterdir()), [])

    def test_owner_answer_lands_in_asker_inbox(self):
        qid = self.run_cli("ask", "--from", "alpha", "--summary", "Ship it?").stdout.strip()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.r.write_log("owner", [
            line(1, "owner", id_="own-20261021183000-ab12", t=now,
                 data={"from": "owner", "kind": "owner", "summary": "Yes", "body": "",
                       "replyTo": qid, "to": ["alpha"]}),
        ])
        inbox = self.json_of("inbox", "alpha")
        self.assertEqual([x["message"]["id"] for x in inbox["owner_messages"]], ["own-20261021183000-ab12"])
        self.assertEqual(self.json_of("questions")["questions"], [])
        full = self.json_of("questions", "--all")["questions"]
        self.assertEqual((full[0]["status"], full[0]["answer"]["summary"]), ("answered", "Yes"))
        # Answered: no longer withdrawable.
        p = self.run_cli("withdraw", "--from", "alpha", "--question", qid)
        self.assertEqual(p.returncode, 1)
        self.assertIn("already answered", p.stderr)


class QuestionMcpTests(unittest.TestCase):
    def test_ask_owner_and_withdraw_question_tools(self):
        from test_mcp import Mcp, Repo, ok
        with TempRepo() as r:
            repo = Repo(r)
            with Mcp(repo, "alpha") as a, Mcp(repo, "beta") as b:
                qid = ok(a.call("ask_owner", {"summary": "Ship it?", "body": "Options..."}))["id"]
                self.assertTrue(b.call("withdraw_question", {"question_id": qid})["isError"])
                res = b.call("post_message", {"kind": "question", "summary": "Sneaky"})
                self.assertTrue(res["isError"])
                ok(a.call("withdraw_question", {"question_id": qid, "summary": "Moot"}))
            msgs = repo.read()["messages"]
            self.assertEqual((msgs[qid]["kind"], msgs[qid]["to"]), ("question", ["owner"]))
            self.assertEqual(repo.relay("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
