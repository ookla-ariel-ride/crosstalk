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
        self.r.write_log("beta", [
            req(1, "beta", "beta-r1", to=["alpha"]),
            req(2, "beta", "beta-r2", to=["rev"]),
        ])
        out = self.inbox("alpha")
        self.assertEqual(self.ids(out), ["beta-r1"])

    def test_broadcast(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1")])
        for pid in ("alpha", "rev", "owner"):
            out = self.inbox(pid)
            self.assertEqual(self.ids(out), ["beta-r1"], pid)

    def test_excludes_other_pids(self):
        self.r.write_log("beta", [
            req(1, "beta", "beta-r1", to=["rev"]),
            req(2, "beta", "beta-r2", to=["alpha", "rev"]),
        ])
        out = self.inbox("alpha")
        self.assertEqual(self.ids(out), ["beta-r2"])

    def test_replied_drops_and_all_keeps(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", to=["alpha"])])
        self.r.write_log("alpha", [rep(1, "alpha", "alpha-r1", "beta-r1")])
        out = self.inbox("alpha")
        self.assertEqual(out["requests"], [])
        out_all = self.inbox("alpha", "--all")
        self.assertEqual(self.ids(out_all), ["beta-r1"])
        self.assertEqual(len(out_all["requests"][0]["replies"]), 1)

    def test_newest_first(self):
        self.r.write_log("beta", [
            req(1, "beta", "beta-r1", t="2026-10-21T18:00:00Z"),
            req(2, "beta", "beta-r2", t="2026-10-21T20:00:00Z"),
            req(3, "beta", "beta-r3", t="2026-10-21T19:00:00Z"),
        ])
        out = self.inbox("alpha")
        self.assertEqual(self.ids(out), ["beta-r2", "beta-r3", "beta-r1"])

    def test_replies_attached_in_order(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", to=["alpha"])])
        self.r.write_log("rev", [rep(1, "rev", "rev-r1", "beta-r1", t="2026-10-21T19:00:00Z")])
        self.r.write_log("owner", [rep(1, "owner", "own-r1", "beta-r1", t="2026-10-21T18:30:00Z")])
        out = self.inbox("alpha", "--all")
        replies = out["requests"][0]["replies"]
        self.assertEqual([r["summary"] for r in replies], ["answer own-r1", "answer rev-r1"])

    def test_string_to_is_one_recipient(self):
        # A string `to` names one recipient, matching the page's help card.
        self.r.write_log("beta", [req(1, "beta", "beta-r1", to="alpha")])
        out = self.inbox("alpha")
        self.assertEqual(self.ids(out), ["beta-r1"])
        out2 = self.inbox("rev")
        self.assertEqual(out2["requests"], [])

    def test_quarantined_requester_listed(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        for p in roster["participants"]:
            if p["id"] == "rev":
                p["trust"] = "quarantined"
        self.r.write_roster(roster)
        self.r.write_log("rev", [req(1, "rev", "rev-r1", to=["alpha"])])
        out = self.inbox("alpha")
        self.assertEqual(self.ids(out), ["rev-r1"])

    def test_quarantined_requester_flagged(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        for p in roster["participants"]:
            if p["id"] == "rev":
                p["trust"] = "quarantined"
        self.r.write_roster(roster)
        self.r.write_log("rev", [req(1, "rev", "rev-r1", to=["alpha"])])
        out = self.inbox("alpha")
        self.assertEqual(out["requests"][0]["trust"], "quarantined")

    def test_approved_requester_flagged(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", to=["alpha"])])
        out = self.inbox("alpha")
        self.assertEqual(out["requests"][0]["trust"], "approved")

    def test_quarantined_reply_flagged(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        for p in roster["participants"]:
            if p["id"] == "rev":
                p["trust"] = "quarantined"
        self.r.write_roster(roster)
        self.r.write_log("beta", [req(1, "beta", "beta-r1", to=["alpha"])])
        self.r.write_log("rev", [rep(1, "rev", "rev-r1", "beta-r1")])
        out = self.inbox("alpha", "--all")
        self.assertEqual(out["requests"][0]["replies"][0]["trust"], "quarantined")

    def test_unknown_requester_flagged(self):
        self.r.write_log("gemini", [req(1, "gemini", "gemini-r1", to=["alpha"])])
        out = self.inbox("alpha")
        self.assertEqual(out["requests"][0]["trust"], "unknown")

    def test_roster_map_present(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", to=["alpha"])])
        out = self.inbox("alpha")
        self.assertIn("roster", out)
        self.assertEqual(out["roster"]["rev"]["trust"], "approved")
        self.assertEqual(out["roster"]["alpha"]["trust"], "approved")

    def test_unknown_pid_exits_1(self):
        p = self.run_cli("inbox", "gemini")
        self.assertEqual(p.returncode, 1)
        self.assertIn("gemini", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_malformed_log_exits_1(self):
        self.r.write_log("rev", [line(1, "rev"), "{not json"])
        p = self.run_cli("inbox", "alpha")
        self.assertEqual(p.returncode, 1)
        self.assertIn("log/rev.jsonl:2:", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_service_block_present(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", to=["alpha"])])
        out = self.inbox("alpha")
        svc = out["service"]
        self.assertEqual(svc["name"], "Crosstalk")
        for key in ("purpose", "capabilities", "commands", "protocol"):
            self.assertIn(key, svc)
        self.assertTrue(any("Crosstalk feature:" in c for c in svc["capabilities"]))

    def test_multi_recipient_stays_open_for_unanswered_peer(self):
        from crosstalk.replay import enrich_messages, load_all_lines, replay
        from crosstalk.requests import open_requests, pending_recipients

        self.r.write_log("beta", [
            req(1, "beta", "beta-r1", to=["alpha", "rev", "alpha"]),
        ])
        out_before = self.inbox("alpha")
        self.assertEqual(self.ids(out_before), ["beta-r1"])
        self.assertEqual(out_before["requests"][0]["pending_from"], ["alpha", "rev"])

        self.r.write_log("alpha", [rep(1, "alpha", "alpha-r1", "beta-r1")])
        out_al = self.inbox("alpha")
        self.assertEqual(out_al["requests"], [])

        out_al_all = self.inbox("alpha", "--all")
        self.assertEqual(self.ids(out_al_all), ["beta-r1"])
        self.assertEqual(out_al_all["requests"][0]["pending_from"], ["rev"])

        out_rev = self.inbox("rev")
        self.assertEqual(self.ids(out_rev), ["beta-r1"])
        self.assertEqual(out_rev["requests"][0]["pending_from"], ["rev"])
        self.assertEqual(len(out_rev["requests"][0]["replies"]), 1)
        self.assertEqual(out_rev["requests"][0]["replies"][0]["from"], "alpha")

        lines = load_all_lines(self.r.root)
        docs = enrich_messages(replay(lines), lines)
        self.assertEqual([did for (did, _, _) in open_requests(docs)], ["beta-r1"])

        self.r.write_log("rev", [rep(1, "rev", "rev-r1", "beta-r1")])
        self.assertEqual(self.inbox("rev")["requests"], [])
        lines2 = load_all_lines(self.r.root)
        docs2 = enrich_messages(replay(lines2), lines2)
        self.assertEqual(open_requests(docs2), [])
        self.assertEqual(pending_recipients({"to": "alpha"}, []), ["alpha"])
        self.assertEqual(pending_recipients({}, []), [])

    def test_broadcast_per_recipient_and_excludes_sender(self):
        from crosstalk.replay import enrich_messages, load_all_lines, replay
        from crosstalk.requests import open_requests

        self.r.write_log("beta", [
            req(1, "beta", "beta-bcast"),
            req(2, "beta", "beta-self", to=["beta", "alpha"]),
        ])
        # Sender does not see its own broadcast in open inbox, but sees explicit self-addressed request
        out_be = self.inbox("beta")
        self.assertEqual(self.ids(out_be), ["beta-self"])
        # --all includes sender's own broadcast
        out_be_all = self.inbox("beta", "--all")
        self.assertEqual(set(self.ids(out_be_all)), {"beta-bcast", "beta-self"})

        # Other participants see broadcast
        self.assertIn("beta-bcast", self.ids(self.inbox("alpha")))
        self.assertIn("beta-bcast", self.ids(self.inbox("rev")))

        # alpha replies to broadcast: drops from alpha's inbox, stays in rev's inbox
        self.r.write_log("alpha", [rep(1, "alpha", "alpha-r1", "beta-bcast")])
        self.assertNotIn("beta-bcast", self.ids(self.inbox("alpha")))
        out_rev = self.inbox("rev")
        self.assertEqual(self.ids(out_rev), ["beta-bcast"])
        self.assertEqual(out_rev["requests"][0]["pending_from"], [])
        self.assertEqual(len(out_rev["requests"][0]["replies"]), 1)

        # In global open_requests(docs), broadcast closes after first reply while multi-recipient stays open
        lines = load_all_lines(self.r.root)
        docs = enrich_messages(replay(lines), lines)
        self.assertEqual([did for (did, _, _) in open_requests(docs)], ["beta-self"])

    def test_owner_messages_unanswered_and_replied(self):
        self.r.write_log("owner", [
            line(1, "owner", id_="own-bcast",
                 data={"from": "owner", "kind": "owner", "summary": "all hands"},
                 t="2026-10-21T18:00:00Z"),
            line(2, "owner", id_="own-rev",
                 data={"from": "owner", "kind": "owner", "summary": "for rev", "to": ["rev"]},
                 t="2026-10-21T19:00:00Z"),
            line(3, "owner", id_="own-al",
                 data={"from": "owner", "kind": "owner", "summary": "for al", "to": ["alpha"]},
                 t="2026-10-21T20:00:00Z"),
        ])
        out = self.inbox("alpha")
        self.assertEqual([m["message"]["id"] for m in out["owner_messages"]], ["own-al", "own-bcast"])
        self.assertEqual(self.inbox("owner")["owner_messages"], [])

        # Another participant replying to broadcast owner message does not close it for alpha
        self.r.write_log("rev", [rep(1, "rev", "rev-r1", "own-bcast", t="2026-10-21T20:30:00Z")])
        out2 = self.inbox("alpha")
        self.assertEqual([m["message"]["id"] for m in out2["owner_messages"]], ["own-al", "own-bcast"])
        bcast_entry = [m for m in out2["owner_messages"] if m["message"]["id"] == "own-bcast"][0]
        self.assertEqual(len(bcast_entry["replies"]), 1)
        self.assertEqual(bcast_entry["replies"][0]["trust"], "approved")

        # Once alpha replies to own-al, own-al drops out for alpha
        # (alpha joined before the broadcast, so the broadcast is still its to answer)
        self.r.write_log("alpha", [
            line(1, "alpha", id_="alpha-c0",
                 data={"from": "alpha", "kind": "check-in", "summary": "here"},
                 t="2026-10-21T17:00:00Z"),
            rep(2, "alpha", "alpha-r1", "own-al", t="2026-10-21T20:45:00Z"),
        ])
        out3 = self.inbox("alpha")
        self.assertEqual([m["message"]["id"] for m in out3["owner_messages"]], ["own-bcast"])

    def test_owner_messages_rejects_spoofed_kind_and_owner_replies(self):
        from crosstalk.requests import unanswered_owner_messages

        docs = {
            ("messages", "rev-spoof"): {
                "from": "rev",
                "kind": "owner",
                "summary": "spoofed owner directive",
                "to": ["alpha"],
                "t": "2026-10-21T20:00:00Z",
            },
            ("messages", "own-top"): {
                "from": "owner",
                "kind": "owner",
                "summary": "real owner directive",
                "to": ["alpha"],
                "t": "2026-10-21T19:00:00Z",
            },
            ("messages", "own-reply"): {
                "from": "owner",
                "kind": "owner",
                "summary": "owner reply",
                "replyTo": "beta-r1",
                "to": ["alpha"],
                "t": "2026-10-21T21:00:00Z",
            },
        }
        res = unanswered_owner_messages(docs, "alpha")
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
        self.r.write_log("beta", [
            line(1, "beta", id_="beta-first",
                 data={"from": "beta", "kind": "check-in", "summary": "hello"},
                 t="2026-10-21T12:00:00Z"),
        ])
        out = self.inbox("beta")
        self.assertEqual([m["message"]["id"] for m in out["owner_messages"]],
                         ["own-new", "own-same"])
        # alpha has no lines yet: it sees every broadcast
        out_al = self.inbox("alpha")
        self.assertEqual([m["message"]["id"] for m in out_al["owner_messages"]],
                         ["own-new", "own-same", "own-group", "own-old"])

    def test_owner_message_naming_participant_listed_even_before_first_line(self):
        self.r.write_log("owner", [
            line(1, "owner", id_="own-to",
                 data={"from": "owner", "kind": "owner", "summary": "welcome", "to": ["beta"]},
                 t="2026-10-21T09:00:00Z"),
            line(2, "owner", id_="own-to-str",
                 data={"from": "owner", "kind": "owner", "summary": "welcome", "to": "beta"},
                 t="2026-10-21T09:30:00Z"),
            line(3, "owner", id_="own-alias",
                 data={"from": "owner", "kind": "decision", "summary": "@be please set up"},
                 t="2026-10-21T10:00:00Z"),
            line(4, "owner", id_="own-name-and-all",
                 data={"from": "owner", "kind": "owner", "summary": "@everyone, esp. @Beta"},
                 t="2026-10-21T10:30:00Z"),
            line(5, "owner", id_="own-bcast",
                 data={"from": "owner", "kind": "owner", "summary": "everyone"},
                 t="2026-10-21T11:00:00Z"),
        ])
        self.r.write_log("beta", [
            line(1, "beta", id_="beta-first",
                 data={"from": "beta", "kind": "check-in", "summary": "hello"},
                 t="2026-10-21T12:00:00Z"),
        ])
        out = self.inbox("beta")
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
            ("messages", "beta-first"): {"from": "beta", "kind": "check-in",
                                       "summary": "hi", "t": "2026-10-21T12:00:00.500Z"},
            ("messages", "alpha-undated"): {"from": "alpha", "kind": "check-in",
                                          "summary": "hi", "t": "not a time"},
        }
        be = sorted(did for (did, _, _) in unanswered_owner_messages(docs, "beta"))
        self.assertEqual(be, ["own-bad-t", "own-no-t"])
        # alpha has only an undated row: no join time, so nothing is hidden
        al = sorted(did for (did, _, _) in unanswered_owner_messages(docs, "alpha"))
        self.assertEqual(al, ["own-bad-t", "own-no-t", "own-old"])

    def test_agreements_to_adopt(self):
        self.r.write_log("beta", [
            line(1, "beta", collection="agreements", id_="a-ready",
                 data={"builder": "beta", "adopter": "alpha",
                       "status": "ready-to-adopt",
                       "commits": {"beta": ["111"], "alpha": []},
                       "updated": "2026-10-21T20:00:00Z"}),
            line(2, "beta", collection="agreements", id_="a-both",
                 data={"builder": "beta", "adopter": "both",
                       "status": "landed-builder",
                       "commits": {"beta": ["222"]},
                       "t": "2026-10-21T19:00:00Z"}),
            line(3, "beta", collection="agreements", id_="a-all",
                 data={"builder": "beta", "adopter": "all",
                       "status": "landed-builder",
                       "t": "2026-10-21T18:00:00Z"}),
            line(4, "beta", collection="agreements", id_="a-held-flag",
                 data={"builder": "beta", "adopter": "alpha",
                       "status": "ready-to-adopt", "hold": True,
                       "updated": "2026-10-21T21:00:00Z"}),
            line(5, "beta", collection="agreements", id_="a-held-status",
                 data={"builder": "beta", "adopter": "alpha",
                       "status": "held", "updated": "2026-10-21T21:00:00Z"}),
            line(6, "beta", collection="agreements", id_="a-agreed",
                 data={"builder": "beta", "adopter": "alpha",
                       "status": "agreed", "updated": "2026-10-21T21:00:00Z"}),
            line(7, "beta", collection="agreements", id_="a-done",
                 data={"builder": "beta", "adopter": "alpha",
                       "status": "ready-to-adopt",
                       "commits": {"alpha": ["abc1234"]},
                       "updated": "2026-10-21T21:00:00Z"}),
            line(8, "beta", collection="agreements", id_="a-rev",
                 data={"builder": "beta", "adopter": "rev",
                       "status": "ready-to-adopt",
                       "updated": "2026-10-21T21:00:00Z"}),
        ])
        out_al = self.inbox("alpha")
        self.assertEqual([a["id"] for a in out_al["agreements_to_adopt"]],
                         ["a-ready", "a-both", "a-all"])
        # Builder does not adopt its own 'both'/'all' agreements
        out_be = self.inbox("beta")
        self.assertEqual(out_be["agreements_to_adopt"], [])
        # Reviewer only adopts agreements explicitly naming it, not 'both'/'all' project agreements
        out_rev = self.inbox("rev")
        self.assertEqual([a["id"] for a in out_rev["agreements_to_adopt"]],
                         ["a-rev"])
        self.assertEqual(self.inbox("owner")["agreements_to_adopt"], [])

    def test_quarantined_or_third_party_reply_does_not_censor_request(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        for p in roster["participants"]:
            if p["id"] == "rev":
                p["trust"] = "quarantined"
        self.r.write_roster(roster)
        self.r.write_log("beta", [
            req(1, "beta", "beta-req-1", to=["alpha"]),
        ])
        self.r.write_log("rev", [
            rep(1, "rev", "rev-hijack", "beta-req-1"),
        ])
        out_open = self.inbox("alpha")
        self.assertEqual(self.ids(out_open), ["beta-req-1"])
        self.assertEqual(out_open["requests"][0]["pending_from"], ["alpha"])
        out_all = self.inbox("alpha", "--all")
        self.assertEqual(out_all["requests"][0]["pending_from"], ["alpha"])
        self.assertEqual(out_all["requests"][0]["replies"][0]["trust"], "quarantined")

        # Even if rev is approved, a third-party reply to a 1-recipient request for alpha
        # does not remove it from alpha's open inbox
        self.r.write_roster(SPEC_ROSTER)
        out_open2 = self.inbox("alpha")
        self.assertEqual(self.ids(out_open2), ["beta-req-1"])
        self.assertEqual(out_open2["requests"][0]["pending_from"], ["alpha"])

        # Owner reply does close the 1-recipient request
        self.r.write_log("owner", [
            rep(1, "owner", "own-close", "beta-req-1"),
        ])
        self.assertEqual(self.inbox("alpha")["requests"], [])

    def test_stale_locks_extreme_timezone_does_not_overflow(self):
        self.r.write_log("alpha", [
            line(1, "alpha", collection="locks", id_="l-alpha-extreme",
                 data={
                     "project": "alpha",
                     "path": "a.rs",
                     "since": "0001-01-01T00:00:00+01:00",
                     "released": None,
                 })
        ])
        out = self.inbox("alpha")
        self.assertEqual(out["stale_locks"], [])

    def test_stale_locks(self):
        from crosstalk.replay import enrich_messages, load_all_lines, replay
        from crosstalk.requests import stale_locks_for

        self.r.write_log("alpha", [
            line(1, "alpha", collection="locks", id_="l-old-2",
                 data={"project": "alpha", "path": "b.rs",
                       "since": "2026-10-21T12:00:00Z"}),
            line(2, "alpha", collection="locks", id_="l-old-1",
                 data={"project": "alpha", "path": "a.rs",
                       "since": "2026-10-21T10:00:00Z", "released": None}),
            line(3, "alpha", collection="locks", id_="l-fresh",
                 data={"project": "alpha", "path": "c.rs",
                       "since": "2099-01-01T00:00:00Z", "released": None}),
            line(4, "alpha", collection="locks", id_="l-released",
                 data={"project": "alpha", "path": "d.rs",
                       "since": "2026-10-21T08:00:00Z", "released": "2026-10-21T09:00:00Z"}),
        ])
        self.r.write_log("beta", [
            line(1, "beta", collection="locks", id_="l-be",
                 data={"project": "beta", "path": "v.rs",
                       "since": "2026-10-21T08:00:00Z", "released": None}),
        ])
        out = self.inbox("alpha")
        self.assertEqual([lk["id"] for lk in out["stale_locks"]], ["l-old-1", "l-old-2"])

        lines = load_all_lines(self.r.root)
        docs = enrich_messages(replay(lines), lines)
        # At 15:00Z: l-old-1 (10:00Z, 5h) is stale; l-old-2 (12:00Z, 3h) is not
        self.assertEqual([lk["id"] for lk in stale_locks_for(docs, "alpha", now_iso="2026-10-21T15:00:00Z")],
                         ["l-old-1"])
        # At 16:00:00Z: l-old-2 is exactly 4h old (not > 4h)
        self.assertEqual([lk["id"] for lk in stale_locks_for(docs, "alpha", now_iso="2026-10-21T16:00:00Z")],
                         ["l-old-1"])
        # At 16:00:01Z: both are > 4h old, sorted oldest since first
        self.assertEqual([lk["id"] for lk in stale_locks_for(docs, "alpha", now_iso="2026-10-21T16:00:01Z")],
                         ["l-old-1", "l-old-2"])

    def test_at_mentions_target_recipients_when_to_omitted(self):
        self.r.write_log("owner", [
            line(1, "owner", id_="own-1", t="2026-10-24T12:00:00Z",
                 data={"from": "owner", "kind": "owner",
                       "summary": "@Alpha check robotron audio",
                       "body": "Ref beta@f0bfbca3 and user@example.com"}),
            line(2, "owner", id_="own-2", t="2026-10-24T12:01:00Z",
                 data={"from": "owner", "kind": "owner",
                       "summary": "@Everyone status check"}),
        ])
        al_out = self.inbox("alpha")
        be_out = self.inbox("beta")
        self.assertEqual([m["message"]["id"] for m in al_out["owner_messages"]],
                         ["own-2", "own-1"])
        self.assertEqual([m["message"]["id"] for m in be_out["owner_messages"]],
                         ["own-2"])

    def test_at_mentions_aliases_groups_broadcast_and_owner_message_kinds(self):
        from crosstalk.requests import extract_mentions, unanswered_owner_messages

        # Aliases and groups come from the roster
        three = {
            "gamma": {"name": "Gamma", "kind": "project", "trust": "approved", "aliases": ["ga"]},
            "alpha": {"name": "Alpha", "kind": "project", "trust": "approved", "aliases": ["al"]},
            "beta": {"name": "Beta", "kind": "project", "trust": "approved", "aliases": ["be"]},
            "rev": {"name": "Rev", "kind": "reviewer", "trust": "approved"},
        }
        self.assertEqual(
            extract_mentions({"summary": "Ping (@al, @be; @ga: check this)"}, roster=three),
            ["alpha", "beta", "gamma"],
        )
        self.assertEqual(
            extract_mentions({"summary": "@projects sync up"}, roster=three),
            ["gamma", "alpha", "beta"],
        )
        self.assertEqual(
            extract_mentions({"summary": "@teams and @reviewers"}, roster=three),
            ["gamma", "alpha", "beta", "rev"],
        )
        # With no roster nothing resolves: no project names are built in
        self.assertEqual(extract_mentions({"summary": "Ping @al and @projects"}), [])
        # Broadcast handles override specific mentions
        for bcast in ("@everyone", "@all", "@channel", "@here"):
            self.assertEqual(
                extract_mentions({"summary": "%s and @Alpha" % bcast}, roster=three),
                [],
            )
        # Commit refs and emails do not trigger mentions
        self.assertEqual(
            extract_mentions({"summary": "See beta@f0bfbca3 or dev@alpha.com"}, roster=three),
            [],
        )
        # @owner is excluded from extract_mentions so "(cc @owner)" does not narrow a broadcast
        custom_roster = {
            "owner": {"name": "Beta", "kind": "person", "trust": "approved"},
            "alpha": {"name": "Alpha", "kind": "project", "trust": "approved"},
        }
        self.assertEqual(extract_mentions({"summary": "@Beta hi"}, roster=custom_roster), [])
        self.assertEqual(extract_mentions({"summary": "@owner hi"}, roster=custom_roster), [])

        # Test CLI inbox with requests and owner_messages of various kinds (alert, agreement, lesson, decision)
        self.r.write_log("owner", [
            line(1, "owner", id_="own-alert", t="2026-10-24T12:00:00Z",
                 data={"from": "owner", "kind": "alert",
                       "summary": "Urgent: @al fix audio"}),
            line(2, "owner", id_="own-agree", t="2026-10-24T12:01:00Z",
                 data={"from": "owner", "kind": "agreement",
                       "summary": "Agreement for @projects"}),
            line(3, "owner", id_="own-lesson", t="2026-10-24T12:02:00Z",
                 data={"from": "owner", "kind": "lesson",
                       "summary": "Lesson for @reviewers"}),
            line(4, "owner", id_="own-decision", t="2026-10-24T12:03:00Z",
                 data={"from": "owner", "kind": "decision",
                       "summary": "Decision for @be and @channel"}),
            line(5, "owner", id_="own-checkin", t="2026-10-24T12:04:00Z",
                 data={"from": "owner", "kind": "check-in",
                       "summary": "Owner routine check-in"}),
        ])
        self.r.write_log("beta", [
            # beta joined before the owner's group messages
            line(1, "beta", id_="beta-checkin", t="2026-10-24T11:00:00Z",
                 data={"from": "beta", "kind": "check-in", "summary": "here"}),
            line(2, "beta", id_="beta-req-al", t="2026-10-24T12:05:00Z",
                 data={"from": "beta", "kind": "request",
                       "summary": "Hey @al can you check beta@f0bfbca3?"}),
            line(3, "beta", id_="beta-req-rev", t="2026-10-24T12:06:00Z",
                 data={"from": "beta", "kind": "request",
                       "summary": "Need @reviewers on this PR"}),
        ])

        al_out = self.inbox("alpha")
        self.assertEqual(
            [m["message"]["id"] for m in al_out["owner_messages"]],
            ["own-decision", "own-agree", "own-alert"],
        )
        self.assertEqual(self.ids(al_out), ["beta-req-al"])
        self.assertEqual(al_out["requests"][0]["pending_from"], ["alpha"])

        be_out = self.inbox("beta")
        self.assertEqual(
            [m["message"]["id"] for m in be_out["owner_messages"]],
            ["own-decision", "own-agree"],
        )
        self.assertEqual(self.ids(be_out), [])

        rev_out = self.inbox("rev")
        self.assertEqual(
            [m["message"]["id"] for m in rev_out["owner_messages"]],
            ["own-decision", "own-lesson"],
        )
        self.assertEqual(self.ids(rev_out), ["beta-req-rev"])

    def test_extract_mentions_subagent_dots_punctuation_quarantine_and_non_string(self):
        from crosstalk.requests import extract_mentions

        roster = {
            "owner": {"name": "Owner", "kind": "person", "trust": "approved"},
            "alpha": {"name": "Alpha", "kind": "project", "trust": "approved", "aliases": ["al"]},
            "beta": {"name": "Beta", "kind": "project", "trust": "approved", "aliases": ["be"]},
            "quarantined-proj": {"name": "QuarantinedProj", "kind": "project", "trust": "quarantined"},
            "rev": {"name": "Rev", "kind": "reviewer", "trust": "approved"},
            "quarantined-rev": {"name": "QuarantinedRev", "kind": "reviewer", "trust": "quarantined"},
            "alpha.verifier": {"name": "AL Verifier", "kind": "agent", "trust": "approved"},
        }
        self.assertEqual(
            extract_mentions(
                {"summary": "Ask @alpha.verifier, @al! (@be?) and @owner."},
                roster=roster,
            ),
            ["alpha.verifier", "alpha", "beta"],
        )
        self.assertEqual(
            extract_mentions({"summary": "@projects and @reviewers"}, roster=roster),
            ["alpha", "beta", "rev"],
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
            "parent": "alpha",
            "color": "#2e7d32",
            "colorDark": "#81c784",
            "trust": "quarantined",
        })
        self.r.write_roster(roster_data)
        roster = load_roster(self.r.root / "participants.json")

        # Quarantined recipient in `to` does not deadlock multi-recipient request
        self.r.write_log("beta", [
            req(1, "beta", "beta-q", to=["alpha", "qbot"], t="2026-10-21T18:00:00Z"),
        ])
        self.r.write_log("owner", [
            line(1, "owner", id_="own-req-bcast", t="2026-10-21T18:05:00Z",
                 data={"from": "owner", "kind": "request", "summary": "Are all projects in sync? (cc @owner)"}),
        ])
        lines = load_all_lines(self.r.root, roster)
        docs = enrich_messages(replay(lines), lines)
        self.assertEqual(
            pending_recipients(docs[("messages", "beta-q")], [], roster=roster),
            ["alpha"],
        )
        self.assertEqual(
            pending_recipients(docs[("messages", "own-req-bcast")], [], roster=roster),
            ["alpha", "beta"],
        )

        # alpha replies to both: be-q closes, own-req-bcast stays open waiting for beta
        self.r.write_log("alpha", [
            rep(1, "alpha", "alpha-r1", "beta-q", t="2026-10-21T19:00:00Z"),
            rep(2, "alpha", "alpha-r2", "own-req-bcast", t="2026-10-21T19:01:00Z"),
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
                [docs2[("messages", "alpha-r2")]],
                roster=roster,
            ),
            ["beta"],
        )

        # beta replies to own-req-bcast: now it closes
        self.r.write_log("beta", [
            req(1, "beta", "beta-q", to=["alpha", "qbot"], t="2026-10-21T18:00:00Z"),
            rep(2, "beta", "beta-r1", "own-req-bcast", t="2026-10-21T19:02:00Z"),
        ])
        lines3 = load_all_lines(self.r.root, roster)
        docs3 = enrich_messages(replay(lines3), lines3)
        self.assertEqual(open_requests(docs3, roster=roster), [])


if __name__ == "__main__":
    unittest.main()
