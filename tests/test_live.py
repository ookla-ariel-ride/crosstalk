import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402,F401  (puts scripts/ on sys.path)
from helpers import TempRepo  # noqa: E402

from crosstalk import live  # noqa: E402

RELAY = Path(__file__).resolve().parent.parent / "scripts" / "relay.py"
TOKEN = "tok-secret-123"


class LiveCase(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.repo.write_roster()
        self.root = self.repo.root
        # A short dir named like a real Claude Code socket dir (AF_UNIX paths are ~104 bytes).
        self.socks = tempfile.mkdtemp(dir="/tmp", prefix="cc-socks-")
        self.live = tempfile.mkdtemp(prefix="crosstalk-live-test-")
        os.rmdir(self.live)  # let register create it with 0700
        self._env = os.environ.get("CROSSTALK_LIVE_DIR")
        os.environ["CROSSTALK_LIVE_DIR"] = self.live
        self._res = list(live.SOCKET_DIR_RES)
        live.SOCKET_DIR_RES.append(re.compile("^" + re.escape(self.socks) + "$"))
        self.servers = []
        self.sock = self.listen("a.sock")

    def tearDown(self):
        for s in self.servers:
            s.close()
        live.SOCKET_DIR_RES[:] = self._res
        if self._env is None:
            os.environ.pop("CROSSTALK_LIVE_DIR", None)
        else:
            os.environ["CROSSTALK_LIVE_DIR"] = self._env
        shutil.rmtree(self.socks, ignore_errors=True)
        shutil.rmtree(self.live, ignore_errors=True)
        self.repo.__exit__()

    def listen(self, name):
        path = os.path.join(self.socks, name)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path)
        s.listen(1)
        self.servers.append(s)
        return path

    def env(self, sock=None, token=TOKEN):
        return {"CLAUDE_CODE_MESSAGING_SOCKET": sock or self.sock,
                "CLAUDE_CODE_MESSAGING_TOKEN": token}

    def reg(self, sid, pid="vectorcraft", sock=None):
        return live.register(self.root, pid, self.env(sock), json.dumps({"session_id": sid}), 1000.0)

    def write_file(self, name, doc):
        d = Path(self.live)
        d.mkdir(mode=0o700, exist_ok=True)
        (d / name).write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")


class RegisterTests(LiveCase):
    def test_register_writes_private_file(self):
        path = self.reg("s1")
        self.assertEqual(path, Path(self.live) / "vectorcraft--s1.json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(self.live).stat().st_mode), 0o700)
        doc = json.loads(path.read_text())
        self.assertEqual(set(doc), {"pid", "session_id", "socket", "token", "registered"})
        self.assertEqual((doc["pid"], doc["session_id"], doc["socket"], doc["token"]),
                         ("vectorcraft", "s1", self.sock, TOKEN))

    def test_register_replaces_same_socket(self):
        self.reg("s1")
        self.reg("s2")
        self.assertEqual(sorted(p.name for p in Path(self.live).iterdir()), ["vectorcraft--s2.json"])

    def test_register_keeps_other_sockets(self):
        self.reg("s1")
        self.reg("s2", sock=self.listen("b.sock"))
        self.assertEqual(len(list(Path(self.live).glob("*.json"))), 2)

    def test_register_silently_ignores_problems(self):
        good = self.env()
        cases = [
            ("vectorcraft", {}, '{"session_id":"s1"}'),
            ("vectorcraft", {"CLAUDE_CODE_MESSAGING_SOCKET": self.sock}, '{"session_id":"s1"}'),
            ("nobody", good, '{"session_id":"s1"}'),
            ("vectorcraft", good, '{"session_id":"../x"}'),
            ("vectorcraft", good, "not json"),
            ("vectorcraft", good, "[1]"),
        ]
        for pid, env, stdin in cases:
            self.assertIsNone(live.register(self.root, pid, env, stdin, 1.0), (pid, env, stdin))
        self.assertEqual(list(Path(self.live).glob("*.json")) if Path(self.live).exists() else [], [])

    def test_unregister_removes_file(self):
        path = self.reg("s1")
        self.assertTrue(live.unregister("vectorcraft", '{"session_id":"s1"}'))
        self.assertFalse(path.exists())
        self.assertFalse(live.unregister("vectorcraft", '{"session_id":"s1"}'))

    def test_unregister_ignores_bad_input(self):
        self.assertFalse(live.unregister("vectorcraft", "nope"))
        self.assertFalse(live.unregister("vectorcraft", '{"session_id":"../x"}'))


class LoadTests(LiveCase):
    def test_load_registrations_validates(self):
        good = {"pid": "vectorcraft", "session_id": "ok", "socket": self.sock,
                "token": TOKEN, "registered": "2026-10-26T00:00:00Z"}
        plain = os.path.join(self.socks, "plain.txt")
        Path(plain).write_text("x")
        bad = {
            "vectorcraft--elsewhere.json": dict(good, session_id="elsewhere", socket="/etc/cc-socks/x.sock"),
            "vectorcraft--notsock.json": dict(good, session_id="notsock", socket=plain),
            "vectorcraft--notoken.json": dict(good, session_id="notoken", token=""),
            "vectorcraft--longtok.json": dict(good, session_id="longtok", token="x" * 513),
            "nobody--s.json": dict(good, pid="nobody", session_id="s"),
            "vectorcraft--mangled.json": "{oops",
        }
        self.write_file("vectorcraft--ok.json", good)
        for name, doc in bad.items():
            self.write_file(name, doc)
        regs, problems = live.load_registrations(self.root)
        self.assertEqual([(r.pid, r.session_id, r.socket, r.token) for r in regs],
                         [("vectorcraft", "ok", self.sock, TOKEN)])
        self.assertEqual(regs[0].path, Path(self.live) / "vectorcraft--ok.json")
        self.assertEqual(len(problems), len(bad), problems)
        for p in problems:
            self.assertNotIn(TOKEN, p)

    def test_surrogate_socket_is_one_bad_file(self):
        good = {"pid": "vectorcraft", "session_id": "ok", "socket": self.sock,
                "token": TOKEN, "registered": "2026-10-26T00:00:00Z"}
        self.write_file("vectorcraft--ok.json", good)
        self.write_file("vectorcraft--sur.json",
                        '{"pid":"vectorcraft","session_id":"sur","socket":"/tmp/cc-socks-1/\\ud800","token":"t"}')
        regs, problems = live.load_registrations(self.root)
        self.assertEqual([r.session_id for r in regs], ["ok"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("vectorcraft--sur.json", problems[0])

    def test_unexpected_error_in_one_file_is_contained(self):
        self.write_file("vectorcraft--ok.json", {"pid": "vectorcraft", "session_id": "ok",
                        "socket": self.sock, "token": TOKEN})
        self.write_file("vectorcraft--boom.json", {"pid": "vectorcraft", "session_id": "boom",
                        "socket": self.sock, "token": TOKEN})
        real = live.token_problem

        def flaky(t):
            raise RuntimeError("boom")
        live.token_problem = flaky
        try:
            regs, problems = live.load_registrations(self.root)
        finally:
            live.token_problem = real
        self.assertEqual(regs, [])
        self.assertEqual(len(problems), 2, problems)

    def test_load_missing_dir(self):
        self.assertEqual(live.load_registrations(self.root), ([], []))


class CliTests(LiveCase):
    def run_relay(self, *args, env=None, stdin=""):
        e = {k: v for k, v in os.environ.items()
             if k not in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN")}
        e.update(env or {})
        return subprocess.run([sys.executable, str(RELAY)] + list(args), input=stdin,
                              capture_output=True, text=True, env=e)

    def test_cli_register_exits_zero_and_prints_nothing(self):
        r = self.run_relay("live", "register", "vectorcraft", stdin="")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_cli_unregister_exits_zero_and_prints_nothing(self):
        r = self.run_relay("live", "unregister", "vectorcraft", stdin="garbage")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_status_never_prints_token(self):
        # The CLI validates against the real roster and real socket dirs, so use a
        # real-looking socket dir name.
        d = "/tmp/cc-socks-%d" % (900000000 + os.getpid())
        os.mkdir(d, 0o700)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            path = d + "/1.sock"
            s.bind(path)
            s.listen(1)
            r = self.run_relay("live", "register", "vectorcraft",
                               env={"CROSSTALK_LIVE_DIR": self.live,
                                    "CLAUDE_CODE_MESSAGING_SOCKET": path,
                                    "CLAUDE_CODE_MESSAGING_TOKEN": TOKEN},
                               stdin='{"session_id":"sess-9"}')
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            r = self.run_relay("live", "status", env={"CROSSTALK_LIVE_DIR": self.live})
            self.assertEqual(r.returncode, 0)
            self.assertIn("vectorcraft", r.stdout)
            self.assertIn("sess-9", r.stdout)
            self.assertNotIn(TOKEN, r.stdout + r.stderr)
        finally:
            s.close()
            shutil.rmtree(d, ignore_errors=True)

    def test_status_survives_surrogate_registration(self):
        self.write_file("vectorcraft--sur.json",
                        '{"pid":"vectorcraft","session_id":"sur","socket":"/tmp/cc-socks-1/\\ud800","token":"t"}')
        r = self.run_relay("live", "status", env={"CROSSTALK_LIVE_DIR": self.live})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_hooks_json_has_absolute_paths(self):
        doc = json.loads(live.hooks_json(self.root, "vectorcraft"))
        start = doc["hooks"]["SessionStart"][0]["hooks"][0]
        end = doc["hooks"]["SessionEnd"][0]["hooks"][0]
        self.assertEqual(start["type"], "command")
        self.assertTrue(start["command"].startswith("python3 /"), start["command"])
        self.assertTrue(start["command"].endswith("relay.py live register vectorcraft"))
        self.assertTrue(end["command"].startswith("python3 /"))
        self.assertTrue(end["command"].endswith("relay.py live unregister vectorcraft"))

    def test_cli_hooks_prints_json(self):
        r = self.run_relay("live", "hooks", "vectorcraft")
        self.assertEqual(r.returncode, 0)
        json.loads(r.stdout)


if __name__ == "__main__":
    unittest.main()
