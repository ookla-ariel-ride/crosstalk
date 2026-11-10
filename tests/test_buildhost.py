"""The build-host poller: probe parsing over a fake ssh, and one post per state change."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts scripts/ on sys.path)
from crosstalk import buildhost
from crosstalk.participants import load_roster

REPO = Path(__file__).resolve().parent.parent
ROSTER = {
    "page": "http://127.0.0.1:8484/",
    "relay": "relay-bot",
    "participants": [
        {"id": "relay-bot", "name": "Relay Bot", "kind": "project", "color": "#2e7d32",
         "colorDark": "#81c784", "trust": "approved"},
        {"id": "owner", "name": "Owner", "kind": "person", "color": "#6b3fa0",
         "colorDark": "#b892e6", "trust": "approved"},
    ],
}
UPTIME = "14:02  up 3 days,  2:11, 2 users, load averages: %s %s %s\n"
DF = ("Filesystem     1024-blocks      Used Available Capacity iused ifree %%iused  Mounted on\n"
      "/dev/disk3s5    1942700360 900000000 %d    50%% 1 2 0%%   /System/Volumes/Data\n")


def ok(load1, free_gib=812.0):
    return 0, UPTIME % (load1, 5.0, 4.0) + DF % int(free_gib * 1024 * 1024), ""


DOWN = (255, "", "ssh: connect to host buildhost port 22: Operation timed out\n")


class ProbeTests(unittest.TestCase):
    def test_parses_macos_uptime_and_df(self):
        seen = []

        def runner(argv, timeout):
            seen.append(argv)
            return ok(13.25, 812.0)
        p = buildhost.probe("buildhost", runner=runner)
        self.assertEqual(p, {"host": "buildhost", "reachable": True, "load": [13.25, 5.0, 4.0],
                             "free_gib": 812.0, "error": None})
        self.assertIn("BatchMode=yes", seen[0])
        self.assertIn("ConnectTimeout=8", seen[0])

    def test_linux_uptime_commas(self):
        out = " 14:02:01 up 3 days, load average: 1.50, 2.00, 3.00\n" + DF % 1048576
        p = buildhost.probe("buildhost", runner=lambda a, t: (0, out, ""))
        self.assertEqual((p["load"], p["free_gib"]), ([1.5, 2.0, 3.0], 1.0))

    def test_unreachable_keeps_last_stderr_line(self):
        p = buildhost.probe("buildhost", runner=lambda a, t: DOWN)
        self.assertFalse(p["reachable"])
        self.assertEqual(p["error"], "ssh: connect to host buildhost port 22: Operation timed out")

    def test_remote_failure_is_still_reachable(self):
        p = buildhost.probe("buildhost", runner=lambda a, t: (1, UPTIME % (1, 1, 1), "df: oops"))
        self.assertTrue(p["reachable"])
        self.assertEqual(p["load"][0], 1.0)
        self.assertIsNone(p["free_gib"])


class PollTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="relay-bot-buildhost-"))
        self.root = self.tmp / "repo"
        (self.root / "log").mkdir(parents=True)
        (self.root / "participants.json").write_text(json.dumps(ROSTER), encoding="utf-8")
        self.roster = load_roster(self.root / "participants.json")
        self.state = self.tmp / "state.json"
        self.now = [1_800_000_000.0]
        self.reply = ok(2.0)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def poll(self, reply=None, dry_run=False):
        if reply is not None:
            self.reply = reply
        self.now[0] += 120
        prober = lambda: buildhost.probe("buildhost", runner=lambda a, t: self.reply)  # noqa: E731
        return buildhost.Poller(self.root, self.state, prober=prober,
                             clock=lambda: self.now[0]).poll(self.roster, dry_run=dry_run)

    def posts(self):
        path = self.root / "log" / "relay-bot.jsonl"
        if not path.exists():
            return []
        return [json.loads(x)["data"] for x in path.read_text(encoding="utf-8").splitlines()]

    def test_quiet_first_poll_and_no_change_post_nothing(self):
        self.poll(ok(2.0))
        self.poll(ok(9.0))
        self.poll(ok(11.9, 60.0))
        self.assertEqual(self.posts(), [])

    def test_unreachable_then_recovered_posts_twice(self):
        self.poll(ok(2.0))
        self.poll(DOWN)
        self.poll(DOWN)
        self.poll(ok(3.0))
        self.poll(ok(3.0))
        posts = self.posts()
        self.assertEqual([p["summary"] for p in posts], [
            "Build host unreachable: ssh: connect to host buildhost port 22: Operation timed out",
            "Build host reachable again: load 3.0, 812 GiB free"])
        self.assertEqual([p["kind"] for p in posts], ["alert", "alert"])
        self.assertEqual([p["important"] for p in posts], [True, False])

    def test_load_hysteresis(self):
        self.poll(ok(11.9))
        plan = self.poll(ok(13.2))
        self.assertEqual(plan["changed"], ["busy"])
        self.poll(ok(11.0))  # still busy: not under 10
        self.poll(ok(12.5))
        self.poll(ok(9.5))
        self.poll(ok(11.0))  # not busy: not yet 12
        self.assertEqual([p["summary"] for p in self.posts()], [
            "Build host: load 13.2 (over 12), 812 GiB free",
            "Build host: load 9.5 (back under 10), 812 GiB free"])

    def test_low_disk_posts_once(self):
        self.poll(ok(1.0, 49.0))
        self.poll(ok(1.0, 52.0))
        self.poll(ok(1.0, 56.0))
        posts = self.posts()
        self.assertEqual([p["summary"] for p in posts], [
            "Build host: load 1.0, 49 GiB free (under 50)", "Build host: load 1.0, 56 GiB free"])
        self.assertTrue(posts[0]["important"])
        self.assertTrue(all(p["from"] == "relay-bot" for p in posts))
        ids = [json.loads(x)["id"] for x in
               (self.root / "log" / "relay-bot.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(i.startswith("relay-bot-buildhost-") for i in ids))
        self.assertEqual(json.loads(self.state.read_text())["posted"], ids[-1])
        self.assertEqual(os.stat(str(self.state)).st_mode & 0o777, 0o600)

    def test_dry_run_writes_nothing(self):
        plan = self.poll(DOWN, dry_run=True)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(len(plan["writes"]), 1)
        self.assertEqual(self.posts(), [])
        self.assertFalse(self.state.exists())

    def test_message_passes_the_log_check(self):
        self.poll(DOWN)
        from crosstalk.logs import check_all
        self.assertEqual(check_all(self.root, self.roster), [])


class PlistTests(unittest.TestCase):
    def test_renders_with_label_interval_and_command(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "buildhost.plist"
            subprocess.run([sys.executable, str(REPO / "scripts" / "launchd" / "render_plist.py"),
                            str(REPO / "scripts" / "launchd" / "crosstalk-buildhost.plist"), str(dest),
                            "/tmp/repo", "/Users/someone"], check=True)
            with open(str(dest), "rb") as f:
                plist = plistlib.load(f)
        self.assertEqual(plist["Label"], "local.crosstalk-buildhost")
        self.assertEqual(plist["StartInterval"], 120)
        self.assertEqual(plist["ProgramArguments"][1:], ["/tmp/repo/scripts/relay.py", "buildhost", "poll"])


if __name__ == "__main__":
    unittest.main()
