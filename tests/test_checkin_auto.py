"""relay.py checkin --auto: a check-in whose facts come from state (crosstalk/checkin.py)."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line, relay_in

ROSTER = copy.deepcopy(SPEC_ROSTER)
ROSTER["relay"] = "rev"
NOW = time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(minutes):
    return iso(NOW - minutes * 60)


def msg(seq, frm, id_, minutes_ago, **data):
    return line(seq, frm, id_=id_, data=dict({"from": frm}, **data), t=ago(minutes_ago))


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class CheckinAutoTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster(ROSTER)
        self.git_dir = Path(tempfile.mkdtemp(prefix="crosstalk-git-"))
        self.git("init", "-q")
        self.git("checkout", "-q", "-b", "main")
        self.commit("old work", 300)
        self.commit("new parser", 50)
        self.commit("faster replay", 10)

    def tearDown(self):
        self.r.__exit__(None, None, None)
        shutil.rmtree(str(self.git_dir), ignore_errors=True)

    def git(self, *args, **env):
        e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com", **env)
        subprocess.run(["git"] + list(args), cwd=str(self.git_dir), check=True, env=e,
                       stdout=subprocess.DEVNULL)

    def commit(self, subject, minutes_ago):
        stamp = "@%d +0000" % int(NOW - minutes_ago * 60)
        self.git("commit", "-q", "--allow-empty", "-m", subject, GIT_COMMITTER_DATE=stamp, GIT_AUTHOR_DATE=stamp)

    def relay(self, *args):
        return subprocess.run([sys.executable, str(relay_in(self.r.root))] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)

    def rows(self, pid="alpha"):
        path = self.r.root / "log" / (pid + ".jsonl")
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def seed(self):
        self.r.write_log("alpha", [
            msg(1, "alpha", "alpha-c0", 200, kind="check-in", summary="status"),
            msg(2, "alpha", "alpha-c1", 120, kind="check-in", summary="status"),
            # Retracted: the window starts at alpha-c1, not here.
            msg(3, "alpha", "alpha-c2", 30, kind="check-in", summary="status", retracted=True),
            msg(4, "alpha", "alpha-q1", 100, kind="request", to=["beta"], summary="Can beta check the parser?"),
            msg(5, "alpha", "alpha-q2", 90, kind="request", to=["beta"], summary="Old ask"),
            msg(6, "alpha", "alpha-o1", 80, kind="question", summary="Ship the parser?"),
            msg(7, "alpha", "alpha-t1", 20, kind="request", summary="TURN ASK alpha parser-fix-1119"),
        ])
        self.r.write_log("beta", [
            msg(1, "beta", "beta-a1", 85, kind="reply", replyTo="alpha-q2", summary="done"),
            msg(2, "beta", "beta-l1", 15, kind="status", summary="LAND START beta speed-up-1119"),
        ])
        self.r.write_log("rev", [
            msg(1, "rev", "rev-review-alpha-1", 40, kind="review", source="codex-review", project="alpha",
                tip="abcdef1234", summary="AL review of abcdef1 (full): approve, no findings"),
            msg(2, "rev", "rev-review-alpha-0", 150, kind="review", source="codex-review", project="alpha",
                tip="0123456789", summary="AL review of 0123456 (full): old"),
            msg(3, "rev", "rev-override-beta-1", 60, kind="disagreement", source="codex-review",
                project="beta", tip="fedcba9876", status="objected", reviewer="alpha",
                objections=[{"id": "x", "from": "alpha", "summary": "Object"}], summary="override"),
            msg(4, "rev", "rev-g1", 18, kind="reply", replyTo="alpha-t1", summary="GRANTED alpha parser-fix-1119"),
        ])

    def test_dry_run_builds_the_facts(self):
        self.seed()
        p = self.relay("checkin", "--from", "alpha", "--auto", "--repo", str(self.git_dir), "--dry-run",
                       "--note", "Parser work goes on.")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout
        head = out.splitlines()[0]
        self.assertTrue(head.startswith("Check-in: main "), head)
        self.assertIn("2 commits since last check-in", head)
        self.assertIn("land: asked, place 1, granted;", head)
        self.assertIn("1 open request", head)
        self.assertIn("1 objected override", head)
        self.assertLessEqual(len(head), 200)
        self.assertIn("previous check-in alpha-c1", out)
        self.assertIn("faster replay", out)
        self.assertIn("new parser", out)
        self.assertNotIn("old work", out)
        self.assertIn("no upstream", out)
        self.assertIn("clean", out)
        self.assertIn("rev-review-alpha-1", out)
        self.assertNotIn("rev-review-alpha-0", out)  # before the window
        self.assertIn("asks your review rev-override-beta-1, tip fedcba9: objected (objection from alpha)", out)
        self.assertIn("TURN ASK alpha-t1 for parser-fix-1119: place 1 of 1, granted (rev-g1)", out)
        self.assertIn("queue: 1 holding, 1 waiting", out)
        self.assertIn("alpha-q1 to beta: Can beta check the parser?", out)
        self.assertNotIn("alpha-q2", out)  # answered
        self.assertIn("alpha-o1: Ship the parser?", out)
        self.assertIn("Notes:\n\nParser work goes on.", out)
        self.assertTrue(out.rstrip().endswith("No answer needed."))
        self.assertEqual(self.rows("alpha")[-1]["id"], "alpha-t1")  # nothing posted

    def test_post_goes_through_the_write_path_and_the_dup_guard(self):
        self.r.write_log("alpha", [msg(1, "alpha", "alpha-c1", 120, kind="check-in", summary="status")])
        args = ("checkin", "--from", "alpha", "--auto", "--repo", str(self.git_dir), "--note", "All quiet.")
        p = self.relay(*args)
        self.assertEqual(p.returncode, 0, p.stderr)
        row = self.rows()[-1]
        self.assertEqual(row["id"], p.stdout.strip())
        self.assertTrue(row["id"].startswith("alpha-"))
        data = row["data"]
        self.assertEqual((data["kind"], data["needs_answer"]), ("check-in", False))
        self.assertIn("Land queue:\n- nothing held or asked", data["body"])
        self.assertIn("Owner items:\n- none", data["body"])
        # The same post again within 10 minutes is a duplicate. The window now starts
        # at the new check-in, so the facts differ: --since pins it to repeat them.
        p = self.relay(*(args + ("--since", ago(120))))
        self.assertEqual(p.returncode, 0, p.stderr)
        before = len(self.rows())
        p = self.relay(*(args + ("--since", ago(120))))
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(len(self.rows()), before)
        p = self.relay(*(args + ("--since", ago(120), "--allow-duplicate")))
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_a_question_in_the_prose_needs_an_answer(self):
        body = Path(self.git_dir) / "note.txt"
        body.write_text("Should beta take the next turn?\n", encoding="utf-8")
        p = self.relay("checkin", "--from", "alpha", "--auto", "--repo", str(self.git_dir),
                       "--body-file", str(body))
        self.assertEqual(p.returncode, 0, p.stderr)
        data = self.rows()[-1]["data"]
        self.assertNotIn("needs_answer", data)
        self.assertNotIn("No answer needed", data["body"])
        self.assertIn("no previous check-in", data["body"])
        self.assertIn("3 commits", data["summary"])  # the last 24 hours

    def test_current_branch_when_no_main_and_bad_input(self):
        self.git("branch", "-q", "-m", "main", "work")
        p = self.relay("checkin", "--from", "alpha", "--auto", "--repo", str(self.git_dir), "--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Commits on work since then: 3", p.stdout)
        p = self.relay("checkin", "--from", "alpha", "--auto", "--repo", str(self.r.root / "log"), "--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Git: not read (no git repository).", p.stdout)
        self.assertIn("warning", p.stderr)
        p = self.relay("checkin", "--from", "alpha", "--auto", "--since", "yesterday", "--dry-run")
        self.assertEqual(p.returncode, 2)
        p = self.relay("checkin", "--from", "alpha")
        self.assertEqual(p.returncode, 2)  # no --auto and no text
        p = self.relay("checkin", "--from", "nobody", "--auto")
        self.assertEqual(p.returncode, 1)

    def test_without_auto_the_body_is_the_prose(self):
        p = self.relay("checkin", "--from", "alpha", "--note", "Main at abc1234, clean.")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = self.rows()[-1]["data"]
        self.assertEqual(data["summary"], "Check-in: Main at abc1234, clean.")
        self.assertEqual(data["body"], "Main at abc1234, clean.\n\nNo answer needed.")


if __name__ == "__main__":
    unittest.main()
