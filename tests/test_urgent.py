"""Urgent-only push for non-Claude harnesses: the filter, the Codex hook, the fallback."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402,F401  (puts scripts/ on sys.path)
from helpers import SPEC_ROSTER, TempRepo, line  # noqa: E402
from test_inbox import rep, req  # noqa: E402

from crosstalk import urgent  # noqa: E402
from crosstalk.notify import Notifier, actionable_items  # noqa: E402
from crosstalk.participants import load_roster  # noqa: E402
from crosstalk.replay import enrich_messages, load_all_lines, replay  # noqa: E402

T0 = 1793016000.0  # 2026-10-26T12:00:00Z
RELAY = Path(__file__).resolve().parent.parent / "scripts" / "relay.py"


def owner_msg(id_, **extra):
    data = {"from": "owner", "kind": "owner", "summary": "hi " + id_}
    data.update(extra)
    return line(1, "owner", id_=id_, data=data)


def alert(frm, id_, to=None, t="2026-10-21T18:00:00Z"):
    data = {"from": frm, "kind": "alert", "summary": "fire " + id_}
    if to is not None:
        data["to"] = to
    return line(1, frm, id_=id_, data=data, t=t)


def codex_roster():
    data = json.loads(json.dumps(SPEC_ROSTER))
    for entry in data["participants"]:
        if entry["id"] == "beta":
            entry["harness"] = "codex"
        elif entry["id"] == "alpha":
            entry["harness"] = "claude-code"
    return data


def event(name, **extra):
    data = {"session_id": "s1", "cwd": "/tmp", "hook_event_name": name, "model": "m",
            "transcript_path": None}
    data.update(extra)
    return json.dumps(data)


class Base(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(codex_roster())
        self.root = self.r.root
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-urgent-test-"))
        self.state = self.tmp / "urgent.json"
        self.now = [T0]
        self.logs = {}

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)
        self.r.__exit__(None, None, None)

    def append(self, pid, *msgs):
        lines = self.logs.setdefault(pid, [])
        for m in msgs:
            m["seq"] = len(lines) + 1
            lines.append(m)
        self.r.write_log(pid, lines)

    def roster(self):
        return load_roster(self.root / "participants.json")

    def docs(self):
        lines = load_all_lines(self.root, self.roster())
        return enrich_messages(replay(lines), lines)

    def hook(self, name="PostToolUse", pid="beta", harness="codex", **extra):
        return urgent.hook_output(self.root, pid, harness, event(name, **extra), self.state,
                                  clock=lambda: self.now[0])


class UrgentFilterTests(Base):
    def test_owner_messages_and_owner_replies_are_urgent(self):
        self.append("beta", line(1, "beta", id_="beta-root",
                                 data={"from": "beta", "kind": "check-in", "summary": "root"}))
        self.append("owner", owner_msg("m-1"), owner_msg("m-2", replyTo="beta-root"))
        self.assertEqual(actionable_items(self.docs(), "beta", self.roster(), urgent=True),
                         ["m-1", "m-2"])

    def test_requests_and_thread_replies_are_not_urgent(self):
        self.append("beta", line(1, "beta", id_="beta-root",
                                 data={"from": "beta", "kind": "check-in", "summary": "root"}))
        self.append("alpha", req(1, "alpha", "m-req", to=["beta"]),
                    rep(2, "alpha", "m-rep", "beta-root"))
        docs, roster = self.docs(), self.roster()
        self.assertEqual(actionable_items(docs, "beta", roster), ["m-rep", "m-req"])
        self.assertEqual(actionable_items(docs, "beta", roster, urgent=True), [])

    def test_peer_alert_is_urgent_only_when_to_names_pid(self):
        self.append("alpha", alert("alpha", "a-to", to=["beta"]), alert("alpha", "a-all"),
                    alert("alpha", "a-other", to=["rev"]), alert("alpha", "a-str", to="beta"))
        self.append("rev", line(1, "rev", id_="a-mention",
                                data={"from": "rev", "kind": "alert", "summary": "@beta fire"}))
        docs, roster = self.docs(), self.roster()
        self.assertEqual(actionable_items(docs, "beta", roster, urgent=True), ["a-str", "a-to"])
        # an incident never interrupts: it has no `to`
        self.assertEqual(actionable_items(docs, "rev", roster, urgent=True), ["a-other"])

    def test_peer_alert_leaves_once_answered_and_quarantined_or_own_never_count(self):
        data = codex_roster()
        for entry in data["participants"]:
            if entry["id"] == "rev":
                entry["trust"] = "quarantined"
        self.r.write_roster(data)
        self.append("alpha", alert("alpha", "a-1", to=["beta"]))
        self.append("rev", alert("rev", "a-q", to=["beta"]))
        self.append("beta", alert("beta", "a-own", to=["beta"]))
        self.assertEqual(actionable_items(self.docs(), "beta", self.roster(), urgent=True), ["a-1"])
        self.append("beta", rep(2, "beta", "beta-ack", "a-1"))
        self.assertEqual(actionable_items(self.docs(), "beta", self.roster(), urgent=True), [])

    def test_default_filter_unchanged(self):
        self.append("alpha", alert("alpha", "a-1", to=["beta"]))
        self.assertEqual(actionable_items(self.docs(), "beta", self.roster()), [])


class CodexHookTests(Base):
    def test_first_run_seeds_backlog_then_new_item_is_injected(self):
        self.append("owner", owner_msg("m-old", to=["beta"]))
        self.assertEqual(self.hook(), "")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        out = json.loads(self.hook())
        self.assertEqual(set(out), {"hookSpecificOutput"})
        hso = out["hookSpecificOutput"]
        self.assertEqual(set(hso), {"hookEventName", "additionalContext"})
        self.assertEqual(hso["hookEventName"], "PostToolUse")
        text = hso["additionalContext"]
        self.assertIn("- owner message from Owner: hi m-1 (m-1)", text)
        self.assertNotIn("m-old", text)
        self.assertIn("relay.py inbox beta", text)
        self.assertTrue(text.startswith("Crosstalk: 1 urgent item for beta."))

    def test_session_start_seeds_silently(self):
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertEqual(self.hook("SessionStart", source="startup"), "")
        self.assertEqual(self.hook(), "")
        st = json.loads(self.state.read_text())
        self.assertEqual(st["hooks"]["codex:beta"]["told"], ["m-1"])
        self.assertIsNone(st["hooks"]["codex:beta"]["last_at"])

    def test_each_item_delivered_once(self):
        self.hook("SessionStart")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertNotEqual(self.hook(), "")
        self.now[0] += 3600
        self.assertEqual(self.hook(), "")
        self.assertEqual(self.hook("Stop", stop_hook_active=False), "")

    def test_ten_minute_gap_between_interruptions(self):
        self.hook("SessionStart")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertIn("m-1", self.hook())
        self.append("alpha", alert("alpha", "a-1", to=["beta"]))
        self.now[0] += 599
        self.assertEqual(self.hook(), "")
        self.now[0] += 1
        out = json.loads(self.hook())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("- alert from Alpha: fire a-1 (a-1)", out)
        self.assertNotIn("m-1", out)

    def test_items_waiting_out_the_gap_arrive_together(self):
        self.hook("SessionStart")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.hook()
        self.append("owner", owner_msg("m-2", to=["beta"]), owner_msg("m-3", to=["beta"]))
        self.now[0] += 60
        self.assertEqual(self.hook(), "")
        self.now[0] += 600
        text = json.loads(self.hook())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("2 urgent items", text)
        self.assertIn("(m-2)", text)
        self.assertIn("(m-3)", text)

    def test_stop_continues_the_turn_with_the_note(self):
        self.hook("SessionStart")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        out = json.loads(self.hook("Stop", stop_hook_active=False))
        self.assertEqual(set(out), {"decision", "reason"})
        self.assertEqual(out["decision"], "block")
        self.assertIn("(m-1)", out["reason"])

    def test_non_urgent_items_never_interrupt(self):
        self.hook("SessionStart")
        self.append("alpha", req(1, "alpha", "m-req", to=["beta"]))
        self.assertEqual(self.hook(), "")
        self.assertEqual(self.hook("Stop"), "")

    def test_harness_keys_are_separate(self):
        self.hook("SessionStart")
        st = json.loads(self.state.read_text())
        self.assertEqual(sorted(st["hooks"]), ["codex:beta"])

    def test_errors_are_silent_no_ops(self):
        self.hook("SessionStart")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        err = io.StringIO()
        with redirect_stderr(err):
            # unknown participant, unknown harness, unknown event, bad stdin
            self.assertEqual(self.hook(pid="nobody"), "")
            self.assertEqual(self.hook(harness="opencode"), "")
            self.assertEqual(self.hook("Interrupt"), "")
            self.assertEqual(urgent.hook_output(self.root, "beta", "codex", "not json{",
                                                self.state, clock=lambda: self.now[0]), "")
            # no state file configured
            self.assertEqual(urgent.hook_output(self.root, "beta", "codex", event("PostToolUse"),
                                                None, clock=lambda: self.now[0]), "")
            # a state path that cannot be written
            bad = self.tmp / "missing-dir" / "x" / "state.json"
            self.assertEqual(urgent.hook_output(self.root, "beta", "codex",
                                                event("PostToolUse"), bad,
                                                clock=lambda: self.now[0]), "")
            # a broken roster
            (self.root / "participants.json").write_text("{nope", encoding="utf-8")
            self.assertEqual(self.hook(), "")
        self.assertEqual(err.getvalue(), "")
        # nothing was marked told by the failed runs
        self.r.write_roster(codex_roster())
        self.assertIn("m-1", self.hook())

    def test_corrupt_state_is_rebuilt_quietly(self):
        self.state.write_text("[broken", encoding="utf-8")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.assertEqual(self.hook(), "")  # first sight again: seeds
        st = json.loads(self.state.read_text())
        self.assertEqual(st["hooks"]["codex:beta"]["told"], ["m-1"])
        self.assertEqual(oct(self.state.stat().st_mode & 0o777), "0o600")

    def test_summary_is_untrusted_one_line(self):
        self.hook("SessionStart")
        self.append("owner", owner_msg("m-1", to=["beta"], summary="a\nb\x07 " + "x" * 400))
        text = json.loads(self.hook())["hookSpecificOutput"]["additionalContext"]
        item = [ln for ln in text.splitlines() if ln.startswith("- ")][0]
        self.assertTrue(item.startswith("- owner message from Owner: a b  x"))
        self.assertLess(len(item), 260)


class CliTests(Base):
    def run_cli(self, *args, stdin=""):
        env = dict(os.environ, CROSSTALK_URGENT_STATE=str(self.state))
        return subprocess.run([sys.executable, str(RELAY)] + list(args), input=stdin,
                              capture_output=True, text=True, env=env, timeout=30)

    def test_cli_bad_arguments_exit_zero_silently(self):
        # The real checkout's roster; the hook must say nothing and exit 0 for
        # a missing --as, an unknown hook, or garbage on stdin.
        for args in (("urgent",), ("urgent", "--as", "beta", "--hook", "nope"),
                     ("urgent", "--bogus")):
            p = self.run_cli(*args, stdin="garbage")
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""), args)

    def test_cli_unknown_participant_is_silent(self):
        p = self.run_cli("urgent", "--as", "no-such-participant-x", "--hook", "codex",
                         stdin=event("PostToolUse"))
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        self.assertFalse(self.state.exists())


class FakeNotify:
    def __init__(self):
        self.calls = []

    def __call__(self, title, message):
        self.calls.append((title, message))


class FallbackTests(Base):
    def fallback(self, **kw):
        self.notes = FakeNotify()
        return urgent.Fallback(self.state, clock=lambda: self.now[0], notify=self.notes, **kw)

    def test_backlog_is_seeded_then_unpicked_item_notifies_after_ten_minutes_once(self):
        self.append("owner", owner_msg("m-old", to=["beta"]))
        fb = self.fallback()
        fb.poll(self.docs(), self.roster())
        self.append("owner", owner_msg("m-1", to=["beta"]))
        fb.poll(self.docs(), self.roster())
        self.now[0] += 599
        fb.poll(self.docs(), self.roster())
        self.assertEqual(self.notes.calls, [])
        self.now[0] += 1
        fb.poll(self.docs(), self.roster())
        self.assertEqual(len(self.notes.calls), 1)
        title, message = self.notes.calls[0]
        self.assertIn("Beta", title)
        self.assertIn("hi m-1", message)
        self.assertNotIn("m-old", message)
        self.now[0] += 3600
        fb.poll(self.docs(), self.roster())
        self.assertEqual(len(self.notes.calls), 1)

    def test_item_picked_up_by_a_hook_never_notifies(self):
        fb = self.fallback()
        fb.poll(self.docs(), self.roster())
        self.hook("SessionStart")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        fb.poll(self.docs(), self.roster())
        self.now[0] += 120
        self.assertIn("m-1", self.hook())
        self.now[0] += 1200
        fb.poll(self.docs(), self.roster())
        self.assertEqual(self.notes.calls, [])

    def test_item_held_back_by_the_gap_still_falls_back(self):
        fb = self.fallback()
        fb.poll(self.docs(), self.roster())
        self.hook("SessionStart")
        self.append("owner", owner_msg("m-1", to=["beta"]))
        self.hook()
        self.append("owner", owner_msg("m-2", to=["beta"]))
        fb.poll(self.docs(), self.roster())
        self.now[0] += 600
        fb.poll(self.docs(), self.roster())
        self.assertEqual(len(self.notes.calls), 1)
        self.assertIn("hi m-2", self.notes.calls[0][1])

    def test_answered_item_does_not_notify(self):
        fb = self.fallback()
        fb.poll(self.docs(), self.roster())
        self.append("alpha", alert("alpha", "a-1", to=["beta"]))
        fb.poll(self.docs(), self.roster())
        self.append("beta", rep(1, "beta", "beta-ack", "a-1"))
        self.now[0] += 900
        fb.poll(self.docs(), self.roster())
        self.assertEqual(self.notes.calls, [])

    def test_only_hook_harness_participants_are_covered(self):
        fb = self.fallback()
        fb.poll(self.docs(), self.roster())
        self.append("owner", owner_msg("m-1"))  # a broadcast: alpha, beta and rev
        fb.poll(self.docs(), self.roster())
        self.now[0] += 600
        fb.poll(self.docs(), self.roster())
        self.assertEqual(len(self.notes.calls), 1)
        self.assertIn("Beta", self.notes.calls[0][0])

    def test_several_items_make_one_notification(self):
        fb = self.fallback()
        fb.poll(self.docs(), self.roster())
        self.append("owner", owner_msg("m-1", to=["beta"]), owner_msg("m-2", to=["beta"]))
        fb.poll(self.docs(), self.roster())
        self.now[0] += 600
        fb.poll(self.docs(), self.roster())
        self.assertEqual(len(self.notes.calls), 1)
        self.assertIn("2 urgent items", self.notes.calls[0][0] + self.notes.calls[0][1])

    def test_fallback_state_survives_restart(self):
        fb = self.fallback()
        fb.poll(self.docs(), self.roster())
        self.append("owner", owner_msg("m-1", to=["beta"]))
        fb.poll(self.docs(), self.roster())
        self.now[0] += 600
        fb.poll(self.docs(), self.roster())
        fb2 = self.fallback()
        self.now[0] += 600
        fb2.poll(self.docs(), self.roster())
        self.assertEqual(self.notes.calls, [])

    def test_notifier_runs_the_fallback_each_pass(self):
        live_dir = self.tmp / "live"
        with mock.patch.dict(os.environ, {"CROSSTALK_LIVE_DIR": str(live_dir)}):
            fb = self.fallback()

            def snapshot():
                return self.docs(), {}
            n = Notifier(self.root, snapshot, None, clock=lambda: self.now[0], fallback=fb)
            try:
                n.poll_once()
                self.append("owner", owner_msg("m-1", to=["beta"]))
                n.poll_once()
                self.now[0] += 600
                n.poll_once()
            finally:
                n.stop()
        self.assertEqual(len(self.notes.calls), 1)

    def test_notifier_survives_a_failing_fallback(self):
        class Boom:
            def poll(self, docs, roster):
                raise RuntimeError("boom")
        with mock.patch.dict(os.environ, {"CROSSTALK_LIVE_DIR": str(self.tmp / "live")}):
            n = Notifier(self.root, lambda: (self.docs(), {}), None, fallback=Boom())
            err = io.StringIO()
            try:
                with redirect_stderr(err):
                    n.poll_once()
            finally:
                n.stop()
        self.assertIn("urgent fallback failed", err.getvalue())

    def test_server_wires_the_fallback_into_its_notifier(self):
        from helpers import RunningServer
        with mock.patch.dict(os.environ, {"CROSSTALK_LIVE_DIR": str(self.tmp / "live")}):
            with RunningServer(self.root, no_pin=True, notify=True,
                               notify_state=None, urgent_state=self.state) as srv:
                self.assertIsInstance(srv._notifier.fallback, urgent.Fallback)
                self.assertEqual(srv._notifier.fallback.state_path, self.state)
            with RunningServer(self.root, no_pin=True, notify=True, notify_state=None) as srv:
                self.assertIsNone(srv._notifier.fallback)


class MacNotifyTests(unittest.TestCase):
    def test_no_op_off_macos(self):
        with mock.patch.object(urgent.sys, "platform", "linux"), \
                mock.patch.object(urgent.subprocess, "run") as run:
            urgent.macos_notify("t", "m")
        run.assert_not_called()

    def test_passes_text_as_arguments_not_script(self):
        with mock.patch.object(urgent.sys, "platform", "darwin"), \
                mock.patch.object(urgent.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(urgent.subprocess, "run") as run:
            urgent.macos_notify('ti"tle', 'mes"sage\\ end')
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "/usr/bin/osascript")
        self.assertEqual(argv[-2:], ['mes"sage\\ end', 'ti"tle'])
        self.assertFalse(any('mes"sage' in a for a in argv[:-2]))

    def test_failure_is_swallowed(self):
        with mock.patch.object(urgent.sys, "platform", "darwin"), \
                mock.patch.object(urgent.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(urgent.subprocess, "run", side_effect=OSError("x")):
            urgent.macos_notify("t", "m")


if __name__ == "__main__":
    unittest.main()
