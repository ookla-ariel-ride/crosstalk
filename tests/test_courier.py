import hashlib
import json
import shutil
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk.courier import load_state, pending, restore, save_state, sent
from crosstalk.importer import import_snapshots
from crosstalk.pull import pull
from crosstalk.replay import load_all_lines, replay

REPO = Path(__file__).resolve().parent.parent
SNAP_A = REPO / "import" / "2uM4ZVpmoYLQYkyXVgvckH-2026-10-21"
SNAP_B = REPO / "import" / "ThPWGePNJjieLDTUqJbfmc-2026-10-21"
PAGE = SPEC_ROSTER["page"]
T0 = "2026-10-21T10:00:00Z"


def roster_hash(root):
    return hashlib.sha256((Path(root) / "participants.json").read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def writes_of(batch_path):
    return load(batch_path)["writes"]


def data_writes(batch_path):
    return [w for w in writes_of(batch_path) if w["collection"] != "participants"]


def snapshot(out):
    """Every file under out, relative path -> text."""
    out = Path(out)
    return {str(p.relative_to(out)): p.read_text(encoding="utf-8")
            for p in sorted(out.rglob("*")) if p.is_file()}


def doc_name(batch_no, coll, did):
    return "%03d-%s-%s-%s.json" % (batch_no, coll, did,
                                   hashlib.sha1(did.encode("utf-8")).hexdigest()[:12])


def tmin(i):
    return "2026-10-21T10:%02d:%02dZ" % (i // 60, i % 60)


class PendingTests(unittest.TestCase):
    def test_batches_at_most_50_and_doc_once(self):
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root)}})
            lines = []
            for i in range(120):
                doc = i % 60
                lines.append(line(i + 1, "rev", id_="rev-%02d" % doc,
                                  op="set" if i < 60 else "update",
                                  data={"n": i}, t=tmin(i)))
            r.write_log("rev", lines)
            out = r.root / "out"
            batches = pending(r.root, out)
            self.assertEqual([p.name for p in batches], ["batch-001.json", "batch-002.json"])
            self.assertEqual([len(writes_of(b)) for b in batches], [50, 10])
            seen = []
            for b in batches:
                ids = [(w["collection"], w["doc_id"]) for w in writes_of(b)]
                self.assertEqual(len(ids), len(set(ids)))
                seen.extend(ids)
            self.assertEqual(len(seen), 60)
            # Collapsed: one set of the full replay, in the docs dir.
            w0 = writes_of(batches[0])[0]
            self.assertEqual((w0["op"], w0["collection"], w0["doc_id"]),
                             ("set", "messages", "rev-00"))
            self.assertEqual(Path(w0["file_path"]),
                             (out / "docs" / doc_name(1, "messages", "rev-00")).resolve())
            self.assertTrue(Path(w0["file_path"]).is_absolute())
            self.assertEqual(load(w0["file_path"]), {"n": 60})
            self.assertNotIn("if_version", w0)
            # advance: batch 1 covers docs 00..49, so lines 1..60 are all covered
            # (line 51 touches doc 50, in batch 2), i.e. only 1..50.
            self.assertEqual(load(batches[0])["advance"], {"rev": 50})
            self.assertEqual(load(batches[1])["advance"], {"rev": 120})
            self.assertEqual(load(batches[0])["page"], PAGE)
            self.assertNotIn("participantsHash", load(batches[0]))

    def test_doc_order_by_last_unsent_line(self):
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root)}})
            r.write_log("rev", [line(1, "rev", id_="rev-a", t=T0),
                                  line(2, "rev", id_="rev-b", t="2026-10-21T10:00:01Z"),
                                  line(3, "rev", id_="rev-a", op="update",
                                       data={"x": 1}, t="2026-10-21T10:00:02Z")])
            r.write_log("beta", [line(1, "beta", id_="beta-c",
                                             t="2026-10-21T10:00:01Z")])
            [b] = pending(r.root, r.root / "out")
            self.assertEqual([w["doc_id"] for w in writes_of(b)],
                             ["beta-c", "rev-b", "rev-a"])
            self.assertEqual(load(b)["advance"], {"rev": 3, "beta": 1})

    def test_pending_then_sent_then_nothing(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("rev", [line(1, "rev", t=T0), line(2, "rev", t=T0)])
            out = r.root / "out"
            batches = pending(r.root, out)
            self.assertEqual(len(batches), 1)
            for b in batches:
                sent(r.root, b)
            state = load_state(r.root)[PAGE]
            self.assertEqual(state["bookmarks"], {"rev": 2})
            self.assertEqual(state["participantsHash"], roster_hash(r.root))
            out2 = r.root / "out2"
            self.assertEqual(pending(r.root, out2), [])
            self.assertFalse(out2.exists() and any(out2.iterdir()))
            # A new line after sent is the only thing carried.
            r.write_log("rev", [line(1, "rev", t=T0), line(2, "rev", t=T0),
                                  line(3, "rev", t=T0)])
            [b] = pending(r.root, r.root / "out3")
            self.assertEqual([w["doc_id"] for w in writes_of(b)], ["rev-0003"])
            self.assertEqual(load(b)["advance"], {"rev": 3})

    def test_bookmarks_move_only_on_sent(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("rev", [line(1, "rev", t=T0, data={"body": "é\n\"x\" 🎉"})])
            out = r.root / "out"
            first = [p.name for p in pending(r.root, out)]
            snap1 = snapshot(out)
            second = [p.name for p in pending(r.root, out)]
            self.assertEqual(first, second)
            self.assertEqual(snapshot(out), snap1)
            self.assertEqual(load_state(r.root), {})
            doc_file = out / "docs" / doc_name(1, "messages", "rev-0001")
            self.assertIn("é", doc_file.read_text(encoding="utf-8"))
            self.assertEqual(load(doc_file), {"body": "é\n\"x\" 🎉"})

    def test_case_only_ids_get_distinct_doc_files(self):
        # On case-insensitive APFS, rev-Ab and rev-ab must not share a file.
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root)}})
            r.write_log("rev", [
                line(1, "rev", id_="rev-Ab", data={"body": "upper"}, t=T0),
                line(2, "rev", id_="rev-ab", data={"body": "lower"}, t=T0),
            ])
            [b] = pending(r.root, r.root / "out")
            ws = {w["doc_id"]: w["file_path"] for w in writes_of(b)}
            self.assertNotEqual(ws["rev-Ab"].lower(), ws["rev-ab"].lower())
            self.assertEqual(load(ws["rev-Ab"]), {"body": "upper"})
            self.assertEqual(load(ws["rev-ab"]), {"body": "lower"})

    def test_stale_batches_removed(self):
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root)}})
            r.write_log("rev", [line(i + 1, "rev", t=T0) for i in range(60)])
            out = r.root / "out"
            self.assertEqual(len(pending(r.root, out)), 2)
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root),
                                       "bookmarks": {"rev": 55}}})
            self.assertEqual([p.name for p in pending(r.root, out)], ["batch-001.json"])
            self.assertEqual(sorted(p.name for p in out.glob("batch-*.json")),
                             ["batch-001.json"])
            self.assertEqual(len(list((out / "docs").iterdir())), 5)

    def test_if_version_from_state(self):
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root),
                                       "versions": {"messages/rev-0001": 4,
                                                    "messages/other": 9}},
                                "https://other": {"versions": {"messages/rev-0002": 7}}})
            r.write_log("rev", [line(1, "rev", t=T0), line(2, "rev", t=T0)])
            [b] = pending(r.root, r.root / "out")
            ws = {w["doc_id"]: w for w in writes_of(b)}
            self.assertEqual(ws["rev-0001"]["if_version"], 4)
            self.assertNotIn("if_version", ws["rev-0002"])

    def test_participants_mirrored_once(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("rev", [line(1, "rev", t=T0)])
            save_state(r.root, {PAGE: {"versions": {"participants/rev": 3}}})
            [b] = pending(r.root, r.root / "out")
            batch = load(b)
            self.assertEqual(batch["participantsHash"], roster_hash(r.root))
            mirror = [w for w in batch["writes"] if w["collection"] == "participants"]
            self.assertEqual([w["doc_id"] for w in mirror],
                             [p["id"] for p in SPEC_ROSTER["participants"]])
            for w, entry in zip(mirror, SPEC_ROSTER["participants"]):
                self.assertEqual(load(w["file_path"]), entry)
            self.assertEqual([w.get("if_version") for w in mirror], [None, None, 3, None])
            sent(r.root, b)
            r.write_log("rev", [line(1, "rev", t=T0), line(2, "rev", t=T0)])
            [b2] = pending(r.root, r.root / "out2")
            self.assertNotIn("participantsHash", load(b2))
            self.assertEqual([w["collection"] for w in writes_of(b2)], ["messages"])
            # Changing the roster brings the mirror back.
            data = json.loads(json.dumps(SPEC_ROSTER))
            data["participants"][2]["name"] = "Rev 2"
            r.write_roster(data)
            [b3] = pending(r.root, r.root / "out3")
            self.assertEqual(load(b3)["participantsHash"], roster_hash(r.root))
            self.assertEqual(len([w for w in writes_of(b3)
                                  if w["collection"] == "participants"]), 4)

    def test_participants_lines_not_carried_but_advance(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("owner", [
                line(1, "owner", "participants", "rev", op="update",
                     data={"trust": "approved"}, t=T0),
                line(2, "owner", id_="owner-x", t=T0),
            ])
            [b] = pending(r.root, r.root / "out")
            batch = load(b)
            ws = [(w["collection"], w["doc_id"]) for w in batch["writes"]]
            self.assertEqual(ws.count(("participants", "rev")), 1)
            self.assertEqual(len(ws), 5)
            self.assertEqual(batch["advance"], {"owner": 2})

    def test_mirror_counts_toward_first_batch(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("rev", [line(i + 1, "rev", t=T0) for i in range(50)])
            batches = pending(r.root, r.root / "out")
            self.assertEqual([len(writes_of(b)) for b in batches], [50, 4])
            self.assertEqual(len(data_writes(batches[0])), 46)
            self.assertIn("participantsHash", load(batches[0]))
            self.assertNotIn("participantsHash", load(batches[1]))
            self.assertEqual(load(batches[0])["advance"], {"rev": 46})
            self.assertEqual(load(batches[1])["advance"], {"rev": 50})
            self.assertEqual(Path(data_writes(batches[1])[0]["file_path"]).name,
                             doc_name(2, "messages", "rev-0047"))

    def test_advance_omits_when_not_higher(self):
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root)}})
            # beta's single line is placed in batch 2; rev finishes in batch 1.
            r.write_log("rev", [line(i + 1, "rev", t=T0) for i in range(50)])
            r.write_log("beta", [line(1, "beta", t="2026-10-21T11:00:00Z")])
            b1, b2 = pending(r.root, r.root / "out")
            self.assertEqual(load(b1)["advance"], {"rev": 50})
            self.assertEqual(load(b2)["advance"], {"beta": 1})

    def test_bad_file_stops_at_bad_line_others_go(self):
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root)}})
            r.write_log("rev", [line(1, "rev", t=T0), "{not json",
                                  line(3, "rev", t=T0)])
            r.write_log("beta", [line(1, "beta", t=T0)])
            [b] = pending(r.root, r.root / "out")
            self.assertEqual(sorted(w["doc_id"] for w in writes_of(b)),
                             ["beta-0001", "rev-0001"])
            self.assertEqual(load(b)["advance"], {"rev": 1, "beta": 1})

    def test_quarantined_and_unknown_carried(self):
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root)}})
            r.write_log("stranger", [line(1, "stranger", t=T0)])
            [b] = pending(r.root, r.root / "out")
            self.assertEqual([w["doc_id"] for w in writes_of(b)], ["stranger-0001"])

    def test_trust_violation_stops_that_file_only(self):
        with TempRepo() as r:
            data = json.loads(json.dumps(SPEC_ROSTER))
            data["participants"].append({"id": "gemini", "name": "Gemini",
                                         "kind": "researcher", "color": "#8a5a00", "colorDark": "#d9b25c", "trust": "quarantined"})
            r.write_roster(data)
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root)}})
            r.write_log("gemini", [line(1, "gemini", t=T0),
                                   line(2, "gemini", id_="m-0001", t=T0),
                                   line(3, "gemini", t=T0)])
            r.write_log("rev", [line(1, "rev", t=T0)])
            [b] = pending(r.root, r.root / "out")
            self.assertEqual(sorted(w["doc_id"] for w in writes_of(b)),
                             ["gemini-0001", "rev-0001"])
            self.assertEqual(load(b)["advance"], {"rev": 1, "gemini": 1})

    def test_other_page_bookmarks_ignored(self):
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"participantsHash": roster_hash(r.root)},
                                "https://other": {"bookmarks": {"rev": 1}}})
            r.write_log("rev", [line(1, "rev", t=T0)])
            [b] = pending(r.root, r.root / "out")
            self.assertEqual([w["doc_id"] for w in writes_of(b)], ["rev-0001"])


class SentTests(unittest.TestCase):
    def test_sent_keeps_max_and_other_keys(self):
        with TempRepo() as r:
            r.write_roster()
            save_state(r.root, {PAGE: {"bookmarks": {"rev": 5, "owner": 1},
                                       "versions": {"messages/x": 2}},
                                "https://other": {"bookmarks": {"rev": 9}}})
            b = r.root / "b.json"
            b.write_text(json.dumps({"page": PAGE, "advance": {"rev": 3, "beta": 2},
                                     "writes": [], "participantsHash": "h"}), encoding="utf-8")
            sent(r.root, b)
            state = load_state(r.root)
            self.assertEqual(state[PAGE], {"bookmarks": {"rev": 5, "owner": 1,
                                                         "beta": 2},
                                           "versions": {"messages/x": 2},
                                           "participantsHash": "h"})
            self.assertEqual(state["https://other"], {"bookmarks": {"rev": 9}})

    def test_sent_refuses_other_page(self):
        with TempRepo() as r:
            r.write_roster()
            b = r.root / "b.json"
            b.write_text(json.dumps({"page": "https://other", "advance": {"rev": 3},
                                     "writes": []}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                sent(r.root, b)
            self.assertEqual(load_state(r.root), {})


class RestoreTests(unittest.TestCase):
    def test_restore_carries_everything_without_versions(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("rev", [line(1, "rev", t=T0), line(2, "rev", t=T0)])
            state = {PAGE: {"bookmarks": {"rev": 2}, "participantsHash": roster_hash(r.root),
                            "versions": {"messages/rev-0001": 4}}}
            save_state(r.root, state)
            self.assertEqual(pending(r.root, r.root / "p"), [])
            [b] = restore(r.root, r.root / "out")
            batch = load(b)
            self.assertEqual(batch["participantsHash"], roster_hash(r.root))
            self.assertEqual(len(batch["writes"]), 6)
            self.assertTrue(all("if_version" not in w for w in batch["writes"]))
            self.assertEqual(batch["advance"], {"rev": 2})
            self.assertEqual(load_state(r.root), state)

    def test_round_trip_real_history(self):
        with TempRepo() as r:
            shutil.copy(str(REPO / "participants.json"), str(r.root / "participants.json"))
            import_snapshots(r.root, [SNAP_A, SNAP_B])
            batches = restore(r.root, r.root / "out")
            self.assertGreater(len(batches), 1)
            db = {}
            for b in batches:
                batch = load(b)
                self.assertLessEqual(len(batch["writes"]), 50)
                keys = [(w["collection"], w["doc_id"]) for w in batch["writes"]]
                self.assertEqual(len(keys), len(set(keys)))
                for w in batch["writes"]:
                    self.assertEqual(w["op"], "set")
                    self.assertNotIn("if_version", w)
                    db[(w["collection"], w["doc_id"])] = load(w["file_path"])
                sent(r.root, b)
            data = {k: v for k, v in db.items() if k[0] != "participants"}
            people = {k: v for k, v in db.items() if k[0] == "participants"}
            self.assertEqual(len(data), 215)
            roster = json.loads((REPO / "participants.json").read_text(encoding="utf-8"))
            self.assertEqual(len(people), len(roster["participants"]))
            self.assertEqual(data, replay(load_all_lines(r.root)))

            exp = r.root / "export"
            listings = {}
            for (coll, did), fields in db.items():
                (exp / coll).mkdir(parents=True, exist_ok=True)
                (exp / coll / (did + ".json")).write_text(
                    json.dumps(fields, ensure_ascii=False), encoding="utf-8")
                listings.setdefault(coll, []).append(json.dumps(
                    {"id": did, "data": fields, "version": 1,
                     "updatedAt": "2026-10-21T12:00:00Z"}, ensure_ascii=False))
            for coll, rows in listings.items():
                (exp / (coll + ".listing.txt")).write_text(
                    "\n".join(rows) + "\n", encoding="utf-8")
            res = pull(r.root, exp)
            self.assertEqual((res.owner_lines, res.approvals, res.skipped), (0, [], []))
            self.assertEqual(len(res.versions), 215 + len(roster["participants"]))
            self.assertEqual(pending(r.root, r.root / "after"), [])


if __name__ == "__main__":
    unittest.main()
