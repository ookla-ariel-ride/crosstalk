import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TempRepo, line

from crosstalk.importer import import_snapshots, merge_snapshots
from crosstalk.logs import check_all, read_log
from crosstalk.participants import load_roster
from crosstalk.replay import load_all_lines, replay

REPO = Path(__file__).resolve().parent.parent
SNAP_A = REPO / "import" / "2uM4ZVpmoYLQYkyXVgvckH-2026-10-21"
SNAP_B = REPO / "import" / "ThPWGePNJjieLDTUqJbfmc-2026-10-21"


def make_snap(root, name, rows):
    """rows: {(collection, id): fields}"""
    d = Path(root) / name
    for (coll, did), fields in rows.items():
        (d / coll).mkdir(parents=True, exist_ok=True)
        (d / coll / (did + ".json")).write_text(json.dumps(fields), encoding="utf-8")
    return d


def writer_of(root, coll, did):
    for p in (Path(root) / "log").glob("*.jsonl"):
        for ln in read_log(p)[0]:
            if (ln.collection, ln.id) == (coll, did):
                return ln.from_, ln
    return None, None


class ImportTests(unittest.TestCase):
    def test_prefix_mapping(self):
        with TempRepo() as r:
            r.write_roster()
            snap = make_snap(r.root, "s", {
                ("messages", "m-0001"): {"t": "2026-01-01T00:00:00Z"},
                ("messages", "vc-x"): {"t": "2026-01-01T00:00:01Z"},
                ("messages", "own-x"): {"t": "2026-01-01T00:00:02Z"},
                ("agreements", "a-y"): {"builder": "vectorcraft", "t": "2026-01-01T00:00:03Z"},
                ("lessons", "lesson-z"): {"project": "both", "t": "2026-01-01T00:00:04Z"},
                ("decisions", "d-q"): {"project": "both", "by": "owner", "builder": "vectorcraft"},
            })
            counts = import_snapshots(r.root, [snap])
            self.assertEqual(writer_of(r.root, "messages", "m-0001")[0], "bewareilive")
            self.assertEqual(writer_of(r.root, "messages", "vc-x")[0], "vectorcraft")
            self.assertEqual(writer_of(r.root, "messages", "own-x")[0], "owner")
            self.assertEqual(writer_of(r.root, "agreements", "a-y")[0], "vectorcraft")
            self.assertEqual(writer_of(r.root, "lessons", "lesson-z")[0], "bewareilive")
            self.assertEqual(writer_of(r.root, "decisions", "d-q")[0], "vectorcraft")
            self.assertEqual(counts, {"bewareilive": 2, "vectorcraft": 3, "owner": 1})
            _, ln = writer_of(r.root, "messages", "m-0001")
            self.assertEqual((ln.op, ln.via), ("set", "import"))

    def test_participant_prefix_names_writer(self):
        # A row written under the new id rule (`<id>-…`) belongs to that participant,
        # whatever its project field says.
        with TempRepo() as r:
            r.write_roster()
            snap = make_snap(r.root, "s", {
                ("messages", "codex-20261022T132907"): {"t": "2026-01-01T00:00:00Z",
                                                        "project": "vectorcraft"},
                ("waiting", "vectorcraft-w1"): {"since": "2026-01-01T00:00:01Z"},
            })
            import_snapshots(r.root, [snap])
            self.assertEqual(writer_of(r.root, "messages", "codex-20261022T132907")[0], "codex")
            self.assertEqual(writer_of(r.root, "waiting", "vectorcraft-w1")[0], "vectorcraft")
            self.assertEqual(check_all(r.root, load_roster(r.root / "participants.json")), [])

    def test_every_imported_line_is_via_import_and_checks_clean(self):
        with TempRepo() as r:
            r.write_roster()
            snap = make_snap(r.root, "s", {
                ("messages", "m-0001"): {"t": "2026-01-01T00:00:00Z", "from": "vectorcraft"},
                ("messages", "vc-1"): {"t": "2026-01-01T00:00:01Z", "from": "bewareilive"},
                ("agreements", "a-y"): {"builder": "vectorcraft"},
            })
            import_snapshots(r.root, [snap])
            lines = load_all_lines(r.root)
            self.assertEqual(len(lines), 3)
            self.assertEqual({ln.via for ln in lines}, {"import"})
            roster = load_roster(r.root / "participants.json")
            self.assertEqual(check_all(r.root, roster), [])

    def test_time_fields(self):
        with TempRepo() as r:
            r.write_roster()
            snap = make_snap(r.root, "s", {
                ("waiting", "w-a"): {"since": "2026-02-02T00:00:00Z", "project": "vectorcraft"},
                ("incidents", "i-a"): {"start": "2026-03-03T00:00:00Z"},
                ("waiting", "w-b"): {"t": 5, "since": "2026-04-04T00:00:00Z"},
                ("waiting", "w-c"): {},
            })
            import_snapshots(r.root, [snap])
            self.assertEqual(writer_of(r.root, "waiting", "w-a")[1].t, "2026-02-02T00:00:00Z")
            self.assertEqual(writer_of(r.root, "incidents", "i-a")[1].t, "2026-03-03T00:00:00Z")
            self.assertEqual(writer_of(r.root, "waiting", "w-b")[1].t, "2026-04-04T00:00:00Z")
            self.assertEqual(writer_of(r.root, "waiting", "w-c")[1].t, "1970-01-01T00:00:00Z")

    def test_order_is_time_then_writer_then_collection_then_id(self):
        with TempRepo() as r:
            r.write_roster()
            t = "2026-01-01T00:00:00Z"
            snap = make_snap(r.root, "s", {
                ("messages", "m-2"): {"t": t}, ("messages", "m-1"): {"t": t},
                ("decisions", "d-1"): {"t": t}, ("messages", "m-0"): {"t": "2025-01-01T00:00:00Z"},
            })
            import_snapshots(r.root, [snap])
            lines, _ = read_log(r.root / "log" / "bewareilive.jsonl")
            self.assertEqual([(l.collection, l.id) for l in lines],
                             [("messages", "m-0"), ("decisions", "d-1"),
                              ("messages", "m-1"), ("messages", "m-2")])

    def test_skips_non_json_and_passes_fields_through(self):
        with TempRepo() as r:
            r.write_roster()
            snap = make_snap(r.root, "s", {("messages", "m-1"): {"t": "2026-01-01T00:00:00Z", "body": "é\n\"x\""}})
            (snap / "messages" / "x.listing.txt").write_text("junk")
            import_snapshots(r.root, [snap])
            docs = replay(load_all_lines(r.root))
            self.assertEqual(list(docs), [("messages", "m-1")])
            self.assertEqual(docs[("messages", "m-1")]["body"], "é\n\"x\"")

    def test_refuses_when_log_not_empty(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("owner", [{"seq": 1, "t": "2026-01-01T00:00:00Z", "from": "owner", "op": "set",
                                   "collection": "messages", "id": "owner-1", "data": {}}])
            snap = make_snap(r.root, "s", {("messages", "m-1"): {}})
            with self.assertRaises(RuntimeError):
                import_snapshots(r.root, [snap])
            self.assertFalse((r.root / "log" / "bewareilive.jsonl").exists())

    def test_duplicate_across_snapshots_raises(self):
        with TempRepo() as r:
            r.write_roster()
            a = make_snap(r.root, "a", {("messages", "m-1"): {}})
            b = make_snap(r.root, "b", {("messages", "m-1"): {}})
            with self.assertRaises(RuntimeError):
                import_snapshots(r.root, [a, b])
            self.assertFalse((r.root / "log" / "bewareilive.jsonl").exists())

    def test_symlinked_files_are_skipped(self):
        with TempRepo() as r:
            r.write_roster()
            target = r.root / "secret.json"
            target.write_text(json.dumps({"kind": "reply", "summary": "secret"}),
                              encoding="utf-8")
            snap = r.root / "s"
            (snap / "messages").mkdir(parents=True)
            (snap / "messages" / "m-1.json").write_text(
                json.dumps({"t": "2026-01-01T00:00:00Z"}), encoding="utf-8")
            (snap / "messages" / "evil.json").symlink_to(target)
            import_snapshots(r.root, [snap])
            docs = replay(load_all_lines(r.root))
            self.assertEqual(list(docs), [("messages", "m-1")])
            self.assertEqual(target.read_text(encoding="utf-8"),
                             json.dumps({"kind": "reply", "summary": "secret"}))

    def test_symlinked_dirs_are_skipped(self):
        with TempRepo() as r:
            r.write_roster()
            outside = r.root / "outside"
            outside.mkdir()
            (outside / "m-1.json").write_text(
                json.dumps({"t": "2026-01-01T00:00:00Z"}), encoding="utf-8")
            snap = r.root / "s"
            snap.mkdir()
            (snap / "messages").symlink_to(outside, target_is_directory=True)
            import_snapshots(r.root, [snap])
            docs = replay(load_all_lines(r.root))
            self.assertEqual(docs, {})

    def test_real_snapshots_import_cleanly(self):
        with TempRepo() as r:
            shutil.copy(str(REPO / "participants.json"), str(r.root / "participants.json"))
            import_snapshots(r.root, [SNAP_A, SNAP_B])
            roster = load_roster(r.root / "participants.json")
            self.assertEqual(check_all(r.root, roster), [])
            self.assertEqual({ln.via for ln in load_all_lines(r.root, roster)}, {"import"})
            docs = replay(load_all_lines(r.root))
            self.assertEqual(len(docs), 215)
            self.assertEqual(docs[("agreements", "a-review-broker-cleanup")]["status"], "ready-to-adopt")


class MergeTests(unittest.TestCase):
    """import --merge: the old page's snapshots folded into a live log."""

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def test_merge_appends_rows_the_log_does_not_have(self):
        r = self.r
        r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "live"})])
        snap = make_snap(r.root, "s", {
            ("messages", "m-0001"): {"t": "2026-01-01T00:00:00Z", "summary": "old"},
        })
        res = merge_snapshots(r.root, [snap])
        self.assertEqual(res["skipped"], [])
        docs = replay(load_all_lines(r.root))
        self.assertIn(("messages", "m-0001"), docs)
        self.assertIn(("messages", "codex-0001"), docs)
        self.assertEqual(docs[("messages", "codex-0001")]["summary"], "live")

    def test_merge_skips_rows_the_log_already_has_and_says_so(self):
        r = self.r
        r.write_log("codex", [line(1, "codex", id_="m-0001", data={"summary": "live"})])
        snap = make_snap(r.root, "s", {
            ("messages", "m-0001"): {"t": "2026-01-01T00:00:00Z", "summary": "old"},
            ("messages", "m-0002"): {"t": "2026-01-01T00:00:01Z", "summary": "older"},
        })
        res = merge_snapshots(r.root, [snap])
        self.assertEqual(res["skipped"], ["messages/m-0001"])
        docs = replay(load_all_lines(r.root))
        # The log's row wins: the snapshot's older copy is skipped, not merged in.
        self.assertEqual(docs[("messages", "m-0001")]["summary"], "live")
        self.assertIn(("messages", "m-0002"), docs)

    def test_merge_writes_the_rows_own_time_and_marks_them_imported(self):
        r = self.r
        snap = make_snap(r.root, "s", {
            ("messages", "m-0001"): {"t": "2026-01-01T00:00:00Z", "summary": "old"},
        })
        merge_snapshots(r.root, [snap])
        lines = load_all_lines(r.root)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].t, "2026-01-01T00:00:00Z")
        self.assertEqual(lines[0].via, "import")
        self.assertEqual(lines[0].op, "set")

    def test_older_imported_rows_sort_back_into_history(self):
        # Review focus 2: a row imported with an older t lands before the newer
        # live row in the replay, not after it.
        r = self.r
        r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "newer"},
                                   t="2026-10-22T12:00:00Z")])
        snap = make_snap(r.root, "s", {
            ("messages", "m-0001"): {"t": "2026-10-20T12:00:00Z", "summary": "older"},
        })
        merge_snapshots(r.root, [snap])
        docs = replay(load_all_lines(r.root))
        self.assertEqual([k for k in docs if k[0] == "messages"],
                         [("messages", "m-0001"), ("messages", "codex-0001")])

    def test_duplicate_across_snapshot_dirs_still_raises(self):
        r = self.r
        a = make_snap(r.root, "a", {("messages", "m-1"): {}})
        b = make_snap(r.root, "b", {("messages", "m-1"): {}})
        with self.assertRaises(RuntimeError):
            merge_snapshots(r.root, [a, b])

    def test_merge_leaves_the_log_clean(self):
        r = self.r
        r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "live"})])
        snap = make_snap(r.root, "s", {
            ("messages", "m-0001"): {"t": "2026-01-01T00:00:00Z", "summary": "old"},
            ("waiting", "w-1"): {"since": "2026-01-01T00:00:00Z", "summary": "w"},
        })
        merge_snapshots(r.root, [snap])
        roster = load_roster(r.root / "participants.json")
        self.assertEqual(check_all(r.root, roster), [])

    def test_merge_of_the_real_snapshots_into_the_real_log(self):
        # The cutover shape: the live log plus the old page's exports.
        r = self.r
        shutil.copy(str(REPO / "participants.json"), str(r.root / "participants.json"))
        live = REPO.parent / "crosstalk" / "log"
        if not live.is_dir():
            self.skipTest("no live log next to the repo")
        for name in ("codex.jsonl", "bewareilive.jsonl", "vectorcraft.jsonl", "owner.jsonl"):
            src = live / name
            if src.is_file():
                shutil.copy(str(src), str(r.root / "log" / name))
        roster = load_roster(r.root / "participants.json")
        before = replay(load_all_lines(r.root))
        res = merge_snapshots(r.root, [SNAP_A, SNAP_B])
        after = replay(load_all_lines(r.root))
        self.assertGreater(len(after), len(before))
        self.assertEqual(check_all(r.root, roster), [])
        # Every skipped row is one the live log already had.
        have = {"/".join(k) for k in before}
        self.assertTrue(all(s in have for s in res["skipped"]), res["skipped"])


class MergeCliTests(unittest.TestCase):
    """relay.py import --merge DIR"""

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

    def test_import_merge_appends_to_a_live_log(self):
        self.r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "live"})])
        snap = make_snap(self.r.root, "s", {
            ("messages", "m-0001"): {"t": "2026-01-01T00:00:00Z", "summary": "old"},
            ("messages", "codex-0001"): {"t": "2026-01-01T00:00:01Z", "summary": "dup"},
        })
        p = self.run_cli("import", "--merge", str(snap))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("messages/codex-0001", p.stderr)
        docs = replay(load_all_lines(self.r.root))
        self.assertIn(("messages", "m-0001"), docs)
        self.assertEqual(docs[("messages", "codex-0001")]["summary"], "live")

    def test_import_without_merge_still_refuses_a_live_log(self):
        self.r.write_log("codex", [line(1, "codex", data={"summary": "live"})])
        snap = make_snap(self.r.root, "s", {("messages", "m-0001"): {}})
        p = self.run_cli("import", str(snap))
        self.assertEqual(p.returncode, 1)
        self.assertIn("already has lines", p.stderr)

    def test_import_merge_reports_duplicate_ids_across_dirs(self):
        a = make_snap(self.r.root, "a", {("messages", "m-1"): {}})
        b = make_snap(self.r.root, "b", {("messages", "m-1"): {}})
        p = self.run_cli("import", "--merge", str(a), str(b))
        self.assertEqual(p.returncode, 1)
        self.assertIn("appears in both", p.stderr)


if __name__ == "__main__":
    unittest.main()
