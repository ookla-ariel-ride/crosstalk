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

    def test_string_to_is_one_recipient(self):
        # A string `to` names one recipient, matching the page's help card.
        self.r.write_log("vectorcraft", [req(1, "vectorcraft", "vc-r1", to="bewareilive")])
        out = self.inbox("bewareilive")
        self.assertEqual(self.ids(out), ["vc-r1"])
        out2 = self.inbox("codex")
        self.assertEqual(out2["requests"], [])

    def test_quarantined_requester_listed(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        for p in roster["participants"]:
            if p["id"] == "codex":
                p["trust"] = "quarantined"
        self.r.write_roster(roster)
        self.r.write_log("codex", [req(1, "codex", "codex-r1", to=["bewareilive"])])
        out = self.inbox("bewareilive")
        self.assertEqual(self.ids(out), ["codex-r1"])

    def test_quarantined_requester_flagged(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        for p in roster["participants"]:
            if p["id"] == "codex":
                p["trust"] = "quarantined"
        self.r.write_roster(roster)
        self.r.write_log("codex", [req(1, "codex", "codex-r1", to=["bewareilive"])])
        out = self.inbox("bewareilive")
        self.assertEqual(out["requests"][0]["trust"], "quarantined")

    def test_approved_requester_flagged(self):
        self.r.write_log("vectorcraft", [req(1, "vectorcraft", "vc-r1", to=["bewareilive"])])
        out = self.inbox("bewareilive")
        self.assertEqual(out["requests"][0]["trust"], "approved")

    def test_quarantined_reply_flagged(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        for p in roster["participants"]:
            if p["id"] == "codex":
                p["trust"] = "quarantined"
        self.r.write_roster(roster)
        self.r.write_log("vectorcraft", [req(1, "vectorcraft", "vc-r1", to=["bewareilive"])])
        self.r.write_log("codex", [rep(1, "codex", "codex-r1", "vc-r1")])
        out = self.inbox("bewareilive", "--all")
        self.assertEqual(out["requests"][0]["replies"][0]["trust"], "quarantined")

    def test_unknown_requester_flagged(self):
        self.r.write_log("gemini", [req(1, "gemini", "gemini-r1", to=["bewareilive"])])
        out = self.inbox("bewareilive")
        self.assertEqual(out["requests"][0]["trust"], "unknown")

    def test_roster_map_present(self):
        self.r.write_log("vectorcraft", [req(1, "vectorcraft", "vc-r1", to=["bewareilive"])])
        out = self.inbox("bewareilive")
        self.assertIn("roster", out)
        self.assertEqual(out["roster"]["codex"]["trust"], "approved")
        self.assertEqual(out["roster"]["bewareilive"]["trust"], "approved")

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

    def test_multi_recipient_stays_open_for_unanswered_peer(self):
        from crosstalk.replay import enrich_messages, load_all_lines, replay
        from crosstalk.requests import open_requests, pending_recipients

        self.r.write_log("vectorcraft", [
            req(1, "vectorcraft", "vc-r1", to=["bewareilive", "codex", "bewareilive"]),
        ])
        out_before = self.inbox("bewareilive")
        self.assertEqual(self.ids(out_before), ["vc-r1"])
        self.assertEqual(out_before["requests"][0]["pending_from"], ["bewareilive", "codex"])

        self.r.write_log("bewareilive", [rep(1, "bewareilive", "bewareilive-r1", "vc-r1")])
        out_bil = self.inbox("bewareilive")
        self.assertEqual(out_bil["requests"], [])

        out_bil_all = self.inbox("bewareilive", "--all")
        self.assertEqual(self.ids(out_bil_all), ["vc-r1"])
        self.assertEqual(out_bil_all["requests"][0]["pending_from"], ["codex"])

        out_codex = self.inbox("codex")
        self.assertEqual(self.ids(out_codex), ["vc-r1"])
        self.assertEqual(out_codex["requests"][0]["pending_from"], ["codex"])
        self.assertEqual(len(out_codex["requests"][0]["replies"]), 1)
        self.assertEqual(out_codex["requests"][0]["replies"][0]["from"], "bewareilive")

        lines = load_all_lines(self.r.root)
        docs = enrich_messages(replay(lines), lines)
        self.assertEqual([did for (did, _, _) in open_requests(docs)], ["vc-r1"])

        self.r.write_log("codex", [rep(1, "codex", "codex-r1", "vc-r1")])
        self.assertEqual(self.inbox("codex")["requests"], [])
        lines2 = load_all_lines(self.r.root)
        docs2 = enrich_messages(replay(lines2), lines2)
        self.assertEqual(open_requests(docs2), [])
        self.assertEqual(pending_recipients({"to": "bewareilive"}, []), ["bewareilive"])
        self.assertEqual(pending_recipients({}, []), [])

    def test_broadcast_per_recipient_and_excludes_sender(self):
        from crosstalk.replay import enrich_messages, load_all_lines, replay
        from crosstalk.requests import open_requests

        self.r.write_log("vectorcraft", [
            req(1, "vectorcraft", "vc-bcast"),
            req(2, "vectorcraft", "vc-self", to=["vectorcraft", "bewareilive"]),
        ])
        # Sender does not see its own broadcast in open inbox, but sees explicit self-addressed request
        out_vc = self.inbox("vectorcraft")
        self.assertEqual(self.ids(out_vc), ["vc-self"])
        # --all includes sender's own broadcast
        out_vc_all = self.inbox("vectorcraft", "--all")
        self.assertEqual(set(self.ids(out_vc_all)), {"vc-bcast", "vc-self"})

        # Other participants see broadcast
        self.assertIn("vc-bcast", self.ids(self.inbox("bewareilive")))
        self.assertIn("vc-bcast", self.ids(self.inbox("codex")))

        # bewareilive replies to broadcast: drops from bewareilive's inbox, stays in codex's inbox
        self.r.write_log("bewareilive", [rep(1, "bewareilive", "bewareilive-r1", "vc-bcast")])
        self.assertNotIn("vc-bcast", self.ids(self.inbox("bewareilive")))
        out_codex = self.inbox("codex")
        self.assertEqual(self.ids(out_codex), ["vc-bcast"])
        self.assertEqual(out_codex["requests"][0]["pending_from"], [])
        self.assertEqual(len(out_codex["requests"][0]["replies"]), 1)

        # In global open_requests(docs), broadcast closes after first reply while multi-recipient stays open
        lines = load_all_lines(self.r.root)
        docs = enrich_messages(replay(lines), lines)
        self.assertEqual([did for (did, _, _) in open_requests(docs)], ["vc-self"])


if __name__ == "__main__":
    unittest.main()
