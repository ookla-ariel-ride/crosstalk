import json
import os
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path

from helpers import RunningServer, TempRepo, line, relay_in

REPO = Path(__file__).resolve().parent.parent

APPEND = ["append", "--from", "alpha", "--op", "set", "--collection", "messages",
          "--id", "alpha-0001", "--data", '{"from": "alpha", "kind": "reply", "summary": "hi"}']


class DataRoot:
    """A code folder (a copy of scripts/) and a separate data folder with a roster."""

    def __init__(self, code, data):
        self.code = code.root
        self.data = data.root
        data.write_roster()
        self.script = str(relay_in(self.code))

    def run(self, *args, env_data=None):
        env = {k: v for k, v in os.environ.items() if k != "CROSSTALK_DATA"}
        if env_data is not None:
            env["CROSSTALK_DATA"] = str(env_data)
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/", env=env)

    def log_lines(self, root):
        path = Path(root) / "log" / "alpha.jsonl"
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class DataRootTests(unittest.TestCase):
    def test_data_root_flag_moves_the_log(self):
        with TempRepo() as code, TempRepo() as data:
            d = DataRoot(code, data)
            p = d.run("--data-root", str(d.data), *APPEND)
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "1"), p.stderr)
            self.assertEqual([ln["id"] for ln in d.log_lines(d.data)], ["alpha-0001"])
            self.assertEqual(d.log_lines(d.code), [])

    def test_env_sets_data_root(self):
        with TempRepo() as code, TempRepo() as data:
            d = DataRoot(code, data)
            p = d.run(*APPEND, env_data=d.data)
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "1"), p.stderr)
            self.assertEqual([ln["id"] for ln in d.log_lines(d.data)], ["alpha-0001"])
            self.assertEqual(d.log_lines(d.code), [])

    def test_flag_wins_over_env(self):
        with TempRepo() as code, TempRepo() as data, TempRepo() as other:
            d = DataRoot(code, data)
            other.write_roster()
            p = d.run("--data-root", str(d.data), *APPEND, env_data=other.root)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual([ln["id"] for ln in d.log_lines(d.data)], ["alpha-0001"])
            self.assertEqual(d.log_lines(other.root), [])
            self.assertEqual(d.log_lines(d.code), [])

    def test_reads_come_from_the_data_root(self):
        with TempRepo() as code, TempRepo() as data:
            d = DataRoot(code, data)
            data.write_log("alpha", [line(1, "alpha"), line(2, "alpha")])
            p = d.run("--data-root=%s" % d.data, "check")
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "ok: 2 lines in 1 files"), p.stderr)

    def test_a_missing_data_root_fails_and_writes_nothing(self):
        with TempRepo() as code, TempRepo() as data:
            d = DataRoot(code, data)
            missing = d.data / "no-such-folder"
            p = d.run("--data-root", str(missing), *APPEND)
            self.assertEqual(p.returncode, 2)
            self.assertIn("is not a folder", p.stderr)
            self.assertFalse(missing.exists())
            self.assertEqual(d.log_lines(d.code), [])

    def test_server_gives_the_notifier_the_code_root(self):
        with TempRepo() as data:
            data.write_roster()
            with RunningServer(data.root, code_root=REPO, no_pin=True, notify=True) as srv:
                self.assertEqual(srv._notifier.code_root, REPO)
                self.assertEqual(srv._notifier.root, data.root)

    def test_page_served_from_code_root(self):
        with TempRepo() as data:
            data.write_roster()
            self.assertFalse((data.root / "index.html").exists())
            with RunningServer(data.root, code_root=REPO, no_pin=True) as srv:
                with urllib.request.urlopen(srv.url + "/", timeout=10) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.read(), (REPO / "index.html").read_bytes())
                with urllib.request.urlopen(srv.url + "/favicon.svg", timeout=10) as resp:
                    self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
