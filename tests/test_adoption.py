import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as P

from helpers import TempRepo, line

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from crosstalk import config  # noqa: E402
from crosstalk.adoption import STATES, adoption_id, gaps, projects, standards, summary  # noqa: E402
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


class AdoptionSetCliTests(unittest.TestCase):
    """relay.py adoption set: one adoptions row per item, with the state it was given."""

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.addCleanup(self.r.__exit__, None, None, None)
        self.r.write_roster()
        self.r.write_log("alpha", [
            line(1, "alpha", collection="agreements", id_="a-sync", data={
                "summary": "Sync", "builder": "alpha", "adopter": "all", "status": "ready-to-adopt"}),
            line(2, "alpha", collection="agreements", id_="a-lint", data={
                "summary": "Lint", "builder": "alpha", "adopter": "all", "status": "ready-to-adopt"})])

    def cli(self, *args):
        return subprocess.run([sys.executable, str(REPO / "scripts" / "relay.py"),
                               "--data-root", str(self.r.root)] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def rows(self, pid="beta"):
        path = self.r.root / "log" / (pid + ".jsonl")
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_each_state_is_recorded_as_given(self):
        for state in STATES:
            p = self.cli("adoption", "set", "--from", "beta", "--item", "a-sync", "--state", state)
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "1"), p.stderr)
            row = self.rows()[-1]
            self.assertEqual((row["collection"], row["id"], row["from"]),
                             ("adoptions", "beta-adopt-a-sync", "beta"))
            self.assertEqual(row["data"]["state"], state)
            self.assertEqual((row["data"]["from"], row["data"]["item"]), ("beta", "a-sync"))
            counts = json.loads(self.cli("adoption", "summary").stdout)["beta"]
            self.assertEqual(counts[state], 1, state)
        self.assertEqual(self.cli("check").returncode, 0)

    def test_two_items_ref_and_note_close_the_gaps(self):
        gaps_before = json.loads(self.cli("adoption", "gaps", "--from", "beta").stdout)
        self.assertEqual(sorted(g["item"] for g in gaps_before), ["a-lint", "a-sync"])
        p = self.cli("adoption", "set", "--from", "beta", "--item", "a-sync", "a-lint",
                     "--state", "planned", "--ref", "abc1234", "--note", "next week")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "2"), p.stderr)
        data = [r["data"] for r in self.rows()]
        self.assertEqual([d["item"] for d in data], ["a-sync", "a-lint"])
        for d in data:
            self.assertEqual((d["state"], d["ref"], d["note"]), ("planned", "abc1234", "next week"))
            self.assertRegex(d["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(json.loads(self.cli("adoption", "gaps", "--from", "beta").stdout), [])

    def test_an_unknown_item_writes_nothing(self):
        p = self.cli("adoption", "set", "--from", "beta", "--item", "a-sync", "a-none",
                     "--state", "adopted")
        self.assertEqual(p.returncode, 1)
        self.assertIn("a-none", p.stderr)
        self.assertEqual(self.rows(), [])

    def test_an_unknown_writer_or_state_writes_nothing(self):
        p = self.cli("adoption", "set", "--from", "nobody", "--item", "a-sync", "--state", "adopted")
        self.assertEqual(p.returncode, 1)
        self.assertIn("unknown participant", p.stderr)
        p = self.cli("adoption", "set", "--from", "beta", "--item", "a-sync", "--state", "none")
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.rows(), [])
        self.assertFalse((self.r.root / "log" / "nobody.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
