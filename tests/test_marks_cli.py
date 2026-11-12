"""Marks on a post: relay.py marks <item> and relay.py marks --missing [--older]."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from test_marks_inbox import MarksInboxBase, msg


def ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class MarksCliTests(MarksInboxBase):
    old_lesson = "alpha-l1"
    new_lesson = "alpha-l2"

    def setUp(self):
        super().setUp()
        self.alpha = [msg(1, "alpha", self.old_lesson, kind="lesson", summary="old rule", t=ago(120)),
                      msg(2, "alpha", self.new_lesson, kind="lesson", summary="new rule", t=ago(10)),
                      msg(3, "alpha", "alpha-c1", kind="check-in", summary="plain", t=ago(5))]
        self.r.write_log("alpha", self.alpha)
        self.ack("gamma", self.lesson_id, "--ok")
        self.ack("beta", self.lesson_id, "--seen")

    def relay(self, *args):
        return self.run_cli(*args)

    def remove(self, did):
        """The log of alpha without the post, numbered again from 1."""
        self.alpha = [dict(ln, seq=n) for n, ln in enumerate((ln for ln in self.alpha if ln["id"] != did), 1)]
        self.r.write_log("alpha", self.alpha)

    def test_marks_prints_each_mark_and_the_owed_names(self):
        out = self.relay("marks", self.lesson_id).stdout
        self.assertIn("✅ gamma", out); self.assertIn("👀 beta", out); self.assertIn("not yet: beta, delta", out)

    def test_marks_json_is_the_view_with_id_and_collection(self):
        out = json.loads(self.relay("marks", self.lesson_id, "--json").stdout)
        self.assertEqual(out, {"id": self.lesson_id, "collection": "messages", "expects": True,
                               "marks": {"beta": "seen", "gamma": "ack"}, "owed": ["beta", "delta"]})

    def test_marks_on_a_post_that_expects_none(self):
        self.ack("beta", "alpha-c1", "--seen")
        out = self.relay("marks", "alpha-c1").stdout
        self.assertIn("👀 beta", out)
        self.assertNotIn("not yet", out)
        self.assertIn("expects no mark", out)

    def test_missing_lists_oldest_first_and_older_cuts(self):
        ids = [o["id"] for o in json.loads(self.relay("marks", "--missing", "--json").stdout)]
        self.assertEqual(ids, [self.old_lesson, self.new_lesson])
        ids = [o["id"] for o in json.loads(self.relay("marks", "--missing", "--older", "60", "--json").stdout)]
        self.assertEqual(ids, [self.old_lesson])

    def test_missing_leaves_out_a_post_nobody_owes(self):
        for pid in ("beta", "delta"):
            self.ack(pid, self.old_lesson, "--none")
        ids = [o["id"] for o in json.loads(self.relay("marks", "--missing", "--json").stdout)]
        self.assertEqual(ids, [self.new_lesson])

    def test_missing_text_names_the_owed(self):
        out = self.relay("marks", "--missing").stdout
        self.assertIn(self.old_lesson, out)
        self.assertIn("not yet: beta, delta", out)

    def test_removed_post_is_not_listed_and_marks_says_so(self):
        self.remove(self.lesson_id)
        self.remove(self.new_lesson)
        self.assertEqual(json.loads(self.relay("marks", "--missing", "--json").stdout), [])
        self.assertEqual(self.relay("marks", self.lesson_id).returncode, 1)

    def test_bad_arguments_are_refused(self):
        self.assertEqual(self.relay("marks").returncode, 2)
        self.assertEqual(self.relay("marks", self.lesson_id, "--missing").returncode, 2)
        self.assertEqual(self.relay("marks", self.lesson_id, "--older", "5").returncode, 2)
        self.assertEqual(self.relay("marks", "--missing", "--older", "-1").returncode, 2)


if __name__ == "__main__":
    unittest.main()
