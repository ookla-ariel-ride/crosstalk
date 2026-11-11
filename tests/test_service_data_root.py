"""The installed service and the command templates carry the data root (issue #29).

`scripts/install-service.sh` takes an optional data root (`--data-root DIR`
or `CROSSTALK_DATA`). With one that differs from the code folder, each
launchd job runs `relay.py` of the code folder with `--data-root DIR`, and
the housekeeper passes it on. With none, each generated plist is what the code
before this change wrote: tests/baseline/ holds those files, rendered with
the root as @ROOT@ and the home as @HOME@.

The tests never load a launchd job: `CROSSTALK_RENDER_ONLY=<dir>` makes the
installer write the plists to <dir> and stop, and a fake `launchctl` first on
PATH records any call it gets. HOME is a temp folder in every run.
"""
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import SPEC_ROSTER, RunningServer, TempRepo, line, relay_in  # noqa: E402

from crosstalk.service import service_block  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_spec = spec_from_file_location("render_plist", str(REPO / "scripts" / "launchd" / "render_plist.py"))
render_plist = module_from_spec(_spec)
_spec.loader.exec_module(render_plist)  # type: ignore[union-attr]
xml_escape = render_plist.xml_escape
BASELINE = Path(__file__).resolve().parent / "baseline"
JOBS = {"local.crosstalk": "launchd-crosstalk.plist",
        "local.crosstalk-housekeeper": "launchd-crosstalk-housekeeper.plist",
        "local.crosstalk-buildhost": "launchd-crosstalk-buildhost.plist"}
NODE = shutil.which("node")


def baseline_plist(label, root, home):
    text = (BASELINE / JOBS[label]).read_text(encoding="utf-8")
    return text.replace("@ROOT@", xml_escape(str(root))).replace("@HOME@", xml_escape(str(home)))


class Installer(unittest.TestCase):
    """Runs a copy of install-service.sh with a temp HOME, a render-only folder and a fake launchctl."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-install-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.out = self.tmp / "agents"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "launchctl-calls"
        fake = self.bin / "launchctl"
        fake.write_text("#!/bin/sh\necho \"$@\" >> '%s'\n" % self.calls, encoding="utf-8")
        fake.chmod(0o755)

    def code_folder(self, name="code"):
        code = self.tmp / name
        code.mkdir()
        relay_in(code)
        return code

    def install(self, code, *args, data_env=None):
        env = {k: v for k, v in os.environ.items() if k != "CROSSTALK_DATA"}
        env.update(HOME=str(self.home), TMPDIR=str(self.tmp), CROSSTALK_RENDER_ONLY=str(self.out),
                   PATH="%s:%s" % (self.bin, env.get("PATH", "/usr/bin:/bin")))
        if data_env is not None:
            env["CROSSTALK_DATA"] = str(data_env)
        p = subprocess.run(["/bin/sh", str(code / "scripts" / "install-service.sh")] + list(args),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           cwd=str(self.tmp), env=env)
        self.assertFalse(self.calls.exists(), "launchctl was called")
        self.assertFalse((self.home / "Library" / "LaunchAgents").exists())
        return p

    def written(self, label):
        return (self.out / (label + ".plist")).read_text(encoding="utf-8")

    def job(self, label):
        with open(str(self.out / (label + ".plist")), "rb") as f:
            return plistlib.load(f)


class NoDataRootTests(Installer):
    def test_install_and_buildhost_write_the_baseline_files(self):
        code = self.code_folder()
        for cmd, labels in (("install", ["local.crosstalk", "local.crosstalk-housekeeper"]),
                            ("buildhost", ["local.crosstalk-buildhost"])):
            p = self.install(code, cmd)
            self.assertEqual(p.returncode, 0, p.stderr)
            for label in labels:
                self.assertEqual(self.written(label), baseline_plist(label, code, self.home), label)

    def test_a_data_root_that_is_the_code_folder_changes_nothing(self):
        code = self.code_folder()
        p = self.install(code, "install", "--data-root", str(code))
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.install(code, "buildhost", data_env=code)
        self.assertEqual(p.returncode, 0, p.stderr)
        for label in JOBS:
            self.assertEqual(self.written(label), baseline_plist(label, code, self.home), label)

    def test_the_repo_installer_writes_the_baseline_files(self):
        p = self.install(REPO)
        self.assertEqual(p.returncode, 0, p.stderr)
        for label in ("local.crosstalk", "local.crosstalk-housekeeper"):
            self.assertEqual(self.written(label), baseline_plist(label, REPO, self.home), label)


class DataRootTests(Installer):
    def check_jobs(self, code, data):
        relay, hk = str(code / "scripts" / "relay.py"), str(code / "scripts" / "housekeeper.sh")
        self.assertEqual(self.job("local.crosstalk")["ProgramArguments"],
                         ["python3", relay, "--data-root", str(data), "serve", "--port", "8484",
                          "--host", "127.0.0.1"])
        self.assertEqual(self.job("local.crosstalk-housekeeper")["ProgramArguments"],
                         ["/bin/sh", hk, "--data-root", str(data)])
        self.assertEqual(self.job("local.crosstalk-buildhost")["ProgramArguments"],
                         ["python3", relay, "--data-root", str(data), "buildhost", "poll"])
        for label in JOBS:
            self.assertEqual(self.job(label)["WorkingDirectory"], str(code))

    def test_the_flag_puts_the_data_root_in_each_job(self):
        code, data = self.code_folder(), self.tmp / "data"
        data.mkdir()
        for cmd in ("install", "buildhost"):
            p = self.install(code, cmd, "--data-root", str(data))
            self.assertEqual(p.returncode, 0, p.stderr)
        self.check_jobs(code, data)
        self.assertIn("data %s" % data, p.stdout)

    def test_crosstalk_data_puts_the_data_root_in_each_job(self):
        code, data = self.code_folder(), self.tmp / "data"
        data.mkdir()
        for cmd in ("install", "buildhost"):
            p = self.install(code, cmd, data_env=data)
            self.assertEqual(p.returncode, 0, p.stderr)
        self.check_jobs(code, data)

    def test_the_flag_wins_over_the_variable(self):
        code, data, other = self.code_folder(), self.tmp / "data", self.tmp / "other"
        data.mkdir()
        other.mkdir()
        p = self.install(code, "--data-root=%s" % data, data_env=other)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(str(other), self.written("local.crosstalk"))
        self.assertIn("--data-root", self.job("local.crosstalk")["ProgramArguments"])

    def test_a_missing_data_root_fails_and_writes_nothing(self):
        code = self.code_folder()
        p = self.install(code, "install", "--data-root", str(self.tmp / "no-such-folder"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("is not a folder", p.stderr)
        self.assertFalse(self.out.exists())

    def test_a_path_with_a_space_gives_jobs_that_run(self):
        code, data = self.code_folder("code dir & <x>"), self.tmp / "data dir 'y'"
        (data / "log").mkdir(parents=True)
        (data / "participants.json").write_text(json.dumps(SPEC_ROSTER), encoding="utf-8")
        (data / "log" / "alpha.jsonl").write_text(json.dumps(line(1, "alpha")) + "\n", encoding="utf-8")
        for cmd in ("install", "buildhost"):
            p = self.install(code, cmd, "--data-root", str(data))
            self.assertEqual(p.returncode, 0, p.stderr)
        self.check_jobs(code, data)
        env = {k: v for k, v in os.environ.items() if k != "CROSSTALK_DATA"}
        env.update(HOME=str(self.home), TMPDIR=str(self.tmp))
        # The server job's relay.py and data root, with `check` in place of `serve`.
        serve = self.job("local.crosstalk")
        argv = serve["ProgramArguments"]
        argv = [sys.executable] + argv[1:argv.index("serve")] + ["check"]
        r = subprocess.run(argv, cwd=serve["WorkingDirectory"], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok: 1 lines in 1 files"), r.stderr)
        # The housekeeper job as launchd runs it; its check reads the data root.
        hk = self.job("local.crosstalk-housekeeper")
        r = subprocess.run(hk["ProgramArguments"], cwd=hk["WorkingDirectory"], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "ok: 1 lines in 1 files")
        self.assertEqual(r.stderr, "")


class HousekeeperTests(unittest.TestCase):
    def test_dry_run_and_data_root_in_either_order(self):
        with TempRepo() as code, TempRepo() as data, tempfile.TemporaryDirectory() as home:
            relay_in(code.root)
            data.write_roster()
            data.write_log("alpha", [line(1, "alpha"), line(2, "alpha")])
            env = {k: v for k, v in os.environ.items() if k != "CROSSTALK_DATA"}
            env.update(HOME=home, TMPDIR=home)
            script = str(code.root / "scripts" / "housekeeper.sh")
            for args in (["--data-root", str(data.root), "--dry-run"],
                         ["--dry-run", "--data-root", str(data.root)]):
                r = subprocess.run(["/bin/sh", script] + args, cwd=home, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("ok: 2 lines in 1 files", r.stdout, args)
                self.assertIn("would not push", r.stdout, args)


class ServiceBlockTests(unittest.TestCase):
    def baseline(self):
        return json.loads((BASELINE / "service-commands.json").read_text(encoding="utf-8"))

    def test_with_one_folder_the_commands_are_what_they_were(self):
        with TempRepo() as r:
            self.assertEqual(service_block(r.root)["commands"], self.baseline())
            self.assertEqual(service_block(r.root, data_root=r.root)["commands"], self.baseline())

    def test_with_a_data_root_each_command_carries_the_placeholder(self):
        with TempRepo() as code, TempRepo() as data:
            cmds = service_block(code.root, data_root=data.root)["commands"]
            self.assertEqual(sorted(cmds), sorted(self.baseline()))
            for name, text in cmds.items():
                want = self.baseline()[name].replace("<repo>/scripts/relay.py",
                                                     "<repo>/scripts/relay.py --data-root <data>")
                self.assertEqual(text, want, name)
                self.assertNotIn(str(data.root), text)

    def test_read_and_inbox_carry_the_data_root(self):
        with TempRepo() as code, TempRepo() as data:
            script = relay_in(code.root)
            code.write_roster()
            data.write_roster()
            env = {k: v for k, v in os.environ.items() if k != "CROSSTALK_DATA"}
            for cmd in (["read"], ["inbox", "alpha"]):
                for args, has in ((["--data-root", str(data.root)], True), ([], False)):
                    p = subprocess.run([sys.executable, str(script)] + args + cmd, env=env, cwd="/",
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    ask = json.loads(p.stdout)["service"]["commands"]["ask"]
                    self.assertEqual("--data-root <data>" in ask, has, (cmd, args))

    def test_the_snapshot_carries_the_placeholder_not_the_path(self):
        with TempRepo() as data:
            data.write_roster()
            with RunningServer(data.root, code_root=REPO, no_pin=True) as srv:
                with urllib.request.urlopen(srv.url + "/api/snapshot", timeout=10) as resp:
                    body = resp.read().decode("utf-8")
            snap = json.loads(body)
            self.assertIn("relay.py --data-root <data> ask", snap["service"]["commands"]["ask"])
            self.assertNotIn(str(data.root), body)
            self.assertNotIn(str(data.root.name), body)

    def test_a_one_folder_snapshot_has_no_data_root(self):
        with TempRepo() as data:
            data.write_roster()
            with RunningServer(data.root, no_pin=True) as srv:
                with urllib.request.urlopen(srv.url + "/api/snapshot", timeout=10) as resp:
                    snap = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(snap["service"]["commands"], self.baseline())


@unittest.skipUnless(NODE, "node is not installed")
class AskTemplateTests(unittest.TestCase):
    """The page's ask template, run in node: it carries the data root when the server's ask command does."""

    def ask_body(self, service):
        page = (REPO / "index.html").read_text(encoding="utf-8")
        fn = re.search(r"\n  (function askBody\(service\) \{\n.*?\n  \})\n", page, re.S).group(1)
        src = "%s\nprocess.stdout.write(askBody(%s));" % (fn, json.dumps(service))
        return subprocess.run([NODE, "-e", src], stdout=subprocess.PIPE, check=True).stdout.decode("utf-8")

    def test_with_one_folder_the_template_is_what_it_was(self):
        want = (BASELINE / "ask-body.txt").read_text(encoding="utf-8")
        self.assertEqual(self.ask_body(None), want)
        self.assertEqual(self.ask_body(service_block(REPO)), want)

    def test_with_a_data_root_the_template_carries_it(self):
        with TempRepo() as data:
            text = self.ask_body(service_block(REPO, data_root=data.root))
        want = (BASELINE / "ask-body.txt").read_text(encoding="utf-8").replace(
            "<crosstalk>/scripts/relay.py ask", "<crosstalk>/scripts/relay.py --data-root <data> ask")
        self.assertEqual(text, want)

    def test_the_page_posts_the_template_of_the_last_snapshot(self):
        page = (REPO / "index.html").read_text(encoding="utf-8")
        for want in ("askService = snap.service || null;", "body: askBody(askService)"):
            self.assertTrue(want in page, want)
        self.assertFalse("ASK_BODY" in page)


if __name__ == "__main__":
    unittest.main()
