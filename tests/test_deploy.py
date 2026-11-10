"""`relay.py deploy` puts one commit into a separate folder and leaves that folder's data alone.

The source is a throwaway git repo made of this checkout's files, with three
commits: `c1` (the code), `c2` (adds two files) and `c3` (removes them again).
No test calls launchctl: the restart step is replaced.
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
from types import SimpleNamespace

from helpers import RunningServer

from crosstalk import deploy as deploy_mod  # noqa: E402
from crosstalk.deploy import DATA_PATHS, MANIFEST, DeployError, is_data_path, read_manifest  # noqa: E402
from crosstalk.service import version  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


def git(cwd, *args):
    p = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false"] + list(args), cwd=str(cwd), env=GIT_ENV,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    assert p.returncode == 0, "git %s\n%s" % (" ".join(args), p.stdout)
    return p.stdout.strip()


def have_git():
    return shutil.which("git") is not None and (REPO / ".git").exists()


def tree(root):
    """{path: bytes} for every file under root."""
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


class DataPathTests(unittest.TestCase):
    def test_every_data_path_is_git_ignored(self):
        ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual([p for p in DATA_PATHS + (MANIFEST,) if p not in ignored], [])

    def test_data_paths_and_the_manifest_are_never_deployed(self):
        for rel in ("log/a.jsonl", "log", "kits/a.md", "local/notes.md", "import/x.json", "participants.json",
                    "tokens.json", "owner-pin.json", MANIFEST, ".git", ".git/HEAD"):
            self.assertTrue(is_data_path(rel), rel)
        for rel in ("index.html", "scripts/relay.py", "logo.svg", "docs/log/x.md", "tokens.json.md"):
            self.assertFalse(is_data_path(rel), rel)


@unittest.skipUnless(have_git(), "needs git and the git checkout")
class DeployTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-deploy-test-")).resolve()
        cls.src = cls.tmp / "checkout"
        cls.home = cls.tmp / "home"
        (cls.home / "Library" / "Logs").mkdir(parents=True)
        out = subprocess.run(["git", "ls-files", "-z", "-c", "-o", "--exclude-standard"], cwd=str(REPO),
                             stdout=subprocess.PIPE, check=True).stdout
        for f in [f for f in out.decode("utf-8").split("\0") if f]:
            if (REPO / f).is_file():
                dest = cls.src / f
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(REPO / f), str(dest))
        git(cls.src, "init", "-q")
        git(cls.src, "add", "-A")
        git(cls.src, "commit", "-q", "-m", "c1")
        cls.c1 = git(cls.src, "rev-parse", "HEAD")
        (cls.src / "extra.txt").write_text("extra\n", encoding="utf-8")
        (cls.src / "gone" / "deep").mkdir(parents=True)
        (cls.src / "gone" / "deep" / "only.txt").write_text("only\n", encoding="utf-8")
        git(cls.src, "add", "-A")
        git(cls.src, "commit", "-q", "-m", "c2")
        cls.c2 = git(cls.src, "rev-parse", "HEAD")
        git(cls.src, "rm", "-q", "-r", "extra.txt", "gone")
        git(cls.src, "commit", "-q", "-m", "c3")
        cls.c3 = git(cls.src, "rev-parse", "HEAD")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def setUp(self):
        self.target = Path(tempfile.mkdtemp(prefix="target-", dir=str(self.tmp))) / "install"
        self.lines = []
        self._saved = (deploy_mod.restart_service, deploy_mod.wait_healthy)

    def tearDown(self):
        deploy_mod.restart_service, deploy_mod.wait_healthy = self._saved

    def deploy(self, ref="HEAD", **kwargs):
        return deploy_mod.deploy(self.src, self.target, ref=ref, out=self.lines.append, **kwargs)

    def relay(self, root, *args):
        env = dict(GIT_ENV, HOME=str(self.home))
        return subprocess.run([sys.executable, str(Path(root) / "scripts" / "relay.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              cwd=str(self.tmp), env=env)

    def seed_data(self):
        """A first session's real data plus the other data paths, in the target."""
        p = self.relay(self.target, "onboard", "first", "--name", "First", "--kind", "project")
        self.assertEqual(p.returncode, 0, p.stdout)
        for rel, data in (("local/notes.md", b"my notes\n"), ("import/old.json", b"{}\n"),
                          ("owner-pin.json", b'{"hash": "x"}\n'), ("log/keep.bin", b"\x00\x01\xff")):
            path = self.target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def data_tree(self):
        return {k: v for k, v in tree(self.target).items() if is_data_path(k) and k != MANIFEST}

    # -- the copy ------------------------------------------------------------

    def test_a_first_deploy_to_a_missing_folder_leaves_a_working_install(self):
        self.assertEqual(self.deploy(), 0)
        m = read_manifest(self.target)
        self.assertEqual((m["sha"], m["ref"], m["source"]), (self.c3, "HEAD", str(self.src)))
        self.assertEqual(m["files"], sorted(git(self.src, "ls-files").splitlines()))
        self.assertFalse((self.target / ".git").exists())
        self.assertFalse((self.target / "participants.json").exists())  # the pre-check wrote nothing here
        p = self.relay(self.target, "check")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("ok: 0 lines in 0 files", p.stdout)
        roster = json.loads((self.target / "participants.json").read_text(encoding="utf-8"))
        self.assertEqual([x["id"] for x in roster["participants"]], ["owner"])

    def test_the_data_in_the_target_survives_a_deploy_byte_for_byte(self):
        self.deploy(self.c2)
        self.seed_data()
        before = self.data_tree()
        for name in ("participants.json", "tokens.json", "kits/first.md", "local/notes.md", "log/keep.bin"):
            self.assertIn(name, before)
        self.assertEqual(self.deploy(self.c3), 0)
        self.assertEqual(self.deploy(self.c1), 0)
        self.assertEqual(self.data_tree(), before)

    def test_a_file_the_new_commit_dropped_goes_and_an_unlisted_file_stays(self):
        self.deploy(self.c2)
        self.assertTrue((self.target / "extra.txt").is_file())
        (self.target / "mine.txt").write_text("mine\n", encoding="utf-8")
        (self.target / "docs" / "mine.md").write_text("mine\n", encoding="utf-8")
        self.assertEqual(self.deploy(self.c3), 0)
        self.assertFalse((self.target / "extra.txt").exists())
        self.assertFalse((self.target / "gone").exists())  # the empty folders go too
        self.assertEqual((self.target / "mine.txt").read_text(encoding="utf-8"), "mine\n")
        self.assertTrue((self.target / "docs" / "mine.md").is_file())
        self.assertNotIn("extra.txt", read_manifest(self.target)["files"])
        self.assertIn("2 removed", self.lines[-1])

    def test_a_folder_that_still_holds_an_unlisted_file_stays(self):
        self.deploy(self.c2)
        (self.target / "gone" / "deep" / "mine.txt").write_text("mine\n", encoding="utf-8")
        self.deploy(self.c3)
        self.assertEqual([p.name for p in (self.target / "gone" / "deep").iterdir()], ["mine.txt"])

    def test_a_deploy_overwrites_an_edited_code_file(self):
        self.deploy()
        (self.target / "index.html").write_text("edited", encoding="utf-8")
        self.deploy()
        self.assertEqual((self.target / "index.html").read_bytes(), (self.src / "index.html").read_bytes())
        self.assertIn("1 new or changed", self.lines[-1])

    def test_it_warns_when_the_working_tree_has_uncommitted_changes(self):
        self.deploy()
        self.assertFalse([ln for ln in self.lines if ln.startswith("warning")])
        note = self.src / "uncommitted.txt"
        note.write_text("x", encoding="utf-8")
        try:
            self.assertEqual(self.deploy(), 0)
        finally:
            note.unlink()
        self.assertTrue([ln for ln in self.lines if "uncommitted changes" in ln])
        self.assertFalse((self.target / "uncommitted.txt").exists())  # only the commit ships

    # -- refusals ------------------------------------------------------------

    def test_it_refuses_a_target_that_holds_git(self):
        self.target.mkdir()
        (self.target / ".git").mkdir()
        with self.assertRaisesRegex(DeployError, "checkout, not a deployment"):
            self.deploy()
        self.assertEqual([p.name for p in self.target.iterdir()], [".git"])

    def test_it_refuses_the_source_checkout_and_a_folder_inside_it(self):
        for target in (self.src, self.src / "build" / "deploy", self.src / "scripts" / ".."):
            with self.assertRaisesRegex(DeployError, "separate folder"):
                deploy_mod.deploy(self.src, target, out=self.lines.append)
        self.assertFalse((self.src / "build").exists())
        self.assertFalse((self.src / MANIFEST).exists())

    def test_it_refuses_a_ref_that_does_not_resolve(self):
        with self.assertRaisesRegex(DeployError, "does not name a commit"):
            self.deploy("no-such-ref")
        self.assertFalse(self.target.exists())

    def test_it_refuses_a_source_that_is_not_a_checkout(self):
        self.deploy()
        with self.assertRaisesRegex(DeployError, "not a git checkout"):
            deploy_mod.deploy(self.target, self.tmp / "other", out=self.lines.append)

    def test_with_no_target_the_command_says_how_to_name_one(self):
        p = self.relay(self.src, "deploy")
        self.assertEqual(p.returncode, 1)
        self.assertIn("crosstalk.deploydir", p.stdout)

    # -- dry run and the pre-check -------------------------------------------

    def test_a_dry_run_changes_nothing(self):
        self.assertEqual(self.deploy(dry_run=True), 0)
        self.assertFalse(self.target.exists())
        self.deploy(self.c2)
        self.seed_data()
        before = tree(self.target)
        self.assertEqual(self.deploy(self.c3, dry_run=True, restart=True), 0)
        self.assertEqual(tree(self.target), before)
        self.assertIn("would remove extra.txt", self.lines)
        self.assertIn("would deploy", self.lines[-1])

    def test_a_failed_pre_check_leaves_the_target_untouched(self):
        self.deploy(self.c2)
        self.seed_data()
        (self.target / "log" / "first.jsonl").write_text("this is not a log line\n", encoding="utf-8")
        before = tree(self.target)
        with self.assertRaisesRegex(DeployError, "nothing was changed"):
            self.deploy(self.c3)
        self.assertEqual(tree(self.target), before)
        self.assertEqual(read_manifest(self.target)["sha"], self.c2)

    # -- restart and rollback ------------------------------------------------

    def fake_restart(self, healthy_shas):
        calls = []

        def restart(target):
            calls.append(("restart", read_manifest(target)["sha"]))
            return True, "installed"

        def wait(port, sha, timeout=30):
            calls.append(("health", port, sha))
            return (sha in healthy_shas), "fake"

        deploy_mod.restart_service, deploy_mod.wait_healthy = restart, wait
        return calls

    def test_a_restart_that_comes_up_healthy_ends_the_deploy(self):
        calls = self.fake_restart({self.c3})
        self.assertEqual(self.deploy(restart=True), 0)
        self.assertEqual(calls, [("restart", self.c3), ("health", 8484, self.c3)])

    def test_a_failed_restart_rolls_back_to_the_previous_commit(self):
        self.deploy(self.c2)
        self.seed_data()
        data = self.data_tree()
        calls = self.fake_restart({self.c2})
        self.assertEqual(self.deploy(self.c3, restart=True), 1)
        self.assertEqual(calls, [("restart", self.c3), ("health", 8484, self.c3),
                                 ("restart", self.c2), ("health", 8484, self.c2)])
        self.assertEqual(read_manifest(self.target)["sha"], self.c2)
        self.assertTrue((self.target / "extra.txt").is_file())  # the rollback put it back
        self.assertEqual(self.data_tree(), data)
        self.assertIn("rolled back to %s; the service is healthy" % self.c2[:12], self.lines[-1])

    def test_a_rollback_that_also_fails_says_so(self):
        self.deploy(self.c2)
        self.fake_restart(set())
        self.assertEqual(self.deploy(self.c3, restart=True), 1)
        self.assertEqual(read_manifest(self.target)["sha"], self.c2)
        self.assertIn("the service is NOT healthy", self.lines[-1])

    def test_a_failed_first_deploy_has_nothing_to_roll_back_to(self):
        self.fake_restart(set())
        self.assertEqual(self.deploy(restart=True), 1)
        self.assertIn("no earlier deployment", self.lines[-1])

    def test_the_port_comes_from_the_plist_template(self):
        self.deploy()
        self.assertEqual(deploy_mod.service_port(self.target), 8484)
        plist = self.target / "scripts" / "launchd" / "crosstalk.plist"
        plist.write_text(plist.read_text(encoding="utf-8").replace(">8484<", ">9191<"), encoding="utf-8")
        self.assertEqual(deploy_mod.service_port(self.target), 9191)

    # -- status and health ---------------------------------------------------

    def test_status_prints_the_commit_and_how_far_the_checkout_is_ahead(self):
        self.assertEqual(deploy_mod.status(self.src, self.target, out=self.lines.append), 1)
        self.assertIn("no deployment", self.lines[-1])
        self.deploy(self.c2)
        del self.lines[:]
        self.assertEqual(deploy_mod.status(self.src, self.target, out=self.lines.append), 0)
        text = "\n".join(self.lines)
        self.assertIn(self.c2, text)
        self.assertIn("ref: %s" % self.c2, text)
        self.assertIn("HEAD is 1 commit(s) ahead", text)

    def test_the_command_line_deploys_and_reports_status(self):
        p = self.relay(self.src, "deploy", "--to", str(self.target), "--ref", self.c2)
        self.assertEqual(p.returncode, 0, p.stdout)
        p = self.relay(self.src, "deploy", "--status", "--to", str(self.target))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("deployed: %s" % self.c2[:7], p.stdout)
        p = self.relay(self.target, "deploy", "--status")  # inside the deployment: it reports on itself
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(self.c2, p.stdout)
        self.assertNotIn("ahead", p.stdout)

    def test_the_default_target_is_the_configured_deploy_folder(self):
        self.assertIsNone(deploy_mod.default_target(self.src))
        git(self.src, "config", "crosstalk.deploydir", str(self.target))
        try:
            self.assertEqual(deploy_mod.default_target(self.src), self.target)
            args = SimpleNamespace(to=None, ref="HEAD", restart=False, dry_run=False, status=False)
            self.assertEqual(deploy_mod.run(self.src, args, out=self.lines.append), 0)
        finally:
            git(self.src, "config", "--unset", "crosstalk.deploydir")
        self.assertEqual(read_manifest(self.target)["sha"], self.c3)

    def test_health_and_the_snapshot_report_the_deployed_commit(self):
        self.deploy()
        self.assertEqual(version(self.target)["sha"], self.c3)
        with RunningServer(self.target) as srv:
            base = "http://127.0.0.1:%d" % srv.port
            with urllib.request.urlopen(base + "/api/health", timeout=10) as r:
                health = json.loads(r.read().decode("utf-8"))
            with urllib.request.urlopen(base + "/api/snapshot", timeout=10) as r:
                snap = json.loads(r.read().decode("utf-8"))
            self.assertEqual(deploy_mod.wait_healthy(srv.port, self.c3, timeout=5)[0], True)
            self.assertEqual(deploy_mod.wait_healthy(srv.port, self.c2, timeout=1)[0], False)
        self.assertTrue(health["ok"])
        self.assertEqual(health["sha"], self.c3)
        self.assertEqual(health["version"]["source"], "deployment")
        self.assertEqual(snap["commit"], read_manifest(self.target)["short"])
        self.assertEqual(snap["build_number"], 3)
        self.assertEqual(snap["service"]["version"]["sha"], self.c3)

    def test_a_checkout_reports_its_head_and_a_plain_folder_reports_nothing(self):
        self.assertEqual(version(self.src), {"sha": self.c3, "source": "checkout"})
        plain = self.tmp / "plain"
        plain.mkdir(exist_ok=True)
        self.assertEqual(version(plain), {"sha": None, "source": None})

    def test_nothing_healthy_on_the_port_is_a_failure(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        ok, text = deploy_mod.wait_healthy(port, self.c3, timeout=1)
        self.assertFalse(ok)
        self.assertIn("/api/health", text)

    # -- the housekeeper in a deployment -------------------------------------

    def housekeeper(self, *args):
        env = dict(GIT_ENV, HOME=str(self.home))
        return subprocess.run(["/bin/sh", str(self.target / "scripts" / "housekeeper.sh")] + list(args),
                              cwd=str(self.tmp), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def test_the_housekeeper_checks_and_skips_the_push_quietly_in_a_deployment(self):
        self.deploy()
        p = self.housekeeper("--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ok: 0 lines in 0 files", p.stdout)
        self.assertIn("would not push (a deployment has no checkout)", p.stdout)
        p = self.housekeeper()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ok: 0 lines in 0 files", p.stdout)
        self.assertNotIn("push", p.stdout)


if __name__ == "__main__":
    unittest.main()
