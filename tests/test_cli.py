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
                      "--id", "codex-0001", "--data", '{"from": "codex", "kind": "reply", "summary": "hi"}')
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "1"), p.stderr)
            p = c.run("append", "--from", "codex", "--op", "set", "--collection", "messages",
                      "--id", "codex-0002", "--data", '{"from": "codex", "kind": "reply", "summary": "yo"}')
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

    def test_append_refuses_trust_violation(self):
        with TempRepo() as r:
            c = Cli(r)
            base = ["append", "--op", "set", "--collection", "messages"]
            p = c.run(*(base + ["--from", "gemini", "--id", "m-0001", "--data", "{}"]))
            self.assertEqual(p.returncode, 1)
            self.assertIn("gemini-", p.stderr)
            p = c.run(*(base + ["--from", "codex", "--id", "codex-1",
                                "--data", '{"from": "owner"}']))
            self.assertEqual(p.returncode, 1)
            self.assertEqual(sorted(x.name for x in (r.root / "log").iterdir()), [])
            p = c.run(*(base + ["--from", "Codex", "--id", "Codex-1", "--data", "{}"]))
            self.assertEqual(p.returncode, 1)
            self.assertEqual(sorted(x.name for x in (r.root / "log").iterdir()), [])

    def test_append_data_file(self):
        with TempRepo() as r:
            c = Cli(r)
            f = r.root / "d.json"
            f.write_text('{"from": "codex", "kind": "reply", "summary": "café"}', encoding="utf-8")
            p = c.run("append", "--from", "codex", "--op", "set", "--collection", "messages",
                      "--id", "codex-0001", "--data-file", str(f))
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "1"), p.stderr)

    def test_append_refuses_message_without_kind_or_summary(self):
        with TempRepo() as r:
            c = Cli(r)
            base = ["append", "--from", "codex", "--op", "set", "--collection", "messages",
                    "--id", "codex-0001", "--data"]
            for data, word in (('{"summary": "hi"}', "kind"), ('{"kind": "reply"}', "summary"),
                               ('{"kind": "reply", "summary": " "}', "summary"),
                               ('{"kind": "reply", "summary": "a\\nb"}', "one line"),
                               ('{"kind": "reply", "summary": "hi", "to": ["gemini"]}', "gemini"),
                               ('{"kind": "reply", "summary": "hi", "to": "owner"}', "list"),
                               ('{"kind": "reply", "summary": "hi", "t": "yesterday"}', "UTC")):
                p = c.run(*(base + [data]))
                self.assertEqual(p.returncode, 1, data)
                self.assertIn(word, p.stderr, data)
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())

    def test_append_update_needs_no_kind(self):
        with TempRepo() as r:
            c = Cli(r)
            p = c.run("append", "--from", "codex", "--op", "update", "--collection", "messages",
                      "--id", "codex-0001", "--data", '{"important": true}')
            self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_append_warns_unknown_kind_and_clock_skew(self):
        with TempRepo() as r:
            c = Cli(r)
            p = c.run("append", "--from", "codex", "--op", "set", "--collection", "messages",
                      "--id", "codex-0001", "--data",
                      '{"from": "codex", "kind": "status", "summary": "hi", "t": "2026-01-01T00:00:00Z",'
                      ' "to": ["owner"]}')
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("warning: unknown kind 'status'", p.stderr)
            self.assertIn("behind the clock", p.stderr)
            self.assertEqual(p.stdout.strip(), "1")

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
            self.assertEqual(p.stdout.strip(),
                             "owner lines: 0, approvals: -, skipped: -, versions: 0")
            self.assertEqual(p.stderr, "")

    def test_pull_warns_on_missing_versions_and_listings(self):
        with TempRepo() as r:
            c = Cli(r)
            r.write_log("codex", [line(i + 1, "codex", data={"n": i}) for i in range(13)])
            exp = r.root / "export"
            (exp / "messages").mkdir(parents=True)
            (exp / "locks").mkdir()
            for i in range(13):
                (exp / "messages" / ("codex-%04d.json" % (i + 1))).write_text(
                    json.dumps({"n": i}), encoding="utf-8")
            (exp / "locks" / "l-1.json").write_text("{}", encoding="utf-8")
            (exp / "messages.listing.txt").write_text(
                '- "codex-0001"  10 bytes  version 2  "t"\n', encoding="utf-8")
            p = c.run("pull", str(exp))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.strip(),
                             "owner lines: 1, approvals: -, skipped: -, versions: 1")
            err = p.stderr.splitlines()
            self.assertIn("warning: no listing locks.listing.txt for locks/ (1 docs)", err)
            missing = [e for e in err if e.startswith("warning: no version for ")]
            self.assertEqual(missing[0], "warning: no version for locks/l-1")
            self.assertEqual(len(missing), 10)
            self.assertIn("... and 3 more", err)

    def test_import_then_check(self):
        with TempRepo() as r:
            c = Cli(r)
            # Real snapshots hold every real participant's rows.
            shutil.copy(str(REPO / "participants.json"), str(r.root / "participants.json"))
            # One snapshot per page: the newest, as the go-live import uses.
            newest = {}
            for d in sorted(p for p in (REPO / "import").iterdir() if p.is_dir()):
                newest[d.name.split("-")[0]] = str(d)
            snaps = sorted(newest.values())
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
