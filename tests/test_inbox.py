"""relay.py inbox: open requests addressed to a participant."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

REPO = Path(__file__).resolve().parent.parent


def req(seq, frm, id_, to=None, t="2026-10-21T18:00:00Z"):
    data = {"from": frm, "kind": "request", "summary": "help " + id_}
    if to is not None:
        data["to"] = to
    return line(seq, frm, id_=id_, data=data, t=t)


def rep(seq, frm, id_, reply_to, t="2026-10-21T19:00:00Z"):
    return line(seq, frm, id_=id_,
                data={"from": frm, "kind": "reply", "summary": "answer " + id_,
                      "replyTo": reply_to}, t=t)


class InboxTests(unittest.TestCase):
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

    def inbox(self, pid, *extra):
        p = self.run_cli("inbox", pid, *extra)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def ids(self, out):
        return [r["request"]["id"] for r in out["requests"]]

    def test_addressed_only(self):
        self.r.write_log("vectorcraft", [
            req(1, "vectorcraft", "vc-r1", to=["bewareilive"]),
            req(2, "vectorcraft", "vc-r2", to=["codex"]),
        ])
        out = self.inbox("bewareilive")
        self.assertEqual(self.ids(out), ["vc-r1"])

    def test_broadcast(self):
        self.r.write_log("vectorcraft", [req(1, "vectorcraft", "vc-r1")])
        for pid in ("bewareilive", "codex", "owner"):
            out = self.inbox(pid)
            self.assertEqual(self.ids(out), ["vc-r1"], pid)

    def test_excludes_other_pids(self):
        self.r.write_log("vectorcraft", [
            req(1, "vectorcraft", "vc-r1", to=["codex"]),
            req(2, "vectorcraft", "vc-r2", to=["bewareilive", "codex"]),
        ])
        out = self.inbox("bewareilive")
        self.assertEqual(self.ids(out), ["vc-r2"])

    def test_replied_drops_and_all_keeps(self):
        self.r.write_log("vectorcraft", [req(1, "vectorcraft", "vc-r1", to=["bewareilive"])])
        self.r.write_log("bewareilive", [rep(1, "bewareilive", "bewareilive-r1", "vc-r1")])
        out = self.inbox("bewareilive")
        self.assertEqual(out["requests"], [])
        out_all = self.inbox("bewareilive", "--all")
        self.assertEqual(self.ids(out_all), ["vc-r1"])
        self.assertEqual(len(out_all["requests"][0]["replies"]), 1)

    def test_newest_first(self):
        self.r.write_log("vectorcraft", [
            req(1, "vectorcraft", "vc-r1", t="2026-10-21T18:00:00Z"),
            req(2, "vectorcraft", "vc-r2", t="2026-10-21T20:00:00Z"),
            req(3, "vectorcraft", "vc-r3", t="2026-10-21T19:00:00Z"),
        ])
        out = self.inbox("bewareilive")
        self.assertEqual(self.ids(out), ["vc-r2", "vc-r3", "vc-r1"])

    def test_replies_attached_in_order(self):
        self.r.write_log("vectorcraft", [req(1, "vectorcraft", "vc-r1", to=["bewareilive"])])
        self.r.write_log("codex", [rep(1, "codex", "codex-r1", "vc-r1", t="2026-10-21T19:00:00Z")])
        self.r.write_log("owner", [rep(1, "owner", "own-r1", "vc-r1", t="2026-10-21T18:30:00Z")])
        out = self.inbox("bewareilive", "--all")
        replies = out["requests"][0]["replies"]
        self.assertEqual([r["summary"] for r in replies], ["answer own-r1", "answer codex-r1"])

    def test_quarantined_requester_listed(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        for p in roster["participants"]:
            if p["id"] == "codex":
                p["trust"] = "quarantined"
        self.r.write_roster(roster)
        self.r.write_log("codex", [req(1, "codex", "codex-r1", to=["bewareilive"])])
        out = self.inbox("bewareilive")
        self.assertEqual(self.ids(out), ["codex-r1"])

    def test_unknown_pid_exits_1(self):
        p = self.run_cli("inbox", "gemini")
        self.assertEqual(p.returncode, 1)
        self.assertIn("gemini", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_malformed_log_exits_1(self):
        self.r.write_log("codex", [line(1, "codex"), "{not json"])
        p = self.run_cli("inbox", "bewareilive")
        self.assertEqual(p.returncode, 1)
        self.assertIn("log/codex.jsonl:2:", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_service_block_present(self):
        self.r.write_log("vectorcraft", [req(1, "vectorcraft", "vc-r1", to=["bewareilive"])])
        out = self.inbox("bewareilive")
        svc = out["service"]
        self.assertEqual(svc["name"], "Crosstalk")
        for key in ("purpose", "capabilities", "commands", "protocol"):
            self.assertIn(key, svc)
        self.assertTrue(any("Crosstalk feature:" in c for c in svc["capabilities"]))


if __name__ == "__main__":
    unittest.main()
