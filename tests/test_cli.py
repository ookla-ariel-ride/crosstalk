import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TempRepo, line

REPO = Path(__file__).resolve().parent.parent


class Cli:
    """A temp repo root holding a copy of scripts/ and participants.json."""

    def __init__(self, r):
        self.root = r.root
        r.write_roster()
        shutil.copytree(str(REPO / "scripts"), str(r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(r.root / "scripts" / "relay.py")

    def run(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")


class CliTests(unittest.TestCase):
    def test_check_clean_exit_0(self):
        with TempRepo() as r:
            c = Cli(r)
            r.write_log("codex", [line(1, "codex")])
            p = c.run("check")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.strip(), "ok: 1 lines in 1 files")

    def test_check_bad_exit_1_prints_location(self):
        with TempRepo() as r:
            c = Cli(r)
            r.write_log("codex", [line(1, "codex"), "{not json"])
            p = c.run("check")
            self.assertEqual(p.returncode, 1)
            self.assertIn("log/codex.jsonl:2:", p.stdout)

    def test_append_prints_seq(self):
        with TempRepo() as r:
            c = Cli(r)
            p = c.run("append", "--from", "codex", "--op", "set", "--collection", "messages",
                      "--id", "codex-0001", "--data", '{"body": "hi"}')
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "1"), p.stderr)
            p = c.run("append", "--from", "codex", "--op", "set", "--collection", "messages",
                      "--id", "codex-0002", "--data", '{"body": "yo"}')
            self.assertEqual(p.stdout.strip(), "2")
            self.assertEqual(c.run("check").returncode, 0)

    def test_append_bad_json_exit_1(self):
        with TempRepo() as r:
            c = Cli(r)
            base = ["append", "--from", "codex", "--op", "set", "--collection", "messages",
                    "--id", "codex-0001"]
            for data in ("{nope", "[1]"):
                p = c.run(*(base + ["--data", data]))
                self.assertEqual(p.returncode, 1, data)
                self.assertTrue(p.stderr.strip())
                self.assertEqual(p.stdout, "")
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())

    def test_append_data_file(self):
        with TempRepo() as r:
            c = Cli(r)
            f = r.root / "d.json"
            f.write_text('{"body": "café"}', encoding="utf-8")
            p = c.run("append", "--from", "codex", "--op", "set", "--collection", "messages",
                      "--id", "codex-0001", "--data-file", str(f))
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "1"), p.stderr)

    def test_pending_prints_paths(self):
        with TempRepo() as r:
            c = Cli(r)
            r.write_log("codex", [line(1, "codex")])
            out = r.root / "out"
            p = c.run("pending", str(out))
            self.assertEqual(p.returncode, 0, p.stderr)
            paths = p.stdout.split()
            self.assertTrue(paths)
            for path in paths:
                self.assertTrue(Path(path).is_file(), path)
            p = c.run("sent", paths[0])
            self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)
            self.assertEqual(c.run("sent", str(r.root / "nope.json")).returncode, 1)

    def test_pending_fatal_problem_still_writes_batches(self):
        with TempRepo() as r:
            c = Cli(r)
            r.write_log("codex", [line(1, "codex"), "{not json"])
            p = c.run("restore", str(r.root / "out"))
            self.assertEqual(p.returncode, 1)
            self.assertIn("log/codex.jsonl:2:", p.stderr)
            self.assertTrue(p.stdout.split())

    def test_pull_prints_summary(self):
        with TempRepo() as r:
            c = Cli(r)
            exp = r.root / "export"
            exp.mkdir()
            p = c.run("pull", str(exp))  # empty log
            self.assertEqual(p.returncode, 1)
            self.assertIn("import", p.stderr)
            r.write_log("codex", [line(1, "codex")])
            p = c.run("pull", str(exp))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.strip(), "owner lines: 0, approvals: -, skipped: -")

    def test_import_then_check(self):
        with TempRepo() as r:
            c = Cli(r)
            snaps = sorted(str(p) for p in (REPO / "import").iterdir() if p.is_dir())
            p = c.run("import", *snaps)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue(p.stdout.strip())
            for ln in p.stdout.strip().splitlines():
                pid, n = ln.split(": ")
                self.assertTrue(int(n) > 0)
            p2 = c.run("check")
            self.assertEqual(p2.returncode, 0, p2.stdout)
            self.assertEqual(c.run("import", *snaps).returncode, 1)


if __name__ == "__main__":
    unittest.main()
