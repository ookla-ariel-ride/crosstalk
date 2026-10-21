import multiprocessing
import unittest

from helpers import TempRepo
from append_worker import worker
from crosstalk.logs import append_line, read_log


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


if __name__ == "__main__":
    unittest.main()
