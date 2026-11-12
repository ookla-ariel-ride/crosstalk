"""Marks in the data the present page loads: /api/snapshot and the static export."""
import copy
import shutil
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, RunningServer, TempRepo, line
from test_export_page import snapshot_in
from test_serve import request

from crosstalk.exporter import build_snapshot, export_page

REPO = Path(__file__).resolve().parent.parent
SINCE = "2026-10-21T18:00:00Z"

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["participants"] += [
    {"id": "gamma", "name": "Gamma", "kind": "project", "color": "#2e7d32", "colorDark": "#81c784",
     "checkInMin": 30, "trust": "approved"},
]


def msg(seq, frm, id_, **data):
    return line(seq, frm, id_=id_, data=dict(data, **{"from": frm}))


def ack(seq, frm, item, mark):
    return line(seq, frm, collection="acks", id_=frm + "--" + item,
                data={"from": frm, "item": item, "mark": mark})


class MarksInSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        self.r.write_config({"marks_since": SINCE})
        self.r.write_log("alpha", [
            msg(1, "alpha", "alpha-l1", kind="lesson", summary="a rule"),
            msg(2, "alpha", "alpha-c1", kind="check-in", summary="working on x"),
        ])
        self.r.write_log("owner", [line(1, "owner", collection="lessons", id_="lesson-alpha-l1",
                                        data={"project": "alpha", "summary": "a rule"})])
        self.r.write_log("beta", [ack(1, "beta", "alpha-l1", "ack")])
        self.r.write_log("gamma", [ack(1, "gamma", "alpha-l1", "seen")])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def check(self, collections):
        lesson = collections["messages"]["alpha-l1"]["marks"]
        self.assertEqual(lesson, {"marks": {"beta": "ack", "gamma": "seen"}, "owed": ["gamma"], "expects": True})
        self.assertEqual(collections["lessons"]["lesson-alpha-l1"]["marks"],
                         {"marks": {"beta": "ack", "gamma": "seen"}, "owed": [], "expects": False})
        self.assertNotIn("marks", collections["messages"]["alpha-c1"])

    def test_the_snapshot_route_carries_marks(self):
        with RunningServer(self.r.root) as srv:
            status, body = request(srv.url + "/api/snapshot")
            self.assertEqual(status, 200)
            self.check(body["collections"])
            # The cached replay holds no marks: a second read gives the same rows.
            _, again = request(srv.url + "/api/snapshot")
            self.assertEqual(body["collections"], again["collections"])
            self.assertNotIn("marks", again["collections"]["acks"]["beta--alpha-l1"])

    def test_the_export_carries_marks(self):
        self.check(build_snapshot(self.r.root)["collections"])
        shutil.copy(str(REPO / "index.html"), str(self.r.root / "index.html"))
        out = self.r.root / "page.html"
        export_page(self.r.root, out)
        self.check(snapshot_in(out.read_text(encoding="utf-8"))["collections"])

    def test_a_post_with_no_mark_and_no_expectation_has_no_key(self):
        self.r.write_config({})
        cols = build_snapshot(self.r.root)["collections"]
        self.assertNotIn("marks", cols["messages"]["alpha-c1"])
        # Marked, so it carries the key, though it expects none without marks_since.
        self.assertEqual(cols["messages"]["alpha-l1"]["marks"]["expects"], False)


FORGED = {"marks": {"beta": "ack"}, "owed": [], "expects": False}


class ForgedMarksTests(unittest.TestCase):
    """Marks come only from `acks` rows: a `marks` field that a writer put in the log never shows."""

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        self.r.write_config({"marks_since": SINCE})
        self.r.write_log("alpha", [
            msg(1, "alpha", "alpha-c1", kind="check-in", summary="plain", marks=FORGED),
            msg(2, "alpha", "alpha-l1", kind="lesson", summary="a rule", marks=FORGED),
            line(3, "alpha", collection="decisions", id_="alpha-d1", data={"summary": "d", "marks": FORGED}),
        ])
        self.r.write_log("beta", [
            msg(1, "beta", "beta-r1", kind="request", summary="help", to=["alpha"]),
            # The form before marks: `none: true` and no `mark`.
            line(2, "beta", collection="acks", id_="beta--alpha-l1",
                 data={"from": "beta", "item": "alpha-l1", "none": True}),
        ])
        self.r.write_log("gamma", [line(1, "gamma", collection="acks", id_="gamma--beta-r1",
                                        data={"from": "gamma", "item": "beta-r1", "none": True})])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def check(self, cols):
        self.assertNotIn("marks", cols["messages"]["alpha-c1"])
        self.assertNotIn("marks", cols["decisions"]["alpha-d1"])
        self.assertEqual(cols["messages"]["alpha-l1"]["marks"],
                         {"marks": {"beta": "none"}, "owed": ["gamma"], "expects": True})
        self.assertEqual(cols["messages"]["beta-r1"]["marks"],
                         {"marks": {"gamma": "none"}, "owed": [], "expects": False})

    def test_the_snapshot_route_drops_a_marks_field_from_the_log(self):
        with RunningServer(self.r.root) as srv:
            _, body = request(srv.url + "/api/snapshot")
            self.check(body["collections"])

    def test_with_marks_copies_the_rows_it_changes(self):
        from crosstalk import marks
        row = {"summary": "x", "marks": FORGED}
        lesson = {"summary": "y"}
        cols = {"messages": {"m1": row, "m2": lesson}, "acks": {"a--m1": {"marks": 1}}}
        marks.with_marks(cols, {("messages", "m2"): {"marks": {}, "owed": [], "expects": True}})
        self.assertNotIn("marks", cols["messages"]["m1"])
        self.assertEqual(row, {"summary": "x", "marks": FORGED})
        self.assertEqual(lesson, {"summary": "y"})
        self.assertEqual(cols["acks"]["a--m1"], {"marks": 1})

    def test_the_export_drops_a_marks_field_from_the_log(self):
        self.check(build_snapshot(self.r.root)["collections"])
        shutil.copy(str(REPO / "index.html"), str(self.r.root / "index.html"))
        out = self.r.root / "page.html"
        export_page(self.r.root, out)
        self.check(snapshot_in(out.read_text(encoding="utf-8"))["collections"])


class PageWordsTests(unittest.TestCase):
    def test_the_page_shows_the_emoji_and_words_of_marks_py(self):
        """index.html holds MARK_SHOWN: [word, emoji, label] in the order the line shows them."""
        import json
        import re
        from crosstalk import marks
        m = re.search(r"var MARK_SHOWN = (\[.*?\]\]);", (REPO / "index.html").read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(m, "no MARK_SHOWN in index.html")
        shown = json.loads(m.group(1))
        self.assertEqual([w for w, _e, _l in shown], ["ack", "applies", "none", "seen", "unclear"])
        self.assertEqual(sorted(w for w, _e, _l in shown), sorted(marks.MARKS))
        for word, emoji, label in shown:
            self.assertEqual(emoji, marks.EMOJI[word], word)
            self.assertEqual(label, marks.LABEL[word], word)


if __name__ == "__main__":
    unittest.main()
