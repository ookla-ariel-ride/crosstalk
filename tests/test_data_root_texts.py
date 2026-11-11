"""Kits, the urgent hook text and the live hooks carry the data root (issue #20).

With the data in another folder than the code, each generated text names
`<code>/scripts/relay.py` and carries `--data-root <data>`. With one folder,
each text is what the code before this change printed: tests/baseline/ holds
those texts, captured from that code with the root replaced by @ROOT@.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402,F401  (puts scripts/ on sys.path)
from helpers import SPEC_ROSTER, line, relay_in  # noqa: E402

from crosstalk import hermes, live, onboard, urgent  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "baseline"
REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "scripts" / "hermes-plugin" / "crosstalk" / "__init__.py"
PAGE = "http://relay.example:8080/"
HARNESSES = (None, "claude-code", "codex", "opencode", "gemini-cli", "hermes", "pi")
TOKEN = "tok-test"  # made up; never a real key
ITEMS = [{"_id": "owner-0001", "kind": "owner", "from_name": "Owner", "summary": "Look at this"}]


def entry(harness):
    e = {"name": "Beta", "kind": "project"}
    if harness:
        e["harness"] = harness
    return e


def texts(root, **code):
    """name -> text for every text with a baseline; `code` is code_root=... or nothing."""
    out = {}
    for h in HARNESSES:
        out["kit-%s.md" % (h or "none")] = onboard.kit_text(root, "beta", TOKEN, entry(h), **code)
    out["urgent.txt"] = urgent.render_urgent("beta", ITEMS, root, **code)
    out["live-hooks.json"] = live.hooks_json(root, "alpha", **code)
    out["hermes-setup.md"] = hermes.setup_text(root, "beta", **code)
    out["hermes-check-in.txt"] = hermes.check_in_prompt(root, "beta", 30, **code)
    return out


def baseline(name, root):
    return (BASELINE / name).read_text(encoding="utf-8").replace("@ROOT@", str(root))


def relay_argvs(text):
    """Every relay.py command in text, as an argv list without `python3`.

    Three forms: a JSON list (`args`, OpenCode's `command`), a TOML or YAML
    `command` string (a JSON string literal that holds a shell command), and a
    shell command line in a code block or an inline code span.
    """
    out = []
    for raw in text.splitlines():
        if "scripts/relay.py" not in raw:
            continue
        s = raw.strip().rstrip(",")
        m = re.match(r'^(?:command = |- command: )(".*")$', s)
        if "[" in s and s.endswith("]"):
            argv = json.loads(s[s.index("["):])
        elif m:
            argv = shlex.split(json.loads(m.group(1)))
        else:
            i = raw.index("python3 ")
            cmd = raw[i:]
            if i > 0 and raw[i - 1] == "`":
                cmd = cmd.split("`", 1)[0]
            argv = shlex.split(cmd)
        if argv and argv[0] == "python3":
            argv = argv[1:]
        out.append(argv)
    return out


class Roots(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-texts-")).resolve()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def folder(self, name, page=True):
        d = self.tmp / name
        d.mkdir()
        if page:
            (d / "participants.json").write_text(
                json.dumps({"page": PAGE, "participants": []}), encoding="utf-8")
        return d

    def assert_two_roots(self, text, code, data, what):
        relay = str(code / "scripts" / "relay.py")
        self.assertNotIn(str(data / "scripts"), text, what)
        argvs = relay_argvs(text)
        self.assertTrue(argvs, what)
        self.assertEqual(len(argvs), text.count("scripts/relay.py"), what)
        for argv in argvs:
            self.assertEqual(argv[:3], [relay, "--data-root", str(data)], (what, argv))


class OneRootTests(Roots):
    def test_with_one_folder_each_text_is_what_it_was(self):
        root = self.folder("root")
        for code in ({}, {"code_root": root}):
            for name, text in texts(root, **code).items():
                self.assertEqual(text, baseline(name, root), (name, code))


class TwoRootTests(Roots):
    """Folders with a space in the name: the commands must still split right."""

    def setUp(self):
        Roots.setUp(self)
        self.code = self.folder("code dir", page=False)
        self.data = self.folder("data dir")

    def test_kits_name_the_code_and_carry_the_data_root(self):
        for h in HARNESSES:
            text = onboard.kit_text(self.data, "beta", TOKEN, entry(h), code_root=self.code)
            self.assert_two_roots(text, self.code, self.data, h)
            self.assertIn(str(self.code / "PROTOCOL.md"), text)
            self.assertIn(str(self.code / "docs" / "mcp.md"), text)
            self.assertIn(PAGE, text)  # the page address still comes from the data's roster

    def test_urgent_text_names_the_code_and_carries_the_data_root(self):
        text = urgent.render_urgent("beta", ITEMS, self.data, code_root=self.code)
        self.assert_two_roots(text, self.code, self.data, "urgent")

    def test_live_hooks_name_the_code_and_carry_the_data_root(self):
        doc = json.loads(live.hooks_json(self.data, "alpha", code_root=self.code))
        relay = str(self.code / "scripts" / "relay.py")
        for event, verb in (("SessionStart", "register"), ("SessionEnd", "unregister")):
            cmd = doc["hooks"][event][0]["hooks"][0]["command"]
            self.assertEqual(shlex.split(cmd), ["python3", relay, "--data-root", str(self.data),
                                                "live", verb, "alpha"])

    def test_hermes_setup_names_the_code_and_carries_the_data_root(self):
        text = hermes.setup_text(self.data, "beta", code_root=self.code)
        self.assert_two_roots(text, self.code, self.data, "hermes setup")
        self.assertIn('        root: %s\n        data_root: %s\n'
                      % (json.dumps(str(self.code)), json.dumps(str(self.data))), text)
        self.assertIn(shlex.quote(str(self.code / "scripts" / "hermes-plugin" / "crosstalk")), text)

    def test_hermes_check_in_names_the_code_and_carries_the_data_root(self):
        text = hermes.check_in_prompt(self.data, "beta", 30, code_root=self.code)
        self.assert_two_roots(text, self.code, self.data, "check-in")
        self.assertIn(str(self.code / "PROTOCOL.md"), text)

    def test_the_plugin_reads_the_data_root_and_names_the_code(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("hermes_plugin_texts_test", str(PLUGIN))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        settings = {"participant": "beta", "root": str(REPO), "data_root": str(self.data)}
        ctx = type("Ctx", (), {"get_config": lambda _s, k, d=None: settings.get(k, d)})()
        made = []
        real = mod.make_pump

        def make_pump(*args, **kwargs):
            made.append(real(*args, **kwargs))
            made[-1].start = lambda: None
            return made[-1]
        mod.make_pump, mod.wire = make_pump, lambda _ctx, _pump: None
        mod.register(ctx)
        self.assertEqual((made[0].root, made[0].code_root), (self.data, REPO))
        text = hermes.check_in_prompt(made[0].root, "beta", 30, code_root=made[0].code_root)
        self.assert_two_roots(text, REPO, self.data, "pump")


class CliTests(Roots):
    """relay.py itself, run from a code folder with the data in another; spaces in both."""

    def setUp(self):
        Roots.setUp(self)
        self.code = self.folder("code dir", page=False)
        self.relay = str(relay_in(self.code))
        self.data = self.folder("data dir")
        roster = json.loads(json.dumps(SPEC_ROSTER))
        roster["page"] = PAGE
        for e in roster["participants"]:
            if e["id"] == "beta":
                e["harness"] = "codex"
        (self.data / "participants.json").write_text(json.dumps(roster), encoding="utf-8")
        (self.data / "log").mkdir()
        self.live_dir = self.tmp / "live"
        self.state = self.tmp / "urgent.json"

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("CROSSTALK_DATA", live.ENV_SOCKET, live.ENV_TOKEN)}
        env.update(CROSSTALK_LIVE_DIR=str(self.live_dir), CROSSTALK_URGENT_STATE=str(self.state))
        env.update(extra)
        return env

    def run_relay(self, *args, stdin="", **env):
        return subprocess.run([sys.executable, self.relay] + list(args), input=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/", env=self.env(**env), timeout=60)

    def shell(self, cmd, stdin=""):
        return subprocess.run(["/bin/sh", "-c", cmd], input=stdin, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, cwd="/",
                              env=self.env(), timeout=60)

    def append_line(self, kit):
        """The kit's `python3 ... append ...` shell line."""
        return next(x for x in kit.splitlines() if x.startswith("python3 ") and " append " in x)

    def assert_kit(self, kit_path, pid):
        kit_path = Path(kit_path)
        self.assertEqual(kit_path.parent, self.data / "kits")
        kit = kit_path.read_text(encoding="utf-8")
        self.assert_two_roots(kit, self.code, self.data, pid)
        p = self.shell(self.append_line(kit))
        self.assertEqual(p.returncode, 0, p.stderr)
        log = (self.data / "log" / (pid + ".jsonl")).read_text(encoding="utf-8")
        self.assertIn('"%s-0001"' % pid, log)
        self.assertFalse((self.code / "log").exists())
        (self.data / "log" / (pid + ".jsonl")).unlink()

    def test_onboard_and_kit_with_the_flag_and_with_the_env(self):
        p = self.run_relay("--data-root", str(self.data), "onboard", "gamma", "--name", "Gamma",
                           "--kind", "project", "--harness", "codex")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assert_kit(p.stdout.splitlines()[0], "gamma")
        p = self.run_relay("kit", "gamma", CROSSTALK_DATA=str(self.data))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assert_kit(p.stdout.splitlines()[0], "gamma")

    def test_live_hooks_run_against_the_data_root(self):
        p = self.run_relay("--data-root", str(self.data), "live", "hooks", "alpha")
        self.assertEqual(p.returncode, 0, p.stderr)
        start = json.loads(p.stdout)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        # alpha is only in the data's roster: the hook gets past the roster check
        # and stops at the missing socket, and writes nothing.
        r = self.shell(start, stdin='{"session_id": "s1"}')
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("unknown participant", r.stderr)
        self.assertIn(live.ENV_SOCKET, r.stderr)
        self.assertFalse(self.live_dir.exists())

    def test_live_hooks_for_hermes(self):
        p = self.run_relay("--data-root", str(self.data), "live", "hooks", "beta",
                           "--harness", "hermes")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assert_two_roots(p.stdout, self.code, self.data, "live hooks --harness hermes")

    def test_urgent_hook_text_names_the_code_and_its_command_runs(self):
        stop = json.dumps({"session_id": "s1", "hook_event_name": "Stop"})
        args = ("--data-root", str(self.data), "urgent", "--as", "beta", "--hook", "codex")
        self.assertEqual(self.run_relay(*args, stdin=stop).stdout, "")  # seeds the backlog
        data = {"from": "owner", "kind": "owner", "summary": "hi"}
        (self.data / "log" / "owner.jsonl").write_text(
            json.dumps(line(1, "owner", id_="m-1", data=data)) + "\n", encoding="utf-8")
        p = self.run_relay(*args, stdin=stop)
        text = json.loads(p.stdout)["reason"]
        self.assertIn("(m-1)", text)
        self.assert_two_roots(text, self.code, self.data, "urgent")
        inbox = re.search(r"`(python3 [^`]*)`", text).group(1)
        r = self.shell(inbox)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("m-1", r.stdout)


def schedule_roster():
    """SPEC_ROSTER with alpha on Claude Code and beta on Hermes, as the baseline was taken."""
    roster = json.loads(json.dumps(SPEC_ROSTER))
    for e in roster["participants"]:
        e["harness"] = {"alpha": "claude-code", "beta": "hermes"}.get(e["id"], e.get("harness"))
    return roster


def relay_heads(text):
    """The first three words after each `python3 ` in text, shell-split.

    A Hermes line is split first, so the commands inside its quoted prompt are
    found too. The words are read lazily: the prose after them may hold an
    apostrophe that is not shell quoting.
    """
    out = []
    for raw in text.splitlines():
        if raw.startswith("hermes cron create "):
            parts = shlex.split(raw)
        else:
            parts = [raw]
        for part in parts:
            for chunk in part.split("python3 ")[1:]:
                lex = shlex.shlex(chunk, posix=True)
                lex.whitespace_split = True
                out.append([lex.get_token() for _ in range(3)])
    return out


class ScheduleTests(Roots):
    """`relay.py schedule standard`: the standard jobs name relay.py and the data root."""

    def setUp(self):
        Roots.setUp(self)
        self.env = {k: v for k, v in os.environ.items() if k != "CROSSTALK_DATA"}

    def data_folder(self, name):
        d = self.tmp / name
        d.mkdir()
        (d / "participants.json").write_text(json.dumps(schedule_roster()), encoding="utf-8")
        (d / "log").mkdir()
        return d

    def run_relay(self, relay, *args):
        p = subprocess.run([sys.executable, str(relay)] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, cwd="/",
                           env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_with_one_folder_the_text_is_what_it_was(self):
        root = self.data_folder("root")
        relay = relay_in(root)
        for pid in ("alpha", "beta"):
            self.assertEqual(self.run_relay(relay, "schedule", "standard", "--from", pid),
                             baseline("schedule-standard-%s.txt" % pid, root))

    def test_without_roots_the_text_names_this_checkout_as_before(self):
        from crosstalk import schedules
        peers = ["alpha", "beta"]
        self.assertEqual(schedules.standard_text("alpha", "claude-code", peers=peers),
                         schedules.standard_text("alpha", "claude-code", peers=peers,
                                                 root=REPO, code_root=REPO))

    def test_with_a_data_root_each_command_names_the_code_and_the_data(self):
        code = self.tmp / "code dir"
        code.mkdir()
        relay = relay_in(code)
        data = self.data_folder("data dir")
        for pid in ("alpha", "beta"):
            text = self.run_relay(relay, "--data-root", str(data), "schedule", "standard", "--from", pid)
            self.assertNotIn(str(data / "scripts"), text)
            heads = relay_heads(text)
            self.assertEqual(len(heads), text.count("scripts/relay.py"), pid)
            for head in heads:
                self.assertEqual(head, [str(relay), "--data-root", str(data)], pid)
            # The `schedule set` line runs in a shell and reports into the data folder.
            line_ = next(x for x in text.splitlines() if " schedule set " in x)
            p = subprocess.run(["/bin/sh", "-c", line_], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, cwd="/",
                               env=self.env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('"%s-sched-check-in"' % pid,
                          (data / "log" / (pid + ".jsonl")).read_text(encoding="utf-8"))
        self.assertFalse((code / "log").exists())


class OneRootCliTests(Roots):
    """relay.py with no data root: what it writes and prints matches the baseline."""

    def test_onboard_kit_and_hooks_match_the_baseline(self):
        root = self.folder("root")
        relay = str(relay_in(root))
        env = {k: v for k, v in os.environ.items() if k != "CROSSTALK_DATA"}

        def run(*args):
            p = subprocess.run([sys.executable, relay] + list(args), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, cwd="/",
                               env=env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            return p.stdout

        kit_path, token = run("onboard", "beta", "--name", "Beta", "--kind", "project",
                              "--harness", "codex").splitlines()
        kit = Path(kit_path).read_text(encoding="utf-8").replace(token, TOKEN)
        self.assertEqual(kit, baseline("kit-codex.md", root))
        self.assertEqual(run("live", "hooks", "alpha"), baseline("live-hooks.json", root) + "\n")
        self.assertEqual(run("live", "hooks", "beta", "--harness", "hermes"),
                         baseline("hermes-setup.md", root))


if __name__ == "__main__":
    unittest.main()
