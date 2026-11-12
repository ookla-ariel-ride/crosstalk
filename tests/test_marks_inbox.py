"""Marks on a post: the inbox sections to_read and to_read_open, and the owner messages
that an acknowledged mark takes out."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TempRepo, line
from test_marks_expect import ROSTER, SINCE

REPO = Path(__file__).resolve().parent.parent
EARLY = "2026-10-21T17:00:00Z"
LATER = "2026-10-21T19:00:00Z"


def msg(seq, frm, id_, t=SINCE, **data):
    return line(seq, frm, id_=id_, data=dict(data, **{"from": frm}), t=t)


class MarksInboxBase(unittest.TestCase):
    lesson_id = "alpha-l1"

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        self.r.write_config({"marks_since": SINCE})
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")
        # A first row for each reader, so no broadcast is older than its first row.
        for pid in ("beta", "gamma", "delta"):
            self.r.write_log(pid, [msg(1, pid, pid + "-c0", kind="sync", summary="hello", t=EARLY)])
        self.alpha = [msg(1, "alpha", self.lesson_id, kind="lesson", summary="a rule", body="the rule in full")]
        self.r.write_log("alpha", self.alpha)

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def ok(self, *args):
        p = self.run_cli(*args)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def inbox(self, pid, *args):
        return json.loads(self.ok("inbox", pid, *args))

    def ack(self, pid, item, flag):
        self.ok("ack", "--from", pid, item, flag)

    def add_alpha(self, *rows):
        self.alpha += list(rows)
        self.r.write_log("alpha", self.alpha)


class ToReadTests(MarksInboxBase):
    def ids(self, out, key="to_read"):
        return [i["id"] for i in out[key]]

    def test_new_lesson_after_the_place_is_in_to_read_with_its_body(self):
        out = self.inbox("beta", "--new")
        self.assertEqual(self.ids(out), [self.lesson_id])
        item = out["to_read"][0]
        for key in ("id", "from", "t", "kind", "summary", "body"):
            self.assertIn(key, item)
        self.assertEqual((item["from"], item["kind"], item["body"]), ("alpha", "lesson", "the rule in full"))
        self.assertEqual(out["to_read_open"], [])

    def test_after_the_ack_with_no_mark_it_moves_to_to_read_open_without_its_body(self):
        self.inbox("beta", "--new", "--ack")
        out = self.inbox("beta", "--new")
        self.assertEqual(out["to_read"], [])
        self.assertEqual(out["to_read_open"], [{"id": self.lesson_id, "from": "alpha", "t": SINCE,
                                                "summary": "a rule"}])

    def test_seen_and_unclear_keep_it_open_and_ok_takes_it_out(self):
        self.inbox("beta", "--new", "--ack")
        for flag in ("--seen", "--unclear"):
            self.ack("beta", self.lesson_id, flag)
            self.assertEqual(self.ids(self.inbox("beta", "--new"), "to_read_open"), [self.lesson_id], flag)
        self.ack("beta", self.lesson_id, "--ok")
        out = self.inbox("beta", "--new")
        self.assertEqual((out["to_read"], out["to_read_open"]), ([], []))
        self.assertEqual(self.inbox("beta")["to_read"], [])

    def test_a_mark_before_the_first_read_keeps_it_out_of_to_read(self):
        self.ack("beta", self.lesson_id, "--applies")
        self.assertEqual(self.inbox("beta", "--new")["to_read"], [])

    def test_without_new_every_owed_post_is_in_to_read(self):
        self.add_alpha(msg(2, "alpha", "alpha-c2", kind="check-in", summary="read this", must_read=True, t=LATER))
        self.inbox("beta", "--ack")
        out = self.inbox("beta")
        self.assertEqual(sorted(self.ids(out)), ["alpha-c2", self.lesson_id])
        self.assertTrue(all("body" in i for i in out["to_read"]))
        self.assertNotIn("to_read_open", out)

    def test_decision_and_lesson_rows_are_in_to_read(self):
        self.r.write_log("owner", [
            line(1, "owner", collection="decisions", id_="owner-d1", data={"by": "owner", "summary": "x", "body": "b"}),
            line(2, "owner", collection="lessons", id_="lesson-solo", data={"project": "owner", "summary": "y"}),
            line(3, "owner", collection="lessons", id_="lesson-" + self.lesson_id,
                 data={"project": "alpha", "summary": "a rule"}),
        ])
        items = {i["id"]: i for i in self.inbox("beta")["to_read"]}
        self.assertEqual(sorted(items), [self.lesson_id, "lesson-solo", "owner-d1"])
        self.assertEqual((items["owner-d1"]["kind"], items["owner-d1"]["from"]), ("decision", "owner"))
        self.assertEqual(items["lesson-solo"]["kind"], "lesson")

    def sections(self, out):
        """Each id the output shows, with the sections that show it."""
        where = {}
        for key, field in (("requests", "request"), ("owner_messages", "message"),
                           ("thread_replies", "message")):
            for item in out.get(key, []):
                where.setdefault(item[field]["id"], []).append(key)
        for key in ("still_open", "to_read", "to_read_open"):
            for item in out.get(key, []):
                where.setdefault(item["id"], []).append(key)
        return where

    def assert_once(self, out):
        twice = {k: v for k, v in self.sections(out).items() if len(v) > 1}
        self.assertEqual(twice, {})

    def test_a_request_shown_in_requests_is_not_in_to_read(self):
        self.add_alpha(msg(2, "alpha", "alpha-r1", kind="request", summary="do it", to=["beta"],
                           must_read=True, t=LATER))
        out = self.inbox("beta", "--new")
        self.assertEqual([i["request"]["id"] for i in out["requests"]], ["alpha-r1"])
        self.assertEqual(self.ids(out), [self.lesson_id])
        self.assert_once(out)
        self.assertEqual(self.ids(self.inbox("beta")), [self.lesson_id])
        self.inbox("beta", "--ack")
        out = self.inbox("beta", "--new")
        self.assertEqual(self.sections(out)["alpha-r1"], ["still_open"])

    def test_a_request_that_needs_no_answer_is_in_to_read(self):
        self.add_alpha(msg(2, "alpha", "alpha-r1", kind="request", summary="fyi", to=["beta"],
                           must_read=True, needs_answer=False, t=LATER))
        out = self.inbox("beta")
        self.assertEqual(out["requests"], [])
        self.assertIn("alpha-r1", self.ids(out))
        self.assertEqual(self.sections(self.inbox("beta", "--new"))["alpha-r1"], ["to_read"])
        self.inbox("beta", "--ack")
        self.assertEqual(self.sections(self.inbox("beta", "--new"))["alpha-r1"], ["to_read_open"])
        all_out = self.inbox("beta", "--all")
        self.assertEqual(self.sections(all_out)["alpha-r1"], ["requests"])

    def test_a_request_answered_by_the_reader_is_in_to_read(self):
        self.add_alpha(msg(2, "alpha", "alpha-r1", kind="request", summary="do it", to=["beta"],
                           must_read=True, t=LATER))
        self.ok("reply", "--from", "beta", "--to-id", "alpha-r1", "--summary", "done")
        out = self.inbox("beta")
        self.assertEqual(out["requests"], [])
        self.assertIn("alpha-r1", self.ids(out))
        self.assert_once(self.inbox("beta", "--new"))

    def test_an_owner_broadcast_shows_in_owner_messages_only(self):
        self.r.write_log("owner", [msg(1, "owner", "owner-b1", kind="owner", summary="to all", t=LATER)])
        out = self.inbox("beta")
        self.assertEqual(self.sections(out)["owner-b1"], ["owner_messages"])
        out = self.inbox("beta", "--new", "--ack")
        self.assertEqual(self.sections(out)["owner-b1"], ["owner_messages"])
        out = self.inbox("beta", "--new")
        self.assertEqual(self.sections(out)["owner-b1"], ["still_open"])
        self.ack("beta", "owner-b1", "--seen")
        self.assertEqual(self.sections(self.inbox("beta", "--new"))["owner-b1"], ["still_open"])
        self.ack("beta", "owner-b1", "--ok")
        self.assertNotIn("owner-b1", self.sections(self.inbox("beta", "--new")))
        self.assertNotIn("owner-b1", self.sections(self.inbox("beta")))

    def test_an_owner_note_that_needs_no_answer_goes_to_to_read_open_after_the_place(self):
        self.r.write_log("owner", [msg(1, "owner", "owner-b1", kind="owner", summary="to all",
                                       needs_answer=False, t=LATER)])
        self.inbox("beta", "--ack")
        self.assertEqual(self.sections(self.inbox("beta", "--new"))["owner-b1"], ["to_read_open"])

    def test_a_must_read_reply_on_my_thread_shows_once(self):
        self.r.write_log("beta", [msg(1, "beta", "beta-c0", kind="sync", summary="hello", t=EARLY),
                                  msg(2, "beta", "beta-q1", kind="sync", summary="a note", t=SINCE)])
        self.add_alpha(msg(2, "alpha", "alpha-a1", kind="reply", summary="read this", replyTo="beta-q1",
                           to=["beta"], must_read=True, t=LATER))
        out = self.inbox("beta")
        self.assertEqual(self.sections(out)["alpha-a1"], ["thread_replies"])
        self.inbox("beta", "--ack")
        self.ok("append", "--from", "beta", "--op", "set", "--collection", "messages", "--id", "beta-c9",
                "--data", json.dumps({"from": "beta", "kind": "check-in", "summary": "check-in"}))
        self.assertEqual(self.sections(self.inbox("beta", "--new"))["alpha-a1"], ["to_read_open"])

    def test_the_cap_of_to_read_open_keeps_the_newest_and_counts_the_rest(self):
        self.add_alpha(*[msg(n + 2, "alpha", "alpha-x%02d" % n, kind="lesson", summary="r%d" % n,
                             t="2026-10-21T18:%02d:00Z" % (n + 1)) for n in range(25)])
        self.inbox("beta", "--ack")
        out = self.inbox("beta", "--new")
        self.assertEqual(self.ids(out, "to_read_open"), ["alpha-x%02d" % n for n in range(24, 4, -1)])
        self.assertEqual(out["to_read_open_more"], 6)
        self.assertIn("to read: 26", self.ok("inbox", "beta", "--new", "--brief").splitlines()[-1])

    def test_a_message_that_gains_its_lessons_row_after_the_place_is_new(self):
        self.add_alpha(msg(2, "alpha", "alpha-n1", kind="sync", summary="a note", t=SINCE))
        self.inbox("beta", "--ack")
        self.assertNotIn("alpha-n1", self.sections(self.inbox("beta", "--new")))
        self.r.write_log("owner", [line(1, "owner", collection="lessons", id_="lesson-alpha-n1",
                                        data={"project": "alpha", "summary": "n"}, t=LATER)])
        out = self.inbox("beta", "--new")
        self.assertEqual(self.sections(out)["alpha-n1"], ["to_read"])
        self.assertNotIn("lesson-alpha-n1", self.sections(out))

    def test_wrong_typed_rows_fail_nothing_and_count_for_nothing(self):
        self.add_alpha(msg(2, "alpha", "alpha-w1", kind="lesson", summary=5, body=["x"], to="beta", t=LATER))
        self.r.write_log("owner", [
            line(1, "owner", collection="decisions", id_="owner-d9", data={"by": "owner", "summary": {}, "t": 7},
                 t=LATER)])
        self.r.write_log("gamma", [msg(1, "gamma", "gamma-c0", kind="sync", summary="hello", t=EARLY),
                                   line(2, "gamma", collection="acks", id_="gamma--" + self.lesson_id,
                                        data={"from": "gamma", "item": self.lesson_id, "mark": "ack"}, t=LATER),
                                   line(3, "gamma", collection="acks", id_="gamma--" + self.lesson_id, op="update",
                                        data={"mark": []}, t=LATER)])
        for args in ((), ("--new",), ("--brief",), ("--new", "--brief")):
            self.ok("inbox", "gamma", *args)
        self.assertIn(self.lesson_id, self.ids(self.inbox("gamma")))
        self.ok("marks", "--missing")
        self.ok("marks", "--missing", "--older", "1", "--json")
        self.ok("marks", "alpha-w1")
        self.assertNotIn("gamma", json.loads(self.ok("marks", self.lesson_id, "--json"))["marks"])


class NotifyTests(MarksInboxBase):
    def test_a_marked_owner_message_no_longer_wakes_the_session(self):
        from crosstalk import notify
        from crosstalk.participants import load_roster
        from crosstalk.replay import enrich_messages, load_all_lines, replay
        self.r.write_log("owner", [msg(1, "owner", "owner-n1", kind="owner", summary="note",
                                       to=["beta", "gamma"], t=LATER)])

        def items(pid):
            roster = load_roster(self.r.root / "participants.json")
            lines = load_all_lines(self.r.root, roster)
            return notify.actionable_items(enrich_messages(replay(lines), lines), pid, roster)

        self.assertIn("owner-n1", items("beta"))
        self.ack("beta", "owner-n1", "--seen")
        self.assertIn("owner-n1", items("beta"))
        self.ack("beta", "owner-n1", "--ok")
        self.assertNotIn("owner-n1", items("beta"))
        self.assertIn("owner-n1", items("gamma"))

    def test_the_sender_does_not_get_its_own_post(self):
        self.assertEqual(self.inbox("alpha")["to_read"], [])
        self.assertEqual(self.inbox("alpha", "--new")["to_read"], [])

    def test_no_marks_since_means_no_to_read(self):
        self.r.write_config({})
        self.assertEqual(self.inbox("beta")["to_read"], [])

    def test_brief_prints_one_line_per_item_and_the_count(self):
        self.add_alpha(msg(2, "alpha", "alpha-c2", kind="check-in", summary="read this", must_read=True, t=LATER))
        lines = self.ok("inbox", "beta", "--new", "--brief").splitlines()
        read = [ln for ln in lines if " read alpha " in ln]
        self.assertEqual(len(read), 2, lines)
        self.assertTrue(any(self.lesson_id + ": a rule" in ln for ln in read))
        self.assertIn("to read: 2", lines[-1])
        self.ok("inbox", "beta", "--ack")
        lines = self.ok("inbox", "beta", "--new", "--brief").splitlines()
        self.assertEqual([ln for ln in lines if " read alpha " in ln], [])
        self.assertIn("to read: 2", lines[-1])
        lines = self.ok("inbox", "beta", "--brief").splitlines()
        self.assertEqual(len([ln for ln in lines if " read alpha " in ln]), 2)
        self.assertIn("to read: 2", lines[-1])


class OwnerMessageMarkTests(MarksInboxBase):
    """An acknowledged mark takes an owner message that is not a request out of
    owner_messages and still_open, for the participant that set it."""

    def setUp(self):
        super().setUp()
        self.r.write_log("owner", [
            msg(1, "owner", "owner-n1", kind="owner", summary="note", to=["beta", "gamma"], t=LATER),
            msg(2, "owner", "owner-r1", kind="request", summary="do it", to=["beta"], t=LATER),
        ])

    def owner_ids(self, pid, *args):
        return [i["message"]["id"] for i in self.inbox(pid, *args)["owner_messages"]]

    def still_open(self, pid):
        return [i["id"] for i in self.inbox(pid, "--new")["still_open"]]

    def test_ok_takes_it_out_for_the_marker_only(self):
        self.inbox("beta", "--ack")
        self.inbox("gamma", "--ack")
        self.assertEqual(self.still_open("beta"), ["owner-r1", "owner-n1"])
        self.ack("beta", "owner-n1", "--ok")
        self.assertEqual(self.owner_ids("beta"), [])
        self.assertEqual(self.still_open("beta"), ["owner-r1"])
        self.assertEqual(self.owner_ids("gamma"), ["owner-n1"])
        self.assertEqual(self.still_open("gamma"), ["owner-n1"])

    def test_applies_and_none_take_it_out_too(self):
        self.ack("beta", "owner-n1", "--applies")
        self.assertEqual(self.owner_ids("beta"), [])
        self.ack("gamma", "owner-n1", "--none")
        self.assertEqual(self.owner_ids("gamma"), [])

    def test_seen_and_unclear_keep_it(self):
        self.inbox("beta", "--ack")
        for flag in ("--seen", "--unclear"):
            self.ack("beta", "owner-n1", flag)
            self.assertEqual(self.owner_ids("beta"), ["owner-n1"], flag)
            self.assertIn("owner-n1", self.still_open("beta"), flag)

    def test_ok_does_not_clear_an_owner_request(self):
        self.inbox("beta", "--ack")
        self.ack("beta", "owner-r1", "--ok")
        out = self.inbox("beta")
        self.assertEqual([i["request"]["id"] for i in out["requests"]], ["owner-r1"])
        self.assertEqual(out["requests"][0]["pending_from"], ["beta"])
        self.assertIn("owner-r1", self.still_open("beta"))

    def test_an_owner_answer_marked_ok_does_not_come_back_as_a_thread_reply(self):
        self.r.write_log("beta", [msg(1, "beta", "beta-c0", kind="sync", summary="hello", t=EARLY),
                                  msg(2, "beta", "beta-q1", kind="sync", summary="a note", t=SINCE)])
        self.r.write_log("owner", [msg(1, "owner", "owner-a1", kind="owner", summary="yes",
                                       replyTo="beta-q1", to=["beta"], t=LATER)])
        out = self.inbox("beta")
        self.assertEqual([i["message"]["id"] for i in out["owner_messages"]], ["owner-a1"])
        self.ack("beta", "owner-a1", "--ok")
        out = self.inbox("beta")
        self.assertEqual(out["owner_messages"], [])
        self.assertEqual(out["thread_replies"], [])


if __name__ == "__main__":
    unittest.main()
