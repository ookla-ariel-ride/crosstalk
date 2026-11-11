import json
import os
import time
import unittest

from helpers import RunningServer, TempRepo
from test_serve import request

from crosstalk import config, vitals


class VitalsTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.root = self.repo.root
        self.state = self.root / "buildhost-state.json"  # never the real one in the home folder

    def tearDown(self):
        self.repo.__exit__(None, None, None)

    def read(self, cfg=None):
        self.repo.write_config(cfg or {})
        return vitals.read(self.root, config.load(self.root), buildhost_state=self.state)

    def usage_file(self, name, value, age_sec=0.0):
        path = self.root / name
        path.write_text(json.dumps({"weeklyUsage": value}), encoding="utf-8")
        when = time.time() - age_sec
        os.utime(str(path), (when, when))
        return path

    def test_vitals_disk_is_a_number(self):
        out = self.read()
        self.assertIsInstance(out["disk_free_gb"], int)
        self.assertGreaterEqual(out["disk_free_gb"], 0)
        self.assertEqual(sorted(out), ["build_host", "disk_free_gb", "usage", "usage_limit"])

    def test_usage_is_null_without_config(self):
        self.usage_file("usage-a.json", 40)
        out = self.read()
        self.assertIsNone(out["usage"])
        self.assertIsNone(out["usage_limit"])
        self.assertIsNone(out["build_host"])

    def test_usage_comes_from_the_newest_file(self):
        self.usage_file("usage-a.json", 40, age_sec=120)
        self.usage_file("usage-b.json", 55.6)
        out = self.read({"usageFile": str(self.root / "usage-*.json"), "usageLimit": 90})
        self.assertEqual((out["usage"], out["usage_limit"]), (56, 90))

    def test_stale_usage_file_is_null(self):
        self.usage_file("usage-a.json", 40, age_sec=11 * 60)
        out = self.read({"usageFile": str(self.root / "usage-*.json")})
        self.assertIsNone(out["usage"])

    def test_build_host_comes_from_the_stored_state(self):
        cfg = {"buildHost": {"ssh": "buildhost"}}
        self.assertIsNone(self.read(cfg)["build_host"])  # nothing stored yet
        self.state.write_text(json.dumps({"reachable": False, "busy": False, "disk_low": False}),
                              encoding="utf-8")
        self.assertEqual(self.read(cfg)["build_host"], {"reachable": False, "load": None})
        self.state.write_text(json.dumps({"reachable": True, "busy": True, "disk_low": False, "load": 13.5}),
                              encoding="utf-8")
        self.assertEqual(self.read(cfg)["build_host"], {"reachable": True, "load": 13.5})
        self.assertIsNone(self.read({})["build_host"])  # no build host set


class ConfigTests(unittest.TestCase):
    def test_model_families_must_be_pairs_of_strings(self):
        with TempRepo() as r:
            self.assertEqual(config.load(r.root)["modelFamilies"], [])
            good = [["^gpt-", "openai"], ["claude", "anthropic"]]
            r.write_config({"modelFamilies": good})
            self.assertEqual(config.load(r.root)["modelFamilies"], good)
            for bad in ("x", [["a"]], [["a", 1]], [["a", "b", "c"]], [["a", "b"], "c"], {"a": "b"}):
                r.write_config({"modelFamilies": bad})
                self.assertEqual(config.load(r.root)["modelFamilies"], [], bad)

    def test_usage_limit_is_a_number_or_null(self):
        with TempRepo() as r:
            self.assertIsNone(config.load(r.root)["usageLimit"])
            for bad in ("90", True, None):
                r.write_config({"usageLimit": bad})
                self.assertIsNone(config.load(r.root)["usageLimit"], bad)
            r.write_config({"usageLimit": 85})
            self.assertEqual(config.load(r.root)["usageLimit"], 85)


class VitalsRouteTests(unittest.TestCase):
    def test_vitals_route_adds_model_families(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_config({"modelFamilies": [["^gpt-", "openai"]], "usageLimit": 90})
            with RunningServer(r.root) as srv:
                srv.state.buildhost_state = r.root / "buildhost-state.json"
                status, body = request(srv.url + "/api/vitals")
        self.assertEqual(status, 200)
        self.assertEqual(body["model_families"], [["^gpt-", "openai"]])
        self.assertEqual(body["usage_limit"], 90)
        self.assertIsInstance(body["disk_free_gb"], int)
        self.assertIsNone(body["usage"])
        self.assertIsNone(body["build_host"])


if __name__ == "__main__":
    unittest.main()
