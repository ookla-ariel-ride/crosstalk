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
    def test_pre_llm_call_gives_new_items_as_context(self):
        self.append("owner", owner_msg("m-old", to=["beta"]))
        self.assertIn("(m-old)", json.loads(self.hook())["context"])
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

    def test_first_sight_delivers_the_backlog(self):
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertIn("(m-1)", json.loads(self.hook())["context"])
        self.append("owner", owner_msg("m-2", to=["beta"]))
        out = json.loads(self.hook())["context"]
        self.assertIn("(m-2)", out)
        self.assertNotIn("(m-1)", out)

    def test_on_session_start_tells_nothing(self):
        # Hermes fires it for delegated children and on a prompt rebuild.
        self.hook()
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertEqual(self.hook("on_session_start", session_id="child"), "")
        self.assertEqual(hermes.told(self.state, "beta"), [])
        self.assertIn("(m-1)", self.hook())

    def test_a_subagent_or_cron_turn_claims_nothing(self):
        self.hook()
        self.append("owner", owner_msg("m-1", to=["beta"]))
        for platform in ("subagent", "cron"):
            self.assertEqual(self.hook(session_id="child", extra={"platform": platform}), "")
        self.assertEqual(hermes.told(self.state, "beta"), [])
        self.assertIn("(m-1)", self.hook(extra={"platform": "cli"}))

    def test_an_item_a_child_skipped_still_reaches_the_fallback(self):
        calls = []
        fb = urgent.Fallback(self.state, clock=lambda: self.now[0],
                             notify=lambda t, m: calls.append((t, m)))
        fb.poll(*hermes.load_snapshot(self.root))
        self.hook()
        self.append("owner", owner_msg("m-1", to=["beta"]))
        fb.poll(*hermes.load_snapshot(self.root))
        self.hook("on_session_start", session_id="child")
        self.hook(session_id="child", extra={"platform": "subagent"})
        self.now[0] += urgent.FALLBACK_SEC
        fb.poll(*hermes.load_snapshot(self.root))
        self.assertEqual(len(calls), 1)

    def test_no_gap_between_turns(self):
        self.hook()
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertNotEqual(self.hook(), "")
        self.append("owner", owner_msg("m-2", to=["beta"]))
        self.now[0] += 1
        self.assertIn("(m-2)", self.hook())

    def test_errors_are_silent(self):
        self.hook()
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
                     "hooks:\n  pre_llm_call:\n", "Don't add it beside the plugin",
                     "a gateway never asks", "the classic CLI doesn't check it",
                     "urgent --as beta --hook hermes", "plugins:\n  enabled:\n    - crosstalk",
                     "allow_gateway_injection: true", 'participant: "beta"',
                     "hermes -p beta plugins enable crosstalk", "mcp_servers:"):
            self.assertIn(want, mcp)
        self.assertNotIn("on_session_start", mcp)
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

    def session(self, sid, key=""):
        """A session that has had its first turn and is idle."""
        self.pump.on_turn_start(sid, key)
        self.pump.on_turn_end(sid)


class PumpTests(PumpBase):
    def test_first_turn_registers_and_delivers_the_backlog(self):
        self.append("owner", owner_msg("m-old", to=["beta"]))
        self.assertIn("(m-old)", self.pump.on_turn_start("s1", "key-1"))
        regs, _ = live.load_registrations(self.root)
        self.assertEqual([(r.session_id, r.session_key, r.harness) for r in regs],
                         [("s1", "key-1", "hermes")])
        self.pump.on_turn_end("s1")
        self.pump.poll_once()
        self.assertEqual(self.injected, [])

    def test_busy_session_is_never_injected_then_idle_gets_the_items(self):
        self.session("s1", "key-1")
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
        self.session("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.pump.poll_once()
        self.append("owner", owner_msg("m-2", to=["beta"]))
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        self.now[0] += hermes.INJECT_GRACE_SEC  # the turn never began: free again
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 2)
        self.assertIn("(m-1)", self.injected[1][0])
        self.assertIn("(m-2)", self.injected[1][0])

    def test_turn_start_gives_new_items_as_context_and_shares_the_told_set(self):
        self.session("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        text = self.pump.on_turn_start("s1", "k")
        self.assertIn("(m-1)", text)
        self.pump.on_turn_end("s1")
        self.pump.poll_once()
        self.assertEqual(self.injected, [])
        # the shell hook sees it as told too
        self.assertEqual(self.hook(), "")

    def test_refused_injection_is_released_and_retried_later(self):
        self.session("s1", "k")
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
        self.session("s1", "k")
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
        self.session("s1", "k")
        self.now[0] += 25 * 60
        self.append("beta", line(1, "beta", id_="beta-ci", t="2026-10-26T12:25:00Z",
                                 data={"from": "beta", "kind": "check-in", "summary": "ci"}))
        self.now[0] += 10 * 60
        self.pump.poll_once()
        self.assertEqual(self.injected, [])
        self.now[0] += 20 * 60
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)

    def test_a_session_with_no_key_uses_its_id(self):
        self.session("s1")  # the classic CLI has no HERMES_SESSION_KEY
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.pump.poll_once()
        self.assertEqual([k for _t, k in self.injected], ["s1"])

    def test_compaction_and_finalize_move_and_remove_the_registration(self):
        self.session("s1", "k")
        self.pump.on_compacted("s1", "s2")
        regs, _ = live.load_registrations(self.root)
        self.assertEqual([r.session_id for r in regs], ["s2"])
        self.pump.on_session_finalize("s2")
        self.assertEqual(live.load_registrations(self.root), ([], []))

    def test_no_state_file_means_no_delivery(self):
        pump = hermes.Pump(self.root, "beta", None, self.inject, clock=lambda: self.now[0])
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertIsNone(pump.on_turn_start("s1", "k"))
        pump.on_turn_end("s1")
        pump.poll_once()
        self.assertEqual(self.injected, [])

    def test_broken_roster_never_raises(self):
        self.session("s1", "k")
        (self.root / "participants.json").write_text("{nope", encoding="utf-8")
        self.assertIsNone(self.pump.on_turn_start("s1", "k"))
        self.pump.on_turn_end("s1")
        self.pump.poll_once()
        self.assertEqual(self.injected, [])
        self.assertTrue(self.log)


class SubagentTests(PumpBase):
    """A child is never a session, whatever its key, and whether or not main is busy."""

    def child_turn(self, key=""):
        self.assertIsNone(self.pump.on_turn_start("child", key, parent="main"))
        self.assertIsNone(self.pump.on_tool_result("child", "r"))
        self.pump.on_turn_end("child")

    def test_classic_cli_child_never_gets_an_injection_while_main_is_busy(self):
        self.pump.on_turn_start("main")  # no key on the classic CLI
        self.child_turn()
        self.assertEqual(sorted(self.pump.sessions), ["main"])
        self.assertEqual([r.session_id for r in live.load_registrations(self.root)[0]], ["main"])
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.pump.poll_once()
        self.assertEqual(self.injected, [])
        self.assertTrue(self.pump.sessions["main"].busy)

    def test_a_child_swallows_nothing(self):
        self.pump.on_turn_start("main")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.pump.poll_once()
        self.child_turn()
        self.assertEqual(hermes.told(self.state, "beta"), [])
        self.pump.on_turn_end("main")
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        self.assertIn("(m-1)", self.injected[0][0])

    def test_gateway_async_child_outlives_the_parents_turn(self):
        self.pump.on_turn_start("main", "k")
        self.pump.on_turn_end("main")  # delegated async: main's turn is over
        self.child_turn("k")  # same key, main idle
        self.assertEqual(sorted(self.pump.sessions), ["main"])
        self.pump.on_turn_start("main", "k")
        self.pump.on_turn_end("child")  # the child's end doesn't free main
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.pump.poll_once()
        self.assertEqual(self.injected, [])

    def test_a_cron_turn_is_not_a_session(self):
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertIsNone(self.pump.on_turn_start("cron_1", "", platform="cron"))
        self.assertEqual(self.pump.sessions, {})
        self.assertIsNone(hermes.told(self.state, "beta"))


class LostInjectionTests(PumpBase):
    """inject_message's True means only scheduled."""

    def lost(self):
        self.session("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.pump.poll_once()
        self.assertEqual(hermes.told(self.state, "beta"), ["m-1"])

    def test_items_are_offered_again_after_the_grace_period(self):
        self.lost()
        self.now[0] += hermes.INJECT_GRACE_SEC - 1
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        self.now[0] += 1
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 2)
        self.assertIn("(m-1)", self.injected[1][0])

    def test_a_turn_that_begins_settles_the_claim(self):
        self.lost()
        self.pump.on_turn_start("s1", "k")
        self.pump.on_turn_end("s1")
        self.now[0] += hermes.INJECT_GRACE_SEC
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)
        self.assertEqual(hermes.told(self.state, "beta"), ["m-1"])

    def test_a_stray_turn_end_doesnt_settle_it(self):
        self.lost()
        self.pump.on_turn_end("s1")
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 1)  # still awaiting: no second injection
        self.now[0] += hermes.INJECT_GRACE_SEC
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 2)

    def test_a_lost_check_in_releases_what_it_counted_as_told(self):
        self.session("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.now[0] += 30 * 60
        self.pump.poll_once()
        self.assertIn("Crosstalk check-in", self.injected[0][0])
        self.assertEqual(hermes.told(self.state, "beta"), ["m-1"])
        self.now[0] += hermes.INJECT_GRACE_SEC
        self.pump.poll_once()
        self.assertEqual(len(self.injected), 2)
        self.assertIn("(m-1)", self.injected[1][0])

    def test_a_refused_check_in_tells_nothing(self):
        self.session("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.now[0] += 30 * 60
        self.accept = False
        self.pump.poll_once()
        self.assertEqual(hermes.told(self.state, "beta"), [])

    def test_a_session_that_ends_releases_to_the_fallback(self):
        calls = []
        fb = urgent.Fallback(self.state, clock=lambda: self.now[0],
                             notify=lambda t, m: calls.append((t, m)))
        fb.poll(*hermes.load_snapshot(self.root))
        self.session("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        fb.poll(*hermes.load_snapshot(self.root))
        self.pump.poll_once()
        self.pump.on_session_finalize("s1")
        self.assertEqual(hermes.told(self.state, "beta"), [])
        self.now[0] += urgent.FALLBACK_SEC
        fb.poll(*hermes.load_snapshot(self.root))
        self.assertEqual(len(calls), 1)

    def test_unload_releases_too(self):
        self.lost()
        self.pump.unregister_all()
        self.assertEqual(hermes.told(self.state, "beta"), [])


class BacklogTests(PumpBase):
    """A gateway restart or a resumed session: what came in meanwhile is delivered."""

    def test_after_a_restart_only_the_outage_is_delivered(self):
        self.session("s1", "k")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertIn("(m-1)", self.pump.on_turn_start("s1", "k"))
        restarted = hermes.Pump(self.root, "beta", self.state, self.inject,
                                clock=lambda: self.now[0])
        self.append("owner", owner_msg("m-2", to=["beta"]))
        restarted.poll_once()  # nothing to deliver to before the first turn
        self.assertEqual(self.injected, [])
        text = restarted.on_turn_start("s1", "k")
        self.assertIn("(m-2)", text)
        self.assertNotIn("(m-1)", text)

    def test_a_new_session_doesnt_swallow_what_another_was_waiting_for(self):
        self.pump.on_turn_start("s1", "k1")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertIn("(m-1)", self.pump.on_turn_start("s2", "k2"))
        self.assertEqual(hermes.told(self.state, "beta"), ["m-1"])


class FakeCli:
    def __init__(self, running):
        self._agent_running = running


class FakeManager:
    def __init__(self, cli):
        self._cli_ref = cli


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
                             ["on_session_end", "on_session_finalize",
                              "post_compaction", "pre_llm_call", "transform_tool_result"])
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

    def test_a_classic_cli_delegate_replayed_through_the_hooks(self):
        # The order Hermes fires them in, with no session key anywhere.
        os.environ["CROSSTALK_URGENT_STATE"] = str(self.state)
        try:
            mod = self.load_plugin()
            ctx = FakeCtx({})
            pump = mod.make_pump(ctx, self.root, "beta")
        finally:
            os.environ.pop("CROSSTALK_URGENT_STATE", None)
        mod.wire(ctx, pump)

        def fire(name, **kwargs):
            if name in ctx.hooks:
                return ctx.hooks[name](**kwargs)

        fire("on_session_start", session_id="main", model="m", platform="cli")
        fire("pre_llm_call", session_id="main", platform="cli", parent_session_id="")
        fire("on_session_start", session_id="child", model="m", platform="subagent")
        fire("pre_llm_call", session_id="child", platform="subagent", parent_session_id="main")
        fire("on_session_end", session_id="child", completed=True, interrupted=False)
        self.append("owner", owner_msg("m-1", to=["beta"]))
        pump.poll_once()
        self.assertEqual(ctx.injected, [])  # main is busy
        fire("on_session_start", session_id="child2", model="m", platform="subagent")
        fire("on_session_end", session_id="main", completed=True, interrupted=False)
        pump.poll_once()
        self.assertEqual([k for _t, _r, k in ctx.injected], ["main"])
        self.assertIn("(m-1)", ctx.injected[0][0])

    def test_pre_llm_call_passes_on_what_shows_a_non_session(self):
        mod = self.load_plugin()
        ctx = FakeCtx({})
        pump = mod.make_pump(ctx, self.root, "beta")
        mod.wire(ctx, pump)
        args = dict(user_message="hi", conversation_history=[], is_first_turn=True, model="m")
        ctx.hooks["pre_llm_call"](session_id="c1", platform="subagent", parent_session_id="s1",
                                  **args)
        ctx.hooks["pre_llm_call"](session_id="c2", platform="cron", parent_session_id="", **args)
        self.assertEqual(pump.sessions, {})
        ctx.hooks["pre_llm_call"](session_id="s1", platform="cli", parent_session_id="", **args)
        self.assertEqual(sorted(pump.sessions), ["s1"])

    def test_inject_refuses_while_the_classic_cli_agent_runs(self):
        mod = self.load_plugin()
        ctx = FakeCtx({})
        ctx._manager = FakeManager(FakeCli(True))
        pump = mod.make_pump(ctx, self.root, "beta")
        self.assertFalse(pump.inject("text", "s1"))
        self.assertEqual(ctx.injected, [])
        ctx._manager._cli_ref._agent_running = False
        self.assertTrue(pump.inject("text", "s1"))
        self.assertEqual(ctx.injected, [("text", "user", "s1")])

    def test_without_participant_it_registers_nothing(self):
        mod = self.load_plugin()
        ctx = FakeCtx({})
        mod.register(ctx)
        self.assertEqual(ctx.hooks, {})


if __name__ == "__main__":
    unittest.main()
