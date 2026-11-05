"""Hermes Agent sessions: the shell hook, live registrations, the kit, the plugin's pump."""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402,F401  (puts scripts/ on sys.path)
from helpers import SPEC_ROSTER, TempRepo, line  # noqa: E402
from test_inbox import req  # noqa: E402

from crosstalk import hermes, live, urgent  # noqa: E402
from crosstalk.notify import Notifier  # noqa: E402
from crosstalk.onboard import kit_text  # noqa: E402

T0 = 1793016000.0  # 2026-10-26T12:00:00Z
REPO = Path(__file__).resolve().parent.parent
RELAY = REPO / "scripts" / "relay.py"
PLUGIN = REPO / "scripts" / "hermes-plugin" / "crosstalk" / "__init__.py"


def hermes_roster():
    data = json.loads(json.dumps(SPEC_ROSTER))
    for entry in data["participants"]:
        if entry["id"] == "beta":
            entry["harness"] = "hermes"
    return data


def owner_msg(id_, **extra):
    data = {"from": "owner", "kind": "owner", "summary": "hi " + id_}
    data.update(extra)
    return line(1, "owner", id_=id_, data=data)


def event(name, **extra):
    data = {"hook_event_name": name, "tool_name": None, "tool_input": None,
            "session_id": "s1", "cwd": "/tmp", "profile": "beta", "extra": {}}
    data.update(extra)
    return json.dumps(data)


class Base(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(hermes_roster())
        self.root = self.r.root
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-hermes-test-"))
        self.state = self.tmp / "urgent.json"
        self.live = self.tmp / "live"
        self._env = os.environ.get("CROSSTALK_LIVE_DIR")
        os.environ["CROSSTALK_LIVE_DIR"] = str(self.live)
        self.now = [T0]
        self.logs = {}

    def tearDown(self):
        if self._env is None:
            os.environ.pop("CROSSTALK_LIVE_DIR", None)
        else:
            os.environ["CROSSTALK_LIVE_DIR"] = self._env
        shutil.rmtree(str(self.tmp), ignore_errors=True)
        self.r.__exit__(None, None, None)

    def append(self, pid, *msgs):
        lines = self.logs.setdefault(pid, [])
        for m in msgs:
            m["seq"] = len(lines) + 1
            lines.append(m)
        self.r.write_log(pid, lines)

    def hook(self, name="pre_llm_call", pid="beta", **extra):
        return urgent.hook_output(self.root, pid, "hermes", event(name, **extra), self.state,
                                  clock=lambda: self.now[0])


class HookTests(Base):
    def test_session_start_seeds_then_pre_llm_call_gives_new_items_as_context(self):
        self.append("owner", owner_msg("m-old", to=["beta"]))
        self.assertEqual(self.hook("on_session_start"), "")
        self.assertEqual(self.hook(), "")
        # every item, not just urgent ones: an ordinary request comes through
        self.append("alpha", req(1, "alpha", "m-req", to=["beta"]))
        out = json.loads(self.hook())
        self.assertEqual(set(out), {"context"})
        self.assertIn("- request from Alpha:", out["context"])
        self.assertIn("(m-req)", out["context"])
        self.assertNotIn("m-old", out["context"])
        self.assertTrue(out["context"].startswith("Crosstalk: 1 new item for beta."))
        self.assertEqual(self.hook(), "")  # once
        st = json.loads(self.state.read_text())
        self.assertEqual(sorted(st["hooks"]), ["hermes:beta"])

    def test_first_sight_seeds_without_session_start(self):
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertEqual(self.hook(), "")
        self.append("owner", owner_msg("m-2", to=["beta"]))
        self.assertIn("(m-2)", json.loads(self.hook())["context"])

    def test_no_gap_between_turns(self):
        self.hook("on_session_start")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertNotEqual(self.hook(), "")
        self.append("owner", owner_msg("m-2", to=["beta"]))
        self.now[0] += 1
        self.assertIn("(m-2)", self.hook())

    def test_errors_are_silent(self):
        self.hook("on_session_start")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(self.hook("post_tool_call"), "")
            self.assertEqual(self.hook(pid="nobody"), "")
            self.assertEqual(urgent.hook_output(self.root, "beta", "hermes", "{nope",
                                                self.state), "")
            self.assertEqual(urgent.hook_output(self.root, "beta", "hermes",
                                                event("pre_llm_call"), None), "")
        self.assertEqual(err.getvalue(), "")
        self.assertIn("(m-1)", self.hook())

    def test_cli_prints_context_and_exits_zero(self):
        # The CLI uses the real checkout's roster, so only its silent paths are checked.
        env = dict(os.environ, CROSSTALK_URGENT_STATE=str(self.state))
        for args, stdin in ((["--as", "no-such-participant-x", "--hook", "hermes"],
                             event("pre_llm_call")),
                            (["--as", "beta", "--hook", "hermes"], "garbage")):
            p = subprocess.run([sys.executable, str(RELAY), "urgent"] + args, input=stdin,
                               capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    def test_fallback_covers_hermes_participants(self):
        self.assertIn("hermes", urgent.HOOK_HARNESSES)
        calls = []
        fb = urgent.Fallback(self.state, clock=lambda: self.now[0],
                             notify=lambda t, m: calls.append((t, m)))
        fb.poll(*hermes.load_snapshot(self.root))  # first sight
        self.append("owner", owner_msg("m-1", to=["beta"]))
        fb.poll(*hermes.load_snapshot(self.root))  # starts the clock
        self.now[0] += urgent.FALLBACK_SEC
        fb.poll(*hermes.load_snapshot(self.root))
        self.assertEqual(len(calls), 1)
        self.assertIn("Beta", calls[0][0])


class LiveTests(Base):
    def test_register_hermes_writes_private_file_without_socket_or_token(self):
        path = live.register_hermes(self.root, "beta", "20261104_1", "agent:main:local:x", T0)
        self.assertIsNotNone(path)
        doc = json.loads(path.read_text())
        self.assertEqual(doc["harness"], "hermes")
        self.assertEqual(doc["session_key"], "agent:main:local:x")
        self.assertEqual(doc["host_pid"], os.getpid())
        self.assertNotIn("token", doc)
        self.assertNotIn("socket", doc)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        regs, problems = live.load_registrations(self.root)
        self.assertEqual(problems, [])
        self.assertEqual([(r.pid, r.harness, r.session_key) for r in regs],
                         [("beta", "hermes", "agent:main:local:x")])
        self.assertTrue(live.unregister_session("beta", "20261104_1"))
        self.assertEqual(live.load_registrations(self.root), ([], []))

    def test_register_hermes_refuses_bad_input(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(live.register_hermes(self.root, "nobody", "s1", "k", T0))
            self.assertIsNone(live.register_hermes(self.root, "beta", "../x", "k", T0))
            self.assertIsNone(live.register_hermes(self.root, "beta", "s1", "a\nb", T0))
            self.assertIsNone(live.register_hermes(self.root, "beta", "s1", "k", T0,
                                                   host_pid=2 ** 22 + 12345))

    def dead_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid

    def test_dead_host_is_a_problem_and_the_notifier_drops_it(self):
        self.live.mkdir(mode=0o700)
        (self.live / "beta--s9.json").write_text(json.dumps(
            {"pid": "beta", "session_id": "s9", "harness": "hermes", "session_key": "k",
             "host_pid": self.dead_pid(), "registered": "x"}))
        regs, problems = live.load_registrations(self.root)
        self.assertEqual(regs, [])
        self.assertEqual(problems, ["beta--s9.json: host process gone"])
        n = Notifier(self.root, lambda: (hermes.load_snapshot(self.root)[0], {}), None)
        with redirect_stderr(io.StringIO()):
            n.poll_once()
        self.assertFalse((self.live / "beta--s9.json").exists())

    def test_notifier_never_sends_to_hermes_but_shows_it_live(self):
        live.register_hermes(self.root, "beta", "s1", "k", T0)
        sent = []
        n = Notifier(self.root, lambda: (hermes.load_snapshot(self.root)[0], {}), None,
                     send=lambda *a: sent.append(a))
        n.poll_once()
        self.append("owner", owner_msg("m-1", to=["beta"]))
        n.poll_once()
        self.assertEqual(sent, [])
        self.assertEqual(n.status()["beta"]["sessions"], 1)
        self.assertTrue((self.live / "beta--s1.json").exists())

    def test_cli_status_and_hooks(self):
        live.register_hermes(self.root, "alpha", "s1", "k", T0)
        env = dict(os.environ, CROSSTALK_LIVE_DIR=str(self.live))
        p = subprocess.run([sys.executable, str(RELAY), "live", "hooks", "beta", "--harness",
                            "hermes"], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("allow_gateway_injection: true", p.stdout)
        self.assertIn("urgent --as beta --hook hermes", p.stdout)
        self.assertIn("hermes -p beta plugins enable crosstalk", p.stdout)
        self.assertIn(str(REPO / "scripts" / "hermes-plugin" / "crosstalk"), p.stdout)
        p = subprocess.run([sys.executable, str(RELAY), "live", "hooks", "beta"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertIn("SessionStart", json.loads(p.stdout)["hooks"])


class KitTests(Base):
    def test_hermes_kit_has_profile_plugin_and_hooks(self):
        text = kit_text(self.root, "beta", "tok-XYZ", {"name": "Beta", "kind": "project",
                                                       "harness": "hermes"})
        mcp = text.split("## MCP server", 1)[1].split("## Your key", 1)[0]
        for want in ("~/.hermes/profiles/beta/config.yaml", "hermes profile create beta",
                     "one Hermes profile per participant", "HERMES.md",
                     "hooks:\n  on_session_start:\n", "  pre_llm_call:\n",
                     "urgent --as beta --hook hermes", "plugins:\n  enabled:\n    - crosstalk",
                     "allow_gateway_injection: true", 'participant: "beta"',
                     "hermes -p beta plugins enable crosstalk", "mcp_servers:"):
            self.assertIn(want, mcp)
        self.assertNotIn("tok-XYZ", mcp)
        codex = kit_text(self.root, "beta", "t", {"name": "Beta", "kind": "project",
                                                  "harness": "codex"})
        self.assertNotIn("allow_gateway_injection", codex)


class PumpBase(Base):
    def setUp(self):
        Base.setUp(self)
        self.injected = []
        self.accept = True
        self.log = []
        self.pump = hermes.Pump(self.root, "beta", self.state, self.inject,
                                clock=lambda: self.now[0], log=self.log.append)

    def inject(self, text, key):
        self.injected.append((text, key))
        return self.accept


class PumpTests(PumpBase):
    def test_session_start_registers_and_seeds(self):
        self.append("owner", owner_msg("m-old", to=["beta"]))
        self.pump.on_session_start("s1", "key-1")
        regs, _ = live.load_registrations(self.root)
        self.assertEqual([(r.session_id, r.session_key, r.harness) for r in regs],
                         [("s1", "key-1", "hermes")])
        self.pump.poll_once()
        self.assertEqual(self.injected, [])

    def test_busy_session_is_never_injected_then_idle_gets_the_items(self):
        self.pump.on_session_start("s1", "key-1")
        self.assertIsNone(self.pump.on_turn_start("s1", "key-1"))
        self.append("alpha", req(1, "alpha", "m-req", to=["beta"]))
        self.pump.poll_once()
        self.assertEqual(self.injected, [])
        # between tool calls: a note, once per turn
        out = self.pump.on_tool_result("s1", '{"ok": true}')
        self.assertTrue(out.startswith('{"ok": true}\n\n[Crosstalk: 1 new item for beta (m-req).'))
        self.assertIsNone(self.pump.on_tool_result("s1", "x"))
        self.pump.on_turn_end("s1")
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        text, key = self.injected[0]
        self.assertEqual(key, "key-1")
        self.assertTrue(text.startswith("[Sent by the Crosstalk plugin for Hermes, not typed"))
        self.assertIn("(m-req)", text)
        # the injected turn starts and ends; nothing is sent twice
        self.pump.on_turn_start("s1", "key-1")
        self.pump.on_turn_end("s1")
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)

    def test_no_second_injection_before_the_injected_turn_starts(self):
        self.pump.on_session_start("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.pump.poll_once()
        self.append("owner", owner_msg("m-2", to=["beta"]))
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        self.now[0] += hermes.INJECT_GRACE_SEC  # the turn never began: free again
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 2)
        self.assertIn("(m-2)", self.injected[1][0])

    def test_turn_start_gives_new_items_as_context_and_shares_the_told_set(self):
        self.pump.on_session_start("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        text = self.pump.on_turn_start("s1", "k")
        self.assertIn("(m-1)", text)
        self.pump.on_turn_end("s1")
        self.pump.poll_once()
        self.assertEqual(self.injected, [])
        # the shell hook sees it as told too
        self.assertEqual(self.hook(), "")

    def test_refused_injection_is_released_and_retried_later(self):
        self.pump.on_session_start("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.accept = False
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        self.accept = True
        self.now[0] += 30
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        self.now[0] += hermes.RETRY_SEC
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 2)
        self.assertIn("(m-1)", self.injected[1][0])

    def test_check_in_prompt_when_due_and_idle(self):
        self.pump.on_session_start("s1", "k")
        self.pump.on_turn_start("s1", "k")
        self.now[0] += 30 * 60
        self.pump.poll_once()
        self.assertEqual(self.injected, [])  # busy: it waits
        self.pump.on_turn_end("s1")
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        self.assertIn("Crosstalk check-in for beta (every 30 minutes)", self.injected[0][0])
        self.assertIn("relay.py inbox beta", self.injected[0][0])
        self.pump.on_turn_start("s1", "k")
        self.pump.on_turn_end("s1")
        self.now[0] += 29 * 60
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        self.now[0] += 60
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 2)

    def test_a_check_in_in_the_log_resets_the_timer(self):
        self.pump.on_session_start("s1", "k")
        self.now[0] += 25 * 60
        self.append("beta", line(1, "beta", id_="beta-ci", t="2026-10-26T12:25:00Z",
                                 data={"from": "beta", "kind": "check-in", "summary": "ci"}))
        self.now[0] += 10 * 60
        self.pump.poll_once()
        self.assertEqual(self.injected, [])
        self.now[0] += 20 * 60
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)

    def test_subagents_are_not_sessions(self):
        self.pump.on_session_start("s1", "k")
        self.pump.on_turn_start("s1", "k")
        self.pump.on_session_start("child", "k")
        self.assertIsNone(self.pump.on_turn_start("child2", "k2", parent="s1"))
        self.assertEqual(sorted(self.pump.sessions), ["s1"])

    def test_compaction_and_finalize_move_and_remove_the_registration(self):
        self.pump.on_session_start("s1", "k")
        self.pump.on_compacted("s1", "s2")
        regs, _ = live.load_registrations(self.root)
        self.assertEqual([r.session_id for r in regs], ["s2"])
        self.pump.on_session_finalize("s2")
        self.assertEqual(live.load_registrations(self.root), ([], []))

    def test_no_state_file_means_no_delivery(self):
        pump = hermes.Pump(self.root, "beta", None, self.inject, clock=lambda: self.now[0])
        pump.on_session_start("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertIsNone(pump.on_turn_start("s1", "k"))
        pump.on_turn_end("s1")
        pump.poll_once()
        self.assertEqual(self.injected, [])

    def test_broken_roster_never_raises(self):
        self.pump.on_session_start("s1", "k")
        (self.root / "participants.json").write_text("{nope", encoding="utf-8")
        self.assertIsNone(self.pump.on_turn_start("s1", "k"))
        self.pump.on_turn_end("s1")
        self.pump.poll_once()
        self.assertEqual(self.injected, [])
        self.assertTrue(self.log)


class FakeCtx:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = {}
        self.unload = []
        self.injected = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def on_unload(self, fn):
        self.unload.append(fn)

    def inject_message(self, content, role="user", *, session_key=None, origin=None):
        self.injected.append((content, role, session_key))
        return True


class PluginTests(Base):
    def load_plugin(self):
        spec = importlib.util.spec_from_file_location("hermes_plugins_test_crosstalk", str(PLUGIN))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_register_wires_the_hooks_and_unload_stops(self):
        os.environ["CROSSTALK_URGENT_STATE"] = str(self.state)
        try:
            mod = self.load_plugin()
            self.assertEqual(mod.DEFAULT_ROOT, REPO)
            ctx = FakeCtx({"participant": "beta", "root": str(self.root)})
            mod.register(ctx)
            self.assertEqual(sorted(ctx.hooks),
                             ["on_session_end", "on_session_finalize", "on_session_start",
                              "post_compaction", "pre_llm_call", "transform_tool_result"])
            ctx.hooks["on_session_start"](session_id="s1", model="m", platform="cli",
                                          telemetry_schema_version="x")
            self.append("owner", owner_msg("m-1", to=["beta"]))
            out = ctx.hooks["pre_llm_call"](session_id="s1", user_message="hi",
                                            conversation_history=[], is_first_turn=False,
                                            model="m", platform="cli", parent_session_id="")
            self.assertIn("(m-1)", out["context"])
            self.assertIsNone(ctx.hooks["transform_tool_result"](tool_name="t", result="r",
                                                                 session_id="s1"))
            ctx.hooks["on_session_end"](session_id="s1", completed=True, interrupted=False)
            self.assertEqual(len(live.load_registrations(self.root)[0]), 1)
            for fn in ctx.unload:
                fn()
            self.assertEqual(live.load_registrations(self.root), ([], []))
        finally:
            os.environ.pop("CROSSTALK_URGENT_STATE", None)

    def test_without_participant_it_registers_nothing(self):
        mod = self.load_plugin()
        ctx = FakeCtx({})
        mod.register(ctx)
        self.assertEqual(ctx.hooks, {})


if __name__ == "__main__":
    unittest.main()
