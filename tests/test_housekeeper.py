"""The housekeeper never commits, and it pushes only when this install turned
`crosstalk.autopush` on."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def git(cwd, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false"] + list(args),
                          cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True)


@unittest.skipUnless(shutil.which("git"), "needs git")
class HousekeeperTests(unittest.TestCase):
    """The housekeeper over a throwaway repo with a bare `origin` beside it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-housekeeper-"))
        self.root = self.tmp / "repo"
        self.origin = self.tmp / "origin.git"
        self.home = self.tmp / "home"
        (self.home / "Library" / "Logs").mkdir(parents=True)
        shutil.copytree(str(REPO / "scripts"), str(self.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(str(REPO / ".gitignore"), str(self.root / ".gitignore"))
        self.assertEqual(git(self.tmp, "init", "-q", "--bare", str(self.origin)).returncode, 0)
        for args in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "first"),
                     ("remote", "add", "origin", str(self.origin))):
            p = git(self.root, *args)
            self.assertEqual(p.returncode, 0, p.stdout)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def run_it(self, *args):
        env = dict(os.environ, HOME=str(self.home), GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_SYSTEM=os.devnull)
        return subprocess.run(["/bin/sh", str(self.root / "scripts" / "housekeeper.sh")] + list(args),
                              cwd=str(self.tmp), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)

    def commits(self, cwd):
        p = git(cwd, "rev-list", "--all", "--count")
        return int(p.stdout.strip() or 0) if p.returncode == 0 else 0

    def test_by_default_it_commits_nothing_and_pushes_nothing(self):
        (self.root / "log").mkdir()
        p = self.run_it()
        # `schedule ran --from crosstalk` fails here (no such participant): the run still ends well.
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("no push", p.stdout)
        self.assertEqual(self.commits(self.root), 1)
        self.assertEqual(self.commits(self.origin), 0)
        self.assertEqual(git(self.root, "status", "--porcelain").stdout, "")

    def test_it_pushes_when_autopush_is_on_and_still_commits_nothing(self):
        git(self.root, "config", "crosstalk.autopush", "true")
        p = self.run_it()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.commits(self.root), 1)
        self.assertEqual(self.commits(self.origin), 1)

    def test_the_dry_run_says_what_it_would_do(self):
        p = self.run_it("--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("would not push", p.stdout)
        self.assertNotIn("commit", p.stdout)
        git(self.root, "config", "crosstalk.autopush", "true")
        p = self.run_it("--dry-run")
        self.assertIn("would run: git push origin HEAD", p.stdout)
        self.assertEqual(self.commits(self.origin), 0)

    def test_a_failed_check_is_logged_and_nothing_is_pushed(self):
        git(self.root, "config", "crosstalk.autopush", "true")
        (self.root / "log").mkdir()
        (self.root / "log" / "owner.jsonl").write_text("{not json\n", encoding="utf-8")
        p = self.run_it()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("check failed", (self.home / "Library" / "Logs" / "crosstalk-check.log").read_text())
        self.assertEqual(self.commits(self.origin), 0)


if __name__ == "__main__":
    unittest.main()
