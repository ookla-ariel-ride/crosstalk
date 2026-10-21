import unittest

import helpers
from helpers import TempRepo
from crosstalk.participants import load_roster


class RosterTests(unittest.TestCase):
    def load(self, data):
        with TempRepo() as r:
            return load_roster(r.write_roster(data))

    def entry(self, **kw):
        base = {"id": "x", "name": "X", "kind": "project"}
        base.update(kw)
        return dict(helpers.SPEC_ROSTER, relay="", participants=[base])

    def test_loads_spec_example(self):
        ro = self.load(None)
        self.assertEqual(set(ro.participants), {"bewareilive", "vectorcraft", "codex", "owner"})
        self.assertEqual(ro.page, helpers.SPEC_ROSTER["page"])
        self.assertEqual(ro.relay, "bewareilive")
        self.assertEqual(ro.participants["vectorcraft"].checkInMin, 30)

    def test_defaults_trust_to_quarantined(self):
        self.assertEqual(self.load(self.entry()).participants["x"].trust, "quarantined")

    def test_agent_without_parent_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.load(self.entry(id="helper", kind="agent"))
        self.assertIn("helper", str(cm.exception))

    def test_bad_id_rejected(self):
        for bad in ("Codex", "a b", "codex\n"):
            with self.assertRaises(ValueError):
                self.load(self.entry(id=bad))

    def test_bad_kind_and_trust_rejected(self):
        with self.assertRaises(ValueError):
            self.load(self.entry(kind="robot"))
        with self.assertRaises(ValueError):
            self.load(self.entry(trust="maybe"))

    def test_repo_falls_back_to_parent(self):
        data = dict(helpers.SPEC_ROSTER)
        data["participants"] = list(data["participants"]) + [
            {"id": "bewareilive.verifier", "name": "Verifier", "kind": "agent", "parent": "bewareilive"}]
        ro = self.load(data)
        self.assertEqual(ro.repo_for("bewareilive.verifier"), "ookla-ariel-ride/bewareilive")
        self.assertIsNone(ro.repo_for("codex"))


if __name__ == "__main__":
    unittest.main()
