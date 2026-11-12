"""Marks on a post: which posts expect a mark, and who still owes one."""
import copy
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

from crosstalk import config as config_mod
from crosstalk import marks
from crosstalk.participants import load_roster
from crosstalk.replay import enrich_messages, load_all_lines, replay

REPO = Path(__file__).resolve().parent.parent

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["relay"] = "relay-bot"
ROSTER["participants"] += [
    {"id": "gamma", "name": "Gamma", "kind": "project", "color": "#2e7d32", "colorDark": "#81c784",
     "checkInMin": 30, "trust": "approved"},
    {"id": "delta", "name": "Delta", "kind": "project", "color": "#5d4037", "colorDark": "#a1887f",
     "checkInMin": 30, "trust": "approved"},
    {"id": "relay-bot", "name": "Relay bot", "kind": "tool", "color": "#e65100", "colorDark": "#ffb74d",
     "trust": "approved"},
    {"id": "eps", "name": "Eps", "kind": "project", "color": "#c2185b", "colorDark": "#f48fb1",
     "checkInMin": 30, "trust": "quarantined"},
]
SINCE = "2026-10-21T18:00:00Z"
PROJECTS = ["alpha", "beta", "delta", "gamma"]


def msg(seq, frm, id_, **data):
    return line(seq, frm, id_=id_, data=dict(data, **{"from": frm}))


class ExpectTests(unittest.TestCase):
    lesson_id = "alpha-l1"

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")
        self.r.write_log("alpha", [
            msg(1, "alpha", self.lesson_id, kind="lesson", summary="a rule"),
            msg(2, "alpha", "alpha-c1", kind="check-in", summary="working on x"),
            msg(3, "alpha", "alpha-c2", kind="check-in", summary="read this", must_read=True),
            msg(4, "alpha", "alpha-n1", kind="sync", summary="a note that became a lesson"),
        ])
        self.r.write_log("owner", [
            line(1, "owner", collection="lessons", id_="lesson-" + self.lesson_id,
                 data={"project": "alpha", "summary": "a rule"}),
            line(2, "owner", collection="lessons", id_="lesson-solo", data={"project": "owner", "summary": "x"}),
            line(3, "owner", collection="lessons", id_="lesson-alpha-n1", data={"project": "alpha", "summary": "n"}),
            msg(4, "owner", "owner-all", kind="owner", summary="to all"),
            msg(5, "owner", "owner-ans", kind="reply", summary="yes", replyTo="alpha-c1", to=["alpha"]),
            msg(6, "owner", "owner-ans2", kind="reply", summary="yes", replyTo="alpha-c1", to=["alpha"],
                must_read=True),
            msg(7, "owner", "owner-some", kind="owner", summary="two", to=["alpha", "rev"]),
            msg(8, "owner", "owner-every", kind="owner", summary="every", to=PROJECTS + ["rev"]),
            line(9, "owner", collection="decisions", id_="owner-d1", data={"by": "owner", "summary": "x"}),
        ])
        self.r.write_log("relay-bot", [line(1, "relay-bot", collection="decisions", id_="relay-bot-d1",
                                            data={"by": "relay-bot", "summary": "y"})])
        self.r.write_log("beta", [line(1, "beta", collection="decisions", id_="beta-d1",
                                       data={"by": "beta", "summary": "z"})])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def roster(self):
        return load_roster(self.r.root / "participants.json")

    def docs(self):
        lines = load_all_lines(self.r.root, self.roster())
        return enrich_messages(replay(lines), lines)

    def config(self, since=SINCE):
        return {"marks_since": since}

    def ack(self, pid, item, *flags):
        p = self.run_cli("ack", "--from", pid, item, *flags)
        self.assertEqual(p.returncode, 0, p.stderr)

    def expects(self, coll, did, config=None):
        return marks.expects_mark(self.docs(), coll, did, self.roster(), config or self.config())

    # Rule 1: lessons.
    def test_a_lesson_message_expects(self):
        self.assertTrue(self.expects("messages", self.lesson_id))

    def test_a_lessons_row_expects_only_when_it_has_no_message(self):
        self.assertTrue(self.expects("lessons", "lesson-solo"))
        self.assertFalse(self.expects("lessons", "lesson-" + self.lesson_id))
        self.assertTrue(self.expects("messages", self.lesson_id))

    def test_a_message_with_a_lessons_row_expects_whatever_its_kind(self):
        self.assertTrue(self.expects("messages", "alpha-n1"))
        self.assertFalse(self.expects("lessons", "lesson-alpha-n1"))

    # Rule 2: rulings.
    def test_rulings_of_the_coordinator_and_the_owner_expect(self):
        self.assertTrue(self.expects("decisions", "relay-bot-d1"))
        self.assertTrue(self.expects("decisions", "owner-d1"))
        self.assertFalse(self.expects("decisions", "beta-d1"))

    def test_a_decision_with_no_known_writer_expects_none(self):
        bare = replay(load_all_lines(self.r.root, self.roster()))
        self.assertFalse(marks.expects_mark(bare, "decisions", "owner-d1", self.roster(), self.config()))

    # Rule 3: the owner's posts.
    def test_owner_posts_to_all_expect(self):
        self.assertTrue(self.expects("messages", "owner-all"))
        self.assertTrue(self.expects("messages", "owner-every"))
        self.assertFalse(self.expects("messages", "owner-some"))

    def test_owner_answer_expects_only_with_must_read(self):
        self.assertFalse(self.expects("messages", "owner-ans"))
        self.assertTrue(self.expects("messages", "owner-ans2"))

    # Rule 4: must_read.
    def test_check_in_expects_only_with_must_read(self):
        self.assertFalse(self.expects("messages", "alpha-c1"))
        self.assertTrue(self.expects("messages", "alpha-c2"))

    # The start time.
    def test_a_post_one_second_before_marks_since_expects_none(self):
        self.assertTrue(self.expects("messages", self.lesson_id, self.config("2026-10-21T18:00:00Z")))
        self.assertFalse(self.expects("messages", self.lesson_id, self.config("2026-10-21T18:00:01Z")))
        self.assertFalse(self.expects("lessons", "lesson-solo", self.config("2026-10-21T18:00:01Z")))

    def test_an_update_after_marks_since_keeps_an_old_post_old(self):
        later = "2026-10-21T19:00:00Z"
        cfg = self.config("2026-10-21T18:30:00Z")
        self.r.write_log("alpha", [
            msg(1, "alpha", self.lesson_id, kind="lesson", summary="a rule"),
            msg(2, "alpha", "alpha-c1", kind="check-in", summary="working on x"),
            msg(3, "alpha", "alpha-c2", kind="check-in", summary="read this", must_read=True),
            msg(4, "alpha", "alpha-n1", kind="sync", summary="a note that became a lesson"),
            msg(5, "alpha", "alpha-l2", kind="lesson", summary="old lesson"),
            line(6, "alpha", id_="alpha-l2", op="update", data={"summary": "old lesson, edited"}, t=later),
            msg(7, "alpha", "alpha-l3", kind="lesson", summary="old lesson", t="2026-10-21T17:00:00Z"),
            line(8, "alpha", id_="alpha-l3", op="update", data={"summary": "edited"}, t=later),
        ])
        docs = self.docs()
        self.assertEqual(docs[("messages", "alpha-l2")]["t"], later)
        for did in ("alpha-l2", "alpha-l3"):
            self.assertFalse(self.expects("messages", did, cfg), did)
        old = {"alpha-l2", "alpha-l3"}
        self.assertFalse(old & {i["id"] for i in marks.to_read(docs, "beta", self.roster(), cfg)})
        self.assertFalse(old & {i["id"] for i in marks.missing(docs, self.roster(), cfg)})
        self.assertTrue(self.expects("messages", "alpha-l2", self.config()))

    def test_to_read_and_missing_parse_marks_since_once(self):
        from unittest import mock
        docs, roster = self.docs(), self.roster()
        real = marks.marks_since
        for fn in (lambda cfg: marks.to_read(docs, "beta", roster, cfg), lambda cfg: marks.missing(docs, roster, cfg)):
            with mock.patch.object(marks, "marks_since", side_effect=real) as spy:
                self.assertTrue(fn(self.config()))
            self.assertEqual(spy.call_count, 1)

    def test_a_must_read_post_expects_whatever_marks_since_holds(self):
        later = self.config("2026-10-21T18:00:01Z")
        self.assertTrue(self.expects("messages", "alpha-c2", later))
        self.assertTrue(self.expects("messages", "owner-ans2", later))
        self.assertFalse(self.expects("messages", "owner-all", later))

    def test_no_key_and_a_malformed_key_mean_only_must_read_posts_expect(self):
        for cfg in ({}, {"marks_since": ""}, {"marks_since": "soon"}, {"marks_since": 7}, None):
            self.assertIsNone(marks.marks_since(cfg), cfg)
            self.assertFalse(marks.expects_mark(self.docs(), "messages", self.lesson_id, self.roster(), cfg), cfg)
            self.assertEqual(marks.owed(self.docs(), "messages", self.lesson_id, self.roster(), cfg), [], cfg)
            self.assertTrue(marks.expects_mark(self.docs(), "messages", "alpha-c2", self.roster(), cfg), cfg)
            self.assertEqual(marks.owed(self.docs(), "messages", "alpha-c2", self.roster(), cfg),
                             ["beta", "delta", "gamma"], cfg)
        self.assertEqual(marks.marks_since({"marks_since": " 2026-10-21 "}), "2026-10-21")

    def test_config_json_carries_the_key(self):
        self.assertIsNone(marks.marks_since(config_mod.of(self.roster())))
        self.r.write_config({"marks_since": SINCE})
        self.assertEqual(marks.marks_since(config_mod.of(self.roster())), SINCE)
        self.r.write_config({"marks_since": 7})
        self.assertIsNone(marks.marks_since(config_mod.of(self.roster())))

    def test_check_warns_on_a_malformed_key_and_passes(self):
        for bad in ("soon", 7):
            self.r.write_config({"marks_since": bad})
            p = self.run_cli("check")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("warning: config.json: marks_since", p.stdout)
        self.r.write_config({"marks_since": SINCE})
        p = self.run_cli("check")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("marks_since", p.stdout)

    # Who must mark.
    def test_markers_are_approved_projects_and_agents_but_not_the_writer_or_owner(self):
        docs = self.docs()
        self.assertEqual(marks.markers(docs, "messages", self.lesson_id, self.roster()), ["beta", "delta", "gamma"])
        roster = {
            "alpha": {"kind": "project", "trust": "approved"},
            "owner": {"kind": "project", "trust": "approved"},
            "kid": {"kind": "agent", "trust": "approved"},
            "rev": {"kind": "reviewer", "trust": "approved"},
            "sage": {"kind": "researcher", "trust": "approved"},
            "relay-bot": {"kind": "tool", "trust": "approved"},
            "pat": {"kind": "person", "trust": "approved"},
            "eps": {"kind": "project", "trust": "quarantined"},
        }
        self.assertEqual(marks.markers(docs, "messages", self.lesson_id, roster), ["kid"])

    def test_markers_keep_only_the_to_names(self):
        self.assertEqual(marks.markers(self.docs(), "messages", "owner-some", self.roster()), ["alpha"])

    # Who owes.
    def test_owed_keeps_seen_and_unclear(self):
        self.ack("beta", self.lesson_id, "--seen"); self.ack("gamma", self.lesson_id, "--ok")
        v = marks.view(self.docs(), "messages", self.lesson_id, self.roster(), self.config())
        self.assertEqual(v, {"marks": {"beta": "seen", "gamma": "ack"}, "owed": ["beta", "delta"], "expects": True})

    def test_owed_drops_ack_applies_and_none_and_keeps_unclear(self):
        self.ack("beta", self.lesson_id, "--ok")
        self.ack("gamma", self.lesson_id, "--applies")
        self.ack("delta", self.lesson_id, "--unclear")
        self.assertEqual(marks.owed(self.docs(), "messages", self.lesson_id, self.roster(), self.config()), ["delta"])
        self.ack("delta", self.lesson_id, "--none")
        self.assertEqual(marks.owed(self.docs(), "messages", self.lesson_id, self.roster(), self.config()), [])

    def test_a_mark_on_the_lessons_row_counts_for_its_message(self):
        self.ack("beta", "lesson-" + self.lesson_id, "--ok")
        v = marks.view(self.docs(), "messages", self.lesson_id, self.roster(), self.config())
        self.assertEqual(v["owed"], ["delta", "gamma"])
        v = marks.view(self.docs(), "lessons", "lesson-" + self.lesson_id, self.roster(), self.config())
        self.assertEqual(v, {"marks": {"beta": "ack"}, "owed": [], "expects": False})

    def test_an_agent_that_left_the_roster_shows_nowhere(self):
        self.ack("beta", self.lesson_id, "--seen")
        self.ack("gamma", self.lesson_id, "--ok")
        left = copy.deepcopy(ROSTER)
        left["participants"] = [p for p in left["participants"] if p["id"] != "beta"]
        for p in left["participants"]:
            if p["id"] == "gamma":
                p["trust"] = "quarantined"
        self.r.write_roster(left)
        v = marks.view(self.docs(), "messages", self.lesson_id, self.roster(), self.config())
        self.assertEqual(v, {"marks": {}, "owed": ["delta"], "expects": True})

    def test_a_post_that_expects_none_owes_nothing_but_shows_its_marks(self):
        self.ack("beta", "alpha-c1", "--seen")
        v = marks.view(self.docs(), "messages", "alpha-c1", self.roster(), self.config())
        self.assertEqual(v, {"marks": {"beta": "seen"}, "owed": [], "expects": False})

    def test_unknown_post_expects_none(self):
        self.assertEqual(marks.view(self.docs(), "messages", "nope", self.roster(), self.config()),
                         {"marks": {}, "owed": [], "expects": False})

    # The flag --must-read.
    def test_append_must_read_sets_the_field_on_a_new_message_only(self):
        p = self.run_cli("append", "--from", "beta", "--op", "set", "--collection", "messages", "--id", "beta-c1",
                         "--data", json.dumps({"from": "beta", "kind": "check-in", "summary": "s"}), "--must-read")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIs(self.docs()[("messages", "beta-c1")]["must_read"], True)
        self.assertTrue(self.expects("messages", "beta-c1"))
        p = self.run_cli("append", "--from", "beta", "--op", "set", "--collection", "decisions", "--id", "beta-d2",
                         "--data", json.dumps({"summary": "s"}), "--must-read")
        self.assertEqual(p.returncode, 1)
        self.assertIn("--must-read is for a new message", p.stderr)

    def test_reply_must_read_sets_the_field(self):
        p = self.run_cli("reply", "--from", "beta", "--to-id", "alpha-c1", "--summary", "noted", "--must-read")
        self.assertEqual(p.returncode, 0, p.stderr)
        mid = p.stdout.strip()
        self.assertIs(self.docs()[("messages", mid)]["must_read"], True)
        self.assertTrue(self.expects("messages", mid))
        p = self.run_cli("reply", "--from", "beta", "--to-id", "alpha-c1", "--summary", "plain")
        self.assertNotIn("must_read", self.docs()[("messages", p.stdout.strip())])

    def test_ask_refuses_must_read(self):
        p = self.run_cli("ask", "--from", "beta", "--summary", "Which?", "--must-read")
        self.assertEqual(p.returncode, 1)
        self.assertIn("--must-read", p.stderr)


if __name__ == "__main__":
    unittest.main()
