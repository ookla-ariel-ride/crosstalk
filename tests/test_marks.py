"""Marks on a post: the word on the `acks` row and the five flags of `relay.py ack`."""
import copy
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk import marks
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay
from crosstalk.requests import acks_by_item

REPO = Path(__file__).resolve().parent.parent

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["participants"] += [
    {"id": "gamma", "name": "Gamma", "kind": "project", "color": "#2e7d32", "colorDark": "#81c784",
     "checkInMin": 30, "trust": "approved"},
    {"id": "delta", "name": "Delta", "kind": "project", "color": "#5d4037", "colorDark": "#a1887f",
     "checkInMin": 30, "trust": "quarantined"},
]


def msg(seq, frm, id_, **data):
    return line(seq, frm, id_=id_, data=dict(data, **{"from": frm}))


class MarkTests(unittest.TestCase):
    request_id = "alpha-r1"
    lesson_id = "alpha-l1"
    question_id = "alpha-q1"
    decision_id = "gamma-d1"

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")
        self.r.write_log("alpha", [
            msg(1, "alpha", self.request_id, kind="request", summary="look", to=["beta", "gamma"]),
            msg(2, "alpha", self.lesson_id, kind="lesson", summary="a rule"),
            msg(3, "alpha", self.question_id, kind="question", summary="Which?"),
        ])
        # The decision names beta in `by`, but gamma wrote it: gamma is its writer.
        self.r.write_log("gamma", [line(1, "gamma", collection="decisions", id_=self.decision_id,
                                        data={"by": "beta", "summary": "use x"})])
        self.r.write_log("owner", [
            line(1, "owner", collection="lessons", id_="lesson-" + self.lesson_id,
                 data={"project": "alpha", "summary": "a rule"}),
            line(2, "owner", collection="lessons", id_="lesson-solo", data={"project": "owner", "summary": "x"}),
        ])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def run_ack(self, pid, item, *flags):
        return self.run_cli("ack", "--from", pid, item, *flags)

    def docs(self):
        roster = load_roster(self.r.root / "participants.json")
        lines = load_all_lines(self.r.root, roster)
        return enrich_messages(replay(lines), lines), roster

    def ack(self, pid, item, *flags):
        p = self.run_ack(pid, item, *flags)
        self.assertEqual(p.returncode, 0, p.stderr)
        row_id = p.stdout.strip()
        self.assertEqual(row_id, pid + "--" + item)
        return self.docs()[0][("acks", row_id)]

    def marks(self):
        docs, roster = self.docs()
        return marks.marks_by_item(docs, roster=roster)

    def pending(self, rid):
        out = json.loads(self.run_cli("inbox", "beta", "--all").stdout)
        return [r for r in out["requests"] if r["request"]["id"] == rid][0]["pending_from"]

    def log_lines(self):
        out = []
        for f in sorted((self.r.root / "log").glob("*.jsonl")):
            out.extend(ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip())
        return out

    def test_the_words_emoji_and_labels(self):
        self.assertEqual(marks.MARKS, ("seen", "ack", "applies", "none", "unclear"))
        self.assertEqual(marks.ACKNOWLEDGED, ("ack", "applies", "none"))
        self.assertEqual([marks.EMOJI[w] for w in marks.MARKS], ["👀", "✅", "🔧", "➖", "❓"])
        self.assertEqual([marks.LABEL[w] for w in marks.MARKS],
                         ["seen", "acknowledged", "applies", "no change", "not clear"])

    def test_each_flag_writes_its_word(self):
        for flag, word in (("--seen", "seen"), ("--ok", "ack"), ("--applies", "applies"), ("--unclear", "unclear")):
            row = self.ack("beta", self.lesson_id, flag)
            self.assertEqual(row["mark"], word)
            self.assertNotIn("none", row)

    def test_none_keeps_its_field_and_gains_the_word(self):
        row = self.ack("beta", self.request_id, "--none")
        self.assertEqual((row["none"], row["mark"]), (True, "none"))

    def test_two_flags_and_no_flag_are_refused(self):
        before = self.log_lines()
        self.assertEqual(self.run_ack("beta", self.lesson_id, "--seen", "--ok").returncode, 2)
        self.assertEqual(self.run_ack("beta", self.lesson_id).returncode, 2)
        self.assertEqual(self.log_lines(), before)

    def test_a_later_mark_replaces_the_earlier_one(self):
        self.ack("beta", self.lesson_id, "--seen")
        self.ack("beta", self.lesson_id, "--applies")
        self.assertEqual(self.marks()[("messages", self.lesson_id)], {"beta": "applies"})

    def test_old_none_row_reads_as_none_and_unknown_word_as_no_mark(self):
        self.assertEqual(marks.mark_of({"none": True}), "none")
        self.assertIsNone(marks.mark_of({"mark": "liked"}))
        self.assertEqual(marks.mark_of({"none": True, "mark": "applies"}), "applies")
        self.assertIsNone(marks.mark_of({}))

    def test_only_none_clears_pending_from(self):
        for flag in ("--seen", "--ok", "--applies", "--unclear"):
            self.ack("beta", self.request_id, flag)
            self.assertIn("beta", self.pending(self.request_id))
        self.ack("beta", self.request_id, "--none")
        self.assertNotIn("beta", self.pending(self.request_id))
        self.ack("beta", self.request_id, "--applies")
        self.assertIn("beta", self.pending(self.request_id))

    def test_none_and_applies_on_one_row_keeps_the_agent_pending(self):
        docs, roster = self.docs()
        docs[("acks", "beta--" + self.request_id)] = {"from": "beta", "item": self.request_id,
                                                      "none": True, "mark": "applies"}
        self.assertEqual(acks_by_item(docs, roster=roster), {})

    def test_owner_question_takes_four_marks_and_refuses_none(self):
        for flag, word in (("--seen", "seen"), ("--ok", "ack"), ("--applies", "applies"), ("--unclear", "unclear")):
            self.assertEqual(self.ack("beta", self.question_id, flag)["mark"], word)
        before = self.log_lines()
        p = self.run_ack("gamma", self.question_id, "--none")
        self.assertEqual(p.returncode, 1)
        self.assertIn("a question to the owner gets an answer, not an ack", p.stderr)
        self.assertEqual(self.log_lines(), before)
        self.assertEqual(self.marks()[("messages", self.question_id)], {"beta": "unclear"})

    def test_own_post_and_unknown_item_are_refused(self):
        before = self.log_lines()
        for pid, item in (("alpha", self.lesson_id), ("alpha", "lesson-" + self.lesson_id),
                          ("gamma", self.decision_id), ("beta", "alpha-nothing")):
            p = self.run_ack(pid, item, "--seen")
            self.assertEqual(p.returncode, 1, (pid, item))
            self.assertTrue(p.stderr.strip(), (pid, item))
        self.assertEqual(self.log_lines(), before)

    def test_hand_appended_mark_on_own_post_shows_nowhere(self):
        for pid, item in (("alpha", self.lesson_id), ("gamma", self.decision_id), ("beta", "alpha-nothing")):
            p = self.run_cli("append", "--from", pid, "--op", "set", "--collection", "acks",
                             "--id", pid + "--" + item,
                             "--data", json.dumps({"from": pid, "item": item, "mark": "ack"}))
            self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.marks(), {})

    def test_unapproved_writer_counts_for_nothing(self):
        self.ack("delta", self.lesson_id, "--ok")
        self.assertEqual(self.marks(), {})

    def test_wrong_typed_fields_count_for_nothing(self):
        docs, roster = self.docs()
        docs[("acks", "beta--" + self.lesson_id)] = {"from": "beta", "item": self.lesson_id, "mark": []}
        docs[("acks", "beta--x")] = {"from": "beta", "item": {}, "mark": "seen"}
        docs[("acks", "3--" + self.lesson_id)] = {"from": 3, "item": self.lesson_id, "mark": "seen"}
        docs[("acks", "gamma--" + self.lesson_id)] = ["gamma", self.lesson_id]
        self.assertEqual(marks.marks_by_item(docs, roster=roster), {})
        self.assertEqual(acks_by_item(docs, roster=roster), {})
        self.assertIsNone(marks.item_ref(docs, {}))
        before = self.log_lines()
        for mark in ([], "liked", 3):
            p = self.run_cli("append", "--from", "beta", "--op", "set", "--collection", "acks",
                             "--id", "beta--" + self.lesson_id,
                             "--data", json.dumps({"from": "beta", "item": self.lesson_id, "mark": mark}))
            self.assertEqual(p.returncode, 1, mark)
        self.assertEqual(self.log_lines(), before)

    def test_lesson_row_shares_the_marks_of_its_message(self):
        lesson = "lesson-" + self.lesson_id
        self.ack("beta", lesson, "--ok")
        docs, _roster = self.docs()
        self.assertEqual(marks.item_ref(docs, lesson), ("messages", self.lesson_id))
        self.assertEqual(self.marks(), {("messages", self.lesson_id): {"beta": "ack"}})
        # A lesson row with no message of its id stays a lesson.
        self.ack("gamma", "lesson-solo", "--applies")
        self.assertEqual(self.marks()[("lessons", "lesson-solo")], {"gamma": "applies"})
        self.assertEqual(self.run_ack("owner", "lesson-solo", "--seen").returncode, 1)

    def test_decision_row_takes_a_mark_and_its_writer_cannot_mark_it(self):
        docs, _roster = self.docs()
        self.assertEqual(marks.writer_of(docs, "decisions", self.decision_id), "gamma")
        self.assertEqual(marks.writer_of(docs, "messages", self.lesson_id), "alpha")
        self.ack("beta", self.decision_id, "--applies")
        self.assertEqual(self.marks(), {("decisions", self.decision_id): {"beta": "applies"}})
        p = self.run_ack("gamma", self.decision_id, "--ok")
        self.assertEqual(p.returncode, 1)

    def test_none_stays_for_requests_only(self):
        for item in (self.lesson_id, self.decision_id):
            self.assertEqual(self.run_ack("beta", item, "--none").returncode, 1, item)

    def test_page_data_keeps_its_shape(self):
        docs, _roster = self.docs()
        self.assertEqual(json.loads(json.dumps({"%s/%s" % k: v for k, v in docs.items()}))["decisions/gamma-d1"],
                         {"by": "beta", "summary": "use x", "t": "2026-10-21T18:00:00Z"})


if __name__ == "__main__":
    unittest.main()
