import multiprocessing
import time
import unittest

from helpers import TempRepo
from append_worker import worker
from crosstalk.logs import append_line, message_problems, read_log
from crosstalk.participants import load_roster


class AppendTests(unittest.TestCase):
    def test_first_line_is_seq_1(self):
        with TempRepo() as r:
            ln = append_line(r.root, "codex", "set", "messages", "codex-1", {"body": "hi"})
            self.assertEqual(ln.seq, 1)
            self.assertRegex(ln.t, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_seq_increments(self):
        with TempRepo() as r:
            append_line(r.root, "codex", "set", "messages", "codex-1", {})
            ln = append_line(r.root, "codex", "update", "messages", "codex-1", {"a": 1}, via="x")
            self.assertEqual(ln.seq, 2)
            lines, problems = read_log(r.root / "log" / "codex.jsonl")
            self.assertEqual(problems, [])
            self.assertEqual(lines[1].via, "x")

    def test_creates_log_dir(self):
        with TempRepo() as r:
            (r.root / "log").rmdir()
            append_line(r.root, "codex", "set", "messages", "codex-1", {})
            self.assertTrue((r.root / "log" / "codex.jsonl").exists())

    def test_round_trips_awkward_text(self):
        body = "line1\nline2 \"quoted\" caf\u00e9 \U0001F3AE"
        with TempRepo() as r:
            append_line(r.root, "codex", "set", "messages", "codex-1", {"body": body}, t="2026-10-21T18:00:00Z")
            raw = (r.root / "log" / "codex.jsonl").read_bytes().decode("utf-8")
            self.assertEqual(raw.count("\n"), 1)
            self.assertTrue(raw.endswith("\n"))
            self.assertIn("caf\u00e9 \U0001F3AE", raw)
            lines, problems = read_log(r.root / "log" / "codex.jsonl")
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
            lines, problems = read_log(r.root / "log" / "codex.jsonl")
            self.assertEqual(problems, [])
            self.assertEqual([l.seq for l in lines], list(range(1, 201)))

    def test_rejects_unknown_op(self):
        with TempRepo() as r:
            with self.assertRaises(ValueError):
                append_line(r.root, "codex", "delete", "messages", "codex-1", {})
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())


    def test_rejects_miscased_from_without_touching_disk(self):
        # On APFS, log/Codex.jsonl would open log/codex.jsonl and poison it.
        with TempRepo() as r:
            append_line(r.root, "codex", "set", "messages", "codex-1", {})
            before = (r.root / "log" / "codex.jsonl").read_bytes()
            with self.assertRaises(ValueError):
                append_line(r.root, "Codex", "set", "messages", "Codex-1", {})
            self.assertEqual((r.root / "log" / "codex.jsonl").read_bytes(), before)
            self.assertEqual(sorted(p.name for p in (r.root / "log").iterdir()),
                             ["codex.jsonl"])

    def test_rejects_invalid_from_and_creates_nothing(self):
        for bad in ("Codex", "codex\n", "../x", "a b", ""):
            with TempRepo() as r:
                (r.root / "log").rmdir()
                with self.assertRaises(ValueError, msg=repr(bad)):
                    append_line(r.root, bad, "set", "messages", "x-1", {})
                self.assertFalse((r.root / "log").exists(), repr(bad))

    def test_roster_enforces_message_shape(self):
        with TempRepo() as r:
            ro = load_roster(r.write_roster())
            with self.assertRaises(ValueError):
                append_line(r.root, "codex", "set", "messages", "codex-1", {"body": "x"}, roster=ro)
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())
            with self.assertRaises(ValueError):
                append_line(r.root, "codex", "set", "messages", "codex-1",
                            {"kind": "review", "summary": "ok"}, roster=ro)
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())
            append_line(r.root, "codex", "set", "messages", "codex-1",
                        {"from": "codex", "kind": "review", "summary": "ok"}, roster=ro)
            append_line(r.root, "codex", "set", "lessons", "codex-l", {}, roster=ro)

    def test_skew_measured_both_ways(self):
        with TempRepo() as r:
            ro = load_roster(r.write_roster())
            now = 1792700000.0

            def t(secs):
                return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(secs))

            ok = {"from": "codex", "kind": "reply", "summary": "x"}
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
                append_line(r.root, "codex", "set", "messages", "codex-1",
                            {"from": "codex", "kind": "reply", "summary": "x", "t": future},
                            roster=ro)
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())

if __name__ == "__main__":
    unittest.main()
