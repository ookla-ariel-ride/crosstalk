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
    land_sync,
    post_incident,
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


class LandSyncTests(unittest.TestCase):
    def test_cli_land_sync_creates_agreement_message_and_releases_locks(self):
        with TempRepo() as r:
            c = PosterCli(r)
            c.run("claim", "--from", "vectorcraft", "--path", "tools/gate-host")
            c.run("claim", "--from", "vectorcraft", "--path", "tools/other")
            c.run("claim", "--from", "bewareilive", "--path", "tools/gate-host")

            p = c.run("land-sync", "--from", "vectorcraft", "--commit", "a1b2c3d",
                      "--summary", "Gate host lanes in Scratch",
                      "--files", "tools/gate-host", "--adopter", "bewareilive",
                      "--body", "Details on gate-host")
            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)
            self.assertEqual(out["message_id"], "vectorcraft-sync-a1b2c3d")
            self.assertEqual(out["agreement_id"], "a-vectorcraft-a1b2c3d")
            self.assertEqual(out["released_locks"], ["l-vectorcraft-tools-gate-host"])

            docs = c.docs()
            msg = docs[("messages", "vectorcraft-sync-a1b2c3d")]
            self.assertEqual(msg["from"], "vectorcraft")
            self.assertEqual(msg["kind"], "sync")
            self.assertEqual(msg["summary"], "Gate host lanes in Scratch")
            self.assertEqual(msg["body"], "Details on gate-host")
            self.assertEqual(msg["refs"], ["vectorcraft@a1b2c3d"])

            agr = docs[("agreements", "a-vectorcraft-a1b2c3d")]
            self.assertEqual(agr["status"], "ready-to-adopt")
            self.assertEqual(agr["builder"], "vectorcraft")
            self.assertEqual(agr["adopter"], "bewareilive")
            self.assertEqual(agr["summary"], "Gate host lanes in Scratch")
            self.assertEqual(agr["commits"], {"vectorcraft": ["a1b2c3d"]})
            self.assertIs(agr["hold"], False)
            self.assertTrue(agr["t"].endswith("Z"))
            self.assertTrue(agr["updated"].endswith("Z"))

            self.assertIsNotNone(docs[("locks", "l-vectorcraft-tools-gate-host")]["released"])
            self.assertIsNone(docs[("locks", "l-vectorcraft-tools-other")]["released"])
            self.assertIsNone(docs[("locks", "l-bewareilive-tools-gate-host")]["released"])
            self.assertEqual(c.run("check").returncode, 0)

    def test_land_sync_updates_existing_agreement_preserving_commits(self):
        with TempRepo() as r:
            c = PosterCli(r)
            c.run("append", "--from", "bewareilive", "--op", "set",
                  "--collection", "agreements", "--id", "a-shared-gate",
                  "--data", json.dumps({
                      "t": "2026-10-20T10:00:00Z",
                      "summary": "Initial proposal",
                      "status": "building",
                      "builder": "vectorcraft",
                      "adopter": "all",
                      "commits": {"bewareilive": ["0000abc"], "vectorcraft": ["1111def"]},
                      "hold": True,
                  }))
            roster = load_roster(r.root / "participants.json")
            res = land_sync(r.root, "vectorcraft", "2222fed", "Updated gate host",
                            agreement_id="a-shared-gate", roster=roster)
            self.assertEqual(res["agreement_id"], "a-shared-gate")
            # Re-running with same commit does not duplicate it in commits[pid]
            land_sync(r.root, "vectorcraft", "2222fed", "Updated gate host",
                      agreement_id="a-shared-gate", roster=roster)

            docs = c.docs()
            agr = docs[("agreements", "a-shared-gate")]
            self.assertEqual(agr["t"], "2026-10-20T10:00:00Z")
            self.assertEqual(agr["status"], "ready-to-adopt")
            self.assertEqual(agr["adopter"], "all")
            self.assertIs(agr["hold"], False)
            self.assertEqual(agr["commits"], {
                "bewareilive": ["0000abc"],
                "vectorcraft": ["1111def", "2222fed"],
            })

    def test_land_sync_validates_commit_and_summary(self):
        with TempRepo() as r:
            c = PosterCli(r)
            for bad_sha in ("not-hex", "123456", "g1b2c3d"):
                p = c.run("land-sync", "--from", "vectorcraft", "--commit", bad_sha,
                          "--summary", "Valid summary")
                self.assertEqual(p.returncode, 1, bad_sha)
            for bad_sum in ("", "   ", "line1\nline2"):
                p = c.run("land-sync", "--from", "vectorcraft", "--commit", "a1b2c3d",
                          "--summary", bad_sum)
                self.assertEqual(p.returncode, 1, bad_sum)


class IncidentTests(unittest.TestCase):
    def test_cli_incident_open_and_resolve(self):
        with TempRepo() as r:
            c = PosterCli(r)
            p = c.run("incident", "--from", "vectorcraft", "--slug", "studio-disk",
                      "--summary", "Studio disk 100% full", "--body", "Scratch grew to 80GB")
            self.assertEqual(p.returncode, 0, p.stderr)
            inc_id = p.stdout.strip()
            self.assertEqual(inc_id, "i-vectorcraft-studio-disk")

            docs = c.docs()
            inc = docs[("incidents", inc_id)]
            self.assertEqual(inc["project"], "vectorcraft")
            self.assertEqual(inc["summary"], "Studio disk 100% full")
            self.assertEqual(inc["body"], "Scratch grew to 80GB")
            self.assertTrue(inc["start"].endswith("Z"))
            self.assertIsNone(inc["end"])

            p2 = c.run("incident", "--from", "vectorcraft", "--slug", "studio-disk",
                       "--resolve", "--fix", "Excluded ~/Scratch from Time Machine",
                       "--fix-ref", "vectorcraft@352b6cd")
            self.assertEqual(p2.returncode, 0, p2.stderr)
            self.assertEqual(p2.stdout.strip(), inc_id)

            docs2 = c.docs()
            inc2 = docs2[("incidents", inc_id)]
            self.assertEqual(inc2["summary"], "Studio disk 100% full")
            self.assertIsNotNone(inc2["end"])
            self.assertTrue(inc2["end"].endswith("Z"))
            self.assertEqual(inc2["fix"], "Excluded ~/Scratch from Time Machine")
            self.assertEqual(inc2["fixRefs"], ["vectorcraft@352b6cd"])
            self.assertEqual(c.run("check").returncode, 0)

    def test_incident_validation_errors(self):
        with TempRepo() as r:
            c = PosterCli(r)
            # Opening without summary fails
            p1 = c.run("incident", "--from", "vectorcraft", "--slug", "studio-disk")
            self.assertEqual(p1.returncode, 1)
            self.assertIn("summary", p1.stderr)

            # Resolving non-existent incident fails
            p2 = c.run("incident", "--from", "vectorcraft", "--slug", "studio-disk", "--resolve")
            self.assertEqual(p2.returncode, 1)
            self.assertIn("no such incident", p2.stderr)

            # Direct function call checks
            roster = load_roster(r.root / "participants.json")
            with self.assertRaises(ValueError):
                post_incident(r.root, "vectorcraft", "studio-disk", summary="  ", roster=roster)
            with self.assertRaises(ValueError):
                post_incident(r.root, "vectorcraft", "studio-disk", resolve=True, roster=roster)

    def test_same_second_and_clock_skew_monotonic_updates(self):
        from unittest.mock import patch
        from crosstalk.consults import adopt_agreement

        with TempRepo() as r:
            r.write_roster()
            roster = load_roster(r.root / "participants.json")
            fixed_ts = "2026-10-25T12:00:00Z"
            with patch("crosstalk.posters.now_iso", return_value=fixed_ts), \
                 patch("crosstalk.consults.now_iso", return_value=fixed_ts):
                land_sync(r.root, "vectorcraft", "1111111", "VC initial sync", roster=roster)
                land_sync(r.root, "bewareilive", "2222222", "BIL follow-up sync",
                          agreement_id="a-vectorcraft-1111111", roster=roster)
                docs = replay(load_all_lines(r.root, roster))
                agr = docs[("agreements", "a-vectorcraft-1111111")]
                self.assertEqual(agr["summary"], "BIL follow-up sync")
                self.assertIn("bewareilive", agr["commits"])

                land_sync(r.root, "vectorcraft", "aaaa111", "VC v1", roster=roster)
                adopt_agreement(r.root, "bewareilive", "a-vectorcraft-aaaa111", "bbbb222", roster=roster)
                land_sync(r.root, "vectorcraft", "cccc333", "VC v2 with second commit",
                          agreement_id="a-vectorcraft-aaaa111", roster=roster)
                docs2 = replay(load_all_lines(r.root, roster))
                agr2 = docs2[("agreements", "a-vectorcraft-aaaa111")]
                self.assertEqual(agr2["status"], "ready-to-adopt")
                self.assertEqual(agr2["commits"]["vectorcraft"], ["aaaa111", "cccc333"])

            with patch("crosstalk.posters.now_iso", return_value="2026-10-25T12:00:10Z"):
                inc_ln = post_incident(r.root, "vectorcraft", "ci-hung", summary="CI hung", roster=roster)
            with patch("crosstalk.posters.now_iso", return_value="2026-10-25T12:00:00Z"):
                post_incident(r.root, "vectorcraft", "ci-hung", resolve=True, fix="Rebooted", roster=roster)
            docs3 = replay(load_all_lines(r.root, roster))
            self.assertIsNotNone(docs3[("incidents", inc_ln.id)]["end"])
            self.assertEqual(docs3[("incidents", inc_ln.id)].get("fix"), "Rebooted")


if __name__ == "__main__":
    unittest.main()
