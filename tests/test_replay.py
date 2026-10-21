import copy
import unittest

from helpers import TempRepo, line
from crosstalk.logs import Line
from crosstalk.replay import load_all_lines, order_key, replay

DEL = {"__delete__": True}


def L(seq, frm, op="set", id_="x-1", data=None, t="2026-10-21T18:00:00Z", collection="messages"):
    return Line(seq=seq, t=t, from_=frm, op=op, collection=collection, id=id_, data=data or {})


class ReplayTests(unittest.TestCase):
    def test_last_set_wins(self):
        docs = replay([L(1, "a", data={"v": 1, "w": 2}), L(2, "a", data={"v": 3}, t="2026-10-21T19:00:00Z")])
        self.assertEqual(docs[("messages", "x-1")], {"v": 3})

    def test_update_merges(self):
        docs = replay([L(1, "a", data={"v": 1}), L(2, "a", op="update", data={"w": 2}, t="2026-10-21T19:00:00Z")])
        self.assertEqual(docs[("messages", "x-1")], {"v": 1, "w": 2})

    def test_delete_marker_removes_field(self):
        docs = replay([L(1, "a", data={"v": 1, "w": 2}),
                       L(2, "a", op="update", data={"v": DEL}, t="2026-10-21T19:00:00Z")])
        self.assertEqual(docs[("messages", "x-1")], {"w": 2})

    def test_delete_marker_must_be_exact(self):
        odd = {"__delete__": True, "x": 1}
        docs = replay([L(1, "a", op="update", data={"v": odd, "w": {"__delete__": False}})])
        self.assertEqual(docs[("messages", "x-1")], {"v": odd, "w": {"__delete__": False}})

    def test_order_by_t_then_from_then_seq(self):
        a = L(1, "codex", data={"v": "codex"})
        b = L(1, "bewareilive", data={"v": "bew"})
        docs = replay([a, b])
        self.assertEqual(docs[("messages", "x-1")], {"v": "codex"})
        self.assertLess(order_key(b), order_key(a))

    def test_update_before_any_set_creates_doc(self):
        docs = replay([L(1, "a", op="update", data={"v": 1})])
        self.assertEqual(docs[("messages", "x-1")], {"v": 1})

    def test_unknown_fields_and_values_pass_through(self):
        data = {"status": "ready-to-adopt", "extra": [1, {"x": None}]}
        docs = replay([L(1, "a", data=data)])
        self.assertEqual(docs[("messages", "x-1")], data)

    def test_does_not_mutate_input(self):
        d1 = {"n": {"k": [1]}}
        d2 = {"v": DEL, "n2": {"z": 1}}
        l1, l2 = L(1, "a", data=d1), L(2, "a", op="update", data=d2, t="2026-10-21T19:00:00Z")
        before = copy.deepcopy((d1, d2))
        docs = replay([l1, l2])
        docs[("messages", "x-1")]["n"]["k"].append(2)
        docs[("messages", "x-1")]["n2"]["z"] = 9
        self.assertEqual((d1, d2), before)

    def test_load_all_lines(self):
        with TempRepo() as r:
            r.write_log("a", [line(1, "a"), line(2, "a")])
            r.write_log("b", [line(1, "b"), "garbage"])
            (r.root / "log" / "relay-state.json").write_text("{}")
            got = load_all_lines(r.root)
            self.assertEqual(sorted((x.from_, x.seq) for x in got), [("a", 1), ("a", 2), ("b", 1)])

    def test_load_all_lines_missing_log_dir(self):
        with TempRepo() as r:
            (r.root / "log").rmdir()
            self.assertEqual(load_all_lines(r.root), [])


if __name__ == "__main__":
    unittest.main()
