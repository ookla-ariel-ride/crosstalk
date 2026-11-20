"""The duplicate guard: a sender's same text within 10 minutes is refused (logs.duplicate_problem)."""
import copy
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo

from crosstalk.consults import post_consult, post_question, post_reply, retract_post
from crosstalk.logs import append_line
from crosstalk.participants import load_roster

REPO = Path(__file__).resolve().parent.parent

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["participants"].append(
    {"id": "gamma", "name": "Gamma", "kind": "project", "color": "#2e7d32",
     "colorDark": "#81c784", "trust": "approved"})

RANKED = "1. parser\n2. lexer\n3. docs"


def ago(minutes):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - minutes * 60))


class DupGuardTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        self.roster = load_roster(self.r.root / "participants.json")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def post(self, mid, to=None, body=RANKED, frm="alpha", t=None, allow=False, **extra):
        data = dict({"from": frm, "kind": "sync", "summary": "ranked list"}, **extra)
        if body is not None:
            data["body"] = body
        if to is not None:
            data["to"] = to
        return append_line(self.r.root, frm, "set", "messages", mid, data, t=t,
                           roster=self.roster, allow_duplicate=allow)

    def refused(self, fn):
        with self.assertRaises(ValueError) as cm:
            fn()
        self.assertIn("duplicate", str(cm.exception))
        return str(cm.exception)

    def test_copies_to_different_readers_refused(self):
        self.post("alpha-1", to=["beta"])
        msg = self.refused(lambda: self.post("alpha-2", to=["gamma"]))
        self.assertIn("alpha-1", msg)
        self.assertIn("--to p1 p2", msg)
        self.assertIn("retract", msg)

    def test_whitespace_does_not_hide_a_copy(self):
        self.post("alpha-1", to=["beta"])
        self.refused(lambda: self.post("alpha-2", to=["gamma"], body="  1. parser\n\n2. lexer  3. docs\n"))

    def test_same_readers_refused(self):
        self.post("alpha-1", to=["beta", "gamma"])
        self.refused(lambda: self.post("alpha-2", to=["beta", "gamma"]))

    def test_no_body_compares_summary(self):
        self.post("alpha-1", body=None, to=["beta"])
        self.refused(lambda: self.post("alpha-2", body=None, to=["gamma"]))
        self.post("alpha-3", body=None, summary="another line")

    def test_after_ten_minutes_allowed(self):
        self.post("alpha-1", to=["beta"], t=ago(11))
        self.post("alpha-2", to=["gamma"])

    def test_different_body_allowed(self):
        self.post("alpha-1", to=["beta"])
        self.post("alpha-2", to=["gamma"], body="something else")

    def test_other_refs_or_options_allowed(self):
        self.post("alpha-1", body=None, refs=["alpha@1111111"])
        self.post("alpha-2", body=None, refs=["alpha@2222222"])
        self.refused(lambda: self.post("alpha-3", body=None, refs=["alpha@2222222"]))
        post_question(self.r.root, "alpha", "Ship it?", options=["now", "later"])
        post_question(self.r.root, "alpha", "Ship it?", options=["now", "never"])

    def test_other_sender_allowed(self):
        self.post("alpha-1", to=["gamma"])
        self.post("beta-1", to=["gamma"], frm="beta")

    def test_update_allowed(self):
        self.post("alpha-1", to=["beta"])
        append_line(self.r.root, "alpha", "update", "messages", "alpha-1",
                    {"body": RANKED}, roster=self.roster)

    def test_set_on_existing_id_allowed(self):
        self.post("alpha-1", to=["beta"])
        self.post("alpha-1", to=["beta", "gamma"])

    def test_land_words_allowed(self):
        for i, s in enumerate(["TURN ASK alpha parser-fix-1119", "LAND START alpha parser-fix-1119",
                               "LAND END alpha parser-fix-1119", "LANE START alpha", "LANE END alpha",
                               "GRANTED alpha parser-fix-1119", "GRANT WITHDRAWN alpha"]):
            self.post("alpha-%da" % i, body=None, summary=s)
            self.post("alpha-%db" % i, body=None, summary=s)

    def test_override_allowed(self):
        self.post("alpha-1", to=["beta"])
        self.post("alpha-2", to=["gamma"], allow=True)

    def test_owner_allowed(self):
        self.post("owner-1", frm="owner", kind="owner", to=["beta"])
        self.post("owner-2", frm="owner", kind="owner", to=["gamma"])

    def test_retracted_post_does_not_count(self):
        self.post("alpha-1", to=["beta"])
        retract_post(self.r.root, "alpha", "alpha-1", "sent to one reader only")
        self.post("alpha-2", to=["beta", "gamma"])

    def test_replies_to_different_messages_allowed(self):
        self.post("beta-1", frm="beta", body=None, summary="proposal one")
        self.post("beta-2", frm="beta", body=None, summary="proposal two")
        post_reply(self.r.root, "alpha", "beta-1", "VOTE APPROVE")
        post_reply(self.r.root, "alpha", "beta-2", "VOTE APPROVE")
        self.refused(lambda: post_reply(self.r.root, "alpha", "beta-2", "VOTE APPROVE"))

    def test_reply_consult_ask_routes(self):
        self.post("beta-1", frm="beta", body=None, summary="question")
        post_reply(self.r.root, "alpha", "beta-1", "answer", body=RANKED, to=["beta"])
        self.refused(lambda: post_reply(self.r.root, "alpha", "beta-1", "answer", body=RANKED,
                                        to=["gamma"]))
        post_reply(self.r.root, "alpha", "beta-1", "answer", body=RANKED, to=["gamma"],
                   allow_duplicate=True)
        post_consult(self.r.root, "gamma", "help?", body=RANKED, to=["alpha"])
        self.refused(lambda: post_consult(self.r.root, "gamma", "help?", body=RANKED, to=["beta"]))
        post_question(self.r.root, "beta", "Which first?")
        self.refused(lambda: post_question(self.r.root, "beta", "Which first?"))


class DupGuardCliTests(unittest.TestCase):
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
                              capture_output=True, text=True, cwd=str(self.r.root))

    def test_consult_copies_and_override(self):
        base = ["consult", "--from", "alpha", "--summary", "ranked list", "--body", RANKED]
        self.assertEqual(self.run_cli(*base, "--to", "beta").returncode, 0)
        out = self.run_cli(*base, "--to", "gamma")
        self.assertEqual(out.returncode, 1)
        self.assertIn("duplicate", out.stderr)
        self.assertEqual(self.run_cli(*base, "--to", "gamma", "--allow-duplicate").returncode, 0)

    def test_append_refused_and_override(self):
        data = '{"from":"alpha","kind":"sync","summary":"s","body":"same","to":["beta"]}'
        base = ["append", "--from", "alpha", "--op", "set", "--collection", "messages", "--data", data]
        self.assertEqual(self.run_cli(*base, "--id", "alpha-1").returncode, 0)
        self.assertEqual(self.run_cli(*base, "--id", "alpha-2").returncode, 1)
        self.assertEqual(self.run_cli(*base, "--id", "alpha-2", "--allow-duplicate").returncode, 0)


if __name__ == "__main__":
    unittest.main()
