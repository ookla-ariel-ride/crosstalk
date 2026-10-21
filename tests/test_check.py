import json
import unittest

from helpers import SPEC_ROSTER, TempRepo, line
from crosstalk.participants import load_roster
from crosstalk.logs import check_all, read_log


class CheckTests(unittest.TestCase):
    def check(self, r):
        return check_all(r.root, load_roster(r.write_roster()))

    def test_clean_log_has_no_problems(self):
        with TempRepo() as r:
            r.write_log("codex", [line(1, "codex"), line(2, "codex")])
            self.assertEqual(self.check(r), [])

    def test_malformed_json(self):
        with TempRepo() as r:
            r.write_log("codex", [line(1, "codex"), "{not json"])
            (p,) = self.check(r)
            self.assertEqual((p.file, p.line, p.fatal), ("log/codex.jsonl", 2, True))

    def test_seq_gap(self):
        with TempRepo() as r:
            r.write_log("codex", [line(1, "codex"), line(3, "codex")])
            (p,) = self.check(r)
            self.assertEqual((p.file, p.line, p.fatal), ("log/codex.jsonl", 2, True))
            self.assertIn("seq", p.message)

    def test_from_mismatch(self):
        with TempRepo() as r:
            r.write_log("owner", [line(1, "codex")])
            (p,) = self.check(r)
            self.assertEqual((p.file, p.line, p.fatal), ("log/owner.jsonl", 1, True))

    def test_new_id_without_prefix(self):
        with TempRepo() as r:
            r.write_log("codex", [line(1, "codex", id_="x-1")])
            (p,) = self.check(r)
            self.assertEqual((p.file, p.line, p.fatal), ("log/codex.jsonl", 1, True))

    def test_update_may_name_any_id(self):
        with TempRepo() as r:
            r.write_log("owner", [line(1, "owner", collection="agreements", id_="a-fast-verify",
                                        op="update", data={"status": "hold"})])
            self.assertEqual(self.check(r), [])

    def test_legacy_ids_allowed(self):
        with TempRepo() as r:
            r.write_log("bewareilive", [line(1, "bewareilive", id_="m-0001")])
            self.assertEqual(self.check(r), [])

    def test_unknown_participant_warns(self):
        with TempRepo() as r:
            r.write_log("gemini", [line(1, "gemini")])
            (p,) = self.check(r)
            self.assertEqual((p.file, p.fatal), ("log/gemini.jsonl", False))

    def test_unknown_collection_fatal(self):
        with TempRepo() as r:
            r.write_log("codex", [line(1, "codex", collection="bogus")])
            (p,) = self.check(r)
            self.assertTrue(p.fatal)

    def test_participants_collection_owner_only(self):
        with TempRepo() as r:
            r.write_log("owner", [line(1, "owner", collection="participants", id_="owner-x")])
            r.write_log("codex", [line(1, "codex", collection="participants")])
            ps = self.check(r)
            self.assertEqual([(p.file, p.fatal) for p in ps], [("log/codex.jsonl", True)])

    def test_lines_before_bad_line_returned(self):
        with TempRepo() as r:
            path = r.write_log("codex", [line(1, "codex"), "oops", line(3, "codex")])
            lines, problems = read_log(path)
            self.assertEqual([l.seq for l in lines], [1])
            self.assertEqual(len(problems), 1)
            self.assertTrue(problems[0].fatal)

    def test_line_to_json_roundtrip(self):
        with TempRepo() as r:
            path = r.write_log("owner", [line(1, "owner", data={"body": "é\n\"q\" 😀"})])
            (l,), _ = read_log(path)
            self.assertNotIn("\n", l.to_json())
            self.assertNotIn("via", l.to_json())



def roster_with_gemini():
    data = json.loads(json.dumps(SPEC_ROSTER))
    data["participants"].append(
        {"id": "gemini", "name": "Gemini", "kind": "researcher", "trust": "quarantined"})
    return data


class TrustTests(unittest.TestCase):
    """Ruling R9: owning prefix and message sender, enforced where the roster is known."""

    def check(self, r):
        return check_all(r.root, load_roster(r.write_roster(roster_with_gemini())))

    def test_quarantined_legacy_id_fatal(self):
        with TempRepo() as r:
            r.write_log("gemini", [line(1, "gemini", id_="m-0001")])
            (p,) = self.check(r)
            self.assertEqual((p.file, p.line, p.fatal), ("log/gemini.jsonl", 1, True))
            self.assertIn("gemini-", p.message)

    def test_quarantined_update_of_other_doc_fatal(self):
        with TempRepo() as r:
            r.write_log("gemini", [line(1, "gemini", id_="codex-0001", op="update",
                                        data={"body": "x"})])
            (p,) = self.check(r)
            self.assertEqual((p.line, p.fatal), (1, True))

    def test_quarantined_own_id_fine(self):
        with TempRepo() as r:
            r.write_log("gemini", [line(1, "gemini", id_="gemini-1", data={"from": "gemini"}),
                                   line(2, "gemini", id_="gemini-1", op="update",
                                        data={"body": "y"})])
            self.assertEqual(self.check(r), [])

    def test_message_from_must_be_writer(self):
        with TempRepo() as r:
            r.write_log("gemini", [line(1, "gemini", id_="gemini-1", data={"from": "owner"})])
            (p,) = self.check(r)
            self.assertEqual((p.file, p.line, p.fatal), ("log/gemini.jsonl", 1, True))
            self.assertIn("from", p.message)

    def test_message_from_checked_for_approved_too(self):
        with TempRepo() as r:
            r.write_log("codex", [line(1, "codex", id_="codex-1", op="update",
                                       data={"from": "owner"})])
            (p,) = self.check(r)
            self.assertTrue(p.fatal)

    def test_message_from_only_on_messages(self):
        with TempRepo() as r:
            r.write_log("gemini", [line(1, "gemini", collection="lessons", id_="gemini-1",
                                        data={"from": "owner"})])
            self.assertEqual(self.check(r), [])

    def test_approved_may_set_others_ids(self):
        with TempRepo() as r:
            r.write_log("bewareilive", [line(1, "bewareilive", collection="agreements",
                                             id_="a-x"),
                                        line(2, "bewareilive", id_="codex-0001", op="update",
                                             data={"seen": True})])
            self.assertEqual(self.check(r), [])

    def test_unknown_writer_touching_other_doc_fatal(self):
        with TempRepo() as r:
            r.write_log("stranger", [line(1, "stranger", id_="codex-1", op="update",
                                          data={"body": "x"})])
            ps = self.check(r)
            self.assertEqual([(p.line, p.fatal) for p in ps], [(0, False), (1, True)])

    def test_imported_mismatched_from_fine(self):
        with TempRepo() as r:
            d = line(1, "bewareilive", id_="m-0001", data={"from": "vectorcraft"})
            d["via"] = "import"
            r.write_log("bewareilive", [d])
            self.assertEqual(self.check(r), [])

    def test_read_log_without_roster_skips_trust(self):
        with TempRepo() as r:
            path = r.write_log("gemini", [line(1, "gemini", id_="m-0001")])
            lines, problems = read_log(path)
            self.assertEqual((len(lines), problems), (1, []))
            roster = load_roster(r.write_roster(roster_with_gemini()))
            lines, problems = read_log(path, roster)
            self.assertEqual((len(lines), len(problems)), (0, 1))

if __name__ == "__main__":
    unittest.main()
