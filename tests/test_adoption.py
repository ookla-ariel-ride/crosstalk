import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as P

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from crosstalk import config  # noqa: E402
from crosstalk.adoption import adoption_id, gaps, projects, standards, summary  # noqa: E402
from crosstalk.logs import adoption_problems  # noqa: E402

ROSTER = SimpleRoster = P(relay="relay-bot", config=dict(config.DEFAULTS, rulingsSince="2026-11-05"), participants={
    "alpha": P(kind="project", trust="approved"), "beta": P(kind="project", trust="approved"),
    "relay-bot": P(kind="project", trust="approved"), "new": P(kind="project", trust="quarantined"),
    "codex": P(kind="reviewer", trust="approved")})
DOCS = {
    ("agreements", "a-1"): {"status": "ready-to-adopt", "adopter": "all", "builder": "alpha", "summary": "one"},
    ("agreements", "a-2"): {"status": "landed-both", "adopter": "all", "builder": "alpha"},
    ("agreements", "a-3"): {"status": "ready-to-adopt", "adopter": "all", "builder": "alpha", "hold": True},
    ("decisions", "d-new"): {"project": "all", "t": "2026-11-08T00:00:00Z", "summary": "rule"},
    ("decisions", "d-old"): {"project": "all", "t": "2026-10-01T00:00:00Z"},
    ("adoptions", "beta-adopt-a-1"): {"from": "beta", "item": "a-1", "state": "adopted"},
}


class AdoptionTest(unittest.TestCase):
    def test_only_approved_projects_but_not_the_coordinator(self):
        self.assertEqual(projects(ROSTER), ["alpha", "beta"])

    def test_standards_skip_closed_held_and_old_items_and_the_builder(self):
        std = standards(DOCS, ROSTER)
        self.assertEqual(sorted(std), ["a-1", "d-new"])
        self.assertEqual(std["a-1"]["needs"], ["beta"])

    def test_a_record_closes_a_gap_only_for_its_writer(self):
        self.assertEqual([g["item"] for g in gaps(DOCS, ROSTER, "beta")], ["d-new"])
        self.assertEqual([g["item"] for g in gaps(DOCS, ROSTER, "alpha")], ["d-new"])

    def test_a_commit_on_the_agreement_counts_as_adopted(self):
        docs = dict(DOCS)
        docs[("agreements", "a-c")] = {"status": "ready-to-adopt", "adopter": "all", "builder": "alpha", "commits": {"alpha": ["aaa"], "beta": ["bbb"]}}
        self.assertNotIn("a-c", [g["item"] for g in gaps(docs, ROSTER, "beta")])

    def test_summary_counts(self):
        self.assertEqual(summary(DOCS, ROSTER)["beta"]["adopted"], 1)
        self.assertEqual(summary(DOCS, ROSTER)["beta"]["open"], 1)

    def test_writer_owns_the_id_and_the_state_is_checked(self):
        self.assertEqual(adoption_problems("adoptions", adoption_id("beta", "a-1"), "beta", {"state": "adopted"}), [])
        self.assertTrue(adoption_problems("adoptions", "beta-adopt-a-1", "alpha", {"state": "adopted"}))
        self.assertTrue(adoption_problems("adoptions", "beta-adopt-a-1", "beta", {"state": "maybe"}))


if __name__ == "__main__":
    unittest.main()
