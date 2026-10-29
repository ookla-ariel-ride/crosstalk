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

    def test_owner_messages_unanswered_and_replied(self):
        self.r.write_log("owner", [
            line(1, "owner", id_="own-bcast",
                 data={"from": "owner", "kind": "owner", "summary": "all hands"},
                 t="2026-10-21T18:00:00Z"),
            line(2, "owner", id_="own-codex",
                 data={"from": "owner", "kind": "owner", "summary": "for codex", "to": ["codex"]},
                 t="2026-10-21T19:00:00Z"),
            line(3, "owner", id_="own-bil",
                 data={"from": "owner", "kind": "owner", "summary": "for bil", "to": ["bewareilive"]},
                 t="2026-10-21T20:00:00Z"),
        ])
        out = self.inbox("bewareilive")
        self.assertEqual([m["message"]["id"] for m in out["owner_messages"]], ["own-bil", "own-bcast"])
        self.assertEqual(self.inbox("owner")["owner_messages"], [])

        # Another participant replying to broadcast owner message does not close it for bewareilive
        self.r.write_log("codex", [rep(1, "codex", "codex-r1", "own-bcast", t="2026-10-21T20:30:00Z")])
        out2 = self.inbox("bewareilive")
        self.assertEqual([m["message"]["id"] for m in out2["owner_messages"]], ["own-bil", "own-bcast"])
        bcast_entry = [m for m in out2["owner_messages"] if m["message"]["id"] == "own-bcast"][0]
        self.assertEqual(len(bcast_entry["replies"]), 1)
        self.assertEqual(bcast_entry["replies"][0]["trust"], "approved")

        # Once bewareilive replies to own-bil, own-bil drops out for bewareilive
        # (bewareilive joined before the broadcast, so the broadcast is still its to answer)
        self.r.write_log("bewareilive", [
            line(1, "bewareilive", id_="bewareilive-c0",
                 data={"from": "bewareilive", "kind": "check-in", "summary": "here"},
                 t="2026-10-21T17:00:00Z"),
            rep(2, "bewareilive", "bewareilive-r1", "own-bil", t="2026-10-21T20:45:00Z"),
        ])
        out3 = self.inbox("bewareilive")
        self.assertEqual([m["message"]["id"] for m in out3["owner_messages"]], ["own-bcast"])

    def test_owner_messages_rejects_spoofed_kind_and_owner_replies(self):
        from crosstalk.requests import unanswered_owner_messages

        docs = {
            ("messages", "codex-spoof"): {
                "from": "codex",
                "kind": "owner",
                "summary": "spoofed owner directive",
                "to": ["bewareilive"],
                "t": "2026-10-21T20:00:00Z",
            },
            ("messages", "own-top"): {
                "from": "owner",
                "kind": "owner",
                "summary": "real owner directive",
                "to": ["bewareilive"],
                "t": "2026-10-21T19:00:00Z",
            },
            ("messages", "own-reply"): {
                "from": "owner",
                "kind": "owner",
                "summary": "owner reply",
                "replyTo": "vc-r1",
                "to": ["bewareilive"],
                "t": "2026-10-21T21:00:00Z",
            },
        }
        res = unanswered_owner_messages(docs, "bewareilive")
        self.assertEqual([did for (did, _, _) in res], ["own-top"])

    def test_owner_broadcast_before_first_line_not_listed(self):
        # A participant can't be expected to answer a broadcast posted before it joined.
        self.r.write_log("owner", [
            line(1, "owner", id_="own-old",
                 data={"from": "owner", "kind": "owner", "summary": "old news for all"},
                 t="2026-10-21T10:00:00.123Z"),
            line(2, "owner", id_="own-group",
                 data={"from": "owner", "kind": "alert", "summary": "@projects sync up"},
                 t="2026-10-21T11:00:00Z"),
            line(3, "owner", id_="own-same",
                 data={"from": "owner", "kind": "owner", "summary": "same second as first line"},
                 t="2026-10-21T12:00:00Z"),
            line(4, "owner", id_="own-new",
                 data={"from": "owner", "kind": "owner", "summary": "new news for all"},
                 t="2026-10-21T13:00:00Z"),
        ])
        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", id_="vc-first",
                 data={"from": "vectorcraft", "kind": "check-in", "summary": "hello"},
                 t="2026-10-21T12:00:00Z"),
        ])
        out = self.inbox("vectorcraft")
        self.assertEqual([m["message"]["id"] for m in out["owner_messages"]],
                         ["own-new", "own-same"])
        # bewareilive has no lines yet: it sees every broadcast
        out_bil = self.inbox("bewareilive")
        self.assertEqual([m["message"]["id"] for m in out_bil["owner_messages"]],
                         ["own-new", "own-same", "own-group", "own-old"])

    def test_owner_message_naming_participant_listed_even_before_first_line(self):
        self.r.write_log("owner", [
            line(1, "owner", id_="own-to",
                 data={"from": "owner", "kind": "owner", "summary": "welcome", "to": ["vectorcraft"]},
                 t="2026-10-21T09:00:00Z"),
            line(2, "owner", id_="own-to-str",
                 data={"from": "owner", "kind": "owner", "summary": "welcome", "to": "vectorcraft"},
                 t="2026-10-21T09:30:00Z"),
            line(3, "owner", id_="own-alias",
                 data={"from": "owner", "kind": "decision", "summary": "@vc please set up"},
                 t="2026-10-21T10:00:00Z"),
            line(4, "owner", id_="own-name-and-all",
                 data={"from": "owner", "kind": "owner", "summary": "@everyone, esp. @VectorCraft"},
                 t="2026-10-21T10:30:00Z"),
            line(5, "owner", id_="own-bcast",
                 data={"from": "owner", "kind": "owner", "summary": "everyone"},
                 t="2026-10-21T11:00:00Z"),
        ])
        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", id_="vc-first",
                 data={"from": "vectorcraft", "kind": "check-in", "summary": "hello"},
                 t="2026-10-21T12:00:00Z"),
        ])
        out = self.inbox("vectorcraft")
        self.assertEqual([m["message"]["id"] for m in out["owner_messages"]],
                         ["own-name-and-all", "own-alias", "own-to-str", "own-to"])

    def test_owner_message_without_valid_time_or_participant_time_listed(self):
        from crosstalk.requests import unanswered_owner_messages

        docs = {
            ("messages", "own-no-t"): {"from": "owner", "kind": "owner", "summary": "no t"},
            ("messages", "own-bad-t"): {"from": "owner", "kind": "owner", "summary": "bad t",
                                        "t": "yesterday"},
            ("messages", "own-old"): {"from": "owner", "kind": "owner", "summary": "old",
                                      "t": "2026-10-21T10:00:00Z"},
            ("messages", "vc-first"): {"from": "vectorcraft", "kind": "check-in",
                                       "summary": "hi", "t": "2026-10-21T12:00:00.500Z"},
            ("messages", "bil-undated"): {"from": "bewareilive", "kind": "check-in",
                                          "summary": "hi", "t": "not a time"},
        }
        vc = sorted(did for (did, _, _) in unanswered_owner_messages(docs, "vectorcraft"))
        self.assertEqual(vc, ["own-bad-t", "own-no-t"])
        # bewareilive has only an undated row: no join time, so nothing is hidden
        bil = sorted(did for (did, _, _) in unanswered_owner_messages(docs, "bewareilive"))
        self.assertEqual(bil, ["own-bad-t", "own-no-t", "own-old"])

    def test_agreements_to_adopt(self):
        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", collection="agreements", id_="a-ready",
                 data={"builder": "vectorcraft", "adopter": "bewareilive",
                       "status": "ready-to-adopt",
                       "commits": {"vectorcraft": ["111"], "bewareilive": []},
                       "updated": "2026-10-21T20:00:00Z"}),
            line(2, "vectorcraft", collection="agreements", id_="a-both",
                 data={"builder": "vectorcraft", "adopter": "both",
                       "status": "landed-builder",
                       "commits": {"vectorcraft": ["222"]},
                       "t": "2026-10-21T19:00:00Z"}),
            line(3, "vectorcraft", collection="agreements", id_="a-all",
                 data={"builder": "vectorcraft", "adopter": "all",
                       "status": "landed-builder",
                       "t": "2026-10-21T18:00:00Z"}),
            line(4, "vectorcraft", collection="agreements", id_="a-held-flag",
                 data={"builder": "vectorcraft", "adopter": "bewareilive",
                       "status": "ready-to-adopt", "hold": True,
                       "updated": "2026-10-21T21:00:00Z"}),
            line(5, "vectorcraft", collection="agreements", id_="a-held-status",
                 data={"builder": "vectorcraft", "adopter": "bewareilive",
                       "status": "held", "updated": "2026-10-21T21:00:00Z"}),
            line(6, "vectorcraft", collection="agreements", id_="a-agreed",
                 data={"builder": "vectorcraft", "adopter": "bewareilive",
                       "status": "agreed", "updated": "2026-10-21T21:00:00Z"}),
            line(7, "vectorcraft", collection="agreements", id_="a-done",
                 data={"builder": "vectorcraft", "adopter": "bewareilive",
                       "status": "ready-to-adopt",
                       "commits": {"bewareilive": ["abc1234"]},
                       "updated": "2026-10-21T21:00:00Z"}),
            line(8, "vectorcraft", collection="agreements", id_="a-codex",
                 data={"builder": "vectorcraft", "adopter": "codex",
                       "status": "ready-to-adopt",
                       "updated": "2026-10-21T21:00:00Z"}),
        ])
        out_bil = self.inbox("bewareilive")
        self.assertEqual([a["id"] for a in out_bil["agreements_to_adopt"]],
                         ["a-ready", "a-both", "a-all"])
        # Builder does not adopt its own 'both'/'all' agreements
        out_vc = self.inbox("vectorcraft")
        self.assertEqual(out_vc["agreements_to_adopt"], [])
        # Reviewer only adopts agreements explicitly naming it, not 'both'/'all' project agreements
        out_codex = self.inbox("codex")
        self.assertEqual([a["id"] for a in out_codex["agreements_to_adopt"]],
                         ["a-codex"])
        self.assertEqual(self.inbox("owner")["agreements_to_adopt"], [])

    def test_quarantined_or_third_party_reply_does_not_censor_request(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        for p in roster["participants"]:
            if p["id"] == "codex":
                p["trust"] = "quarantined"
        self.r.write_roster(roster)
        self.r.write_log("vectorcraft", [
            req(1, "vectorcraft", "vc-req-1", to=["bewareilive"]),
        ])
        self.r.write_log("codex", [
            rep(1, "codex", "codex-hijack", "vc-req-1"),
        ])
        out_open = self.inbox("bewareilive")
        self.assertEqual(self.ids(out_open), ["vc-req-1"])
        self.assertEqual(out_open["requests"][0]["pending_from"], ["bewareilive"])
        out_all = self.inbox("bewareilive", "--all")
        self.assertEqual(out_all["requests"][0]["pending_from"], ["bewareilive"])
        self.assertEqual(out_all["requests"][0]["replies"][0]["trust"], "quarantined")

        # Even if codex is approved, a third-party reply to a 1-recipient request for bewareilive
        # does not remove it from bewareilive's open inbox
        self.r.write_roster(SPEC_ROSTER)
        out_open2 = self.inbox("bewareilive")
        self.assertEqual(self.ids(out_open2), ["vc-req-1"])
        self.assertEqual(out_open2["requests"][0]["pending_from"], ["bewareilive"])

        # Owner reply does close the 1-recipient request
        self.r.write_log("owner", [
            rep(1, "owner", "own-close", "vc-req-1"),
        ])
        self.assertEqual(self.inbox("bewareilive")["requests"], [])

    def test_stale_locks_extreme_timezone_does_not_overflow(self):
        self.r.write_log("bewareilive", [
            line(1, "bewareilive", collection="locks", id_="l-bewareilive-extreme",
                 data={
                     "project": "bewareilive",
                     "path": "a.rs",
                     "since": "0001-01-01T00:00:00+01:00",
                     "released": None,
                 })
        ])
        out = self.inbox("bewareilive")
        self.assertEqual(out["stale_locks"], [])

    def test_stale_locks(self):
        from crosstalk.replay import enrich_messages, load_all_lines, replay
        from crosstalk.requests import stale_locks_for

        self.r.write_log("bewareilive", [
            line(1, "bewareilive", collection="locks", id_="l-old-2",
                 data={"project": "bewareilive", "path": "b.rs",
                       "since": "2026-10-21T12:00:00Z"}),
            line(2, "bewareilive", collection="locks", id_="l-old-1",
                 data={"project": "bewareilive", "path": "a.rs",
                       "since": "2026-10-21T10:00:00Z", "released": None}),
            line(3, "bewareilive", collection="locks", id_="l-fresh",
                 data={"project": "bewareilive", "path": "c.rs",
                       "since": "2099-01-01T00:00:00Z", "released": None}),
            line(4, "bewareilive", collection="locks", id_="l-released",
                 data={"project": "bewareilive", "path": "d.rs",
                       "since": "2026-10-21T08:00:00Z", "released": "2026-10-21T09:00:00Z"}),
        ])
        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", collection="locks", id_="l-vc",
                 data={"project": "vectorcraft", "path": "v.rs",
                       "since": "2026-10-21T08:00:00Z", "released": None}),
        ])
        out = self.inbox("bewareilive")
        self.assertEqual([lk["id"] for lk in out["stale_locks"]], ["l-old-1", "l-old-2"])

        lines = load_all_lines(self.r.root)
        docs = enrich_messages(replay(lines), lines)
        # At 15:00Z: l-old-1 (10:00Z, 5h) is stale; l-old-2 (12:00Z, 3h) is not
        self.assertEqual([lk["id"] for lk in stale_locks_for(docs, "bewareilive", now_iso="2026-10-21T15:00:00Z")],
                         ["l-old-1"])
        # At 16:00:00Z: l-old-2 is exactly 4h old (not > 4h)
        self.assertEqual([lk["id"] for lk in stale_locks_for(docs, "bewareilive", now_iso="2026-10-21T16:00:00Z")],
                         ["l-old-1"])
        # At 16:00:01Z: both are > 4h old, sorted oldest since first
        self.assertEqual([lk["id"] for lk in stale_locks_for(docs, "bewareilive", now_iso="2026-10-21T16:00:01Z")],
                         ["l-old-1", "l-old-2"])

    def test_at_mentions_target_recipients_when_to_omitted(self):
        self.r.write_log("owner", [
            line(1, "owner", id_="own-1", t="2026-10-24T12:00:00Z",
                 data={"from": "owner", "kind": "owner",
                       "summary": "@BewareILive check robotron audio",
                       "body": "Ref vectorcraft@f0bfbca3 and user@example.com"}),
            line(2, "owner", id_="own-2", t="2026-10-24T12:01:00Z",
                 data={"from": "owner", "kind": "owner",
                       "summary": "@Everyone status check"}),
        ])
        bil_out = self.inbox("bewareilive")
        vc_out = self.inbox("vectorcraft")
        self.assertEqual([m["message"]["id"] for m in bil_out["owner_messages"]],
                         ["own-2", "own-1"])
        self.assertEqual([m["message"]["id"] for m in vc_out["owner_messages"]],
                         ["own-2"])

    def test_at_mentions_aliases_groups_broadcast_and_owner_message_kinds(self):
        from crosstalk.requests import extract_mentions, unanswered_owner_messages

        # Aliases and groups come from the roster
        three = {
            "avoidmissingball": {"name": "AvoidMissingBall", "kind": "project", "trust": "approved", "aliases": ["amb"]},
            "bewareilive": {"name": "BewareILive", "kind": "project", "trust": "approved", "aliases": ["bil"]},
            "vectorcraft": {"name": "VectorCraft", "kind": "project", "trust": "approved", "aliases": ["vc"]},
            "codex": {"name": "Codex", "kind": "reviewer", "trust": "approved"},
        }
        self.assertEqual(
            extract_mentions({"summary": "Ping (@bil, @vc; @amb: check this)"}, roster=three),
            ["bewareilive", "vectorcraft", "avoidmissingball"],
        )
        self.assertEqual(
            extract_mentions({"summary": "@projects sync up"}, roster=three),
            ["avoidmissingball", "bewareilive", "vectorcraft"],
        )
        self.assertEqual(
            extract_mentions({"summary": "@teams and @reviewers"}, roster=three),
            ["avoidmissingball", "bewareilive", "vectorcraft", "codex"],
        )
        # With no roster nothing resolves: no project names are built in
        self.assertEqual(extract_mentions({"summary": "Ping @bil and @projects"}), [])
        # Broadcast handles override specific mentions
        for bcast in ("@everyone", "@all", "@channel", "@here"):
            self.assertEqual(
                extract_mentions({"summary": "%s and @BewareILive" % bcast}, roster=three),
                [],
            )
        # Commit refs and emails do not trigger mentions
        self.assertEqual(
            extract_mentions({"summary": "See vectorcraft@f0bfbca3 or dev@bewareilive.com"}, roster=three),
            [],
        )
        # @owner is excluded from extract_mentions so "(cc @owner)" does not narrow a broadcast
        custom_roster = {
            "owner": {"name": "VectorCraft", "kind": "person", "trust": "approved"},
            "bewareilive": {"name": "BewareILive", "kind": "project", "trust": "approved"},
        }
        self.assertEqual(extract_mentions({"summary": "@VectorCraft hi"}, roster=custom_roster), [])
        self.assertEqual(extract_mentions({"summary": "@owner hi"}, roster=custom_roster), [])

        # Test CLI inbox with requests and owner_messages of various kinds (alert, agreement, lesson, decision)
        self.r.write_log("owner", [
            line(1, "owner", id_="own-alert", t="2026-10-24T12:00:00Z",
                 data={"from": "owner", "kind": "alert",
                       "summary": "Urgent: @bil fix audio"}),
            line(2, "owner", id_="own-agree", t="2026-10-24T12:01:00Z",
                 data={"from": "owner", "kind": "agreement",
                       "summary": "Agreement for @projects"}),
            line(3, "owner", id_="own-lesson", t="2026-10-24T12:02:00Z",
                 data={"from": "owner", "kind": "lesson",
                       "summary": "Lesson for @reviewers"}),
            line(4, "owner", id_="own-decision", t="2026-10-24T12:03:00Z",
                 data={"from": "owner", "kind": "decision",
                       "summary": "Decision for @vc and @channel"}),
            line(5, "owner", id_="own-checkin", t="2026-10-24T12:04:00Z",
                 data={"from": "owner", "kind": "check-in",
                       "summary": "Owner routine check-in"}),
        ])
        self.r.write_log("vectorcraft", [
            # vectorcraft joined before the owner's group messages
            line(1, "vectorcraft", id_="vc-checkin", t="2026-10-24T11:00:00Z",
                 data={"from": "vectorcraft", "kind": "check-in", "summary": "here"}),
            line(2, "vectorcraft", id_="vc-req-bil", t="2026-10-24T12:05:00Z",
                 data={"from": "vectorcraft", "kind": "request",
                       "summary": "Hey @bil can you check vectorcraft@f0bfbca3?"}),
            line(3, "vectorcraft", id_="vc-req-rev", t="2026-10-24T12:06:00Z",
                 data={"from": "vectorcraft", "kind": "request",
                       "summary": "Need @reviewers on this PR"}),
        ])

        bil_out = self.inbox("bewareilive")
        self.assertEqual(
            [m["message"]["id"] for m in bil_out["owner_messages"]],
            ["own-decision", "own-agree", "own-alert"],
        )
        self.assertEqual(self.ids(bil_out), ["vc-req-bil"])
        self.assertEqual(bil_out["requests"][0]["pending_from"], ["bewareilive"])

        vc_out = self.inbox("vectorcraft")
        self.assertEqual(
            [m["message"]["id"] for m in vc_out["owner_messages"]],
            ["own-decision", "own-agree"],
        )
        self.assertEqual(self.ids(vc_out), [])

        codex_out = self.inbox("codex")
        self.assertEqual(
            [m["message"]["id"] for m in codex_out["owner_messages"]],
            ["own-decision", "own-lesson"],
        )
        self.assertEqual(self.ids(codex_out), ["vc-req-rev"])

    def test_extract_mentions_subagent_dots_punctuation_quarantine_and_non_string(self):
        from crosstalk.requests import extract_mentions

        roster = {
            "owner": {"name": "Owner", "kind": "person", "trust": "approved"},
            "bewareilive": {"name": "BewareILive", "kind": "project", "trust": "approved", "aliases": ["bil"]},
            "vectorcraft": {"name": "VectorCraft", "kind": "project", "trust": "approved", "aliases": ["vc"]},
            "quarantined-proj": {"name": "QuarantinedProj", "kind": "project", "trust": "quarantined"},
            "codex": {"name": "Codex", "kind": "reviewer", "trust": "approved"},
            "quarantined-rev": {"name": "QuarantinedRev", "kind": "reviewer", "trust": "quarantined"},
            "bewareilive.verifier": {"name": "BIL Verifier", "kind": "agent", "trust": "approved"},
        }
        self.assertEqual(
            extract_mentions(
                {"summary": "Ask @bewareilive.verifier, @bil! (@vc?) and @owner."},
                roster=roster,
            ),
            ["bewareilive.verifier", "bewareilive", "vectorcraft"],
        )
        self.assertEqual(
            extract_mentions({"summary": "@projects and @reviewers"}, roster=roster),
            ["bewareilive", "vectorcraft", "codex"],
        )
        self.assertEqual(
            extract_mentions({"summary": 123, "body": ["not-a-str"]}, roster=roster),
            [],
        )

    def test_pending_recipients_quarantine_and_owner_broadcast_request(self):
        from crosstalk.participants import load_roster
        from crosstalk.replay import enrich_messages, load_all_lines, replay
        from crosstalk.requests import open_requests, pending_recipients

        roster_data = json.loads(json.dumps(SPEC_ROSTER))
        roster_data["participants"].append({
            "id": "qbot",
            "name": "QBot",
            "kind": "agent",
            "parent": "bewareilive",
            "color": "#2e7d32",
            "colorDark": "#81c784",
            "trust": "quarantined",
        })
        self.r.write_roster(roster_data)
        roster = load_roster(self.r.root / "participants.json")

        # Quarantined recipient in `to` does not deadlock multi-recipient request
        self.r.write_log("vectorcraft", [
            req(1, "vectorcraft", "vc-q", to=["bewareilive", "qbot"], t="2026-10-21T18:00:00Z"),
        ])
        self.r.write_log("owner", [
            line(1, "owner", id_="own-req-bcast", t="2026-10-21T18:05:00Z",
                 data={"from": "owner", "kind": "request", "summary": "Are all projects in sync? (cc @owner)"}),
        ])
        lines = load_all_lines(self.r.root, roster)
        docs = enrich_messages(replay(lines), lines)
        self.assertEqual(
            pending_recipients(docs[("messages", "vc-q")], [], roster=roster),
            ["bewareilive"],
        )
        self.assertEqual(
            pending_recipients(docs[("messages", "own-req-bcast")], [], roster=roster),
            ["bewareilive", "vectorcraft"],
        )

        # bewareilive replies to both: vc-q closes, own-req-bcast stays open waiting for vectorcraft
        self.r.write_log("bewareilive", [
            rep(1, "bewareilive", "bewareilive-r1", "vc-q", t="2026-10-21T19:00:00Z"),
            rep(2, "bewareilive", "bewareilive-r2", "own-req-bcast", t="2026-10-21T19:01:00Z"),
        ])
        lines2 = load_all_lines(self.r.root, roster)
        docs2 = enrich_messages(replay(lines2), lines2)
        self.assertEqual(
            [did for (did, _, _) in open_requests(docs2, roster=roster)],
            ["own-req-bcast"],
        )
        self.assertEqual(
            pending_recipients(
                docs2[("messages", "own-req-bcast")],
                [docs2[("messages", "bewareilive-r2")]],
                roster=roster,
            ),
            ["vectorcraft"],
        )

        # vectorcraft replies to own-req-bcast: now it closes
        self.r.write_log("vectorcraft", [
            req(1, "vectorcraft", "vc-q", to=["bewareilive", "qbot"], t="2026-10-21T18:00:00Z"),
            rep(2, "vectorcraft", "vectorcraft-r1", "own-req-bcast", t="2026-10-21T19:02:00Z"),
        ])
        lines3 = load_all_lines(self.r.root, roster)
        docs3 = enrich_messages(replay(lines3), lines3)
        self.assertEqual(open_requests(docs3, roster=roster), [])


if __name__ == "__main__":
    unittest.main()
