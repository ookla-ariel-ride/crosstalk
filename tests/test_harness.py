"""The roster's optional `harness` field: the tool a participant's sessions run in (claude-code, codex, ...)."""
import json
import unittest

import helpers
from helpers import RunningServer, TempRepo
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


if __name__ == "__main__":
    unittest.main()
