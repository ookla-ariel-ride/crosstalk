"""Agents' scheduled jobs: schedules.py, relay.py schedule, the log rule, and the page's data."""
import re
import json
import shutil
import subprocess
import sys
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from helpers import RunningServer, TempRepo

from crosstalk.logs import append_line, schedule_problems
from crosstalk.participants import load_roster
from crosstalk.schedules import (
    CronError, STANDARD_JOBS, job_id, mark_ran, next_run, period_minutes, remove_job, schedule_view, set_job,
    standard_jobs, standard_text, status)

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc
LA = ZoneInfo("America/Los_Angeles")


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class NextRunTests(unittest.TestCase):
    def test_every_30_minutes_is_strictly_after(self):
        self.assertEqual(next_run("*/30 * * * *", utc(2026, 11, 8, 10, 0), UTC), utc(2026, 11, 8, 10, 30))
        self.assertEqual(next_run("*/30 * * * *", utc(2026, 11, 8, 10, 7, 12), UTC), utc(2026, 11, 8, 10, 30))

    def test_crosses_day_month_and_year(self):
        self.assertEqual(next_run("15 3 * * *", utc(2026, 11, 8, 3, 15), UTC), utc(2026, 11, 9, 3, 15))
        self.assertEqual(next_run("0 0 1 * *", utc(2026, 11, 30, 23, 59), UTC), utc(2026, 12, 1, 0, 0))
        self.assertEqual(next_run("0 0 1 1 *", utc(2026, 12, 31, 23, 59), UTC), utc(2027, 1, 1, 0, 0))

    def test_short_months_and_leap_day(self):
        self.assertEqual(next_run("0 12 31 * *", utc(2026, 11, 1), UTC), utc(2026, 12, 31, 12, 0))
        self.assertEqual(next_run("0 0 29 2 *", utc(2026, 11, 8), UTC), utc(2028, 2, 29, 0, 0))

    def test_an_impossible_date_has_no_next_run(self):
        self.assertIsNone(next_run("0 0 31 2 *", utc(2026, 11, 8), UTC))

    def test_weekdays_names_and_seven_is_sunday(self):
        # 2026-11-08 is a Sunday.
        self.assertEqual(next_run("0 9 * * 1-5", utc(2026, 11, 8), UTC), utc(2026, 11, 9, 9, 0))
        self.assertEqual(next_run("0 9 * * 7", utc(2026, 11, 8, 10), UTC), utc(2026, 11, 15, 9, 0))
        self.assertEqual(next_run("0 9 * * sun", utc(2026, 11, 8, 10), UTC), utc(2026, 11, 15, 9, 0))
        self.assertEqual(next_run("0 9 1 jan *", utc(2026, 11, 8), UTC), utc(2027, 1, 1, 9, 0))

    def test_day_of_month_and_weekday_are_either_or(self):
        # Vixie cron: both restricted means either matches. Nov 13 is the first Friday after.
        self.assertEqual(next_run("0 0 20 * 5", utc(2026, 11, 8), UTC), utc(2026, 11, 13, 0, 0))

    def test_lists_ranges_and_steps(self):
        self.assertEqual(next_run("5,10-12/2 * * * *", utc(2026, 11, 8, 10, 10), UTC), utc(2026, 11, 8, 10, 12))

    def test_local_time_is_the_cron_clock(self):
        self.assertEqual(next_run("0 9 * * *", utc(2026, 11, 8, 12), LA), utc(2026, 11, 8, 17, 0))

    def test_spring_forward_skips_the_missing_hour(self):
        # 2026-03-08 02:30 does not exist in Los Angeles; the next 02:30 is the 9th.
        self.assertEqual(next_run("30 2 * * *", utc(2026, 3, 8, 9), LA), utc(2026, 3, 9, 9, 30))

    def test_fall_back_fires_in_both_repeated_hours(self):
        first = next_run("30 1 * * *", utc(2026, 11, 1, 8), LA)
        self.assertEqual(first, utc(2026, 11, 1, 8, 30))
        self.assertEqual(next_run("30 1 * * *", first, LA), utc(2026, 11, 1, 9, 30))

    def test_a_half_hour_zone_does_not_miss_midnight(self):
        ist = timezone(timedelta(hours=5, minutes=30))
        self.assertEqual(next_run("10 0 * * 1", utc(2026, 11, 8, 0), ist), utc(2026, 11, 8, 18, 40))

    def test_unsupported_expressions_raise(self):
        for bad in ("@hourly", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *",
                    "* * * 13 *", "* * * * 8", "*/0 * * * *", "5/15 * * * *", "9-3 * * * *", "a * * * *", "", 5):
            with self.assertRaises(CronError, msg=repr(bad)):
                next_run(bad, utc(2026, 11, 8), UTC)

    def test_period(self):
        self.assertEqual(period_minutes("*/30 * * * *", utc(2026, 11, 8), UTC), 30)
        self.assertEqual(period_minutes("0 3 * * *", utc(2026, 11, 8), UTC), 1440)
        self.assertIsNone(period_minutes("0 0 31 2 *", utc(2026, 11, 8), UTC))


NOW = utc(2026, 11, 8, 12, 0)


def job(next_=None, period=30, reported=NOW, **kw):
    d = {"from": "alpha", "name": "check-in", "cron": "*/30 * * * *", "reported": iso(reported), "runs": []}
    d["next"] = iso(next_) if next_ else None
    d["periodMin"] = period
    d.update(kw)
    return d


class StatusTests(unittest.TestCase):
    def test_a_future_run_is_scheduled(self):
        self.assertEqual(status(job(NOW + timedelta(minutes=5)), NOW), "scheduled")

    def test_late_only_after_half_the_period(self):
        doc = job(NOW - timedelta(minutes=15))
        self.assertEqual(status(doc, NOW), "scheduled")
        self.assertEqual(status(job(NOW - timedelta(minutes=15, seconds=1)), NOW), "late")

    def test_grace_is_at_least_five_minutes(self):
        self.assertEqual(status(job(NOW - timedelta(minutes=5), period=1), NOW), "scheduled")
        self.assertEqual(status(job(NOW - timedelta(minutes=5, seconds=1), period=1), NOW), "late")
        self.assertEqual(status(job(NOW - timedelta(minutes=6), period=None), NOW), "late")

    def test_stale_when_no_report_for_three_periods_or_two_hours(self):
        due = NOW - timedelta(minutes=100)
        self.assertEqual(status(job(due, reported=NOW - timedelta(minutes=120)), NOW), "late")
        self.assertEqual(status(job(due, reported=NOW - timedelta(minutes=121)), NOW), "stale")
        long = job(NOW - timedelta(hours=20), period=480, reported=NOW - timedelta(hours=23))
        self.assertEqual(status(long, NOW), "late")
        long["reported"] = iso(NOW - timedelta(hours=25))
        self.assertEqual(status(long, NOW), "stale")

    def test_a_future_run_is_never_stale(self):
        far = job(NOW + timedelta(days=9), period=None, reported=NOW - timedelta(days=5))
        self.assertEqual(status(far, NOW), "scheduled")

    def test_no_known_next_run(self):
        self.assertEqual(status(job(None), NOW), "unknown")
        self.assertEqual(status(job(None, period=None, reported=NOW - timedelta(hours=25)), NOW), "stale")

    def test_a_row_with_bad_times_is_stale_not_running(self):
        self.assertEqual(status({"name": "x", "next": "soon", "reported": "whenever"}, NOW), "stale")


class ViewTests(unittest.TestCase):
    def test_per_participant_with_last_run_and_newest_first_history(self):
        runs = [{"at": iso(NOW - timedelta(minutes=30 * i)), "result": "ok"} for i in range(12, 0, -1)]
        docs = {("schedules", "alpha-sched-a"): job(NOW + timedelta(minutes=9), runs=runs),
                ("schedules", "beta-sched-b"): job(None, **{"from": "beta", "name": "b"}),
                ("schedules", "alpha-sched-gone"): job(NOW, removed=iso(NOW)),
                ("schedules", "junk"): "not a doc",
                ("messages", "m"): {"name": "no"}}
        out = schedule_view(docs, NOW)
        self.assertEqual([p["id"] for p in out["participants"]], ["alpha", "beta"])
        a = out["participants"][0]["jobs"]
        self.assertEqual(len(a), 1)
        self.assertEqual((a[0]["id"], a[0]["name"], a[0]["status"]), ("alpha-sched-a", "check-in", "scheduled"))
        self.assertEqual(a[0]["last_run"], runs[-1])
        self.assertEqual(len(a[0]["runs"]), 10)
        self.assertEqual(a[0]["runs"][0], runs[-1])
        self.assertNotIn("running", json.dumps(out))


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        self.roster = load_roster(self.r.root / "participants.json")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def doc(self, jid="alpha-sched-check-in"):
        from crosstalk.reports import load_docs
        return load_docs(self.r.root, self.roster)[0].get(("schedules", jid))

    def set_(self, **kw):
        kw.setdefault("now", NOW)
        kw.setdefault("tz", UTC)
        return set_job(self.r.root, "alpha", "Check in", kw.pop("cron", "*/30 * * * *"), roster=self.roster, **kw)

    def test_the_page_lists_the_standard_job_names(self):
        page = (REPO / "index.html").read_text(encoding="utf-8")
        names = re.search(r"STANDARD_JOB_NAMES = \[(.*?)\]", page).group(1)
        self.assertEqual(re.findall(r'"([^"]+)"', names), [j[0] for j in STANDARD_JOBS])

    def test_standard_jobs_have_valid_crons_and_record_their_runs(self):
        for pid in ("alpha", "delta", "epsilon-kit"):
            for job in standard_jobs(pid):
                self.assertIsNotNone(next_run(job["cron"], NOW, UTC), job["cron"])
                self.assertIn("schedule ran --from %s --name %s" % (pid, job["name"]), job["prompt"])

    def test_projects_never_share_a_minute_within_a_job_window(self):
        peers = ["a", "b", "c", "d", "e", "f"]
        seen = [(j["cron"]) for p in peers for j in standard_jobs(p, peers)]
        self.assertEqual(len(seen), len(set(seen)))
        half_hour = [int(j["cron"].split(",")[0]) for p in peers for j in standard_jobs(p, peers)[:2]]
        self.assertEqual(len(half_hour), len(set(half_hour)))
        retro = [int(standard_jobs(p, peers)[3]["cron"].split()[0]) for p in peers]
        self.assertTrue(all(m < 60 for m in retro))

    def test_standard_text_skips_reported_jobs_and_fits_the_harness(self):
        names = [j[0] for j in STANDARD_JOBS]
        self.assertIn("reports all", standard_text("alpha", "claude-code", names))
        hermes = standard_text("alpha", "hermes", names[1:])
        self.assertIn("hermes cron create --name check-in", hermes)
        self.assertNotIn("--name usage-guard", hermes)
        self.assertIn("CronCreate", standard_text("alpha", "claude-code"))

    def test_about_survives_a_rerun_and_a_set_without_it(self):
        self.set_(about="  Reads the inbox  ")
        mark_ran(self.r.root, "alpha", "Check in", "ok", now=NOW, tz=UTC, roster=self.roster)
        self.set_()
        from crosstalk.schedules import _current
        self.assertEqual(_current(self.r.root, self.roster, job_id("alpha", "Check in"))["about"], "Reads the inbox")

    def test_job_ids_are_owned_slugs(self):
        self.assertEqual(job_id("alpha", "Check in!"), "alpha-sched-check-in")
        with self.assertRaises(ValueError):
            job_id("alpha", "!!!")

    def test_set_computes_next_and_period(self):
        self.set_()
        d = self.doc()
        self.assertEqual((d["next"], d["periodMin"], d["from"], d["name"], d["reported"], d["runs"]),
                         ("2026-11-08T12:30:00Z", 30, "alpha", "Check in", iso(NOW), []))

    def test_an_unsupported_cron_needs_next(self):
        with self.assertRaises(ValueError):
            self.set_(cron="@daily")
        self.assertIsNone(self.doc())
        self.set_(cron="@daily", next_="2026-11-09T00:00:00Z", every_min=1440)
        self.assertEqual((self.doc()["next"], self.doc()["periodMin"]), ("2026-11-09T00:00:00Z", 1440))

    def test_a_bad_next_is_refused(self):
        with self.assertRaises(ValueError):
            self.set_(next_="tomorrow")

    def test_ran_updates_next_and_keeps_ten(self):
        self.set_()
        for i in range(12):
            mark_ran(self.r.root, "alpha", "Check in", "ok" if i else "failed", note="n%d" % i,
                     now=NOW + timedelta(minutes=30 * i), tz=UTC, roster=self.roster)
        d = self.doc()
        self.assertEqual(len(d["runs"]), 10)
        self.assertEqual(d["runs"][-1], {"at": "2026-11-08T17:30:00Z", "result": "ok", "note": "n11"})
        self.assertEqual(d["next"], "2026-11-08T18:00:00Z")
        self.assertEqual(d["reported"], "2026-11-08T17:30:00Z")

    def test_ran_without_a_computable_cron_uses_every_min_or_clears(self):
        self.set_(cron="@daily", next_="2026-11-09T00:00:00Z", every_min=60)
        mark_ran(self.r.root, "alpha", "Check in", "ok", now=NOW, tz=UTC, roster=self.roster)
        self.assertEqual(self.doc()["next"], "2026-11-08T13:00:00Z")
        self.set_(cron="@daily", next_="2026-11-09T00:00:00Z")
        mark_ran(self.r.root, "alpha", "Check in", "ok", now=NOW, tz=UTC, roster=self.roster)
        self.assertIsNone(self.doc()["next"])

    def test_ran_refuses_a_bad_result_and_an_unknown_job(self):
        self.set_()
        with self.assertRaises(ValueError):
            mark_ran(self.r.root, "alpha", "Check in", "maybe", now=NOW, tz=UTC, roster=self.roster)
        with self.assertRaises(ValueError):
            mark_ran(self.r.root, "alpha", "other", "ok", now=NOW, tz=UTC, roster=self.roster)

    def test_remove_hides_and_set_again_keeps_history(self):
        self.set_()
        mark_ran(self.r.root, "alpha", "Check in", "ok", now=NOW, tz=UTC, roster=self.roster)
        remove_job(self.r.root, "alpha", "Check in", now=NOW, roster=self.roster)
        self.assertEqual(self.doc()["removed"], iso(NOW))
        with self.assertRaises(ValueError):
            mark_ran(self.r.root, "alpha", "Check in", "ok", now=NOW, tz=UTC, roster=self.roster)
        with self.assertRaises(ValueError):
            remove_job(self.r.root, "alpha", "Check in", now=NOW, roster=self.roster)
        self.set_()
        self.assertNotIn("removed", self.doc())
        self.assertEqual(len(self.doc()["runs"]), 1)


class ValidatorTests(unittest.TestCase):
    good = {"from": "alpha", "name": "n", "cron": "* * * * *", "next": None, "reported": "2026-11-08T12:00:00Z", "runs": []}

    def problems(self, op="set", did="alpha-sched-n", frm="alpha", **over):
        data = dict(self.good)
        data.update(over)
        return schedule_problems("schedules", op, did, frm, data, now=NOW.timestamp())

    def test_good_row_and_other_collections_pass(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(schedule_problems("messages", "set", "x", "alpha", {}), [])

    def test_id_must_be_the_writers_schedule_id(self):
        self.assertTrue(self.problems(did="beta-sched-n"))
        self.assertTrue(self.problems(did="alpha-n"))
        self.assertEqual(self.problems(did="beta-sched-n", frm="owner", **{"from": "owner"}), [])

    def test_shape(self):
        for over in ({"name": ""}, {"name": "a\nb"}, {"name": "x" * 81}, {"cron": ""}, {"from": "beta"},
                     {"next": "soon"}, {"next": 5}, {"reported": "2026-11-08 12:00"}, {"removed": "x"},
                     {"periodMin": 0}, {"periodMin": "30"}, {"runs": "x"}, {"runs": [{"at": "x", "result": "ok"}]},
                     {"runs": [{"at": "2026-11-08T12:00:00Z", "result": "maybe"}]},
                     {"runs": [{"at": "2026-11-08T12:00:00Z", "result": "ok", "note": "n" * 201}]},
                     {"runs": [{"at": "2026-11-08T12:00:00Z", "result": "ok"}] * 11},
                     {"reported": "2026-11-08T12:03:00Z"}):
            self.assertTrue(self.problems(**over), over)

    def test_update_checks_only_what_it_carries(self):
        self.assertEqual(schedule_problems("schedules", "update", "alpha-sched-n", "alpha",
                                           {"removed": "2026-11-08T12:00:00Z"}, now=NOW.timestamp()), [])

    def test_append_refuses_a_bad_row_and_check_accepts_a_good_one(self):
        with TempRepo() as r:
            r.write_roster()
            roster = load_roster(r.root / "participants.json")
            with self.assertRaises(ValueError):
                append_line(r.root, "alpha", "set", "schedules", "beta-sched-n", dict(self.good), roster=roster)
            data = dict(self.good, reported=iso(datetime.now(UTC)))
            append_line(r.root, "alpha", "set", "schedules", "alpha-sched-n", data, roster=roster)


class CliAndPageTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def cli(self, *args):
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
        return subprocess.run([sys.executable, str(self.r.root / "scripts" / "relay.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, cwd="/")

    def test_cli_round_trip(self):
        p = self.cli("schedule", "set", "--from", "alpha", "--name", "check in", "--cron", "*/30 * * * *")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "alpha-sched-check-in"), p.stderr)
        p = self.cli("schedule", "ran", "--from", "alpha", "--name", "check in", "--result", "ok", "--note", "fine")
        self.assertEqual(p.returncode, 0, p.stderr)
        job = json.loads(self.cli("schedule", "list").stdout)["participants"][0]["jobs"][0]
        self.assertEqual((job["status"], job["last_run"]["result"], job["last_run"]["note"]), ("scheduled", "ok", "fine"))
        self.assertTrue(job["next"].endswith(":00Z"))
        self.assertEqual(self.cli("check").returncode, 0)
        p = self.cli("schedule", "remove", "--from", "alpha", "--name", "check in")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(self.cli("schedule", "list").stdout)["participants"], [])

    def test_cli_errors(self):
        p = self.cli("schedule", "set", "--from", "alpha", "--name", "x", "--cron", "@daily")
        self.assertEqual(p.returncode, 1)
        self.assertIn("--next", p.stderr)
        p = self.cli("schedule", "set", "--from", "nobody", "--name", "x", "--cron", "* * * * *")
        self.assertEqual(p.returncode, 1)
        p = self.cli("schedule", "ran", "--from", "alpha", "--name", "x", "--result", "maybe")
        self.assertEqual(p.returncode, 2)

    def test_snapshot_and_page_carry_the_collection(self):
        self.assertEqual(self.cli("schedule", "set", "--from", "beta", "--name", "n", "--cron", "0 3 * * *").returncode, 0)
        with RunningServer(self.r.root) as srv:
            with urllib.request.urlopen(srv.url + "/api/snapshot") as resp:
                snap = json.loads(resp.read().decode("utf-8"))
            with urllib.request.urlopen(srv.url + "/api/schedules") as resp:
                one = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(snap["collections"]["schedules"]["beta-sched-n"]["cron"], "0 3 * * *")
        self.assertIn("beta-sched-n", one["docs"])

    def test_page_subscribes_and_keeps_the_same_thresholds(self):
        html = (REPO / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'var COLS = \[[^\]]*"schedules"')
        self.assertIn('["schedules", "Schedules", "schedules"]', html)
        from crosstalk import schedules as s
        self.assertIn("SCHED_GRACE_MIN = %d, SCHED_STALE_MIN = %d, SCHED_STALE_NO_PERIOD_MIN = %d"
                      % (s.GRACE_MIN, s.STALE_MIN, s.STALE_NO_PERIOD_MIN), html)


if __name__ == "__main__":
    unittest.main()
