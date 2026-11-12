"""A fresh clone works, and it holds nothing of the install it came from.

The tests copy the tracked files, and only those, to an empty folder: what a
fork or a clone gets. Then they run a first session there, with HOME pointed at
an empty folder so that nothing of this machine's install leaks in.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

from helpers import RunningServer

# App data and local state: a clone must not carry any of it (the list deploy protects).
from crosstalk.deploy import DATA_PATHS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO), capture_output=True, check=True).stdout
    return [f for f in out.decode("utf-8").split("\0") if f]


def have_git():
    return shutil.which("git") is not None and (REPO / ".git").exists()


@unittest.skipUnless(have_git(), "needs the git checkout")
class CleanInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-clean-"))
        cls.root = cls.tmp / "clone"
        cls.home = cls.tmp / "home"
        cls.home.mkdir()
        cls.files = tracked_files()
        for f in cls.files:
            src = REPO / f
            if src.is_file():  # a file deleted in the working tree is not in a clone of it
                dest = cls.root / f
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dest))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def relay(self, *args):
        env = dict(os.environ, HOME=str(self.home))
        return subprocess.run([sys.executable, str(self.root / "scripts" / "relay.py")] + list(args),
                              capture_output=True, text=True, cwd=str(self.tmp), env=env)

    def ok(self, *args):
        p = self.relay(*args)
        self.assertEqual(p.returncode, 0, "relay.py %s\n%s%s" % (" ".join(args), p.stdout, p.stderr))
        return p.stdout

    def test_1_the_repo_tracks_no_app_data(self):
        found = sorted(f for f in self.files if f.startswith(DATA_PATHS))
        self.assertEqual(found, [], "app data is tracked; git rm --cached it and git-ignore it")

    def test_2_a_clone_starts_empty_with_the_owner_alone(self):
        self.assertIn("ok: 0 lines in 0 files", self.ok("check"))
        roster = json.loads((self.root / "participants.json").read_text(encoding="utf-8"))
        self.assertEqual([p["id"] for p in roster["participants"]], ["owner"])
        self.assertEqual(json.loads(self.ok("read"))["collections"].get("messages", {}), {})

    def test_3_a_first_session_runs_end_to_end(self):
        for pid, name in (("first", "First"), ("second", "Second")):
            self.ok("onboard", pid, "--name", name, "--kind", "project")
        roster_path = self.root / "participants.json"
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
        for p in roster["participants"]:
            p["trust"] = "approved"  # the owner approves on the page; here the test does
        roster_path.write_text(json.dumps(roster), encoding="utf-8")
        ask = self.ok("consult", "--from", "first", "--to", "second", "--summary", "Can you read this?").strip()
        inbox = json.loads(self.ok("inbox", "second"))
        self.assertEqual([r["request"]["id"] for r in inbox["requests"]], [ask])
        self.ok("reply", "--from", "second", "--to-id", ask, "--summary", "Yes")
        self.assertEqual(json.loads(self.ok("inbox", "second"))["requests"], [])
        self.assertIn("ok: ", self.ok("check"))
        for cmd in (("queue",), ("questions",), ("schedule", "list"), ("reviews", "open"),
                    ("adoption", "summary"), ("live", "status"), ("schedule", "standard", "--from", "first")):
            self.ok(*cmd)

    def test_4_the_server_serves_the_page_and_the_api(self):
        with RunningServer(self.root) as srv:
            base = "http://127.0.0.1:%d" % srv.port
            with urllib.request.urlopen(base + "/api/health", timeout=10) as r:
                self.assertTrue(json.loads(r.read().decode("utf-8"))["ok"])
            with urllib.request.urlopen(base + "/", timeout=10) as r:
                self.assertIn("<title>", r.read().decode("utf-8").lower())
            with urllib.request.urlopen(base + "/api/snapshot", timeout=10) as r:
                snap = json.loads(r.read().decode("utf-8"))
            self.assertIn("owner", snap["roster"])

    def test_6_git_ignores_the_data_and_gitleaks_guards_it(self):
        for path in ("log/messages.jsonl", "import/a.json", "kits/a.md", "participants.json", "tokens.json",
                     "owner-pin.json", "read-state.json", "inbox-state.json", "inbox-state.json.lock",
                     "local/notes.md", ".install-names", ".env"):
            p = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=str(REPO))
            self.assertEqual(p.returncode, 0, "%s is not git-ignored" % path)
        config = (self.root / ".gitleaks.toml").read_text(encoding="utf-8")
        self.assertIn("useDefault = true", config)
        for data in ("tokens", "kits/", "owner-pin", "participants", "log/"):
            self.assertIn(data, config)
        for hook in ("pre-commit", "pre-push"):
            path = self.root / ".githooks" / hook
            self.assertTrue(os.access(str(path), os.X_OK), "%s is not executable" % hook)
            self.assertIn("gitleaks", path.read_text(encoding="utf-8"))

    def test_5_no_name_from_the_local_install_is_in_a_tracked_file(self):
        """Each install lists its own names in the git-ignored `.install-names`, one per
        line (project ids, host names, user names). No tracked file may hold one."""
        names_file = REPO / ".install-names"
        if not names_file.exists():
            self.skipTest("no .install-names file in this checkout")
        names = [n.strip().lower() for n in names_file.read_text(encoding="utf-8").splitlines()
                 if n.strip() and not n.startswith("#")]
        hits = []
        for f in self.files:
            path = self.root / f
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8").lower()
            except UnicodeDecodeError:
                continue
            for n in names:
                if n in text:
                    hits.append("%s: %s (%d)" % (f, n, text.count(n)))
        self.assertEqual(hits, [], "%d file-and-name pairs hold a name of this install" % len(hits))


if __name__ == "__main__":
    unittest.main()
