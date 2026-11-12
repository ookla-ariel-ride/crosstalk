"""The roster's optional `harness` field, and the `harness` and `model` a message names for who spoke."""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

import helpers
from helpers import RunningServer, TempRepo
from crosstalk.logs import read_log
from crosstalk.mcp import McpServer
from crosstalk.onboard import onboard
from crosstalk.participants import load_roster
from crosstalk.tokens import token_hash
from test_onboard import cli_setup, run_cli
from test_serve import request


class HarnessRosterTests(unittest.TestCase):
    def load(self, **kw):
        base = {"id": "x", "name": "X", "kind": "project", "color": "#8a5a00", "colorDark": "#d9b25c"}
        base.update(kw)
        with TempRepo() as r:
            return load_roster(r.write_roster(dict(helpers.SPEC_ROSTER, relay="", participants=[base])))

    def test_harness_is_optional(self):
        self.assertIsNone(self.load().participants["x"].harness)

    def test_known_and_new_ids_load(self):
        for ok in ("claude-code", "codex", "pi", "hermes", "gemini-cli", "new-tool2"):
            self.assertEqual(self.load(harness=ok).participants["x"].harness, ok)

    def test_bad_harness_refused(self):
        for bad in ("", "Claude", "claude code", "claude_code", "-x", "x-", "a--b", "<b>", "x" * 33, 5, ["codex"], True):
            with self.assertRaises(ValueError, msg=repr(bad)) as cm:
                self.load(harness=bad)
            self.assertIn("harness", str(cm.exception))


class HarnessOnboardTests(unittest.TestCase):
    def entry(self, r, pid):
        raw = json.loads((r.root / "participants.json").read_text(encoding="utf-8"))
        return [p for p in raw["participants"] if p["id"] == pid][0]

    def test_onboard_writes_harness(self):
        with TempRepo() as r:
            r.write_roster()
            onboard(r.root, "x", "X", "tool", harness="codex")
            self.assertEqual(self.entry(r, "x")["harness"], "codex")
            self.assertEqual(load_roster(r.root / "participants.json").participants["x"].harness, "codex")

    def test_onboard_without_harness_leaves_it_out(self):
        with TempRepo() as r:
            r.write_roster()
            onboard(r.root, "x", "X", "tool")
            self.assertNotIn("harness", self.entry(r, "x"))

    def test_onboard_refuses_bad_harness(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError):
                onboard(r.root, "x", "X", "tool", harness="Claude Code")
            self.assertFalse((r.root / "kits" / "x.md").exists())

    def test_cli_harness_flag(self):
        with TempRepo() as r:
            script = cli_setup(r)
            res = run_cli(script, "onboard", "x", "--name", "X", "--kind", "tool", "--harness", "pi")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(self.entry(r, "x")["harness"], "pi")


class HarnessServerTests(unittest.TestCase):
    OWNER_TOKEN = "the-owner-token"

    def owner(self, srv):
        return {"Authorization": "Bearer " + self.OWNER_TOKEN}

    def server(self, r):
        return RunningServer(r.root, tokens={"owner": token_hash(self.OWNER_TOKEN)})

    def test_owner_sets_harness_and_snapshot_carries_it(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "update", "collection": "participants",
                                        "id": "rev", "data": {"harness": "codex"}}, self.owner(srv))
                self.assertEqual(status, 201, body)
                status, bad = request(srv.url + "/api/append", "POST",
                                      {"from": "owner", "op": "update", "collection": "participants",
                                       "id": "rev", "data": {"harness": "Not An Id"}}, self.owner(srv))
                self.assertEqual(status, 400)
                status, snap = request(srv.url + "/api/snapshot")
            self.assertEqual(snap["roster"]["rev"]["harness"], "codex")

    def test_onboard_api_takes_harness(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = request(srv.url + "/api/onboard", "POST",
                                       {"id": "x", "name": "X", "kind": "tool", "harness": "hermes"}, self.owner(srv))
                self.assertEqual(status, 200, body)
            raw = json.loads((r.root / "participants.json").read_text(encoding="utf-8"))
            self.assertEqual([p for p in raw["participants"] if p["id"] == "x"][0]["harness"], "hermes")


SPEAKER_VARS = ("CROSSTALK_HARNESS", "CROSSTALK_MODEL")


class SpeakerCliTests(unittest.TestCase):
    """`--harness` and `--model` on a post; the two variables fill them when no flag is given."""

    def setUp(self):
        self.r = TempRepo().__enter__()
        self.addCleanup(self.r.__exit__, None, None, None)
        self.script = cli_setup(self.r)
        self.r.write_log("alpha", [helpers.line(1, "alpha", data={"from": "alpha", "kind": "request",
                                                                    "summary": "Look at this"})])

    def run_cli(self, *args, env=None):
        full = {k: v for k, v in os.environ.items() if k not in SPEAKER_VARS}
        full.update(env or {})
        return subprocess.run([sys.executable, str(self.script)] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/", env=full)

    def rows(self, pid="beta"):
        path = self.r.root / "log" / (pid + ".jsonl")
        if not path.exists():
            return []
        return [json.loads(x)["data"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def reply(self, *extra, env=None):
        return self.run_cli("reply", "--from", "beta", "--to-id", "alpha-0001", "--summary", "Done",
                            *extra, env=env)

    def test_reply_carries_harness_and_model_from_flags(self):
        res = self.reply("--harness", "codex", "--model", "gpt-5.5")
        self.assertEqual(res.returncode, 0, res.stderr)
        row = self.rows()[-1]
        self.assertEqual((row["harness"], row["model"]), ("codex", "gpt-5.5"))

    def test_env_fills_harness_and_model(self):
        res = self.reply(env={"CROSSTALK_HARNESS": "claude-code", "CROSSTALK_MODEL": "claude-opus-5-5"})
        self.assertEqual(res.returncode, 0, res.stderr)
        row = self.rows()[-1]
        self.assertEqual((row["harness"], row["model"]), ("claude-code", "claude-opus-5-5"))

    def test_flag_wins_over_env(self):
        res = self.reply("--model", "gpt-5.5", env={"CROSSTALK_HARNESS": "pi", "CROSSTALK_MODEL": "other"})
        self.assertEqual(res.returncode, 0, res.stderr)
        row = self.rows()[-1]
        self.assertEqual((row["harness"], row["model"]), ("pi", "gpt-5.5"))

    def test_absent_when_neither_given(self):
        res = self.reply()
        self.assertEqual(res.returncode, 0, res.stderr)
        row = self.rows()[-1]
        self.assertNotIn("harness", row)
        self.assertNotIn("model", row)

    def test_model_over_64_chars_is_rejected(self):
        res = self.reply("--model", "m" * 65)
        self.assertEqual(res.returncode, 1)
        self.assertIn("model", res.stderr)
        self.assertEqual(self.rows(), [])
        res = self.reply("--model", "m" * 64)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_bad_harness_is_rejected_by_the_roster_rule(self):
        for bad in ("Claude Code", "x" * 33):
            res = self.reply(env={"CROSSTALK_HARNESS": bad})
            self.assertEqual(res.returncode, 1, bad)
            self.assertIn("harness", res.stderr)
        self.assertEqual(self.rows(), [])

    def test_model_with_a_newline_is_rejected(self):
        res = self.reply("--model", "a\nb")
        self.assertEqual(res.returncode, 1)
        self.assertIn("model", res.stderr)

    def test_ask_and_consult_carry_the_fields(self):
        flags = ("--harness", "codex", "--model", "gpt-5.5")
        for cmd in (["ask", "--from", "beta", "--summary", "May I?"],
                    ["consult", "--from", "beta", "--summary", "Thoughts?", "--to", "alpha"]):
            res = self.run_cli(*(cmd + list(flags)))
            self.assertEqual(res.returncode, 0, res.stderr)
            row = self.rows()[-1]
            self.assertEqual((row["harness"], row["model"]), ("codex", "gpt-5.5"), cmd[0])

    def test_append_fills_only_absent_keys(self):
        data = json.dumps({"from": "beta", "kind": "sync", "summary": "hi", "model": "mine"})
        res = self.run_cli("append", "--from", "beta", "--op", "set", "--collection", "messages",
                           "--id", "beta-0001", "--data", data, "--harness", "codex", "--model", "flag",
                           env={"CROSSTALK_MODEL": "env"})
        self.assertEqual(res.returncode, 0, res.stderr)
        row = self.rows()[-1]
        self.assertEqual((row["harness"], row["model"]), ("codex", "mine"))

    def test_append_leaves_other_collections_alone(self):
        data = json.dumps({"from": "beta", "summary": "lock"})
        res = self.run_cli("append", "--from", "beta", "--op", "set", "--collection", "locks",
                           "--id", "beta-lock-1", "--data", data,
                           env={"CROSSTALK_HARNESS": "codex", "CROSSTALK_MODEL": "gpt-5.5"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("harness", self.rows()[-1])


class SpeakerLogTests(unittest.TestCase):
    def check(self, data):
        with TempRepo() as r:
            r.write_roster()
            path = r.write_log("beta", [helpers.line(1, "beta", data=dict(
                {"from": "beta", "kind": "sync", "summary": "hi"}, **data))])
            return read_log(path)

    def test_log_without_the_fields_passes(self):
        self.assertEqual(self.check({})[1], [])

    def test_log_with_good_fields_passes(self):
        self.assertEqual(self.check({"harness": "codex", "model": "gpt-5.5"})[1], [])

    def test_log_with_a_bad_field_is_refused(self):
        for bad in ({"harness": "Not An Id"}, {"model": ""}, {"model": "x" * 65}, {"model": 5},
                    {"harness": None}):
            lines, problems = self.check(bad)
            self.assertEqual(lines, [], bad)
            self.assertIn(list(bad)[0], problems[0].message)


class SpeakerServerTests(unittest.TestCase):
    OWNER_TOKEN = HarnessServerTests.OWNER_TOKEN

    def test_append_api_checks_the_fields(self):
        with TempRepo() as r:
            r.write_roster()
            hdr = {"Authorization": "Bearer " + self.OWNER_TOKEN}
            with RunningServer(r.root, tokens={"owner": token_hash(self.OWNER_TOKEN)}) as srv:
                msg = {"from": "owner", "kind": "owner", "summary": "hi"}
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "set", "collection": "messages",
                                        "id": "owner-1", "data": dict(msg, model="m" * 65)}, hdr)
                self.assertEqual(status, 400, body)
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "set", "collection": "messages",
                                        "id": "owner-2", "data": msg}, hdr)
                self.assertEqual(status, 201, body)
            rows = [json.loads(x)["data"] for x in
                    (r.root / "log" / "owner.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows, [msg])


class SpeakerMcpTests(unittest.TestCase):
    def server(self, r):
        srv = McpServer(r.root, "beta")
        srv.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "0"}}})
        return srv

    def test_mcp_posts_fill_the_fields_from_the_env(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("alpha", [helpers.line(1, "alpha", data={"from": "alpha", "kind": "request",
                                                                "summary": "Look"})])
            srv = self.server(r)
            env = {"CROSSTALK_HARNESS": "codex", "CROSSTALK_MODEL": "gpt-5.5"}
            with mock.patch.dict(os.environ, env):
                for i, (tool, args) in enumerate((
                        ("post_message", {"kind": "sync", "summary": "hi"}),
                        ("reply", {"to_id": "alpha-0001", "summary": "ok"}),
                        ("consult", {"summary": "Thoughts?", "to": ["alpha"]}),
                        ("ask_owner", {"summary": "May I?"})), start=1):
                    res = srv.handle({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                      "params": {"name": tool, "arguments": args}})
                    self.assertFalse(res["result"].get("isError"), res)
            rows = [json.loads(x)["data"] for x in
                    (r.root / "log" / "beta.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 4)
            for row in rows:
                self.assertEqual((row["harness"], row["model"]), ("codex", "gpt-5.5"), row)

    def test_mcp_without_the_env_adds_no_key(self):
        with TempRepo() as r:
            r.write_roster()
            srv = self.server(r)
            with mock.patch.dict(os.environ, {}):
                for k in SPEAKER_VARS:
                    os.environ.pop(k, None)
                res = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "post_message",
                                             "arguments": {"kind": "sync", "summary": "hi"}}})
            self.assertFalse(res["result"].get("isError"), res)
            row = json.loads((r.root / "log" / "beta.jsonl").read_text(encoding="utf-8"))["data"]
            self.assertNotIn("harness", row)
            self.assertNotIn("model", row)


if __name__ == "__main__":
    unittest.main()
