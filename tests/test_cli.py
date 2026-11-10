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
            r.write_log("rev", [line(1, "rev")])
            p = c.run("check")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.strip(), "ok: 1 lines in 1 files")

    def test_check_bad_exit_1_prints_location(self):
        with TempRepo() as r:
            c = Cli(r)
            r.write_log("rev", [line(1, "rev"), "{not json"])
            p = c.run("check")
            self.assertEqual(p.returncode, 1)
            self.assertIn("log/rev.jsonl:2:", p.stdout)

    def test_append_prints_seq(self):
        with TempRepo() as r:
            c = Cli(r)
            p = c.run("append", "--from", "rev", "--op", "set", "--collection", "messages",
                      "--id", "rev-0001", "--data", '{"from": "rev", "kind": "reply", "summary": "hi"}')
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "1"), p.stderr)
            p = c.run("append", "--from", "rev", "--op", "set", "--collection", "messages",
                      "--id", "rev-0002", "--data", '{"from": "rev", "kind": "reply", "summary": "yo"}')
            self.assertEqual(p.stdout.strip(), "2")
            self.assertEqual(c.run("check").returncode, 0)

    def test_append_bad_json_exit_1(self):
        with TempRepo() as r:
            c = Cli(r)
            base = ["append", "--from", "rev", "--op", "set", "--collection", "messages",
                    "--id", "rev-0001"]
            for data in ("{nope", "[1]"):
                p = c.run(*(base + ["--data", data]))
                self.assertEqual(p.returncode, 1, data)
                self.assertTrue(p.stderr.strip())
                self.assertEqual(p.stdout, "")
            self.assertFalse((r.root / "log" / "rev.jsonl").exists())

    def test_append_refuses_trust_violation(self):
        with TempRepo() as r:
            c = Cli(r)
            base = ["append", "--op", "set", "--collection", "messages"]
            p = c.run(*(base + ["--from", "gemini", "--id", "m-0001", "--data", "{}"]))
            self.assertEqual(p.returncode, 1)
            self.assertIn("gemini-", p.stderr)
            p = c.run(*(base + ["--from", "rev", "--id", "rev-1",
                                "--data", '{"from": "owner"}']))
            self.assertEqual(p.returncode, 1)
            self.assertEqual(sorted(x.name for x in (r.root / "log").iterdir()), [])
            p = c.run(*(base + ["--from", "Rev", "--id", "Rev-1", "--data", "{}"]))
            self.assertEqual(p.returncode, 1)
            self.assertEqual(sorted(x.name for x in (r.root / "log").iterdir()), [])

    def test_append_data_file(self):
        with TempRepo() as r:
            c = Cli(r)
            f = r.root / "d.json"
            f.write_text('{"from": "rev", "kind": "reply", "summary": "café"}', encoding="utf-8")
            p = c.run("append", "--from", "rev", "--op", "set", "--collection", "messages",
                      "--id", "rev-0001", "--data-file", str(f))
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "1"), p.stderr)

    def test_append_refuses_message_without_kind_or_summary(self):
        with TempRepo() as r:
            c = Cli(r)
            base = ["append", "--from", "rev", "--op", "set", "--collection", "messages",
                    "--id", "rev-0001", "--data"]
            for data, word in (('{"summary": "hi"}', "kind"), ('{"kind": "reply"}', "summary"),
                               ('{"kind": "reply", "summary": " "}', "summary"),
                               ('{"kind": "reply", "summary": "a\\nb"}', "one line"),
                               ('{"kind": "reply", "summary": "hi", "to": ["gemini"]}', "gemini"),
                               ('{"kind": "reply", "summary": "hi", "to": "owner"}', "list"),
                               ('{"kind": "reply", "summary": "hi", "t": "yesterday"}', "UTC")):
                p = c.run(*(base + [data]))
                self.assertEqual(p.returncode, 1, data)
                self.assertIn(word, p.stderr, data)
            self.assertFalse((r.root / "log" / "rev.jsonl").exists())

    def test_append_update_needs_no_kind(self):
        with TempRepo() as r:
            c = Cli(r)
            p = c.run("append", "--from", "rev", "--op", "update", "--collection", "messages",
                      "--id", "rev-0001", "--data", '{"important": true}')
            self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_append_refuses_changing_another_participants_message(self):
        with TempRepo() as r:
            c = Cli(r)
            r.write_log("alpha", [line(1, "alpha", id_="alpha-q", data={
                "from": "alpha", "kind": "question", "summary": "Ship it?"})])
            p = c.run("append", "--from", "rev", "--op", "update", "--collection", "messages",
                      "--id", "alpha-q", "--data", '{"kind": "reply"}')
            self.assertEqual(p.returncode, 1)
            self.assertIn("only its writer or the owner", p.stderr)
            self.assertFalse((r.root / "log" / "rev.jsonl").exists())
            self.assertEqual(c.run("check").returncode, 0)

    def test_append_warns_unknown_kind_and_clock_skew(self):
        with TempRepo() as r:
            c = Cli(r)
            p = c.run("append", "--from", "rev", "--op", "set", "--collection", "messages",
                      "--id", "rev-0001", "--data",
                      '{"from": "rev", "kind": "status", "summary": "hi", "t": "2026-01-01T00:00:00Z",'
                      ' "to": ["owner"]}')
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("warning: unknown kind 'status'", p.stderr)
            self.assertIn("behind the clock", p.stderr)
            self.assertEqual(p.stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main()
