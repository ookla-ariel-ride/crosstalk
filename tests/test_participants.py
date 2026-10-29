import json
import unittest

import helpers
from helpers import TempRepo
from crosstalk.participants import load_roster


class RosterTests(unittest.TestCase):
    def load(self, data):
        with TempRepo() as r:
            return load_roster(r.write_roster(data))

    def entry(self, **kw):
        base = {"id": "x", "name": "X", "kind": "project", "color": "#8a5a00", "colorDark": "#d9b25c"}
        base.update(kw)
        return dict(helpers.SPEC_ROSTER, relay="", participants=[base])

    def test_loads_spec_example(self):
        ro = self.load(None)
        self.assertEqual(set(ro.participants), {"alpha", "beta", "rev", "owner"})
        self.assertEqual(ro.page, helpers.SPEC_ROSTER["page"])
        self.assertEqual(ro.relay, "alpha")
        self.assertEqual(ro.participants["beta"].checkInMin, 30)

    def test_defaults_trust_to_quarantined(self):
        self.assertEqual(self.load(self.entry()).participants["x"].trust, "quarantined")

    def test_agent_without_parent_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.load(self.entry(id="helper", kind="agent"))
        self.assertIn("helper", str(cm.exception))

    def test_bad_id_rejected(self):
        for bad in ("Rev", "a b", "rev\n"):
            with self.assertRaises(ValueError):
                self.load(self.entry(id=bad))

    def test_bad_name_rejected(self):
        for bad in ("<script>", "a<b", "a>b", "a\nb", "a\tb", "a\x00b"):
            with self.assertRaises(ValueError) as cm:
                self.load(self.entry(name=bad))
            self.assertIn("x", str(cm.exception))

    def test_bad_kind_and_trust_rejected(self):
        with self.assertRaises(ValueError):
            self.load(self.entry(kind="robot"))
        with self.assertRaises(ValueError):
            self.load(self.entry(trust="maybe"))

    def test_repo_falls_back_to_parent(self):
        data = dict(helpers.SPEC_ROSTER)
        data["participants"] = list(data["participants"]) + [
            {"id": "alpha.verifier", "name": "Verifier", "kind": "agent", "parent": "alpha",
             "color": "#8a5a00", "colorDark": "#d9b25c"}]
        ro = self.load(data)
        self.assertEqual(ro.repo_for("alpha.verifier"), "owner/alpha")
        self.assertIsNone(ro.repo_for("rev"))

    def test_color_and_dark_color_required(self):
        for field in ("color", "colorDark"):
            e = self.entry()
            del e["participants"][0][field]
            with self.assertRaises(ValueError) as cm:
                self.load(e)
            self.assertIn(field, str(cm.exception))

    def test_bad_color_format_rejected(self):
        for bad in ("red", "#abc", "#12345g", 7):
            with self.assertRaises(ValueError):
                self.load(self.entry(color=bad))

    def test_low_contrast_color_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.load(self.entry(color="#f0f0f0"))
        self.assertIn("contrast", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            self.load(self.entry(colorDark="#202020"))
        self.assertIn("colorDark", str(cm.exception))

    def test_duplicate_or_near_colors_rejected(self):
        for light, dark in (("#b3261e", "#d9b25c"), ("#b5281f", "#d9b25c"), ("#8a5a00", "#ef6a5f")):
            data = json.loads(json.dumps(helpers.SPEC_ROSTER))
            data["participants"].append({"id": "x", "name": "X", "kind": "project", "color": light, "colorDark": dark})
            with self.assertRaises(ValueError) as cm:
                self.load(data)
            self.assertIn("too close", str(cm.exception))
            self.assertIn("alpha", str(cm.exception))

    def test_colors_kept_lowercase(self):
        ro = self.load(self.entry(color="#8A5A00"))
        self.assertEqual(ro.participants["x"].color, "#8a5a00")

    def test_aliases_loaded_lowercase(self):
        ro = self.load(self.entry(aliases=["xp", "Ex"]))
        self.assertEqual(ro.participants["x"].aliases, ["xp", "ex"])
        self.assertEqual(self.load(self.entry()).participants["x"].aliases, [])

    def test_bad_aliases_rejected(self):
        for bad in ("xp", ["a b"], [""], [3], ["everyone"], ["projects"], ["owner"], ["x"]):
            with self.assertRaises(ValueError):
                self.load(self.entry(aliases=bad))

    def test_alias_taken_by_another_participant_rejected(self):
        a = {"id": "alpha", "name": "Alpha", "kind": "project", "color": "#8a5a00", "colorDark": "#d9b25c",
             "aliases": ["beta"]}
        b = {"id": "beta", "name": "Beta", "kind": "project", "color": "#1f5f8b", "colorDark": "#6fb2e2"}
        with self.assertRaises(ValueError) as cm:
            self.load(dict(helpers.SPEC_ROSTER, relay="", participants=[a, b]))
        self.assertIn("beta", str(cm.exception))
        b["aliases"] = ["al"]
        a["aliases"] = ["al"]
        with self.assertRaises(ValueError):
            self.load(dict(helpers.SPEC_ROSTER, relay="", participants=[a, b]))

    def test_item_names_loaded(self):
        ro = self.load(self.entry(itemNames={"Centiped": "Centipede"}))
        self.assertEqual(ro.participants["x"].itemNames, {"centiped": "Centipede"})
        self.assertEqual(self.load(self.entry()).participants["x"].itemNames, {})

    def test_bad_item_names_rejected(self):
        for bad in (["a"], {"": "A"}, {"a": ""}, {"a": 3}, {"a": "<b>"}):
            with self.assertRaises(ValueError):
                self.load(self.entry(itemNames=bad))


if __name__ == "__main__":
    unittest.main()
