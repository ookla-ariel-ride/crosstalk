"""ack --none: a participant records that a request needs nothing from it; its name leaves pending_from."""
import copy
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk import notify
from crosstalk.logs import read_log
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.requests import acks_by_item, open_requests

REPO = Path(__file__).resolve().parent.parent

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["participants"] += [
    {"id": "gamma", "name": "Gamma", "kind": "project", "color": "#2e7d32", "colorDark": "#81c784",
     "checkInMin": 30, "trust": "approved"},
    {"id": "delta", "name": "Delta", "kind": "project", "color": "#5d4037", "colorDark": "#a1887f",
     "checkInMin": 30, "trust": "quarantined"},
    # Two ids whose acks would share the id alpha--b--x: alpha's ack of b--x and alpha--b's ack of x.
    {"id": "b", "name": "B", "kind": "project", "color": "#8a5a00", "colorDark": "#d9b25c",
     "checkInMin": 30, "trust": "approved"},
    {"id": "alpha--b", "name": "Alpha B", "kind": "project", "color": "#c77700", "colorDark": "#f0a73a",
     "checkInMin": 30, "trust": "approved"},
]


def msg(seq, frm, id_, t="2026-10-21T18:00:00Z", **data):
    data = dict(data, **{"from": frm})
    return line(seq, frm, id_=id_, data=data, t=t)


def ack_row(seq, frm, item, id_=None):
    return line(seq, frm, collection="acks", id_=id_ or "%s--%s" % (frm, item),
                data={"from": frm, "item": item, "none": True, "t": "2026-10-21T18:05:00Z"})


class AckTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")
        self.r.write_log("beta", [
            msg(1, "beta", "beta-r2", kind="request", summary="two", to=["alpha", "gamma"]),
            msg(2, "beta", "beta-r1", kind="request", summary="one", to=["alpha"]),
            msg(3, "beta", "beta-s1", kind="sync", summary="status"),
        ])
        self.r.write_log("alpha", [msg(1, "alpha", "alpha-q1", kind="question", summary="Which?")])

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

    def inbox(self, pid, *extra):
        return json.loads(self.ok("inbox", pid, *extra))

    def item(self, pid, rid, *extra):
        hits = [r for r in self.inbox(pid, *extra)["requests"] if r["request"]["id"] == rid]
        return hits[0] if hits else None

    def docs(self):
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root, roster)
        return enrich_messages(replay(lines), lines), roster

    def log_lines(self):
        out = []
        for f in sorted((self.r.root / "log").glob("*.jsonl")):
            out.extend(ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip())
        return out

    def test_ack_clears_one_name_from_pending_from(self):
        self.assertEqual(self.ok("ack", "--from", "alpha", "beta-r2", "--none").strip(), "alpha--beta-r2")
        # `inbox` lists only the requests addressed to its pid, so the requester's view is a recipient's.
        it = self.item("gamma", "beta-r2")
        self.assertEqual(it["pending_from"], ["gamma"])
        self.assertEqual(it["acked"], ["alpha"])
        self.assertIsNone(self.item("alpha", "beta-r2"))
        self.assertEqual(self.item("alpha", "beta-r2", "--all")["acked"], ["alpha"])
        docs, roster = self.docs()
        self.assertIn("beta-r2", [d for d, _doc, _r in open_requests(docs, roster=roster)])

    def test_ack_closes_a_one_recipient_request(self):
        self.ok("ack", "--from", "alpha", "beta-r1", "--none")
        docs, roster = self.docs()
        self.assertNotIn("beta-r1", [d for d, _doc, _r in open_requests(docs, roster=roster)])
        self.assertIsNone(self.item("alpha", "beta-r1"))
        self.assertEqual(self.item("alpha", "beta-r1", "--all")["pending_from"], [])

    def test_ack_from_a_quarantined_participant_counts_for_nothing(self):
        self.ok("ack", "--from", "delta", "beta-r1", "--none")
        docs, roster = self.docs()
        self.assertIn(("acks", "delta--beta-r1"), docs)
        self.assertEqual(acks_by_item(docs, roster=roster), {})
        self.assertEqual(self.item("alpha", "beta-r1", "--all")["acked"], [])
        self.assertIn("beta-r1", [d for d, _doc, _r in open_requests(docs, roster=roster)])

    def test_ack_of_an_item_not_in_the_log_counts_for_nothing(self):
        self.r.write_log("gamma", [ack_row(1, "gamma", "beta-gone"), ack_row(2, "gamma", "beta-s1")])
        docs, roster = self.docs()
        self.assertIn(("acks", "gamma--beta-gone"), docs)
        self.assertEqual(acks_by_item(docs, roster=roster), {})
        self.assertEqual(self.item("gamma", "beta-r2")["pending_from"], ["alpha", "gamma"])

    def test_ack_refuses_a_question(self):
        before = self.log_lines()
        p = self.run_cli("ack", "--from", "gamma", "alpha-q1", "--none")
        self.assertEqual(p.returncode, 1)
        self.assertIn("a question to the owner gets an answer, not an ack", p.stderr)
        self.assertEqual(self.log_lines(), before)

    def test_ack_refuses_a_reply(self):
        self.ok("reply", "--from", "alpha", "--to-id", "beta-r1", "--summary", "done")
        before = self.log_lines()
        reply_id =[json.loads(x)["id"] for x in before if json.loads(x)["data"].get("kind") == "reply"][0]
        for item in (reply_id, "beta-nothing", "beta-s1"):
            p = self.run_cli("ack", "--from", "gamma", item, "--none")
            self.assertEqual(p.returncode, 1, item)
        self.assertEqual(self.log_lines(), before)
        p = self.run_cli("ack", "--from", "nobody", "beta-r1", "--none")
        self.assertEqual(p.returncode, 1)
        self.assertNotEqual(self.run_cli("ack", "--from", "gamma", "beta-r2").returncode, 0)
        self.assertEqual(self.log_lines(), before)

    def test_ack_id_rule(self):
        before = self.log_lines()
        data = {"from": "alpha", "item": "x", "none": True}
        cases = (
            ("beta", "set", "alpha--x", dict(data)),
            ("alpha", "set", "alpha--x", dict(data, **{"from": "beta"})),
            ("alpha", "set", "alpha--y", dict(data)),
            ("alpha", "set", "alpha--x", {"item": "x", "none": True}),
            ("beta", "update", "alpha--x", {"none": False}),
        )
        for frm, op, did, d in cases:
            p = self.run_cli("append", "--from", frm, "--op", op, "--collection", "acks",
                             "--id", did, "--data", json.dumps(d))
            self.assertEqual(p.returncode, 1, (frm, op, did, d))
        self.assertEqual(self.log_lines(), before)
        self.ok("append", "--from", "alpha", "--op", "set", "--collection", "acks",
                "--id", "alpha--x", "--data", json.dumps(data))
        # An update may not forge `from` on another participant's ack, nor on its own.
        before = self.log_lines()
        for frm, d in (("gamma", {"from": "gamma"}), ("alpha", {"from": "beta"})):
            p = self.run_cli("append", "--from", frm, "--op", "update", "--collection", "acks",
                             "--id", "alpha--x", "--data", json.dumps(d))
            self.assertEqual(p.returncode, 1, (frm, d))
        self.assertEqual(self.log_lines(), before)

    def test_ack_does_not_close_a_request_with_no_recipients(self):
        # `to: []` names nobody: a reply closes it, an ack does not (an ack clears one name, no other).
        self.r.write_log("beta", [msg(1, "beta", "beta-r0", kind="request", summary="none", to=[])])
        self.r.write_log("gamma", [ack_row(1, "gamma", "beta-r0")])
        docs, roster = self.docs()
        self.assertEqual(acks_by_item(docs, roster=roster), {"beta-r0": {"gamma"}})
        self.assertIn("beta-r0", [d for d, _doc, _r in open_requests(docs, roster=roster)])

    def test_withdrawal_can_need_no_answer(self):
        from crosstalk.requests import needs_answer
        ask = {"kind": "request", "summary": "TURN ASK WITHDRAWN alpha feature-0101", "body": "Done. No answer needed."}
        self.assertIs(needs_answer(ask), False)
        self.assertIs(needs_answer(dict(ask, body="Done.")), True)
        self.assertIs(needs_answer(dict(ask, summary="TURN ASK alpha feature-0101")), True)

    def test_an_id_with_a_double_dash_cannot_ack(self):
        self.r.write_log("b", [msg(1, "b", "b--x", kind="request", summary="help", to=["alpha", "alpha--b"])])
        self.r.write_log("alpha--b", [msg(1, "alpha--b", "alpha--b-r1", kind="request", summary="x", to=["alpha"])])
        before = self.log_lines()
        p = self.run_cli("ack", "--from", "alpha--b", "b--x", "--none")
        self.assertEqual(p.returncode, 1)
        self.assertIn("holds '--', so it cannot ack; post a reply instead", p.stderr)
        p = self.run_cli("append", "--from", "alpha--b", "--op", "set", "--collection", "acks",
                         "--id", "alpha--b--x", "--data",
                         json.dumps({"from": "alpha--b", "item": "x", "none": True}))
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.log_lines(), before)
        self.assertEqual(self.ok("ack", "--from", "alpha", "b--x", "--none").strip(), "alpha--b--x")
        docs, roster = self.docs()
        self.assertEqual(acks_by_item(docs, roster=roster), {"b--x": {"alpha"}})
        # A row from such a writer that reached the docs some other way counts for nothing.
        docs[("acks", "alpha--b--alpha--b-r1")] = {"from": "alpha--b", "item": "alpha--b-r1", "none": True}
        self.assertEqual(acks_by_item(docs, roster=roster), {"b--x": {"alpha"}})

    def test_a_bad_acks_row_stops_its_log(self):
        # Pinned as it is today: read_log keeps the lines before the bad row and stops with a fatal problem.
        path = self.r.write_log("gamma", [
            msg(1, "gamma", "gamma-m1", kind="sync", summary="before"),
            line(2, "gamma", collection="acks", id_="gamma-beta-r1",
                 data={"from": "gamma", "item": "beta-r1", "none": True}),
            msg(3, "gamma", "gamma-m3", kind="sync", summary="after"),
        ])
        lines, problems = read_log(path, load_roster(self.r.root / "participants.json"))
        self.assertEqual([ln.id for ln in lines], ["gamma-m1"])
        self.assertEqual([(p.line, p.fatal) for p in problems], [(2, True)])
        self.assertIn("an ack id must start with 'gamma--'", problems[0].message)

    def test_ack_is_no_message(self):
        before = self.ok("read", "--collection", "messages")
        docs, roster = self.docs()
        items = notify.actionable_items(docs, "beta", roster)
        self.ok("ack", "--from", "alpha", "beta-r2", "--none")
        self.assertEqual(self.ok("read", "--collection", "messages"), before)
        docs, roster = self.docs()
        self.assertEqual(notify.actionable_items(docs, "beta", roster), items)

    def test_live_notice_skips_an_acked_request(self):
        docs, roster = self.docs()
        self.assertEqual(notify.actionable_items(docs, "alpha", roster), ["beta-r1", "beta-r2"])
        self.ok("ack", "--from", "alpha", "beta-r2", "--none")
        docs, roster = self.docs()
        self.assertEqual(notify.actionable_items(docs, "alpha", roster), ["beta-r1"])
        self.assertEqual(notify.actionable_items(docs, "gamma", roster), ["beta-r2"])


if __name__ == "__main__":
    unittest.main()
