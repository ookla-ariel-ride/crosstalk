"""Tests for ergonomic replies, peer consults, and agreement adoption (Track 3)."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.service import SERVICE

REPO = Path(__file__).resolve().parent.parent


def roster_with_amb():
    data = json.loads(json.dumps(SPEC_ROSTER))
    data["participants"].append({
        "id": "avoidmissingball",
        "name": "AvoidMissingBall",
        "kind": "project",
        "repo": "ookla-ariel-ride/avoidmissingball",
        "color": "#2e6f40",
        "colorDark": "#68c783",
        "checkInMin": 30,
        "trust": "approved",
    })
    return data


class ConsultsUnitTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(roster_with_amb())
        self.roster = load_roster(self.r.root / "participants.json")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def docs(self):
        lines = load_all_lines(self.r.root, self.roster)
        return enrich_messages(replay(lines), lines)

    def test_post_reply_populates_reply_to_and_recipient(self):
        from crosstalk.consults import post_reply

        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", id_="vc-r1",
                 data={"from": "vectorcraft", "kind": "request", "summary": "Need parity check"})
        ])
        ln = post_reply(
            self.r.root,
            "bewareilive",
            "vc-r1",
            "Confirmed on Williams hardware",
            body="Details with 'apostrophes' included",
            refs=["bewareilive@51e7709"],
            roster=self.roster,
        )
        self.assertTrue(ln.id.startswith("bewareilive-"))
        doc = self.docs()[("messages", ln.id)]
        self.assertEqual(doc["from"], "bewareilive")
        self.assertEqual(doc["kind"], "reply")
        self.assertEqual(doc["replyTo"], "vc-r1")
        self.assertEqual(doc["to"], ["vectorcraft"])
        self.assertEqual(doc["summary"], "Confirmed on Williams hardware")
        self.assertEqual(doc["body"], "Details with 'apostrophes' included")
        self.assertEqual(doc["refs"], ["bewareilive@51e7709"])

    def test_post_reply_unknown_message_raises(self):
        from crosstalk.consults import post_reply

        with self.assertRaises(ValueError) as ctx:
            post_reply(self.r.root, "bewareilive", "no-such-msg", "Ack", roster=self.roster)
        self.assertIn("no such message 'no-such-msg'", str(ctx.exception))

    def test_post_consult_creates_request_with_refs_and_to(self):
        from crosstalk.consults import post_consult

        ln = post_consult(
            self.r.root,
            "avoidmissingball",
            "Centipede RNG bank window",
            body="Question about POKEY timer...",
            refs=["avoidmissingball@91bab82"],
            to=["vectorcraft", "bewareilive"],
            roster=self.roster,
        )
        self.assertTrue(ln.id.startswith("avoidmissingball-"))
        doc = self.docs()[("messages", ln.id)]
        self.assertEqual(doc["from"], "avoidmissingball")
        self.assertEqual(doc["kind"], "request")
        self.assertEqual(doc["summary"], "Centipede RNG bank window")
        self.assertEqual(doc["body"], "Question about POKEY timer...")
        self.assertEqual(doc["refs"], ["avoidmissingball@91bab82"])
        self.assertEqual(doc["to"], ["vectorcraft", "bewareilive"])

    def test_post_consult_drops_sender_from_to(self):
        from crosstalk.consults import post_consult
        from crosstalk.requests import inbox_for

        ln = post_consult(
            self.r.root, "avoidmissingball", "Own id in --to",
            to=["vectorcraft", "avoidmissingball", "bewareilive"], roster=self.roster,
        )
        self.assertEqual(self.docs()[("messages", ln.id)]["to"], ["vectorcraft", "bewareilive"])
        self.assertNotIn(ln.id, [did for did, _, _ in inbox_for(self.docs(), "avoidmissingball", roster=self.roster)])

    def test_post_consult_to_only_sender_raises(self):
        from crosstalk.consults import post_consult

        with self.assertRaises(ValueError) as ctx:
            post_consult(self.r.root, "avoidmissingball", "Only me", to=["avoidmissingball"], roster=self.roster)
        self.assertIn("--to", str(ctx.exception))

    def test_adopt_agreement_specific_adopter_lands_both(self):
        from crosstalk.consults import adopt_agreement

        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", collection="agreements", id_="a-fast-verify",
                 data={
                     "t": "2026-10-25T10:00:00Z",
                     "summary": "Fast verify lane",
                     "builder": "vectorcraft",
                     "adopter": "bewareilive",
                     "status": "ready-to-adopt",
                     "commits": {"vectorcraft": ["a1b2c3d"]},
                 })
        ])
        res = adopt_agreement(
            self.r.root,
            "bewareilive",
            "a-fast-verify",
            "51e7709",
            summary="Adopted fast verify",
            roster=self.roster,
        )
        self.assertEqual(res["agreement_id"], "a-fast-verify")
        self.assertEqual(res["status"], "landed-both")
        self.assertTrue(res["message_id"].startswith("bewareilive-"))

        docs = self.docs()
        agr = docs[("agreements", "a-fast-verify")]
        self.assertEqual(agr["status"], "landed-both")
        self.assertEqual(agr["commits"]["vectorcraft"], ["a1b2c3d"])
        self.assertEqual(agr["commits"]["bewareilive"], ["51e7709"])
        self.assertIn("updated", agr)

        msg = docs[("messages", res["message_id"])]
        self.assertEqual(msg["kind"], "sync")
        self.assertEqual(msg["summary"], "Adopted fast verify")
        self.assertEqual(msg["refs"], ["a-fast-verify", "bewareilive@51e7709"])

    def test_adopt_agreement_multi_project_waits_for_all_projects(self):
        from crosstalk.consults import adopt_agreement

        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", collection="agreements", id_="a-shared-hook",
                 data={
                     "t": "2026-10-25T10:00:00Z",
                     "summary": "Shared pre-commit hook",
                     "builder": "vectorcraft",
                     "adopter": "all",
                     "status": "ready-to-adopt",
                     "commits": {"vectorcraft": ["a1b2c3d"]},
                 })
        ])
        # First sister project adopts: avoidmissingball still hasn't adopted
        res1 = adopt_agreement(
            self.r.root, "bewareilive", "a-shared-hook", "51e7709", roster=self.roster
        )
        self.assertEqual(res1["status"], "ready-to-adopt")

        # Duplicate commit by same project is deduplicated
        adopt_agreement(
            self.r.root, "bewareilive", "a-shared-hook", "51e7709", roster=self.roster
        )
        self.assertEqual(
            self.docs()[("agreements", "a-shared-hook")]["commits"]["bewareilive"],
            ["51e7709"],
        )

        # Second sister project adopts: all non-builder approved projects now have commits
        res2 = adopt_agreement(
            self.r.root, "avoidmissingball", "a-shared-hook", "91bab82", roster=self.roster
        )
        self.assertEqual(res2["status"], "landed-both")
        agr = self.docs()[("agreements", "a-shared-hook")]
        self.assertEqual(agr["status"], "landed-both")
        self.assertEqual(agr["commits"]["avoidmissingball"], ["91bab82"])

    def test_adopt_agreement_refuses_bad_sha_missing_or_held(self):
        from crosstalk.consults import adopt_agreement

        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", collection="agreements", id_="a-held-one",
                 data={
                     "summary": "Held agreement",
                     "builder": "vectorcraft",
                     "status": "ready-to-adopt",
                     "hold": True,
                 }),
            line(2, "vectorcraft", collection="agreements", id_="a-status-held",
                 data={
                     "summary": "Legacy held status",
                     "builder": "vectorcraft",
                     "status": "held",
                 }),
        ])
        with self.assertRaises(ValueError):
            adopt_agreement(self.r.root, "bewareilive", "a-held-one", "not-a-sha", roster=self.roster)
        with self.assertRaises(ValueError) as ctx_missing:
            adopt_agreement(self.r.root, "bewareilive", "a-missing", "51e7709", roster=self.roster)
        self.assertIn("no such agreement 'a-missing'", str(ctx_missing.exception))
        with self.assertRaises(ValueError) as ctx_hold:
            adopt_agreement(self.r.root, "bewareilive", "a-held-one", "51e7709", roster=self.roster)
        self.assertIn("is on hold", str(ctx_hold.exception))
        with self.assertRaises(ValueError) as ctx_status_held:
            adopt_agreement(self.r.root, "bewareilive", "a-status-held", "51e7709", roster=self.roster)
        self.assertIn("is on hold", str(ctx_status_held.exception))

    def test_adopt_agreement_handles_list_adopter_and_malformed_commits(self):
        from crosstalk.consults import adopt_agreement

        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", collection="agreements", id_="a-list-adopter",
                 data={
                     "builder": "vectorcraft",
                     "adopter": ["bewareilive", "avoidmissingball"],
                     "status": "ready-to-adopt",
                     "summary": "List adopter agreement",
                 }),
            line(2, "vectorcraft", collection="agreements", id_="a-list-commits",
                 data={
                     "builder": "vectorcraft",
                     "adopter": "bewareilive",
                     "status": "ready-to-adopt",
                     "commits": ["a1b2c3d"],
                     "summary": "List commits agreement",
                 }),
            line(3, "vectorcraft", collection="agreements", id_="a-str-commits",
                 data={
                     "builder": "vectorcraft",
                     "adopter": "bewareilive",
                     "status": "ready-to-adopt",
                     "commits": {"vectorcraft": "a1b2c3d"},
                     "summary": "String commits agreement",
                 }),
        ])
        res1 = adopt_agreement(self.r.root, "bewareilive", "a-list-adopter", "51e7709", roster=self.roster)
        self.assertEqual(res1["status"], "ready-to-adopt")
        res1b = adopt_agreement(self.r.root, "avoidmissingball", "a-list-adopter", "91bab82", roster=self.roster)
        self.assertEqual(res1b["status"], "landed-both")

        res2 = adopt_agreement(self.r.root, "bewareilive", "a-list-commits", "51e7709", roster=self.roster)
        self.assertEqual(res2["status"], "landed-both")
        self.assertEqual(self.docs()[("agreements", "a-list-commits")]["commits"], {"bewareilive": ["51e7709"]})

        res3 = adopt_agreement(self.r.root, "bewareilive", "a-str-commits", "51e7709", roster=self.roster)
        self.assertEqual(res3["status"], "landed-both")
        self.assertEqual(
            self.docs()[("agreements", "a-str-commits")]["commits"],
            {"vectorcraft": ["a1b2c3d"], "bewareilive": ["51e7709"]},
        )

    def test_adopt_agreement_invalid_summary_leaves_no_partial_write(self):
        from crosstalk.consults import adopt_agreement

        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", collection="agreements", id_="a-fast-verify",
                 data={
                     "t": "2026-10-25T10:00:00Z",
                     "summary": "Fast verify",
                     "builder": "vectorcraft",
                     "adopter": "bewareilive",
                     "status": "ready-to-adopt",
                     "commits": {"vectorcraft": ["a1b2c3d"]},
                 })
        ])
        for bad_summary in ("Line 1\nLine 2", "Line 1\rLine 2", "Trailing\n", "   "):
            with self.assertRaises(ValueError):
                adopt_agreement(
                    self.r.root,
                    "bewareilive",
                    "a-fast-verify",
                    "51e7709",
                    summary=bad_summary,
                    roster=self.roster,
                )
        self.assertFalse((self.r.root / "log" / "bewareilive.jsonl").exists())

    def test_consult_and_reply_reject_unknown_pid_and_bad_or_existing_msg_id(self):
        from crosstalk.consults import adopt_agreement, post_consult, post_reply

        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", id_="vc-r1",
                 data={"from": "vectorcraft", "kind": "request", "summary": "Help?"}),
            line(2, "vectorcraft", collection="agreements", id_="a-1",
                 data={"builder": "vectorcraft", "adopter": "bewareilive",
                       "status": "ready-to-adopt", "summary": "Agr"}),
        ])
        self.r.write_log("owner", [
            line(1, "owner", id_="own-1",
                 data={"from": "owner", "kind": "owner", "summary": "Real owner instruction"})
        ])

        with self.assertRaises(ValueError):
            post_consult(self.r.root, "ghost", "Ghost consult", roster=self.roster)
        with self.assertRaises(ValueError):
            post_reply(self.r.root, "ghost", "vc-r1", "Ghost reply", roster=self.roster)
        with self.assertRaises(ValueError):
            adopt_agreement(self.r.root, "ghost", "a-1", "1234567", roster=self.roster)
        self.assertFalse((self.r.root / "log" / "ghost.jsonl").exists())

        # Cannot use non-prefixed msg_id or overwrite another message
        with self.assertRaises(ValueError):
            post_consult(self.r.root, "bewareilive", "Spoof", msg_id="own-1", roster=self.roster)
        with self.assertRaises(ValueError):
            post_reply(self.r.root, "bewareilive", "vc-r1", "Spoof", msg_id="own-1", roster=self.roster)

        # Cannot overwrite own existing message id either
        ln = post_consult(self.r.root, "bewareilive", "First", msg_id="bewareilive-custom1", roster=self.roster)
        self.assertEqual(ln.id, "bewareilive-custom1")
        with self.assertRaises(ValueError):
            post_consult(self.r.root, "bewareilive", "Second", msg_id="bewareilive-custom1", roster=self.roster)
        with self.assertRaises(ValueError):
            post_reply(self.r.root, "bewareilive", "vc-r1", "Reply dup", msg_id="bewareilive-custom1", roster=self.roster)

    def test_adopt_agreement_builder_or_reviewer_does_not_prematurely_land_both(self):
        from crosstalk.consults import adopt_agreement

        self.r.write_roster(SPEC_ROSTER)
        roster = load_roster(self.r.root / "participants.json")
        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", collection="agreements", id_="a-all-proj",
                 data={
                     "builder": "vectorcraft",
                     "adopter": "all",
                     "status": "ready-to-adopt",
                     "commits": {"vectorcraft": ["1111111"]},
                     "summary": "Shared agreement",
                 })
        ])
        res_builder = adopt_agreement(self.r.root, "vectorcraft", "a-all-proj", "2222222", roster=roster)
        self.assertEqual(res_builder["status"], "ready-to-adopt")
        res_reviewer = adopt_agreement(self.r.root, "codex", "a-all-proj", "3333333", roster=roster)
        self.assertEqual(res_reviewer["status"], "ready-to-adopt")
        res_adopter = adopt_agreement(self.r.root, "bewareilive", "a-all-proj", "4444444", roster=roster)
        self.assertEqual(res_adopter["status"], "landed-both")

    def test_consult_and_reply_reject_trailing_newline_and_wrap_bare_str_refs_to(self):
        from crosstalk.consults import post_consult, post_reply

        for bad in ("Trailing newline\n", "Leading\nnewline", "Carriage\rreturn", "   "):
            with self.assertRaises(ValueError):
                post_consult(self.r.root, "bewareilive", bad, roster=self.roster)

        ln = post_consult(
            self.r.root,
            "bewareilive",
            "  Clean summary  ",
            refs="bewareilive@51e7709",
            to="vectorcraft",
            roster=self.roster,
        )
        doc = self.docs()[("messages", ln.id)]
        self.assertEqual(doc["summary"], "Clean summary")
        self.assertEqual(doc["refs"], ["bewareilive@51e7709"])
        self.assertEqual(doc["to"], ["vectorcraft"])

        for bad in ("Trailing newline\n", "Carriage\rreturn", ""):
            with self.assertRaises(ValueError):
                post_reply(self.r.root, "vectorcraft", ln.id, bad, roster=self.roster)

        rep_ln = post_reply(
            self.r.root,
            "vectorcraft",
            ln.id,
            "  Ack reply  ",
            refs="vectorcraft@a1b2c3d",
            roster=self.roster,
        )
        rep_doc = self.docs()[("messages", rep_ln.id)]
        self.assertEqual(rep_doc["summary"], "Ack reply")
        self.assertEqual(rep_doc["refs"], ["vectorcraft@a1b2c3d"])

    def test_post_consult_auto_populates_to_from_at_mentions_when_to_omitted(self):
        from crosstalk.consults import post_consult

        # Single @mention in summary
        ln1 = post_consult(
            self.r.root,
            "vectorcraft",
            "@BewareILive how does defender sound work?",
            body="See vectorcraft@f0bfbca3 for context",
            roster=self.roster,
        )
        doc1 = self.docs()[("messages", ln1.id)]
        self.assertEqual(doc1["to"], ["bewareilive"])

        # Aliases and body @mentions
        ln2 = post_consult(
            self.r.root,
            "vectorcraft",
            "Audio timing question",
            body="Asking @bil and @amb (cc @reviewers)",
            roster=self.roster,
        )
        doc2 = self.docs()[("messages", ln2.id)]
        self.assertEqual(doc2["to"], ["bewareilive", "avoidmissingball", "codex"])

        # @everyone broadcast leaves `to` omitted
        ln3 = post_consult(
            self.r.root,
            "vectorcraft",
            "@everyone status check on @bil",
            roster=self.roster,
        )
        doc3 = self.docs()[("messages", ln3.id)]
        self.assertNotIn("to", doc3)

        # @projects or self-mention filters out sender pid and quarantined participants
        ln4 = post_consult(
            self.r.root,
            "vectorcraft",
            "@projects check shared bus timing (from @vectorcraft, cc @owner)",
            roster=self.roster,
        )
        doc4 = self.docs()[("messages", ln4.id)]
        self.assertEqual(doc4["to"], ["bewareilive", "avoidmissingball"])

        ln5 = post_consult(
            self.r.root,
            "vectorcraft",
            "Note from @vectorcraft (cc @owner)",
            roster=self.roster,
        )
        doc5 = self.docs()[("messages", ln5.id)]
        self.assertNotIn("to", doc5)


class ConsultsCliTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(roster_with_amb())
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def read_replay(self):
        p = self.run_cli("read")
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)["collections"]

    def test_cli_reply(self):
        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", id_="vc-r1",
                 data={"from": "vectorcraft", "kind": "request", "summary": "Check Williams timing"})
        ])
        p = self.run_cli(
            "reply",
            "--from", "bewareilive",
            "--to-id", "vc-r1",
            "--summary", "Confirmed on Williams hardware",
            "--body", "Details...",
            "--ref", "bewareilive@51e7709",
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        msg_id = p.stdout.strip()
        self.assertTrue(msg_id.startswith("bewareilive-"))
        msg = self.read_replay()["messages"][msg_id]
        self.assertEqual(msg["replyTo"], "vc-r1")
        self.assertEqual(msg["to"], ["vectorcraft"])
        self.assertEqual(msg["summary"], "Confirmed on Williams hardware")
        self.assertEqual(msg["body"], "Details...")
        self.assertEqual(msg["refs"], ["bewareilive@51e7709"])

        p_bad = self.run_cli(
            "reply",
            "--from", "bewareilive",
            "--to-id", "unknown-id",
            "--summary", "Nope",
        )
        self.assertEqual(p_bad.returncode, 1)
        self.assertIn("no such message", p_bad.stderr)

    def test_cli_consult(self):
        p = self.run_cli(
            "consult",
            "--from", "avoidmissingball",
            "--summary", "Centipede RNG bank window",
            "--body", "Question...",
            "--ref", "avoidmissingball@91bab82",
            "--to", "vectorcraft", "bewareilive",
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        msg_id = p.stdout.strip()
        self.assertTrue(msg_id.startswith("avoidmissingball-"))
        msg = self.read_replay()["messages"][msg_id]
        self.assertEqual(msg["kind"], "request")
        self.assertEqual(msg["summary"], "Centipede RNG bank window")
        self.assertEqual(msg["body"], "Question...")
        self.assertEqual(msg["refs"], ["avoidmissingball@91bab82"])
        self.assertEqual(msg["to"], ["vectorcraft", "bewareilive"])

        p_mention = self.run_cli(
            "consult",
            "--from", "vectorcraft",
            "--summary", "@BewareILive how does defender sound work?",
        )
        self.assertEqual(p_mention.returncode, 0, p_mention.stderr)
        mention_id = p_mention.stdout.strip()
        mention_msg = self.read_replay()["messages"][mention_id]
        self.assertEqual(mention_msg["to"], ["bewareilive"])

    def test_cli_adopt(self):
        self.r.write_log("vectorcraft", [
            line(1, "vectorcraft", collection="agreements", id_="a-fast-verify",
                 data={
                     "t": "2026-10-25T10:00:00Z",
                     "summary": "Fast verify",
                     "builder": "vectorcraft",
                     "adopter": "bewareilive",
                     "status": "ready-to-adopt",
                     "commits": {"vectorcraft": ["a1b2c3d"]},
                 })
        ])
        p = self.run_cli(
            "adopt",
            "--from", "bewareilive",
            "--agreement", "a-fast-verify",
            "--commit", "51e7709",
            "--summary", "Adopted fast verify",
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["agreement_id"], "a-fast-verify")
        self.assertEqual(out["status"], "landed-both")
        self.assertTrue(out["message_id"].startswith("bewareilive-"))

        colls = self.read_replay()
        self.assertEqual(colls["agreements"]["a-fast-verify"]["status"], "landed-both")
        self.assertEqual(colls["agreements"]["a-fast-verify"]["commits"]["bewareilive"], ["51e7709"])
        self.assertEqual(colls["messages"][out["message_id"]]["kind"], "sync")

    def test_service_and_protocol_advertise_new_commands(self):
        for cmd_key in ("write", "request", "read", "inbox", "check",
                        "reply", "consult", "adopt", "claim", "release", "land-sync", "incident"):
            self.assertIn(cmd_key, SERVICE["commands"])
        protocol_text = (REPO / "PROTOCOL.md").read_text(encoding="utf-8")
        for sub in ("reply", "consult", "adopt", "claim", "release", "land-sync", "incident"):
            self.assertIn(sub, protocol_text)


if __name__ == "__main__":
    unittest.main()
