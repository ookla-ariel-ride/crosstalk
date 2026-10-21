import json
import shutil
import unittest
from pathlib import Path

from helpers import TempRepo

from crosstalk.importer import import_snapshots
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
            self.assertEqual((ln.op, ln.via), ("set", None))

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

    def test_real_snapshots_import_cleanly(self):
        with TempRepo() as r:
            shutil.copy(str(REPO / "participants.json"), str(r.root / "participants.json"))
            import_snapshots(r.root, [SNAP_A, SNAP_B])
            roster = load_roster(r.root / "participants.json")
            self.assertEqual([p for p in check_all(r.root, roster) if p.fatal], [])
            docs = replay(load_all_lines(r.root))
            self.assertEqual(len(docs), 215)
            self.assertEqual(docs[("agreements", "a-review-broker-cleanup")]["status"], "ready-to-adopt")


if __name__ == "__main__":
    unittest.main()
