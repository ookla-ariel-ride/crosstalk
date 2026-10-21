import json
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk.courier import load_state, save_state
from crosstalk.logs import read_log
from crosstalk.pull import PullResult, parse_listing, pull
from crosstalk.replay import load_all_lines, replay

PAGE = SPEC_ROSTER["page"]


def make_export(root, docs, listings=None):
    """docs: {(collection, id): fields}; listings: {collection: text}."""
    d = Path(root) / "export"
    d.mkdir(exist_ok=True)
    for (coll, did), fields in docs.items():
        (d / coll).mkdir(parents=True, exist_ok=True)
        (d / coll / (did + ".json")).write_text(json.dumps(fields), encoding="utf-8")
    for coll, text in (listings or {}).items():
        (d / (coll + ".listing.txt")).write_text(text, encoding="utf-8")
    return d


def owner_lines(root):
    path = Path(root) / "log" / "owner.jsonl"
    return read_log(path)[0] if path.exists() else []


def roster_with_gemini():
    data = json.loads(json.dumps(SPEC_ROSTER))
    data["participants"].append(
        {"id": "gemini", "name": "Gemini", "kind": "researcher", "trust": "quarantined"})
    return data


T0 = "2026-10-21T10:00:00Z"
T1 = "2026-10-21T11:00:00Z"


class ParseListingTests(unittest.TestCase):
    def test_parse_listing_summary(self):
        text = ("Saved 2 documents to /tmp/x/agreements\n"
                '- "a-fast-verify"  612 bytes  version 1  "2026-10-21T12:00:00Z"\n'
                '- "a-review-broker-cleanup"  1204 bytes  version 7  "2026-10-21T13:00:00Z"\n')
        self.assertEqual(parse_listing(text),
                         {"a-fast-verify": 1, "a-review-broker-cleanup": 7})

    def test_parse_listing_json_lines(self):
        text = ("3 documents in locks:\n"
                '{"id":"l-vc-githooks-branch-reviews","data":{"holder":"x"},'
                '"version":2,"updatedAt":"2026-10-21T17:20:00Z"}\n')
        self.assertEqual(parse_listing(text), {"l-vc-githooks-branch-reviews": 2})


class PullTests(unittest.TestCase):
    def test_refuses_on_empty_log(self):
        with TempRepo() as r:
            r.write_roster()
            exp = make_export(r.root, {("messages", "own-x"): {"body": "hi"}})
            with self.assertRaises(RuntimeError) as cm:
                pull(r.root, exp)
            self.assertEqual(str(cm.exception), "log is empty; run import first")
            self.assertEqual(owner_lines(r.root), [])

    def test_no_changes_no_lines(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"body": "a", "n": 1}, t=T0),
                                  line(2, "codex", "agreements", "codex-a",
                                       data={"status": "ready-to-adopt"}, t=T0)])
            exp = make_export(r.root, {("messages", "codex-0001"): {"body": "a", "n": 1},
                                       ("agreements", "codex-a"): {"status": "ready-to-adopt"}})
            res = pull(r.root, exp)
            self.assertIsInstance(res, PullResult)
            self.assertEqual(res.owner_lines, 0)
            self.assertEqual(res.approvals, [])
            self.assertEqual(owner_lines(r.root), [])

    def test_owner_created_doc(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"body": "a"}, t=T0)])
            exp = make_export(r.root, {("messages", "codex-0001"): {"body": "a"},
                                       ("messages", "own-x"): {"body": "from owner"}})
            res = pull(r.root, exp)
            self.assertEqual(res.owner_lines, 1)
            [ln] = owner_lines(r.root)
            self.assertEqual((ln.op, ln.collection, ln.id, ln.data, ln.via, ln.from_),
                             ("set", "messages", "own-x", {"body": "from owner"}, "page", "owner"))

    def test_owner_and_agent_same_doc(self):
        # Review Focus 2: the agent's unsent line and the owner's page edit both survive.
        with TempRepo() as r:
            r.write_roster()
            r.write_log("vectorcraft", [
                line(1, "vectorcraft", "agreements", "a-fast-verify",
                     data={"status": "proposed", "title": "Fast verify"}, t=T0),
                line(2, "vectorcraft", "agreements", "a-fast-verify", op="update",
                     data={"status": "building"}, t=T1),
            ])
            save_state(r.root, {PAGE: {"bookmarks": {"vectorcraft": 1}}})
            exp = make_export(r.root, {("agreements", "a-fast-verify"):
                                       {"status": "proposed", "title": "Fast verify",
                                        "hold": True}})
            res = pull(r.root, exp)
            self.assertEqual(res.owner_lines, 1)
            [ln] = owner_lines(r.root)
            self.assertEqual((ln.op, ln.data, ln.via), ("update", {"hold": True}, "page"))
            doc = replay(load_all_lines(r.root))[("agreements", "a-fast-verify")]
            self.assertEqual(doc, {"status": "building", "title": "Fast verify", "hold": True})

    def test_failed_write_through_not_owner(self):
        # Review Focus 3: the page holds the old value because the agent's write failed.
        with TempRepo() as r:
            r.write_roster()
            r.write_log("vectorcraft", [
                line(1, "vectorcraft", "locks", "l-vc-githooks-branch-reviews",
                     data={"holder": "x", "since": T0}, t=T0),
                line(2, "vectorcraft", "locks", "l-vc-githooks-branch-reviews", op="update",
                     data={"released": T1}, t=T1),
            ])
            save_state(r.root, {PAGE: {"bookmarks": {"vectorcraft": 1}}})
            exp = make_export(r.root, {("locks", "l-vc-githooks-branch-reviews"):
                                       {"holder": "x", "since": T0}})
            res = pull(r.root, exp)
            self.assertEqual(res.owner_lines, 0)
            self.assertEqual(owner_lines(r.root), [])
            doc = replay(load_all_lines(r.root))[("locks", "l-vc-githooks-branch-reviews")]
            self.assertEqual(doc["released"], T1)

    def test_owner_removed_field(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", "decisions", "codex-d",
                                       data={"title": "x", "hold": True}, t=T0)])
            save_state(r.root, {PAGE: {"bookmarks": {"codex": 1}}})
            exp = make_export(r.root, {("decisions", "codex-d"): {"title": "x"}})
            res = pull(r.root, exp)
            self.assertEqual(res.owner_lines, 1)
            [ln] = owner_lines(r.root)
            self.assertEqual((ln.op, ln.data), ("update", {"hold": {"__delete__": True}}))
            self.assertEqual(replay(load_all_lines(r.root))[("decisions", "codex-d")],
                             {"title": "x"})

    def test_doc_missing_from_page_no_lines(self):
        # R4: pull never infers deletions.
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"body": "a"}, t=T0)])
            save_state(r.root, {PAGE: {"bookmarks": {"codex": 1}}})
            exp = make_export(r.root, {})
            res = pull(r.root, exp)
            self.assertEqual(res.owner_lines, 0)
            self.assertEqual(owner_lines(r.root), [])

    def test_ignores_unknown_dirs(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"body": "a"}, t=T0)])
            exp = make_export(r.root, {("messages", "codex-0001"): {"body": "a"},
                                       ("scratch", "own-y"): {"z": 1}})
            self.assertEqual(pull(r.root, exp).owner_lines, 0)

    def test_versions_saved_under_page(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"body": "a"}, t=T0)])
            save_state(r.root, {PAGE: {"bookmarks": {"codex": 1},
                                       "versions": {"messages/old": 9},
                                       "participantsHash": "abc"},
                                "https://other": {"bookmarks": {"codex": 5}}})
            exp = make_export(
                r.root, {("messages", "codex-0001"): {"body": "a"}},
                {"messages": '- "codex-0001"  10 bytes  version 3  "t"\n',
                 "locks": '{"id":"l-1","data":{},"version":4,"updatedAt":"t"}\n',
                 "scratch": '- "s-1"  10 bytes  version 8  "t"\n'})
            res = pull(r.root, exp)
            self.assertEqual(res.versions, {"messages/codex-0001": 3, "locks/l-1": 4})
            state = load_state(r.root)
            self.assertEqual(state[PAGE], {"bookmarks": {"codex": 1},
                                           "versions": {"messages/codex-0001": 3,
                                                        "locks/l-1": 4},
                                           "participantsHash": "abc"})
            self.assertEqual(state["https://other"], {"bookmarks": {"codex": 5}})


class ApprovalTests(unittest.TestCase):
    def test_approval_updates_roster(self):
        with TempRepo() as r:
            r.write_roster(roster_with_gemini())
            r.write_log("codex", [line(1, "codex", data={"body": "a"}, t=T0)])
            exp = make_export(r.root, {("messages", "codex-0001"): {"body": "a"},
                                       ("participants", "gemini"):
                                       {"name": "Gemini", "kind": "researcher",
                                        "trust": "approved"}})
            res = pull(r.root, exp)
            self.assertEqual(res.approvals, ["gemini"])
            raw = json.loads((r.root / "participants.json").read_text(encoding="utf-8"))
            self.assertEqual(set(raw), {"page", "relay", "participants"})
            entry = [p for p in raw["participants"] if p["id"] == "gemini"][0]
            self.assertEqual(entry["trust"], "approved")
            others = [p for p in raw["participants"] if p["id"] != "gemini"]
            self.assertEqual(others, SPEC_ROSTER["participants"])

    def test_approval_writes_owner_line(self):
        with TempRepo() as r:
            r.write_roster(roster_with_gemini())
            r.write_log("codex", [line(1, "codex", data={"body": "a"}, t=T0)])
            exp = make_export(r.root, {("participants", "gemini"): {"trust": "approved"}})
            res = pull(r.root, exp)
            self.assertEqual(res.owner_lines, 1)
            [ln] = owner_lines(r.root)
            self.assertEqual((ln.op, ln.collection, ln.id, ln.data, ln.via),
                             ("update", "participants", "gemini", {"trust": "approved"}, "page"))

    def test_roster_rewritten_with_indent_and_unicode(self):
        with TempRepo() as r:
            data = roster_with_gemini()
            data["participants"][-1]["name"] = "Gémini"
            r.write_roster(data)
            r.write_log("codex", [line(1, "codex", data={"body": "a"}, t=T0)])
            exp = make_export(r.root, {("participants", "gemini"): {"trust": "approved"}})
            pull(r.root, exp)
            text = (r.root / "participants.json").read_text(encoding="utf-8")
            self.assertIn("Gémini", text)
            self.assertIn('\n  "participants": [\n', text)

    def test_unchanged_trust_does_nothing(self):
        with TempRepo() as r:
            r.write_roster(roster_with_gemini())
            r.write_log("codex", [line(1, "codex", data={"body": "a"}, t=T0)])
            before = (r.root / "participants.json").read_text(encoding="utf-8")
            exp = make_export(r.root, {("participants", "gemini"):
                                       {"trust": "quarantined", "name": "Renamed"},
                                       ("participants", "codex"): {"trust": "approved"}})
            res = pull(r.root, exp)
            self.assertEqual((res.owner_lines, res.approvals), (0, []))
            self.assertEqual((r.root / "participants.json").read_text(encoding="utf-8"), before)
            self.assertEqual(owner_lines(r.root), [])

    def test_unknown_participant_ignored_but_versioned(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"body": "a"}, t=T0)])
            before = (r.root / "participants.json").read_text(encoding="utf-8")
            exp = make_export(r.root, {("participants", "stranger"): {"trust": "approved"}},
                              {"participants": '- "stranger"  20 bytes  version 2  "t"\n'})
            res = pull(r.root, exp)
            self.assertEqual((res.owner_lines, res.approvals), (0, []))
            self.assertEqual(res.versions, {"participants/stranger": 2})
            self.assertEqual((r.root / "participants.json").read_text(encoding="utf-8"), before)
            self.assertEqual(owner_lines(r.root), [])


class StateTests(unittest.TestCase):
    def test_missing_state_is_empty(self):
        with TempRepo() as r:
            self.assertEqual(load_state(r.root), {})

    def test_state_round_trip(self):
        with TempRepo() as r:
            state = {PAGE: {"bookmarks": {"codex": 2}, "versions": {"messages/m-1": 1},
                            "participantsHash": "h"}}
            save_state(r.root, state)
            self.assertEqual(load_state(r.root), state)
            text = (r.root / "log" / "relay-state.json").read_text(encoding="utf-8")
            self.assertEqual(text, json.dumps(state, indent=2, sort_keys=True) + "\n")
            self.assertEqual([p.name for p in (r.root / "log").iterdir()], ["relay-state.json"])


if __name__ == "__main__":
    unittest.main()
