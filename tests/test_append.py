import multiprocessing
import time
import unittest

from helpers import TempRepo, line
from append_worker import worker
from crosstalk.logs import append_line, message_problems, read_log
from crosstalk.participants import load_roster


class MessageIdentityTests(unittest.TestCase):
    """Nobody but a message's writer or the owner may change its from, kind or replyTo."""

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        self.r.write_log("alpha", [line(1, "alpha", id_="alpha-q", data={
            "from": "alpha", "kind": "question", "summary": "Ship it?", "to": ["owner"]})])
        self.r.write_log("owner", [line(1, "owner", id_="own-1", data={
            "from": "owner", "kind": "owner", "summary": "A note"})])
        self.roster = load_roster(self.r.root / "participants.json")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def append(self, pid, op, id_, data, **kw):
        return append_line(self.r.root, pid, op, "messages", id_, data, roster=self.roster, **kw)

    def refused(self, pid, op, id_, data):
        with self.assertRaises(ValueError) as e:
            self.append(pid, op, id_, data)
        self.assertIn("only its writer or the owner", str(e.exception))
        self.assertFalse((self.r.root / "log" / (pid + ".jsonl")).exists())

    def test_cannot_hijack_or_close_another_participants_question(self):
        self.refused("beta", "update", "alpha-q", {"from": "beta"})
        self.refused("beta", "update", "alpha-q", {"kind": "reply"})
        self.refused("beta", "update", "alpha-q", {"replyTo": "own-1"})

    def test_cannot_turn_the_owners_row_into_an_answer(self):
        self.refused("beta", "update", "own-1", {"replyTo": "alpha-q"})

    def test_cannot_reopen_a_question_by_deleting_the_answers_reply_to(self):
        self.r.write_log("owner", [line(1, "owner", id_="own-2", data={
            "from": "owner", "kind": "owner", "summary": "Yes", "replyTo": "alpha-q"})])
        self.refused("beta", "update", "own-2", {"replyTo": {"__delete__": True}})

    def test_cannot_set_over_another_participants_message(self):
        self.refused("beta", "set", "own-1", {"from": "beta", "kind": "reply", "summary": "Mine now"})

    def test_an_update_that_changes_nothing_or_other_fields_passes(self):
        self.append("beta", "update", "alpha-q", {"kind": "question", "seen": True})
        self.append("beta", "update", "alpha-q", {"important": True})

    def test_the_writer_may_change_its_own_message(self):
        self.append("alpha", "update", "alpha-q", {"kind": "reply", "replyTo": "own-1"})

    def test_the_owner_keeps_its_powers(self):
        self.append("owner", "update", "alpha-q", {"kind": "reply", "replyTo": "own-1"})

    def test_imported_lines_are_exempt(self):
        self.append("beta", "update", "alpha-q", {"kind": "reply"}, via="import")

    def test_a_new_id_passes(self):
        self.append("beta", "update", "beta-new", {"kind": "reply"})
        self.append("alpha", "set", "alpha-2", {"from": "alpha", "kind": "reply", "summary": "hi"})

    def test_after_an_old_overwrite_the_last_set_is_the_writer(self):
        # m-0177 in the live log: a later `set` from another participant took the id.
        self.r.write_log("alpha", [line(1, "alpha", id_="m-1", t="2026-10-21T18:00:00Z", data={
            "from": "alpha", "kind": "request", "summary": "First"})])
        self.r.write_log("beta", [line(1, "beta", id_="m-1", t="2026-10-21T18:10:00Z", data={
            "from": "beta", "kind": "check-in", "summary": "Second"})])
        self.append("beta", "update", "m-1", {"kind": "sync"})
        with self.assertRaises(ValueError):
            self.append("alpha", "update", "m-1", {"kind": "request"})


class AppendTests(unittest.TestCase):
    def test_first_line_is_seq_1(self):
        with TempRepo() as r:
            ln = append_line(r.root, "rev", "set", "messages", "rev-1", {"body": "hi"})
            self.assertEqual(ln.seq, 1)
            self.assertRegex(ln.t, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_seq_increments(self):
        with TempRepo() as r:
            append_line(r.root, "rev", "set", "messages", "rev-1", {})
            ln = append_line(r.root, "rev", "update", "messages", "rev-1", {"a": 1}, via="x")
            self.assertEqual(ln.seq, 2)
            lines, problems = read_log(r.root / "log" / "rev.jsonl")
            self.assertEqual(problems, [])
            self.assertEqual(lines[1].via, "x")

    def test_creates_log_dir(self):
        with TempRepo() as r:
            (r.root / "log").rmdir()
            append_line(r.root, "rev", "set", "messages", "rev-1", {})
            self.assertTrue((r.root / "log" / "rev.jsonl").exists())

    def test_round_trips_awkward_text(self):
        body = "line1\nline2 \"quoted\" caf\u00e9 \U0001F3AE"
        with TempRepo() as r:
            append_line(r.root, "rev", "set", "messages", "rev-1", {"body": body}, t="2026-10-21T18:00:00Z")
            raw = (r.root / "log" / "rev.jsonl").read_bytes().decode("utf-8")
            self.assertEqual(raw.count("\n"), 1)
            self.assertTrue(raw.endswith("\n"))
            self.assertIn("caf\u00e9 \U0001F3AE", raw)
            lines, problems = read_log(r.root / "log" / "rev.jsonl")
            self.assertEqual(problems, [])
            self.assertEqual(lines[0].data["body"], body)

    def test_concurrent_appends_unique_seq(self):
        with TempRepo() as r:
            procs = [multiprocessing.Process(target=worker, args=(str(r.root), 25)) for _ in range(8)]
            for p in procs:
                p.start()
            for p in procs:
                p.join()
                self.assertEqual(p.exitcode, 0)
            lines, problems = read_log(r.root / "log" / "rev.jsonl")
            self.assertEqual(problems, [])
            self.assertEqual([l.seq for l in lines], list(range(1, 201)))

    def test_rejects_unknown_op(self):
        with TempRepo() as r:
            with self.assertRaises(ValueError):
                append_line(r.root, "rev", "delete", "messages", "rev-1", {})
            self.assertFalse((r.root / "log" / "rev.jsonl").exists())


    def test_rejects_miscased_from_without_touching_disk(self):
        # On APFS, log/Rev.jsonl would open log/rev.jsonl and poison it.
        with TempRepo() as r:
            append_line(r.root, "rev", "set", "messages", "rev-1", {})
            before = (r.root / "log" / "rev.jsonl").read_bytes()
            with self.assertRaises(ValueError):
                append_line(r.root, "Rev", "set", "messages", "Rev-1", {})
            self.assertEqual((r.root / "log" / "rev.jsonl").read_bytes(), before)
            self.assertEqual(sorted(p.name for p in (r.root / "log").iterdir()),
                             ["rev.jsonl"])

    def test_rejects_invalid_from_and_creates_nothing(self):
        for bad in ("Rev", "rev\n", "../x", "a b", ""):
            with TempRepo() as r:
                (r.root / "log").rmdir()
                with self.assertRaises(ValueError, msg=repr(bad)):
                    append_line(r.root, bad, "set", "messages", "x-1", {})
                self.assertFalse((r.root / "log").exists(), repr(bad))

    def test_roster_enforces_message_shape(self):
        with TempRepo() as r:
            ro = load_roster(r.write_roster())
            with self.assertRaises(ValueError):
                append_line(r.root, "rev", "set", "messages", "rev-1", {"body": "x"}, roster=ro)
            self.assertFalse((r.root / "log" / "rev.jsonl").exists())
            with self.assertRaises(ValueError):
                append_line(r.root, "rev", "set", "messages", "rev-1",
                            {"kind": "review", "summary": "ok"}, roster=ro)
            self.assertFalse((r.root / "log" / "rev.jsonl").exists())
            append_line(r.root, "rev", "set", "messages", "rev-1",
                        {"from": "rev", "kind": "review", "summary": "ok"}, roster=ro)
            append_line(r.root, "rev", "set", "lessons", "rev-l", {}, roster=ro)

    def test_skew_measured_both_ways(self):
        with TempRepo() as r:
            ro = load_roster(r.write_roster())
            now = 1792700000.0

            def t(secs):
                return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(secs))

            ok = {"from": "rev", "kind": "reply", "summary": "x"}
            self.assertEqual(message_problems("messages", "set", dict(ok, t=t(now + 90)), ro, now), ([], []))
            (e,) = message_problems("messages", "set", dict(ok, t=t(now + 300)), ro, now)[0]
            self.assertIn("5 min ahead of", e)
            (w,) = message_problems("messages", "set", dict(ok, t=t(now - 300)), ro, now)[1]
            self.assertIn("5 min behind", w)

    def test_a_future_t_is_refused(self):
        with TempRepo() as r:
            ro = load_roster(r.write_roster())
            future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 600))
            with self.assertRaises(ValueError):
                append_line(r.root, "rev", "set", "messages", "rev-1",
                            {"from": "rev", "kind": "reply", "summary": "x", "t": future},
                            roster=ro)
            self.assertFalse((r.root / "log" / "rev.jsonl").exists())

if __name__ == "__main__":
    unittest.main()
