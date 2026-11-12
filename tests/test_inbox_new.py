"""relay.py inbox --new, --ack and --ack-to: only what came after the last ack."""
import itertools
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SPEC_ROSTER, TempRepo, line

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
STATE = "inbox-state.json"

from crosstalk import participants as pmod  # noqa: E402


def t(minute, second=0):
    return "2026-11-11T10:%02d:%02dZ" % (minute, second)


def req(seq, frm, id_, at, to=("alpha",), **extra):
    data = {"from": frm, "kind": "request", "summary": "help " + id_, "to": list(to)}
    data.update(extra)
    return line(seq, frm, id_=id_, data=data, t=at)


def owner_msg(seq, id_, at, **extra):
    data = {"from": "owner", "kind": "owner", "summary": "note " + id_, "to": ["alpha"]}
    data.update(extra)
    return line(seq, "owner", id_=id_, data=data, t=at)


def agreement(seq, id_, at, **extra):
    data = {"builder": "beta", "adopter": "alpha", "status": "ready-to-adopt", "t": at}
    data.update(extra)
    return line(seq, "beta", collection="agreements", id_=id_, data=data, t=at)


def distinct_colors(field, panel, n):
    """n colors that pass the roster's contrast and delta E rules next to SPEC_ROSTER's."""
    taken = [p[field] for p in SPEC_ROSTER["participants"]]
    out = []
    for rgb in itertools.product(range(0, 256, 0x33), repeat=3):
        c = "#%02x%02x%02x" % rgb
        if pmod.contrast(c, panel) >= pmod.MIN_CONTRAST and all(
                pmod.delta_e(c, o) >= pmod.MIN_DELTA_E for o in taken + out):
            out.append(c)
            if len(out) == n:
                return out
    raise AssertionError("no %d colors for %s" % (n, field))


class InboxNewTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def inbox(self, *args):
        p = self.run_cli("inbox", "alpha", *args)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def state(self):
        path = self.r.root / STATE
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def ids(self, out, key="requests"):
        return sorted((i.get("request") or i.get("message") or i)["id"] for i in out[key])

    def test_first_new_returns_everything_and_a_next(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1)), req(2, "beta", "beta-r2", t(2))])
        out = self.inbox("--new")
        self.assertEqual(self.ids(out), ["beta-r1", "beta-r2"])
        self.assertIsNone(out["place"])
        self.assertEqual(out["next"], {"t": t(2), "seen": ["beta-r2"]})
        self.assertEqual(out["still_open"], [])
        self.assertEqual(self.state(), {})

    def test_after_ack_a_quiet_inbox_is_empty_and_next_still_has_a_value(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1))])
        self.inbox("--new", "--ack")
        out = self.inbox("--new")
        self.assertEqual(out["requests"], [])
        self.assertEqual(out["place"], {"t": t(1), "seen": ["beta-r1"]})
        self.assertEqual(out["next"], {"t": t(1), "seen": ["beta-r1"]})

    def test_row_in_seen_is_not_new(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r5", t(1))])
        self.inbox("--ack")
        self.assertEqual(self.inbox("--new")["requests"], [])

    def test_row_of_the_same_second_not_in_seen_is_new(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r5", t(1))])
        self.inbox("--ack")
        self.r.write_log("beta", [req(1, "beta", "beta-r5", t(1)), req(2, "beta", "beta-r1", t(1))])
        out = self.inbox("--new")
        self.assertEqual(self.ids(out), ["beta-r1"])
        self.assertEqual(out["next"], {"t": t(1), "seen": ["beta-r1", "beta-r5"]})

    def test_row_without_own_t_uses_its_line_time(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1))])
        self.inbox("--ack")
        rows = [req(1, "beta", "beta-r1", t(1)), req(2, "beta", "beta-r2", t(5))]
        self.assertNotIn("t", rows[1]["data"])
        self.r.write_log("beta", rows)
        out = self.inbox("--new")
        self.assertEqual(self.ids(out), ["beta-r2"])

    def test_row_with_an_old_own_t_written_after_the_place_is_new(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(5))])
        self.inbox("--ack")
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(5)),
                                  req(2, "beta", "beta-r2", t(6), t=t(0))])
        self.assertEqual(self.ids(self.inbox("--new")), ["beta-r2"])

    def test_agreement_updated_after_the_place_is_new(self):
        rows = [agreement(1, "a-one", t(1), status="agreed"), req(2, "beta", "beta-r1", t(2))]
        self.r.write_log("beta", rows)
        self.inbox("--ack")
        rows.append(line(3, "beta", collection="agreements", id_="a-one", op="update",
                         data={"status": "ready-to-adopt"}, t=t(3)))
        self.r.write_log("beta", rows)
        out = self.inbox("--new")
        self.assertEqual([a["id"] for a in out["agreements_to_adopt"]], ["a-one"])
        self.assertEqual(out["agreements_open"], 1)

    def test_agreements_open_counts_all(self):
        self.r.write_log("beta", [agreement(1, "a-one", t(1)), agreement(2, "a-two", t(2))])
        self.inbox("--ack")
        out = self.inbox("--new")
        self.assertEqual(out["agreements_to_adopt"], [])
        self.assertEqual(out["agreements_open"], 2)

    def test_still_open_has_no_body(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1), body="a long body")])
        self.inbox("--ack")
        out = self.inbox("--new")
        self.assertEqual(out["requests"], [])
        self.assertEqual(out["still_open"],
                         [{"id": "beta-r1", "from": "beta", "t": t(1), "summary": "help beta-r1"}])
        self.assertEqual(out["still_open_more"], 0)

    def test_still_open_leaves_out_needs_answer_false(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1))])
        self.r.write_log("owner", [owner_msg(1, "owner-m1", t(2)),
                                   owner_msg(2, "owner-m2", t(3), body="FYI. No answer needed.")])
        full = self.inbox()
        self.assertEqual(self.ids(full, "owner_messages"), ["owner-m1", "owner-m2"])
        self.inbox("--ack")
        self.assertEqual([s["id"] for s in self.inbox("--new")["still_open"]],
                         ["owner-m1", "beta-r1"])

    def test_still_open_cap_says_how_many_it_left_out(self):
        self.r.write_log("beta", [req(i, "beta", "beta-r%02d" % i, t(i)) for i in range(1, 26)])
        self.inbox("--ack")
        out = self.inbox("--new")
        self.assertEqual(len(out["still_open"]), 20)
        self.assertEqual(out["still_open"][0]["id"], "beta-r25")
        self.assertEqual(out["still_open_more"], 5)

    def test_ack_moves_to_next_of_the_same_run(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1)), req(2, "beta", "beta-r2", t(1))])
        out = self.inbox("--new", "--ack")
        self.assertEqual(self.state(), {"alpha": out["next"]})

    def test_row_after_the_read_stays_new(self):
        rows = [req(1, "beta", "beta-r5", t(1))]
        self.r.write_log("beta", rows)
        nxt = self.inbox("--new")["next"]
        rows.append(req(2, "beta", "beta-r1", t(1)))
        self.r.write_log("beta", rows)
        p = self.run_cli("inbox", "alpha", "--ack-to", json.dumps(nxt))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), nxt)
        self.assertEqual(self.ids(self.inbox("--new")), ["beta-r1"])

    def test_ack_without_new_prints_the_full_output_and_moves(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1))])
        full = self.inbox()
        self.assertEqual(self.inbox("--ack"), full)
        self.assertEqual(self.state(), {"alpha": {"t": t(1), "seen": ["beta-r1"]}})

    def test_broken_pipe_does_not_move_the_place(self):
        long = "x" * 200
        self.r.write_log("beta", [req(i, "beta", "beta-r%03d" % i, t(i // 60, i % 60), body=long)
                                  for i in range(1, 400)])
        p = subprocess.Popen([sys.executable, self.script, "inbox", "alpha", "--new", "--ack"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/")
        p.stdout.close()
        _out, errs = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 1)
        self.assertNotIn(b"Traceback", errs)
        self.assertNotIn("alpha", self.state())

    def test_ack_to_moves_and_refuses_a_place_behind(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1)), req(2, "beta", "beta-r2", t(2))])
        place = {"t": t(2), "seen": ["beta-r2"]}
        p = self.run_cli("inbox", "alpha", "--ack-to", json.dumps(place))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.state(), {"alpha": place})
        p = self.run_cli("inbox", "alpha", "--ack-to", json.dumps({"t": t(1), "seen": ["beta-r1"]}))
        self.assertEqual(p.returncode, 1)
        self.assertIn("behind", p.stderr)
        self.assertEqual(self.state(), {"alpha": place})

    def test_ack_to_refuses_a_place_ahead_of_the_log(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1))])
        p = self.run_cli("inbox", "alpha", "--ack-to", json.dumps({"t": t(2), "seen": []}))
        self.assertEqual(p.returncode, 1)
        self.assertIn("ahead", p.stderr)
        self.assertEqual(self.state(), {})

    def test_ack_to_refuses_bad_json(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1))])
        for bad in ("{not json", "[]", '{"t": "2026-11-1T10:01:00Z", "seen": []}', '{"t": "%s"}' % t(1)):
            p = self.run_cli("inbox", "alpha", "--ack-to", bad)
            self.assertEqual(p.returncode, 1, bad)
            self.assertIn("not a place", p.stderr)
        p = self.run_cli("inbox", "alpha", "--new", "--ack-to", json.dumps({"t": t(1), "seen": []}))
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.state(), {})

    def test_broken_state_file_means_everything_is_new(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1))])
        (self.r.root / STATE).write_text("{broken", encoding="utf-8")
        out = self.inbox("--new")
        self.assertEqual(self.ids(out), ["beta-r1"])
        self.assertIsNone(out["place"])

    def test_new_brief_marks_needs_answer_and_prints_counts(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1)), agreement(2, "a-one", t(2))])
        self.inbox("--ack")
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1)), agreement(2, "a-one", t(2)),
                                  req(3, "beta", "beta-r2", t(3))])
        self.r.write_log("owner", [owner_msg(1, "owner-m1", t(4), body="FYI. No answer needed.")])
        p = self.run_cli("inbox", "alpha", "--new", "--brief")
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 3, p.stdout)
        self.assertTrue(lines[0].startswith(" ") and "owner-m1" in lines[0], lines[0])
        self.assertTrue(lines[1].startswith("!") and "beta-r2" in lines[1], lines[1])
        self.assertEqual(lines[2], "still open 1, agreements open 1, stale locks 0")
        plain = self.run_cli("inbox", "alpha", "--brief").stdout.splitlines()
        self.assertEqual(plain[-1], "adopt 1, stale locks 0")

    def test_quiet_check_in_is_small(self):
        roster = json.loads(json.dumps(SPEC_ROSTER))
        colors = zip(distinct_colors("color", pmod.PANEL_LIGHT, 26),
                     distinct_colors("colorDark", pmod.PANEL_DARK, 26))
        for i, (light, dark) in enumerate(colors):
            roster["participants"].append({"id": "p%02d" % i, "name": "P%02d" % i, "kind": "project",
                                           "repo": "owner/p%02d" % i, "trust": "approved",
                                           "color": light, "colorDark": dark})
        self.assertEqual(len(roster["participants"]), 30)
        self.r.write_roster(roster)
        rows = [agreement(i, "a-%03d" % i, t(i // 60, i % 60)) for i in range(1, 251)]
        rows += [req(250 + i, "beta", "beta-r%02d" % i, t(5, i), body="b" * 500) for i in range(1, 26)]
        self.r.write_log("beta", rows)
        self.inbox("--ack")
        p = self.run_cli("inbox", "alpha", "--new", "--ack")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["agreements_open"], 250)
        self.assertEqual(len(out["still_open"]), 20)
        self.assertLess(len(p.stdout), 8000)

    def test_plain_inbox_is_unchanged(self):
        self.r.write_log("beta", [req(1, "beta", "beta-r1", t(1)), agreement(2, "a-one", t(2))])
        before = self.run_cli("inbox", "alpha").stdout
        self.inbox("--ack")
        self.assertTrue(self.state())
        self.assertEqual(self.run_cli("inbox", "alpha").stdout, before)
        self.assertNotIn("still_open", json.loads(before))


if __name__ == "__main__":
    unittest.main()
