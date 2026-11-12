"""Marks in the data the v2 page loads: /api/thread, /api/threads and /api/section."""
import unittest

from helpers import RunningServer, TempRepo, line
from test_marks_page import FORGED, ROSTER, SINCE, ack, msg
from test_serve import request


class MarksInV2RoutesTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        self.r.write_config({"marks_since": SINCE})
        self.r.write_log("alpha", [
            msg(1, "alpha", "alpha-l1", kind="lesson", summary="a rule", marks=FORGED),
            msg(2, "alpha", "alpha-c1", kind="check-in", summary="working on x", marks=FORGED),
        ])
        self.r.write_log("owner", [
            line(1, "owner", collection="lessons", id_="lesson-alpha-l1",
                 data={"project": "alpha", "summary": "a rule", "marks": FORGED}),
            line(2, "owner", collection="decisions", id_="owner-d1", data={"summary": "use x", "marks": FORGED}),
            line(3, "owner", collection="decisions", id_="owner-d2", data={"summary": "plain", "marks": FORGED}),
        ])
        self.r.write_log("beta", [ack(1, "beta", "alpha-l1", "ack"), ack(2, "beta", "owner-d1", "applies")])
        self.r.write_log("gamma", [ack(1, "gamma", "lesson-alpha-l1", "seen")])
        self.srv = RunningServer(self.r.root)
        self.srv.__enter__()

    def tearDown(self):
        self.srv.__exit__(None, None, None)
        self.r.__exit__(None, None, None)

    def get(self, path):
        status, body = request(self.srv.url + path)
        self.assertEqual(status, 200, body)
        return body

    def test_the_thread_puts_marks_on_each_message(self):
        body = self.get("/api/thread/alpha-l1")
        self.assertEqual(body["messages"][0]["marks"],
                         {"marks": {"beta": "ack", "gamma": "seen"}, "owed": ["gamma"], "expects": True})
        self.assertEqual(body["thread"]["marks_count"], {"acknowledged": 1, "expected": 2})
        # A message with no mark and no expectation has no key, and a writer's own field is dropped.
        plain = self.get("/api/thread/alpha-c1")
        self.assertNotIn("marks", plain["messages"][0])
        self.assertNotIn("marks_count", plain["thread"])

    def test_the_thread_list_counts_the_acknowledged_marks_of_a_root_that_expects_them(self):
        rows = {t["root"]: t for t in self.get("/api/threads")["threads"]}
        self.assertEqual(rows["alpha-l1"]["marks_count"], {"acknowledged": 1, "expected": 2})
        self.assertNotIn("marks_count", rows["alpha-c1"])

    def test_a_count_follows_a_new_mark(self):
        later = line(2, "gamma", collection="acks", id_="gamma--alpha-l1", t="2026-10-21T18:05:00Z",
                     data={"from": "gamma", "item": "alpha-l1", "mark": "none", "t": "2026-10-21T18:05:00Z"})
        self.r.write_log("gamma", [ack(1, "gamma", "lesson-alpha-l1", "seen"), later])
        rows = {t["root"]: t for t in self.get("/api/threads")["threads"]}
        self.assertEqual(rows["alpha-l1"]["marks_count"], {"acknowledged": 2, "expected": 2})

    def test_the_lessons_section_puts_marks_on_each_row(self):
        rows = {r["id"]: r for r in self.get("/api/section/lessons")["rows"]}
        self.assertEqual(rows["lesson-alpha-l1"]["marks"],
                         {"marks": {"beta": "ack", "gamma": "seen"}, "owed": [], "expects": False})

    def test_the_decisions_section_puts_marks_on_each_row(self):
        rows = {r["id"]: r for r in self.get("/api/section/decisions")["rows"]}
        self.assertEqual(rows["owner-d1"]["marks"]["marks"], {"beta": "applies"})
        self.assertEqual(rows["owner-d1"]["marks"]["expects"], True)
        self.assertEqual(rows["owner-d1"]["marks"]["owed"], ["alpha", "gamma"])
        self.assertEqual(rows["owner-d2"]["marks"]["marks"], {})
        # Without marks_since a decision with no mark carries no key, and the log's field is dropped.
        self.r.write_config({})
        rows = {r["id"]: r for r in self.get("/api/section/decisions")["rows"]}
        self.assertNotIn("marks", rows["owner-d2"])

    def test_a_marks_field_from_the_log_never_reaches_the_v2_routes(self):
        self.r.write_config({})
        for path in ("/api/thread/alpha-c1", "/api/section/lessons", "/api/section/decisions"):
            body = self.get(path)
            for row in body.get("messages", body.get("rows", [])):
                self.assertNotEqual(row.get("marks"), FORGED, (path, row["id"]))


if __name__ == "__main__":
    unittest.main()
