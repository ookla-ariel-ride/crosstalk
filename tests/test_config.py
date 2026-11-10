"""config.json and the coordinator: neutral defaults, and nothing fails when neither is set."""
import json
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path

import helpers
from helpers import RunningServer, TempRepo, line

from crosstalk import buildhost, config
from crosstalk.autogrant import Granter
from crosstalk.participants import load_roster
from crosstalk.schedules import standard_jobs


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def roster(self, relay=""):
        return load_roster(self.r.write_roster(dict(helpers.SPEC_ROSTER, relay=relay)))

    def test_no_file_reads_as_the_defaults(self):
        self.assertEqual(config.load(self.r.root), config.DEFAULTS)
        self.assertEqual(config.of(self.roster()), config.DEFAULTS)
        self.assertEqual(config.DEFAULTS["buildHost"]["ssh"], "")
        self.assertEqual(config.DEFAULTS["repoUrl"], "")

    def test_a_file_overrides_key_by_key_and_bad_values_fall_back(self):
        self.r.write_config({"repoUrl": " https://example.com/o/r ", "buildHost": {"ssh": "box", "loadHigh": 20},
                             "usage": {"limit": "high", "glob": 7}, "unknown": 1})
        cfg = config.of(self.roster())
        self.assertEqual(cfg["repoUrl"], "https://example.com/o/r")
        self.assertEqual((cfg["buildHost"]["ssh"], cfg["buildHost"]["loadHigh"], cfg["buildHost"]["loadClear"]),
                         ("box", 20.0, 10.0))
        self.assertEqual((cfg["usage"]["limit"], cfg["usage"]["glob"]), (90.0, ""))
        self.assertNotIn("unknown", cfg)
        (self.r.root / "config.json").write_text("{nope", encoding="utf-8")
        self.assertEqual(config.load(self.r.root), config.DEFAULTS)

    def test_the_example_file_holds_every_key_and_no_other(self):
        example = json.loads((Path(__file__).resolve().parent.parent / "config.example.json").read_text(encoding="utf-8"))

        def keys(d):
            return {k: keys(v) if isinstance(v, dict) else None for k, v in d.items()}
        self.assertEqual(keys(example), keys(config.DEFAULTS))

    def test_the_coordinator_is_the_rosters_relay(self):
        self.assertEqual(config.coordinator(self.roster()), "")
        self.assertEqual(config.coordinator(self.roster("alpha")), "alpha")
        self.assertEqual(config.coordinator(None), "")

    def test_with_no_coordinator_the_posters_stay_quiet(self):
        roster = self.roster()
        with self.assertRaises(ValueError):
            buildhost.Poller(self.r.root, None, prober=lambda: {}).poll(roster, dry_run=True)
        docs = {("messages", "beta-1"): {"from": "beta", "kind": "request", "summary": "TURN ASK beta x-0101",
                                         "t": "2026-01-01T00:00:00Z"}}
        posts = []
        g = Granter(self.r.root, lambda: (docs, {"beta": {}}), post=lambda *a, **k: posts.append(a),
                    pause_path=None, roster=lambda: roster)
        self.assertIsNone(g.poll_once())
        self.assertEqual(posts, [])

    def test_unset_checks_are_skipped_when_a_coordinator_grants(self):
        roster = self.roster("alpha")
        docs = {("messages", "beta-1"): {"from": "beta", "kind": "request", "summary": "TURN ASK beta x-0101",
                                         "t": "2026-01-01T00:00:00Z"}}
        posts = []
        g = Granter(self.r.root, lambda: (docs, {"beta": {}}), post=lambda *a, **k: posts.append((a, k)),
                    pause_path=None, roster=lambda: roster)
        self.assertEqual(g.poll_once(), "beta-1")
        (_root, pid, to_id, summary), kw = posts[0]
        self.assertEqual((pid, to_id, summary), ("alpha", "beta-1", "GRANTED beta x-0101"))
        self.assertNotIn("load", kw["body"])
        self.assertNotIn("usage", kw["body"])

    def test_a_probe_with_no_host_runs_no_ssh(self):
        ran = []
        p = buildhost.probe("", runner=lambda a, t: ran.append(a) or (0, "", ""))
        self.assertEqual((p["reachable"], p["error"], ran), (False, buildhost.NO_HOST, []))

    def test_the_usage_guard_prompt_takes_its_limits_from_the_config(self):
        prompt = {j["name"]: j["prompt"] for j in standard_jobs("beta")}["usage-guard"]
        self.assertIn("Read your harness's weekly usage. Step 1 at weekly usage 90", prompt)
        self.assertIn("Step 2 at 95:", prompt)
        cfg = config.defaults()
        cfg["usage"].update(glob="~/u/usage*.json", limit=80.0, stopLimit=85.0, graceMin=20.0)
        prompt = {j["name"]: j["prompt"] for j in standard_jobs("beta", cfg=cfg)}["usage-guard"]
        self.assertIn("Read the newest ~/u/usage*.json", prompt)
        self.assertIn("Step 2 at 85 or 20 minutes after step 1:", prompt)

    def test_the_cli_prints_the_coordinator_or_exits_1(self):
        relay = str(helpers.relay_in(self.r.root))
        self.roster()
        p = subprocess.run([sys.executable, relay, "coordinator"], capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout), (1, ""))
        self.roster("alpha")
        p = subprocess.run([sys.executable, relay, "coordinator"], capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout), (0, "alpha\n"))
        p = subprocess.run([sys.executable, relay, "buildhost", "poll", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("no build host set", p.stderr)

    def test_the_snapshot_carries_the_settings_the_page_uses(self):
        self.r.write_roster(dict(helpers.SPEC_ROSTER, relay="alpha"))
        self.r.write_config({"repoUrl": "https://example.com/o/r", "rulingsSince": "2026-01-01"})
        self.r.write_log("alpha", [line(1, "alpha", data={"from": "alpha", "kind": "note", "summary": "hi"})])
        with RunningServer(self.r.root) as srv:
            with urllib.request.urlopen(srv.url + "/api/snapshot") as resp:
                snap = json.loads(resp.read().decode("utf-8"))
            self.assertEqual((snap["coordinator"], snap["repo_url"], snap["rulings_since"]),
                             ("alpha", "https://example.com/o/r", "2026-01-01"))
            # a change to config.json reaches the next snapshot
            self.r.write_config({"repoUrl": ""})
            with urllib.request.urlopen(srv.url + "/api/snapshot") as resp:
                self.assertEqual(json.loads(resp.read().decode("utf-8"))["repo_url"], "")


if __name__ == "__main__":
    unittest.main()
