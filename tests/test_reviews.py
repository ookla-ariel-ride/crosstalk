"""The Codex review scanner: report parsing, first-run baseline, posts, overrides, objections."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import helpers
from helpers import TempRepo
from crosstalk import reviews
from crosstalk.logs import append_line
from crosstalk.notify import Notifier
from crosstalk.participants import load_roster
from crosstalk.urgent import load_snapshot

REPO = Path(__file__).resolve().parent.parent

# Synthetic reports in each project's format (reduced from the real ones; no real text).
# alpha writes like BewareILive: short-hash names, a `tip` line in the meta, Tier on line 3.
# beta writes like VectorCraft: full-hash names, a Model line then Tier, a .checked list.
# gamma writes like AvoidMissingBall: full-hash names, a Model line and no Tier.
A1 = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
A2 = "a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2"
B1 = "b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1"
C1 = "c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1"
C2 = "c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2"
BASE = "b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0"

ALPHA_NEEDS = """# Codex Adversarial Review

Tier: full

Target: branch diff against %s
Verdict: needs-attention

Alpha summary paragraph that must never be posted.

Findings:
- [medium] Alpha first headline (src/alpha.rs:1-2)
  Alpha finding body that must never be posted.
  Recommendation: Alpha recommendation that must never be posted.
- [high] Alpha second headline (src/alpha.rs:3)
  Alpha second body.
  Recommendation: Alpha second recommendation.

Next steps:
- Alpha next step.
""" % BASE

ALPHA_LIGHT = """# Codex Adversarial Review

Tier: light (doc-only: every commit changes only Markdown under docs/, playbook/ or log/, or a README)

Target: branch diff against %s
Verdict: approve

Alpha light summary.

No material findings.
""" % BASE

BETA_NEEDS = """# Codex Adversarial Review

Model: model-x (asked for by codex-review; BETA_CODEX_MODEL)
Tier: full

Target: branch diff against %s
Verdict: needs-attention

Beta summary paragraph that must never be posted.

Findings:
- [high] Beta only headline (tools/beta.sh:10-12)
  Beta finding body that must never be posted.
  Recommendation: Beta recommendation.

Next steps:
- Beta next step.
""" % BASE

GAMMA_APPROVE = """# Codex Adversarial Review

Model: model-x (asked for by codex-review; GAMMA_CODEX_MODEL)

Target: branch diff against %s
Verdict: approve

Gamma summary paragraph.

No material findings.
""" % BASE

EMPTY_DIFF = "# Not reviewed: empty diff\n\nVerdict: approve\n\n"


def meta(tip=None, diff="d1" * 20, review=None, commits=()):
    lines = (["tip %s" % tip] if tip else []) + ["base %s" % BASE, "diff %s" % diff]
    if review:
        lines.append("review %s" % review)
    lines += ["commit %s" % c for c in commits]
    return "\n".join(lines) + "\n"


ROSTER = {
    "page": "http://127.0.0.1:8484/",
    "relay": "",
    "participants": [
        {"id": "alpha", "name": "Alpha", "kind": "project", "color": "#b3261e", "colorDark": "#ef6a5f",
         "trust": "approved", "aliases": ["al"], "reviewDir": "REVIEWS/alpha"},
        {"id": "beta", "name": "Beta", "kind": "project", "color": "#1f5f8b", "colorDark": "#6fb2e2",
         "trust": "approved", "reviewDir": "REVIEWS/beta"},
        {"id": "gamma", "name": "Gamma", "kind": "project", "color": "#c77700", "colorDark": "#f0a73a",
         "trust": "approved", "aliases": ["ga"], "reviewDir": "REVIEWS/gamma"},
        {"id": "codex", "name": "Codex", "kind": "reviewer", "color": "#00727a", "colorDark": "#45c2bf",
         "trust": "approved"},
        {"id": "owner", "name": "Owner", "kind": "person", "color": "#6b3fa0", "colorDark": "#b892e6",
         "trust": "approved"},
        {"id": "crosstalk", "name": "Crosstalk", "kind": "project", "color": "#2e7d32",
         "colorDark": "#81c784", "trust": "approved"},
    ],
}


class ParseTests(unittest.TestCase):
    def test_alpha_format(self):
        r = reviews.parse_report(ALPHA_NEEDS)
        self.assertEqual((r["verdict"], r["tier"], r["model"], r["findings"]),
                         ("needs-attention", "full", None, 2))
        self.assertEqual(r["headlines"], ["[medium] Alpha first headline (src/alpha.rs:1-2)",
                                          "[high] Alpha second headline (src/alpha.rs:3)"])

    def test_light_tier_drops_the_reason(self):
        r = reviews.parse_report(ALPHA_LIGHT)
        self.assertEqual((r["verdict"], r["tier"], r["findings"], r["headlines"]),
                         ("approve", "light", 0, []))

    def test_beta_format_has_a_model(self):
        r = reviews.parse_report(BETA_NEEDS)
        self.assertEqual((r["verdict"], r["tier"], r["model"], r["findings"]),
                         ("needs-attention", "full", "model-x", 1))

    def test_no_tier_line_reads_as_full(self):
        r = reviews.parse_report(GAMMA_APPROVE)
        self.assertEqual((r["verdict"], r["tier"], r["model"], r["findings"]),
                         ("approve", "full", "model-x", 0))

    def test_empty_diff_and_unfinished(self):
        self.assertEqual(reviews.parse_report(EMPTY_DIFF)["tier"], "empty")
        self.assertIsNone(reviews.parse_report("# Codex Adversarial Review\n\nTier: full\n"))

    def test_findings_outside_the_section_or_indented_do_not_count(self):
        text = ALPHA_NEEDS.replace("Next steps:\n- Alpha next step.",
                                   "Next steps:\n- [low] not a finding\n  - [high] nested")
        self.assertEqual(reviews.parse_report(text)["findings"], 2)

    def test_headline_is_capped_and_one_line(self):
        long = ALPHA_NEEDS.replace("Alpha first headline", "x\x07" + "y" * 400)
        h = reviews.parse_report(long)["headlines"][0]
        self.assertLessEqual(len(h), reviews.HEADLINE_MAX)
        self.assertNotIn("\x07", h)

    def test_meta(self):
        m = reviews.parse_meta(meta(tip=A1, review=A2, commits=[A1, A2]))
        self.assertEqual((m["tip"], m["base"], m["diff"], m["review"]), (A1, BASE, "d1" * 20, A2))
        self.assertNotIn("tip", reviews.parse_meta(meta()))

    def test_position(self):
        P = reviews.position_of
        self.assertEqual(P({"summary": "Agree: fine"}), "agree")
        self.assertEqual(P({"summary": "agreed, the reason holds"}), "agree")
        self.assertEqual(P({"summary": "Objection: the finding is real"}), "object")
        self.assertEqual(P({"summary": "OBJECT - see body"}), "object")
        self.assertIsNone(P({"summary": "Looking at it"}))
        self.assertIsNone(P({"summary": "Agreement drafted"}))
        self.assertEqual(P({"summary": "Agree", "position": "object"}), "object")
        self.assertIsNone(P({"summary": None}))


class RosterTests(unittest.TestCase):
    def load(self, **kw):
        entry = {"id": "x", "name": "X", "kind": "project", "color": "#8a5a00", "colorDark": "#d9b25c"}
        entry.update(kw)
        with TempRepo() as r:
            return load_roster(r.write_roster(dict(helpers.SPEC_ROSTER, relay="", participants=[entry])))

    def test_review_dir_optional_and_kept(self):
        self.assertIsNone(self.load().participants["x"].reviewDir)
        self.assertEqual(self.load(reviewDir="~/x/reviews").participants["x"].reviewDir, "~/x/reviews")
        self.assertEqual(self.load(reviewDir="/abs/reviews").participants["x"].reviewDir, "/abs/reviews")

    def test_bad_review_dir_rejected(self):
        for bad in ("", "relative/dir", 7, ["~/x"], "~/a\nb", "~/" + "x" * 400, "~user/x"):
            with self.assertRaises(ValueError) as cm:
                self.load(reviewDir=bad)
            self.assertIn("reviewDir", str(cm.exception))

    def test_live_roster_names_the_three_review_dirs(self):
        ro = load_roster(REPO / "participants.json")
        dirs = {pid: p.reviewDir for pid, p in ro.participants.items() if p.reviewDir}
        self.assertEqual(dirs, {"bewareilive": "~/GitHub/bil-reviews",
                                "vectorcraft": "~/.cache/vectorcraft/reviews",
                                "avoidmissingball": "~/.cache/avoidmissingball/reviews"})


class ScanBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-reviews-"))
        self.root = self.tmp / "repo"
        (self.root / "log").mkdir(parents=True)
        self.dirs = {p: self.tmp / "reviews" / p for p in ("alpha", "beta", "gamma")}
        for d in self.dirs.values():
            d.mkdir(parents=True)
        self.write_roster()
        self.state = self.tmp / "state.json"
        self.now = [1000.0]

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def write_roster(self, drop=()):
        raw = json.loads(json.dumps(ROSTER))
        for p in raw["participants"]:
            if "reviewDir" in p:
                if p["id"] in drop:
                    del p["reviewDir"]
                else:
                    p["reviewDir"] = str(self.tmp / "reviews" / p["id"])
        (self.root / "participants.json").write_text(json.dumps(raw), encoding="utf-8")

    def put(self, project, name, text, mtime=None):
        path = self.dirs[project] / name
        path.write_text(text, encoding="utf-8")
        if mtime is not None:
            os.utime(str(path), (mtime, mtime))
        return path

    def seed(self):
        """Every project's starting reports: alpha a1 (needs-attention) and a2 (light),
        a copy record a3 with no report, beta b1, gamma c1 and an empty-diff c2."""
        self.put("alpha", "branch-a1a1a1a.md", ALPHA_NEEDS, 100)
        self.put("alpha", "branch-a1a1a1a.meta", meta(tip=A1, diff="d1" * 20), 99)
        self.put("alpha", "branch-a2a2a2a.md", ALPHA_LIGHT, 101)
        self.put("alpha", "branch-a2a2a2a.meta", meta(tip=A2, diff="d2" * 20), 99)
        self.put("alpha", "branch-a3a3a3a.meta", meta(tip="a3" * 20, diff="d2" * 20, review=A2), 99)
        self.put("alpha", "review-a9a9a9a.md", "# Codex Adversarial Review\n\nVerdict: needs-attention\n")
        self.put("alpha", "review-%s.resolved" % ("a9" * 20), "old per-commit reason\n")
        (self.dirs["alpha"] / "log").mkdir()
        self.put("alpha", "runner.flock", "")
        self.put("beta", "branch-%s.md" % B1, BETA_NEEDS, 102)
        self.put("beta", "branch-%s.meta" % B1, meta(diff="f1" * 20), 99)
        self.put("beta", "branch-%s.checked" % B1, "c9" * 20 + "\n")
        self.put("gamma", "branch-%s.md" % C1, GAMMA_APPROVE, 103)
        self.put("gamma", "branch-%s.meta" % C1, meta(diff="31" * 20), 99)
        self.put("gamma", "branch-%s.md" % C2, EMPTY_DIFF, 104)
        self.put("gamma", "branch-%s.meta" % C2, meta(diff="empty"), 99)

    def scanner(self, **kw):
        kw.setdefault("clock", lambda: self.now[0])
        return reviews.Scanner(self.root, self.state, **kw)

    def scan(self, sc=None, dry_run=False):
        docs, roster = load_snapshot(self.root)
        return (sc or self.scanner()).scan(docs, roster, dry_run=dry_run)

    def docs(self):
        return load_snapshot(self.root)[0]

    def msg(self, did):
        return self.docs().get(("messages", did))

    def log_lines(self, pid="crosstalk"):
        path = self.root / "log" / (pid + ".jsonl")
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def post(self, pid, did, data, op="set"):
        roster = load_roster(self.root / "participants.json")
        append_line(self.root, pid, op, "messages", did, data, roster=roster)


class FirstRunTests(ScanBase):
    def test_first_run_records_everything_and_posts_nothing(self):
        self.seed()
        self.put("alpha", "branch-a1a1a1a.resolved", "a reason\n")
        plan = self.scan()
        self.assertEqual(self.log_lines(), [])
        self.assertEqual(plan["writes"], [])
        a = plan["projects"]["alpha"]
        self.assertTrue(a["first_run"])
        self.assertEqual((a["reports"], a["overrides"], a["recorded"]), (2, 1, 3))
        self.assertTrue(self.state.exists())
        # The next pass finds nothing new either.
        plan = self.scan()
        self.assertEqual(plan["writes"], [])
        self.assertFalse(plan["projects"]["alpha"]["first_run"])

    def test_missing_dir_is_silent_and_baselines_when_it_appears(self):
        shutil.rmtree(str(self.dirs["beta"]))
        err = io.StringIO()
        with redirect_stderr(err):
            plan = self.scan()
        self.assertEqual(err.getvalue(), "")
        self.assertTrue(plan["projects"]["beta"]["missing"])
        self.dirs["beta"].mkdir()
        self.put("beta", "branch-%s.md" % B1, BETA_NEEDS)
        self.put("beta", "branch-%s.meta" % B1, meta(diff="f1" * 20))
        plan = self.scan()
        self.assertTrue(plan["projects"]["beta"]["first_run"])
        self.assertEqual(self.log_lines(), [])

    def test_dry_run_writes_nothing(self):
        self.seed()
        plan = self.scan(dry_run=True)
        self.assertTrue(plan["dry_run"])
        self.assertTrue(plan["projects"]["alpha"]["first_run"])
        self.assertEqual(plan["projects"]["alpha"]["recorded"], 2)
        self.assertFalse(self.state.exists())
        self.scan()
        self.put("gamma", "branch-%s.md" % ("c3" * 20), GAMMA_APPROVE)
        before = self.state.read_text(encoding="utf-8")
        plan = self.scan(dry_run=True)
        self.assertEqual([w["id"] for w in plan["writes"]], [reviews.review_id("gamma", "c3" * 20, "none")])
        self.assertEqual(self.state.read_text(encoding="utf-8"), before)
        self.assertEqual(self.log_lines(), [])

    def test_no_poster_in_roster_posts_nothing(self):
        raw = json.loads((self.root / "participants.json").read_text(encoding="utf-8"))
        raw["participants"] = [p for p in raw["participants"] if p["id"] != "crosstalk"]
        (self.root / "participants.json").write_text(json.dumps(raw), encoding="utf-8")
        self.seed()
        self.scan()
        self.put("gamma", "branch-%s.md" % ("c3" * 20), GAMMA_APPROVE)
        self.assertEqual(self.scan()["writes"], [])


class ReviewPostTests(ScanBase):
    def setUp(self):
        ScanBase.setUp(self)
        self.seed()
        self.scan()

    def test_new_report_posts_a_review(self):
        tip = "a4" * 20
        self.put("alpha", "branch-a4a4a4a.meta", meta(tip=tip, diff="d4" * 20, commits=[tip]))
        self.put("alpha", "branch-a4a4a4a.md", ALPHA_NEEDS)
        self.scan()
        rid = reviews.review_id("alpha", tip, "d4" * 20)
        self.assertTrue(rid.startswith("crosstalk-"))
        m = self.msg(rid)
        self.assertEqual(m["from"], "crosstalk")
        self.assertEqual(m["kind"], "review")
        self.assertEqual(m["source"], "codex-review")
        self.assertEqual((m["project"], m["tip"], m["base"], m["diff"]), ("alpha", tip, BASE, "d4" * 20))
        self.assertEqual((m["tier"], m["verdict"], m["findings"]), ("full", "needs-attention", 2))
        self.assertEqual(m["summary"], "AL review of a4a4a4a (full): needs-attention, 2 findings")
        self.assertEqual(m["report"], str(self.dirs["alpha"] / "branch-a4a4a4a.md"))
        flat = json.dumps(m)
        for secret in ("must never be posted", "Alpha first headline", "recommendation", "next step"):
            self.assertNotIn(secret, flat)

    def test_report_path_under_home_is_written_with_a_tilde(self):
        home = str(Path.home())
        self.assertEqual(reviews.display_path(Path(home) / "x" / "branch-1.md"), "~/x/branch-1.md")
        self.assertEqual(reviews.display_path(Path("/elsewhere/branch-1.md")), "/elsewhere/branch-1.md")

    def test_full_hash_names_and_no_tier(self):
        tip = "c3" * 20
        self.put("gamma", "branch-%s.meta" % tip, meta(diff="33" * 20))
        self.put("gamma", "branch-%s.md" % tip, GAMMA_APPROVE)
        self.scan()
        m = self.msg(reviews.review_id("gamma", tip, "33" * 20))
        self.assertEqual(m["summary"], "GA review of c3c3c3c (full): approve, no findings")
        self.assertEqual((m["tip"], m["model"]), (tip, "model-x"))

    def test_rerun_updates_instead_of_duplicating(self):
        self.put("beta", "branch-%s.md" % B1, BETA_NEEDS.replace("needs-attention", "approve"), 500)
        self.scan()
        rid = reviews.review_id("beta", B1, "f1" * 20)
        self.assertEqual(self.msg(rid)["verdict"], "approve")
        self.put("beta", "branch-%s.md" % B1, BETA_NEEDS, 600)
        self.scan()
        ids = [ln["id"] for ln in self.log_lines()]
        self.assertEqual(ids, [rid, rid])
        self.assertEqual(self.msg(rid)["verdict"], "needs-attention")
        # The same text written again (touched) posts nothing.
        self.put("beta", "branch-%s.md" % B1, BETA_NEEDS, 700)
        self.scan()
        self.assertEqual(len(self.log_lines()), 2)

    def test_a_new_diff_on_the_same_tip_is_a_new_review(self):
        self.put("beta", "branch-%s.meta" % B1, meta(diff="f2" * 20))
        self.put("beta", "branch-%s.md" % B1, BETA_NEEDS.replace("Beta only", "Beta rerun"), 500)
        self.scan()
        self.assertIsNotNone(self.msg(reviews.review_id("beta", B1, "f2" * 20)))

    def test_moved_files_with_new_mtimes_post_nothing(self):
        for f in self.dirs["alpha"].iterdir():
            if f.is_file():
                os.utime(str(f), (5000, 5000))
        self.scan()
        self.assertEqual(self.log_lines(), [])

    def test_unfinished_and_empty_diff_reports_are_not_posted(self):
        self.put("gamma", "branch-%s.md" % ("c4" * 20), "# Codex Adversarial Review\n\n")
        self.put("gamma", "branch-%s.md" % ("c5" * 20), EMPTY_DIFF)
        self.put("gamma", "branch-%s.meta" % ("c5" * 20), meta(diff="empty"))
        self.scan()
        self.assertEqual(self.log_lines(), [])
        # Once the unfinished one is complete it posts.
        self.put("gamma", "branch-%s.md" % ("c4" * 20), GAMMA_APPROVE)
        self.scan()
        self.assertEqual([ln["id"] for ln in self.log_lines()],
                         [reviews.review_id("gamma", "c4" * 20, "none")])

    def test_cap_carries_the_rest_over(self):
        for i in range(5):
            tip = ("%x" % (10 + i)) * 40
            self.put("gamma", "branch-%s.md" % tip, GAMMA_APPROVE, 2000 + i)
        self.scan()
        self.assertEqual(len(self.log_lines()), reviews.MAX_ITEMS_PER_PASS)
        self.scan()
        self.assertEqual(len(self.log_lines()), 5)
        tips = [ln["data"]["tip"][0] for ln in self.log_lines()]
        self.assertEqual(tips, ["a", "b", "c", "d", "e"])  # oldest first

    def test_cap_is_per_project(self):
        for p, name in (("alpha", "branch-a5a5a5a.md"), ("beta", "branch-%s.md" % ("b5" * 20))):
            for i in range(4):
                self.put(p, name.replace("5", str(i + 5)), ALPHA_LIGHT, 2000 + i)
        self.scan()
        by = {}
        for ln in self.log_lines():
            by[ln["data"]["project"]] = by.get(ln["data"]["project"], 0) + 1
        self.assertEqual(by, {"alpha": 3, "beta": 3})

    def test_project_without_review_dir_is_not_scanned(self):
        self.write_roster(drop=("gamma",))
        self.put("gamma", "branch-%s.md" % ("c6" * 20), GAMMA_APPROVE)
        plan = self.scan()
        self.assertNotIn("gamma", plan["projects"])
        self.assertEqual(self.log_lines(), [])


class OverrideTests(ScanBase):
    def setUp(self):
        ScanBase.setUp(self)
        self.seed()
        self.scan()

    def resolve(self, project, stem, reason, mtime=None):
        return self.put(project, "branch-%s.resolved" % stem, reason + "\n", mtime)

    def test_resolved_posts_a_disagreement_and_asks_one_other_project(self):
        self.resolve("alpha", "a1a1a1a", "The finding is out of scope: see docs/x.md.")
        self.scan()
        oid = reviews.override_id("alpha", A1, "d1" * 20)
        d = self.msg(oid)
        self.assertEqual((d["from"], d["kind"], d["source"]), ("crosstalk", "disagreement", "codex-review"))
        self.assertEqual((d["project"], d["tip"], d["status"]), ("alpha", A1, "awaiting"))
        self.assertEqual(d["headlines"], ["[medium] Alpha first headline (src/alpha.rs:1-2)",
                                          "[high] Alpha second headline (src/alpha.rs:3)"])
        self.assertEqual(d["reason"], "The finding is out of scope: see docs/x.md.")
        self.assertEqual(set(d["positions"]), {"codex", "alpha"})
        self.assertEqual(d["positions"]["alpha"], d["reason"])
        self.assertIn("2 findings", d["positions"]["codex"])
        self.assertEqual(d["review"], reviews.review_id("alpha", A1, "d1" * 20))
        self.assertIn(d["reviewer"], ("beta", "gamma"))
        self.assertTrue(d["summary"].startswith("AL overrode the Codex review of a1a1a1a"))
        self.assertNotIn("must never be posted", json.dumps(d))
        ask = self.msg(d["request"])
        self.assertEqual((ask["kind"], ask["to"], ask["replyTo"], ask["from"]),
                         ("request", [d["reviewer"]], oid, "crosstalk"))
        self.assertIn("Agree", ask["body"])
        self.assertIn("Object", ask["body"])

    def test_reviewers_rotate_between_the_other_two(self):
        self.resolve("alpha", "a1a1a1a", "reason one", 300)
        self.resolve("alpha", "a2a2a2a", "reason two", 301)
        self.scan()
        asked = [self.msg(reviews.override_id("alpha", t, d))["reviewer"]
                 for t, d in ((A1, "d1" * 20), (A2, "d2" * 20))]
        self.assertEqual(sorted(asked), ["beta", "gamma"])
        tip = "a6" * 20
        self.put("alpha", "branch-a6a6a6a.md", ALPHA_NEEDS)
        self.put("alpha", "branch-a6a6a6a.meta", meta(tip=tip, diff="d6" * 20))
        self.resolve("alpha", "a6a6a6a", "reason three", 302)
        self.scan()
        self.assertEqual(self.msg(reviews.override_id("alpha", tip, "d6" * 20))["reviewer"], asked[0])

    def test_long_reason_and_many_headlines_are_capped(self):
        many = ALPHA_NEEDS.replace("Next steps:", "".join(
            "- [low] extra %d\n  body\n" % i for i in range(10)) + "\nNext steps:")
        self.put("alpha", "branch-a1a1a1a.md", many, 400)
        self.resolve("alpha", "a1a1a1a", "r" * 5000)
        self.scan()
        d = self.msg(reviews.override_id("alpha", A1, "d1" * 20))
        self.assertEqual(len(d["headlines"]), reviews.HEADLINES_MAX)
        self.assertEqual(d["findings"], 12)
        self.assertLessEqual(len(d["reason"]), reviews.REASON_MAX)

    def test_rewritten_reason_updates_the_same_disagreement(self):
        self.resolve("alpha", "a1a1a1a", "first reason", 300)
        self.scan()
        self.resolve("alpha", "a1a1a1a", "second reason", 400)
        self.scan()
        oid = reviews.override_id("alpha", A1, "d1" * 20)
        ops = [(ln["op"], ln["id"]) for ln in self.log_lines()]
        self.assertEqual(ops.count(("set", oid)), 1)
        self.assertIn(("update", oid), ops)
        d = self.msg(oid)
        self.assertEqual((d["reason"], d["positions"]["alpha"]), ("second reason", "second reason"))
        self.assertEqual(sum(1 for ln in self.log_lines() if ln["data"].get("kind") == "request"), 1)

    def ask(self):
        self.resolve("alpha", "a1a1a1a", "reason")
        self.scan()
        oid = reviews.override_id("alpha", A1, "d1" * 20)
        return oid, self.msg(oid)

    def test_objection_is_recognised_and_forwarded_to_the_project(self):
        oid, d = self.ask()
        who = d["reviewer"]
        self.post(who, who + "-1", {"from": who, "kind": "reply", "replyTo": d["request"],
                                    "summary": "Object: the finding is real", "to": ["crosstalk"]})
        self.scan()
        d = self.msg(oid)
        self.assertEqual(d["status"], "objected")
        self.assertEqual(d["objections"], [{"id": who + "-1", "from": who,
                                            "summary": "Object: the finding is real"}])
        fwd = self.msg(reviews.forward_id(who + "-1"))
        self.assertEqual((fwd["kind"], fwd["to"], fwd["replyTo"]), ("request", ["alpha"], who + "-1"))
        self.assertIn("advisory", fwd["body"].lower())
        # A second pass changes nothing.
        n = len(self.log_lines())
        self.scan()
        self.assertEqual(len(self.log_lines()), n)
        # The project sees it in its inbox.
        from crosstalk.requests import inbox_for
        roster = load_roster(self.root / "participants.json")
        self.assertIn(reviews.forward_id(who + "-1"), [i for i, _d, _r in inbox_for(self.docs(), "alpha", roster)])

    def test_agreement_sets_agreed_and_forwards_nothing(self):
        oid, d = self.ask()
        who = d["reviewer"]
        self.post(who, who + "-1", {"from": who, "kind": "reply", "replyTo": d["request"],
                                    "summary": "Agree: the reason holds"})
        self.scan()
        self.assertEqual(self.msg(oid)["status"], "agreed")
        self.assertEqual(self.msg(oid)["agreed"], [who])
        self.assertFalse(any(ln["id"].startswith("crosstalk-objection-") for ln in self.log_lines()))

    def test_position_field_and_latest_reply_win(self):
        oid, d = self.ask()
        who = d["reviewer"]
        self.post(who, who + "-1", {"from": who, "kind": "reply", "replyTo": oid,
                                    "summary": "Agree at first", "t": "2026-10-21T18:00:00Z"})
        self.post(who, who + "-2", {"from": who, "kind": "reply", "replyTo": who + "-1",
                                    "summary": "On second thought", "position": "object"})
        self.scan()
        self.assertEqual(self.msg(oid)["status"], "objected")

    def test_replies_from_the_project_owner_and_crosstalk_do_not_count(self):
        oid, d = self.ask()
        self.post("alpha", "alpha-1", {"from": "alpha", "kind": "reply", "replyTo": oid,
                                       "summary": "Object to nothing, just context"})
        self.post("owner", "owner-1", {"from": "owner", "kind": "owner", "replyTo": oid,
                                       "summary": "Objection noted"})
        self.scan()
        self.assertEqual(self.msg(oid)["status"], "awaiting")

    def test_resolved_disagreement_is_no_longer_tracked(self):
        oid, d = self.ask()
        who = d["reviewer"]
        self.post(who, who + "-1", {"from": who, "kind": "reply", "replyTo": d["request"],
                                    "summary": "Object: late"})
        self.post("owner", oid, {"resolution": {"choice": "alpha", "by": "owner",
                                                "t": "2026-10-29T00:00:00Z"}}, op="update")
        self.scan()
        self.assertEqual(self.msg(oid)["status"], "awaiting")
        self.assertEqual(reviews.open_overrides(*load_snapshot(self.root)), [])

    def test_open_overrides_lists_unsettled_ones(self):
        oid, d = self.ask()
        who = d["reviewer"]
        self.post(who, who + "-1", {"from": who, "kind": "reply", "replyTo": d["request"],
                                    "summary": "Object: no"})
        self.scan()
        rows = reviews.open_overrides(*load_snapshot(self.root))
        self.assertEqual([(r["id"], r["status"], r["project"]) for r in rows], [(oid, "objected", "alpha")])
        self.assertEqual(rows[0]["objections"][0]["from"], who)


class PollTests(ScanBase):
    def setUp(self):
        ScanBase.setUp(self)
        # Never read or prune the real live registrations.
        patcher = mock.patch.dict(os.environ, {"CROSSTALK_LIVE_DIR": str(self.tmp / "live")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_poll_runs_every_interval(self):
        self.seed()
        sc = self.scanner(interval=60)
        calls = []
        real = sc.scan
        sc.scan = lambda docs, roster, dry_run=False: calls.append(1) or real(docs, roster)
        docs, roster = load_snapshot(self.root)
        sc.poll(docs, roster)
        self.now[0] += 30
        sc.poll(docs, roster)
        self.now[0] += 31
        sc.poll(docs, roster)
        self.assertEqual(len(calls), 2)

    def test_notifier_runs_the_scanner_each_pass(self):
        seen = []

        class Rec:
            def poll(self, docs, roster):
                seen.append(roster.participants["alpha"].reviewDir)

        snap = lambda: load_snapshot(self.root)
        n = Notifier(self.root, snap, None, reviews=Rec())
        try:
            n.poll_once()
            n.wait_reviews(5)
        finally:
            n.stop()
        self.assertEqual(seen, [str(self.dirs["alpha"])])

    def test_notifier_survives_a_failing_scanner(self):
        class Boom:
            def poll(self, docs, roster):
                raise RuntimeError("boom")

        n = Notifier(self.root, lambda: load_snapshot(self.root), None, reviews=Boom())
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                n.poll_once()
                n.wait_reviews(5)
        finally:
            n.stop()
        self.assertIn("review scan failed", err.getvalue())

    def test_a_stalled_scan_never_holds_up_the_live_pass(self):
        # A review folder in iCloud can block a read on a file that isn't
        # downloaded; the scan runs on its own thread so pushes keep going.
        gate, calls = threading.Event(), []

        class Stuck:
            def poll(self, docs, roster):
                calls.append(1)
                gate.wait(10)

        n = Notifier(self.root, lambda: load_snapshot(self.root), None, reviews=Stuck())
        try:
            t0 = time.monotonic()
            n.poll_once()
            n.poll_once()
            self.assertLess(time.monotonic() - t0, 5)
            self.assertEqual(calls, [1])  # the second pass skips while one scan runs
        finally:
            gate.set()
            n.wait_reviews(5)
            n.stop()

    def test_server_wires_the_scanner_into_its_notifier(self):
        with helpers.RunningServer(self.root, notify=True, reviews_state=self.state,
                                   notify_poll_sec=3600) as srv:
            self.assertIsInstance(srv._notifier.reviews, reviews.Scanner)
            self.assertEqual(srv._notifier.reviews.state_path, self.state)
        with helpers.RunningServer(self.root, notify=True, notify_poll_sec=3600) as srv:
            self.assertIsNone(srv._notifier.reviews)

    def test_state_path_from_env(self):
        old = os.environ.get("CROSSTALK_REVIEWS_STATE")
        try:
            os.environ["CROSSTALK_REVIEWS_STATE"] = ""
            self.assertIsNone(reviews.state_path_from_env())
            os.environ["CROSSTALK_REVIEWS_STATE"] = "/tmp/x.json"
            self.assertEqual(reviews.state_path_from_env(), Path("/tmp/x.json"))
            del os.environ["CROSSTALK_REVIEWS_STATE"]
            self.assertEqual(reviews.state_path_from_env(), reviews.DEFAULT_STATE)
        finally:
            if old is None:
                os.environ.pop("CROSSTALK_REVIEWS_STATE", None)
            else:
                os.environ["CROSSTALK_REVIEWS_STATE"] = old


class CliTests(ScanBase):
    def setUp(self):
        ScanBase.setUp(self)
        shutil.copytree(str(REPO / "scripts"), str(self.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.seed()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.root / "scripts" / "relay.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def test_scan_dry_run_then_scan_then_open(self):
        p = self.run_cli("reviews", "scan", "--dry-run", "--state", str(self.state))
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertTrue(out["dry_run"])
        self.assertTrue(out["projects"]["alpha"]["first_run"])
        self.assertFalse(self.state.exists())
        p = self.run_cli("reviews", "scan", "--state", str(self.state))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.state.exists())
        self.put("alpha", "branch-a1a1a1a.resolved", "reason\n")
        p = self.run_cli("reviews", "scan", "--state", str(self.state))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(json.loads(p.stdout)["writes"]), 2)
        p = self.run_cli("reviews", "open")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = json.loads(p.stdout)
        self.assertEqual([r["status"] for r in rows], ["awaiting"])

    def test_scan_needs_a_state_path(self):
        env = dict(os.environ, CROSSTALK_REVIEWS_STATE="")
        p = subprocess.run([sys.executable, str(self.root / "scripts" / "relay.py"), "reviews", "scan"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           cwd="/", env=env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("state", p.stderr)


if __name__ == "__main__":
    unittest.main()
