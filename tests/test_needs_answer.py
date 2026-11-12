"""needs_answer: a request whose field is false, or whose body ends with "No answer needed", waits for nobody."""
import copy
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk import notify
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.requests import (
    all_requests_for,
    inbox_for,
    needs_answer,
    open_requests,
    pending_recipients,
)

REPO = Path(__file__).resolve().parent.parent

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["participants"].append(
    {"id": "gamma", "name": "Gamma", "kind": "project", "color": "#2e7d32", "colorDark": "#81c784",
     "checkInMin": 30, "trust": "approved"})


def msg(seq, frm, id_, t="2026-10-21T18:00:00Z", **data):
    data = dict(data, **{"from": frm})
    return line(seq, frm, id_=id_, data=data, t=t)


class HelperTests(unittest.TestCase):
    def test_field_false_needs_no_answer(self):
        self.assertIs(needs_answer({"needs_answer": False, "body": "x"}), False)

    def test_sentence_at_the_end(self):
        for body in ("Done. No answer needed", "Done. No answer needed.", "Done.\nNo answer needed.  \n"):
            self.assertIs(needs_answer({"body": body}), False, body)

    def test_sentence_in_the_middle_needs_an_answer(self):
        self.assertIs(needs_answer({"body": 'He wrote "No answer needed" but I ask: why?'}), True)
        self.assertIs(needs_answer({}), True)


class NeedsAnswerTests(unittest.TestCase):
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
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def inbox(self, pid, *extra):
        p = self.run_cli("inbox", pid, *extra)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def docs(self):
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root, roster)
        return enrich_messages(replay(lines), lines), roster

    def messages(self):
        docs, _roster = self.docs()
        return {did: doc for (coll, did), doc in docs.items() if coll == "messages"}

    def log_lines(self):
        out = []
        for f in sorted((self.r.root / "log").glob("*.jsonl")):
            out.extend(ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip())
        return out

    def check_waits_for_nobody(self):
        docs, roster = self.docs()
        doc = docs[("messages", "beta-r1")]
        self.assertEqual(pending_recipients(doc, [], roster=roster), [])
        self.assertNotIn("beta-r1", [d for d, _doc, _r in inbox_for(docs, "alpha", roster=roster)])
        self.assertNotIn("beta-r1", [d for d, _doc, _r in open_requests(docs, roster=roster)])
        self.assertIn("beta-r1", [d for d, _doc, _r in all_requests_for(docs, "alpha", roster=roster)])
        self.assertEqual(self.inbox("alpha")["requests"], [])
        listed = self.inbox("alpha", "--all")["requests"]
        self.assertEqual([r["request"]["id"] for r in listed], ["beta-r1"])
        self.assertIs(listed[0]["needs_answer"], False)
        self.assertEqual(listed[0]["pending_from"], [])

    def test_no_answer_request_waits_for_nobody(self):
        self.r.write_log("beta", [msg(1, "beta", "beta-r1", kind="request", summary="fyi",
                                      to=["alpha", "gamma"], needs_answer=False)])
        self.check_waits_for_nobody()

    def test_sentence_request_waits_for_nobody(self):
        self.r.write_log("beta", [msg(1, "beta", "beta-r1", kind="request", summary="fyi",
                                      to=["alpha", "gamma"], body="Landed.\n\nNo answer needed.")])
        self.check_waits_for_nobody()

    def test_live_notice_skips_a_no_answer_request(self):
        self.r.write_log("beta", [
            msg(1, "beta", "beta-r1", kind="request", summary="fyi", to=["alpha", "gamma"],
                needs_answer=False),
            msg(2, "beta", "beta-r2", kind="request", summary="help", to=["alpha", "gamma"]),
        ])
        docs, roster = self.docs()
        self.assertEqual(notify.actionable_items(docs, "alpha", roster), ["beta-r2"])

    def test_inbox_items_carry_needs_answer(self):
        self.r.write_log("alpha", [msg(1, "alpha", "alpha-m1", kind="sync", summary="status")])
        self.r.write_log("beta", [
            msg(1, "beta", "beta-r1", kind="request", summary="help", to=["alpha"]),
            msg(2, "beta", "beta-t1", kind="reply", summary="seen", replyTo="alpha-m1",
                t="2026-10-21T18:01:00Z"),
            msg(3, "beta", "beta-t2", kind="reply", summary="noted", replyTo="alpha-m1",
                body="Thanks. No answer needed.", t="2026-10-21T18:02:00Z"),
        ])
        self.r.write_log("owner", [msg(1, "owner", "owner-1", kind="owner", summary="hi", to=["alpha"])])
        out = self.inbox("alpha")
        self.assertEqual([(r["request"]["id"], r["needs_answer"]) for r in out["requests"]],
                         [("beta-r1", True)])
        self.assertEqual([(m["message"]["id"], m["needs_answer"]) for m in out["owner_messages"]],
                         [("owner-1", True)])
        self.assertEqual([(m["message"]["id"], m["needs_answer"]) for m in out["thread_replies"]],
                         [("beta-t2", False), ("beta-t1", True)])
        self.assertNotIn("needs_answer", out["requests"][0]["request"])

    def test_reply_no_answer_sets_field_and_sentence(self):
        self.r.write_log("beta", [msg(1, "beta", "beta-r1", kind="request", summary="help", to=["alpha"])])
        cases = (("ok", "ok\n\nNo answer needed."),
                 ("ok. No answer needed.", "ok. No answer needed."),
                 (None, "No answer needed."))
        for body, want in cases:
            args = ["reply", "--from", "alpha", "--to-id", "beta-r1", "--summary", "done", "--no-answer"]
            if body is not None:
                args += ["--body", body]
            p = self.run_cli(*args)
            self.assertEqual(p.returncode, 0, p.stderr)
            doc = self.messages()[p.stdout.strip()]
            self.assertIs(doc["needs_answer"], False)
            self.assertEqual(doc["body"], want)

    def test_consult_no_answer(self):
        p = self.run_cli("consult", "--from", "beta", "--summary", "fyi", "--to", "alpha",
                         "--body", "Landed", "--no-answer")
        self.assertEqual(p.returncode, 0, p.stderr)
        mid = p.stdout.strip()
        doc = self.messages()[mid]
        self.assertIs(doc["needs_answer"], False)
        self.assertEqual(doc["body"], "Landed\n\nNo answer needed.")
        self.assertEqual(self.inbox("alpha")["requests"], [])

    def test_append_no_answer_on_a_new_message(self):
        p = self.run_cli("append", "--from", "beta", "--op", "set", "--collection", "messages",
                         "--id", "beta-n1", "--no-answer",
                         "--data", json.dumps({"from": "beta", "kind": "request", "summary": "fyi",
                                               "to": ["alpha"], "body": "Landed"}))
        self.assertEqual(p.returncode, 0, p.stderr)
        doc = self.messages()["beta-n1"]
        self.assertIs(doc["needs_answer"], False)
        self.assertEqual(doc["body"], "Landed\n\nNo answer needed.")

    def test_append_no_answer_elsewhere_is_refused(self):
        self.r.write_log("beta", [msg(1, "beta", "beta-r1", kind="request", summary="help", to=["alpha"])])
        before = self.log_lines()
        for op, coll, did in (("update", "messages", "beta-r1"), ("set", "locks", "beta-l1")):
            p = self.run_cli("append", "--from", "beta", "--op", op, "--collection", coll,
                             "--id", did, "--no-answer", "--data", json.dumps({"summary": "x"}))
            self.assertEqual(p.returncode, 1, (op, coll))
            self.assertIn("--no-answer is for a new message", p.stderr)
        self.assertEqual(self.log_lines(), before)

    def test_ask_refuses_no_answer(self):
        p = self.run_cli("ask", "--from", "alpha", "--summary", "Which one?", "--no-answer")
        self.assertEqual(p.returncode, 1)
        self.assertIn("a question to the owner needs an answer; ask takes no --no-answer", p.stderr)
        self.assertEqual(self.log_lines(), [])


if __name__ == "__main__":
    unittest.main()
