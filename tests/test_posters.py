"""Machine posters: claim, release, land-sync, incident."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo

from crosstalk.participants import load_roster
from crosstalk.posters import (
    claim_file,
    release_file,
    slugify_path,
)
from crosstalk.replay import load_all_lines, replay

REPO = Path(__file__).resolve().parent.parent


class PosterCli:
    def __init__(self, r):
        self.root = r.root
        r.write_roster()
        shutil.copytree(str(REPO / "scripts"), str(r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(r.root / "scripts" / "relay.py")

    def run(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def docs(self):
        roster = load_roster(self.root / "participants.json")
        lines = load_all_lines(self.root, roster)
        return replay(lines)


class SlugifyTests(unittest.TestCase):
    def test_slugify_basic_paths(self):
        self.assertEqual(slugify_path(".githooks/pre-commit"), "githooks-pre-commit")
        self.assertEqual(slugify_path("tools/gate-host"), "tools-gate-host")
        self.assertEqual(slugify_path("//Src/Foo__Bar.rs//"), "src-foo-bar-rs")

    def test_slugify_max_48_chars(self):
        long_path = "a/b/" + ("very-long-segment-" * 5) + "end.rs"
        slug = slugify_path(long_path)
        self.assertLessEqual(len(slug), 48)
        self.assertFalse(slug.startswith("-"))
        self.assertFalse(slug.endswith("-"))

    def test_slugify_empty_raises(self):
        for bad in ("", "...", "///", "---"):
            with self.assertRaises(ValueError):
                slugify_path(bad)


class ClaimReleaseTests(unittest.TestCase):
    def test_cli_claim_and_release_by_path(self):
        with TempRepo() as r:
            c = PosterCli(r)
            p = c.run("claim", "--from", "bewareilive", "--path", ".githooks/pre-commit",
                      "--repo", "ookla-ariel-ride/bewareilive", "--holder", "sync-agent",
                      "--note", "updating hook")
            self.assertEqual(p.returncode, 0, p.stderr)
            lock_id = p.stdout.strip()
            self.assertEqual(lock_id, "l-bewareilive-githooks-pre-commit")

            docs = c.docs()
            doc = docs[("locks", lock_id)]
            self.assertEqual(doc["path"], ".githooks/pre-commit")
            self.assertEqual(doc["project"], "bewareilive")
            self.assertEqual(doc["repo"], "ookla-ariel-ride/bewareilive")
            self.assertEqual(doc["holder"], "sync-agent")
            self.assertEqual(doc["note"], "updating hook")
            self.assertTrue(doc["since"].endswith("Z"))
            self.assertIsNone(doc["released"])

            p2 = c.run("release", "--from", "bewareilive", "--path", ".githooks/pre-commit")
            self.assertEqual(p2.returncode, 0, p2.stderr)
            self.assertEqual(p2.stdout.strip(), lock_id)

            docs2 = c.docs()
            doc2 = docs2[("locks", lock_id)]
            self.assertEqual(doc2["path"], ".githooks/pre-commit")
            self.assertIsNotNone(doc2["released"])
            self.assertTrue(doc2["released"].endswith("Z"))
            self.assertEqual(c.run("check").returncode, 0)

    def test_claim_defaults_repo_from_participant_and_parent(self):
        with TempRepo() as r:
            roster_data = json.loads(json.dumps(SPEC_ROSTER))
            roster_data["participants"].append({
                "id": "bewareilive.sync",
                "name": "BIL Sync",
                "kind": "agent",
                "parent": "bewareilive",
                "color": "#2e7d32",
                "colorDark": "#81c784",
                "trust": "approved",
            })
            r.write_roster(roster_data)
            roster = load_roster(r.root / "participants.json")

            ln1 = claim_file(r.root, "bewareilive", "tools/gate-host", roster=roster)
            ln2 = claim_file(r.root, "bewareilive.sync", "mame/harness.lua", roster=roster)
            docs = replay(load_all_lines(r.root, roster))
            self.assertEqual(docs[("locks", ln1.id)]["repo"], "ookla-ariel-ride/bewareilive")
            self.assertEqual(docs[("locks", ln2.id)]["repo"], "ookla-ariel-ride/bewareilive")

    def test_release_by_id_and_missing_lock_error(self):
        with TempRepo() as r:
            c = PosterCli(r)
            p = c.run("release", "--from", "bewareilive", "--path", ".githooks/pre-commit")
            self.assertEqual(p.returncode, 1)
            self.assertIn("no such lock", p.stderr)

            c.run("claim", "--from", "bewareilive", "--path", ".githooks/pre-commit")
            p2 = c.run("release", "--from", "bewareilive", "--id", "l-bewareilive-githooks-pre-commit")
            self.assertEqual(p2.returncode, 0, p2.stderr)
            self.assertIsNotNone(c.docs()[("locks", "l-bewareilive-githooks-pre-commit")]["released"])

    def test_claim_unknown_participant_fails(self):
        with TempRepo() as r:
            c = PosterCli(r)
            p = c.run("claim", "--from", "ghost", "--path", "tools/gate-host")
            self.assertEqual(p.returncode, 1)
            self.assertIn("ghost", p.stderr)


if __name__ == "__main__":
    unittest.main()
