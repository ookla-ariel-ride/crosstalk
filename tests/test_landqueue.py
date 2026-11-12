"""The build host land queue: landqueue.land_queue, relay.py queue, and GET /api/queue."""
import calendar
import json
import shutil
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path

from helpers import RunningServer, TempRepo, line

from crosstalk.landqueue import branches_of, is_withdrawal, land_queue, land_word

REPO = Path(__file__).resolve().parent.parent
PROJECTS = ("gamma", "beta", "alpha", "epsilon-kit")


def docs_of(*msgs):
    """msgs: (minute, sender, summary[, extra]) in any order; ids are unique per row."""
    docs = {}
    for i, m in enumerate(msgs):
        minute, sender, summary = m[:3]
        doc = {"from": sender, "kind": "sync", "summary": summary,
               "t": "2026-11-08T10:%02d:00Z" % minute}
        if len(m) > 3:
            doc.update(m[3])
        docs[("messages", "%s-%02d-%d" % (sender, minute, i))] = doc
    return docs


def queue(*msgs):
    return land_queue(docs_of(*msgs), projects=PROJECTS, coordinator="relay-bot")


class ParseTests(unittest.TestCase):
    def test_fixed_words_only_at_the_start_and_in_capitals(self):
        self.assertEqual(land_word("TURN ASK gamma a-1108"), "TURN ASK")
        self.assertEqual(land_word("  LAND END beta x-1108"), "LAND END")
        self.assertEqual(land_word("LANE START alpha"), "LANE START")
        self.assertIsNone(land_word("ALP land start 01:42: maple-cities-1108"))
        self.assertIsNone(land_word("land end: juniper-1108"))
        self.assertIsNone(land_word("Run B ended; LAND END later"))
        self.assertIsNone(land_word("LAND ENDED"))
        self.assertEqual(land_word("land --queue x-1108: landed; main is now abc; turn released"), "LAND END")
        self.assertEqual(land_word("land --queue x-1108: stopped at y; main is abc; turn released"), "LAND END")
        self.assertIsNone(land_word("land --queue x-1108 is still running"))
        self.assertIsNone(land_word(None))

    def test_branches_come_from_the_head_and_skip_projects_shas_and_times(self):
        s = "TURN ASK alpha dockside-ghost-1108 (44e63936), maple-bonus-1108: suite passed in lane-3"
        self.assertEqual(branches_of(s, "TURN ASK", PROJECTS), ["dockside-ghost-1108", "maple-bonus-1108"])
        self.assertEqual(branches_of("LAND START epsilon-kit 05:41 hooks-fix-1108.", "LAND START", PROJECTS),
                         ["hooks-fix-1108"])

    def test_withdrawal_form(self):
        self.assertTrue(is_withdrawal("TURN ASK WITHDRAWN gamma"))
        self.assertTrue(is_withdrawal("TURN ASK withdrawn: no longer needed"))
        self.assertFalse(is_withdrawal("TURN ASK gamma withdrawn-fix-1108"))
        self.assertFalse(is_withdrawal("TURN ASK withdrawnish"))


class QueueTests(unittest.TestCase):
    def test_empty(self):
        q = land_queue({})
        self.assertEqual((q["holders"], q["waiting"], q["next"], q["slots_free"], q["may_start"]),
                         ([], [], None, 2, False))

    def test_waiting_asks_keep_post_order(self):
        q = queue((12, "gamma", "TURN ASK gamma gam-a-1108"),
                  (5, "alpha", "TURN ASK alpha alp-a-1108"),
                  (9, "beta", "TURN ASK beta bet-a-1108"))
        self.assertEqual([w["project"] for w in q["waiting"]],
                         ["alpha", "beta", "gamma"])
        self.assertEqual(q["next"], "alpha")
        self.assertTrue(q["may_start"])
        self.assertEqual(q["waiting"][0]["branches"], ["alp-a-1108"])

    def test_a_repeat_ask_updates_branches_and_keeps_its_place(self):
        q = queue((1, "alpha", "TURN ASK alpha a-1108"),
                  (2, "gamma", "TURN ASK gamma v-1108"),
                  (3, "alpha", "TURN ASK alpha a-1108, b-1108"))
        self.assertEqual([w["project"] for w in q["waiting"]], ["alpha", "gamma"])
        self.assertEqual(q["waiting"][0]["branches"], ["a-1108", "b-1108"])
        self.assertEqual(q["waiting"][0]["t"], "2026-11-08T10:01:00Z")

    def test_a_second_ask_adds_its_branches(self):
        q = queue((1, "gamma", "TURN ASK gamma tpl-1110 2601296a"),
                  (2, "beta", "TURN ASK beta b-1110"),
                  (3, "gamma", "TURN ASK gamma: count-1109 87d4571e (1 row verified)"),
                  (4, "gamma", "TURN ASK gamma tpl-1110, count-1109"))
        self.assertEqual([w["project"] for w in q["waiting"]], ["gamma", "beta"])
        self.assertEqual(q["waiting"][0]["branches"], ["tpl-1110", "count-1109"])
        self.assertEqual(q["waiting"][0]["updated"], "gamma-04-3")

    def test_a_second_ask_that_cites_the_first_adds_the_branch_after_the_colon(self):
        docs = docs_of((1, "alpha", "TURN ASK alpha next17-1109 e5d4ece9: one buildbox land run"))
        (ask_id,) = [did for _, did in docs]
        docs[("messages", "alp-2")] = {
            "from": "alpha", "kind": "reply", "replyTo": ask_id, "t": "2026-11-08T10:02:00Z",
            "summary": "TURN ASK %s now covers two branches in one land run: next17-1109 e5d4ece9, "
                       "then palette-1110 5d293e65" % ask_id}
        q = land_queue(docs, projects=PROJECTS, coordinator="relay-bot")
        self.assertEqual(q["waiting"][0]["branches"], ["next17-1109", "palette-1110"])

    def test_a_withdrawal_drops_the_added_branches_too(self):
        q = queue((1, "gamma", "TURN ASK gamma tpl-1110"),
                  (2, "gamma", "TURN ASK gamma count-1109"),
                  (3, "gamma", "TURN ASK WITHDRAWN gamma"),
                  (4, "gamma", "TURN ASK gamma new-1110"))
        self.assertEqual([w["branches"] for w in q["waiting"]], [["new-1110"]])

    def test_land_start_makes_a_holder_and_land_end_hands_to_the_next(self):
        msgs = [(1, "gamma", "TURN ASK gamma v-1108"),
                (2, "beta", "TURN ASK beta b-1108"),
                (3, "alpha", "TURN ASK alpha a-1108"),
                (4, "gamma", "LAND START gamma v-1108"),
                (5, "beta", "LAND START beta b-1108: merges clean")]
        q = queue(*msgs)
        self.assertEqual([h["project"] for h in q["holders"]], ["gamma", "beta"])
        self.assertEqual(q["holders"][0]["since"], "2026-11-08T10:04:00Z")
        self.assertEqual(q["holders"][1]["branches"], ["b-1108"])
        self.assertTrue(q["holders"][0]["ask"].startswith("gamma-01"))
        self.assertEqual(q["next"], "alpha")
        self.assertEqual(q["slots_free"], 0)
        self.assertFalse(q["may_start"])
        q = queue(*msgs + [(6, "gamma", "LAND END gamma v-1108: landed, main abc1234")])
        self.assertEqual([h["project"] for h in q["holders"]], ["beta"])
        self.assertEqual(q["slots_free"], 1)
        self.assertEqual(q["next"], "alpha")
        self.assertTrue(q["may_start"])

    def test_holder_branches_fall_back_to_the_ask(self):
        q = queue((1, "gamma", "TURN ASK gamma v-1108, w-1108"),
                  (2, "gamma", "LAND START gamma"))
        self.assertEqual(q["holders"][0]["branches"], ["v-1108", "w-1108"])
        self.assertEqual(q["waiting"], [])

    def test_two_holders_at_most_and_only_distinct_projects(self):
        q = queue((1, "gamma", "LAND START gamma v-1108"),
                  (2, "gamma", "LAND START gamma w-1108"),
                  (3, "beta", "LAND START beta b-1108"),
                  (4, "alpha", "LAND START alpha a-1108"))
        self.assertEqual([h["project"] for h in q["holders"]],
                         ["gamma", "beta", "alpha"])
        self.assertEqual(q["slots_free"], 0)
        self.assertEqual(len(q["notes"]), 2)
        self.assertIn("already holding", q["notes"][0])
        self.assertIn("2 holders already", q["notes"][1])

    def test_a_holding_project_with_an_open_ask_is_skipped_for_next(self):
        q = queue((1, "gamma", "LAND START gamma v-1108"),
                  (2, "gamma", "TURN ASK gamma w-1108"),
                  (3, "beta", "TURN ASK beta b-1108"))
        self.assertEqual([w["project"] for w in q["waiting"]], ["gamma", "beta"])
        self.assertTrue(q["waiting"][0]["holding"])
        self.assertFalse(q["waiting"][1]["holding"])
        self.assertEqual(q["next"], "beta")
        q = queue((1, "gamma", "LAND START gamma v-1108"),
                  (2, "gamma", "TURN ASK gamma w-1108"))
        self.assertIsNone(q["next"])
        self.assertFalse(q["may_start"])

    def test_withdrawal_by_the_asker_removes_the_ask(self):
        q = queue((1, "gamma", "TURN ASK gamma v-1108"),
                  (2, "beta", "TURN ASK beta b-1108"),
                  (3, "alpha", "TURN ASK WITHDRAWN gamma"),
                  (4, "gamma", "TURN ASK WITHDRAWN gamma: v-1108 needs a rebase"))
        self.assertEqual([w["project"] for w in q["waiting"]], ["beta"])
        self.assertEqual(q["next"], "beta")

    def test_land_end_without_holding_is_noted_and_ignored(self):
        q = queue((1, "beta", "TURN ASK beta b-1108"),
                  (2, "beta", "LAND END beta b-1108"))
        self.assertEqual([w["project"] for w in q["waiting"]], ["beta"])
        self.assertIn("not holding", q["notes"][0])

    def test_unrelated_messages_are_ignored(self):
        docs = docs_of((1, "gamma", "TURN ASK gamma v-1108"),
                       (2, "beta", "ALP land start 01:42: maple-cities-1108"),
                       (3, "beta", "Check-in: LAND START later today"),
                       (4, "alpha", "LANE START alpha a-1108"),
                       (5, "alpha", "LANE END alpha a-1108"))
        docs[("decisions", "d-1")] = {"from": "relay-bot", "summary": "LAND START x-1108"}
        docs[("messages", "no-sender")] = {"summary": "LAND START x-1108", "t": "2026-11-08T10:06:00Z"}
        q = land_queue(docs, projects=PROJECTS, coordinator="relay-bot")
        self.assertEqual(q["holders"], [])
        self.assertEqual([w["project"] for w in q["waiting"]], ["gamma"])
        self.assertEqual(q["notes"], [])


def msg(seq, frm, summary, minute):
    return line(seq, frm, id_="%s-%d" % (frm, seq), t="2026-11-08T10:%02d:00Z" % minute,
                data={"from": frm, "kind": "sync", "summary": summary})


def granted(minute, ask_id, project="gamma"):
    return (minute, "relay-bot", "GRANTED %s x-1110" % project,
            {"kind": "reply", "replyTo": ask_id})


class GrantAndLaneTests(unittest.TestCase):
    ASK = "gamma-01-0"  # docs_of id of the first message below

    def test_granted_ask_reserves_a_slot_and_leaves_next(self):
        q = queue((1, "gamma", "TURN ASK gamma x-1110"),
                  (2, "beta", "TURN ASK beta y-1110"),
                  granted(3, self.ASK))
        gam = q["waiting"][0]
        self.assertEqual(gam["granted"], "relay-bot-03-2")
        self.assertEqual(q["next"], "beta")
        self.assertEqual(q["slots_free"], 1)

    def test_land_start_after_grant_turns_reservation_into_holder(self):
        q = queue((1, "gamma", "TURN ASK gamma x-1110"), granted(3, self.ASK),
                  (4, "gamma", "LAND START gamma x-1110"))
        self.assertEqual([h["project"] for h in q["holders"]], ["gamma"])
        self.assertEqual((q["waiting"], q["slots_free"]), ([], 1))

    def test_grant_lapses_after_ttl_with_now(self):
        msgs = ((1, "gamma", "TURN ASK gamma x-1110"), granted(3, self.ASK))
        t3 = calendar.timegm((2026, 11, 8, 10, 3, 0))
        fresh = land_queue(docs_of(*msgs), projects=PROJECTS, coordinator="relay-bot", now=t3 + 60)
        late = land_queue(docs_of(*msgs), projects=PROJECTS, coordinator="relay-bot", now=t3 + 31 * 60)
        self.assertEqual(fresh["slots_free"], 1)
        self.assertEqual(late["slots_free"], 2)
        self.assertTrue(late["waiting"][0]["grant_lapsed"])
        self.assertIsNone(late["next"])  # a lapsed grant is not granted again by itself

    def test_reask_clears_the_grant(self):
        q = queue((1, "gamma", "TURN ASK gamma x-1110"), granted(3, self.ASK),
                  (5, "gamma", "TURN ASK gamma x-1110 again"))
        self.assertNotIn("granted", q["waiting"][0])
        self.assertEqual(q["next"], "gamma")

    def test_grant_withdrawn_frees_the_slot_and_the_ask_keeps_its_place(self):
        grant = "relay-bot-03-2"  # docs_of id of granted(3, ...) below
        void = {"kind": "reply", "replyTo": grant}
        q = queue((1, "gamma", "TURN ASK gamma x-1110"), (2, "beta", "TURN ASK beta y-1110"),
                  granted(3, self.ASK), (4, "relay-bot", "GRANT WITHDRAWN gamma x-1110", void))
        self.assertEqual([(w["project"], "granted" in w) for w in q["waiting"]],
                         [("gamma", False), ("beta", False)])
        self.assertEqual((q["next"], q["slots_free"], q["holders"]), ("gamma", 2, []))
        # only the coordinator's counts
        q = queue((1, "gamma", "TURN ASK gamma x-1110"), (2, "beta", "TURN ASK beta y-1110"),
                  granted(3, self.ASK), (4, "beta", "GRANT WITHDRAWN gamma x-1110", void))
        self.assertEqual(q["waiting"][0]["granted"], grant)

    def wound(self, step, *msgs, by="owner"):
        """The queue with a wind-down record of `by` at `step` (minute 30)."""
        docs = docs_of(*msgs)
        docs[("state", by + "-wind-down")] = {"from": by, "step": step, "started": None, "by": by,
                                              "reason": None, "t": "2026-11-08T10:30:00Z"}
        return land_queue(docs, projects=PROJECTS, coordinator="relay-bot")

    def test_a_hand_posted_grant_without_a_start_is_void_during_step_1(self):
        msgs = ((1, "gamma", "TURN ASK gamma x-1110"), (2, "beta", "TURN ASK beta y-1110"),
                granted(3, self.ASK))
        for step in (1, 2):
            q = self.wound(step, *msgs)
            gam = q["waiting"][0]
            self.assertEqual((gam["id"], gam["grant_void"]), (self.ASK, "wind-down step %d" % step))
            self.assertEqual((q["holders"], q["slots_free"]), ([], 2))
        # the coordinator's own record counts too; another writer's does not
        self.assertIn("grant_void", self.wound(1, *msgs, by="relay-bot")["waiting"][0])
        self.assertNotIn("grant_void", self.wound(1, *msgs, by="beta")["waiting"][0])
        # (a): at step 0 the grant that did not lapse is in force again
        q = self.wound(0, *msgs)
        self.assertNotIn("grant_void", q["waiting"][0])
        self.assertEqual((q["waiting"][0]["granted"], q["slots_free"], q["next"]),
                         ("relay-bot-03-2", 1, "beta"))

    def test_a_holder_with_land_start_keeps_its_slot_through_steps_1_and_2(self):
        msgs = ((1, "gamma", "TURN ASK gamma x-1110"), granted(3, self.ASK),
                (4, "gamma", "LAND START gamma x-1110"))
        for step in (1, 2):
            q = self.wound(step, *msgs)
            self.assertEqual(([h["project"] for h in q["holders"]], q["slots_free"]), (["gamma"], 1))

    def test_a_branch_asked_after_the_grant_stays_waiting_at_the_start(self):
        # A project asks for a second branch as the server grants the first.
        msgs = [(1, "epsilon-kit", "TURN ASK epsilon-kit x-1110 2686753: one buildbox land run"),
                (2, "epsilon-kit", "TURN ASK epsilon-kit agent-1110 6cc3b53: one buildbox land run"),
                (3, "relay-bot", "GRANTED epsilon-kit x-1110", {"kind": "reply", "replyTo": "epsilon-kit-01-0"}),
                (4, "epsilon-kit", "LANE START epsilon-kit x-1110")]
        q = queue(*msgs)
        self.assertEqual(q["holders"][0]["branches"], ["x-1110"])
        left = q["waiting"][0]
        self.assertEqual((left["id"], left["branches"], left["holding"]),
                         ("epsilon-kit-01-0", ["agent-1110"], True))
        self.assertNotIn("granted", left)
        self.assertEqual((q["next"], q["slots_free"]), (None, 1))
        q = queue(*msgs, (5, "epsilon-kit", "LANE END epsilon-kit x-1110"))
        self.assertEqual((q["holders"], q["next"], q["may_start"]), ([], "epsilon-kit", True))

    def test_a_start_takes_every_branch_its_grant_names(self):
        q = queue((1, "gamma", "TURN ASK gamma x-1110"),
                  (2, "gamma", "TURN ASK gamma y-1109"),
                  (3, "relay-bot", "GRANTED gamma x-1110, y-1109", {"kind": "reply", "replyTo": "gamma-01-0"}),
                  (4, "gamma", "LAND START x-1110-land and y-1109-land"))
        self.assertEqual(q["waiting"], [])
        self.assertEqual(q["holders"][0]["branches"], ["x-1110-land", "y-1109-land"])

    def test_a_start_without_a_grant_takes_the_whole_ask(self):
        q = queue((1, "gamma", "TURN ASK gamma x-1110"),
                  (2, "gamma", "TURN ASK gamma y-1109"),
                  (3, "gamma", "LAND START gamma x-1110"))
        self.assertEqual(q["waiting"], [])

    def test_lane_start_with_ask_holds_and_lane_end_frees(self):
        q = queue((1, "epsilon-kit", "TURN ASK epsilon-kit t-1110"),
                  (2, "epsilon-kit", "LANE START t-1110: gate starts"))
        self.assertEqual([h["word"] for h in q["holders"]], ["LANE START"])
        self.assertEqual(q["waiting"], [])
        q = queue((1, "epsilon-kit", "TURN ASK epsilon-kit t-1110"),
                  (2, "epsilon-kit", "LANE START t-1110: gate starts"),
                  (3, "epsilon-kit", "LANE END t-1110: on main"))
        self.assertEqual((q["holders"], q["notes"]), ([], []))

    def test_lane_without_ask_is_a_test_lane(self):
        q = queue((2, "epsilon-kit", "LANE START lane-3"), (3, "epsilon-kit", "LANE END lane-3"))
        self.assertEqual((q["holders"], q["waiting"], q["notes"]), ([], [], []))

    def test_branch_after_the_colon_is_found_by_its_date(self):
        q = queue((1, "alpha",
                   "TURN ASK alpha: one buildbox land run of maple-probes-1109 "
                   "91f5b81b (Maple attacker feed fix)"))
        self.assertEqual(q["waiting"][0]["branches"], ["maple-probes-1109"])

    def test_a_dated_name_inside_a_longer_branch_name_is_no_branch(self):
        q = queue((1, "alpha", "TURN ASK alpha maple-1110, tool-fix-1110-min, readme-1110: all approved"))
        self.assertEqual(q["waiting"][0]["branches"], ["maple-1110", "tool-fix-1110-min", "readme-1110"])

    def test_turn_free_frees_the_holder(self):
        self.assertEqual(land_word("Turn free: v-1108 stopped"), "LAND END")
        self.assertEqual(land_word("TURN FREE gamma"), "LAND END")
        self.assertIsNone(land_word("turn freely"))
        for end in ("Turn free: v-1108 stopped", "TURN FREE gamma"):
            q = queue((1, "gamma", "LAND START gamma v-1108"), (2, "gamma", end))
            self.assertEqual((q["holders"], q["notes"]), ([], []))

    RESTART = [(1, "gamma", "LAND START gamma v-1108"),
               (2, "gamma", "TURN ASK gamma v-1108"),
               (3, "relay-bot", "GRANTED gamma v-1108", {"kind": "reply", "replyTo": "gamma-02-1"}),
               (4, "gamma", "LAND START gamma v-1108")]

    def test_a_new_land_start_for_the_same_branch_replaces_a_stopped_turn(self):
        t4 = calendar.timegm((2026, 11, 8, 10, 4, 0))
        q = land_queue(docs_of(*self.RESTART), projects=PROJECTS, coordinator="relay-bot",
                       now=t4 + 40 * 60)
        self.assertEqual([(h["id"], h["since"]) for h in q["holders"]],
                         [("gamma-04-3", "2026-11-08T10:04:00Z")])
        self.assertEqual(q["waiting"], [])
        self.assertEqual(q["notes"], ["gamma-04-3: LAND START from gamma replaces gamma-01-0, "
                                      "which had no LAND END"])

    def test_land_end_after_a_restart_leaves_no_holder_and_no_ask(self):
        q = queue(*self.RESTART, (5, "gamma", "LAND END gamma v-1108: landed"))
        self.assertEqual((q["holders"], q["waiting"], q["slots_free"]), ([], [], 2))

    def test_a_granted_ask_closes_when_its_branch_lands_without_a_start(self):
        q = queue((1, "gamma", "TURN ASK gamma v-1108"),
                  (2, "relay-bot", "GRANTED gamma v-1108", {"kind": "reply", "replyTo": "gamma-01-0"}),
                  (3, "gamma", "land --queue v-1108: landed; main is now abc1234; turn released",
                   {"kind": "reply", "replyTo": "beta-02-9"}))
        self.assertEqual((q["holders"], q["waiting"]), ([], []))
        self.assertEqual(q["notes"], ["gamma-03-2: LAND END from gamma closes granted ask gamma-01-0"])

    def test_land_end_without_start_keeps_the_ungranted_branches(self):
        q = queue((1, "gamma", "TURN ASK gamma x-1110 y-1110"),
                  (2, "beta", "TURN ASK beta z-1110"),
                  (3, "relay-bot", "GRANTED gamma x-1110", {"kind": "reply", "replyTo": "gamma-01-0"}),
                  (4, "gamma", "LAND END gamma x-1110: landed"))
        self.assertEqual(q["holders"], [])
        left = q["waiting"][0]
        self.assertEqual((left["id"], left["project"], left["branches"]), ("gamma-01-0", "gamma", ["y-1110"]))
        self.assertNotIn("granted", left)
        self.assertNotIn("granted_branches", left)
        self.assertEqual([w["project"] for w in q["waiting"]], ["gamma", "beta"])
        self.assertEqual((q["next"], q["slots_free"], q["may_start"]), ("gamma", 2, True))
        self.assertEqual(q["notes"], ["gamma-04-3: LAND END from gamma closes x-1110 of granted ask "
                                      "gamma-01-0; y-1110 stay waiting"])

    def test_land_end_without_start_of_every_branch_closes_the_ask(self):
        q = queue((1, "gamma", "TURN ASK gamma x-1110 y-1110"),
                  (2, "relay-bot", "GRANTED gamma x-1110", {"kind": "reply", "replyTo": "gamma-01-0"}),
                  (3, "gamma", "LAND END gamma x-1110 y-1110: landed"))
        self.assertEqual((q["holders"], q["waiting"], q["next"]), ([], [], None))
        self.assertEqual(q["notes"], ["gamma-03-2: LAND END from gamma closes granted ask gamma-01-0"])

    def test_coordinator_land_words_still_claim_nothing(self):
        q = queue((1, "relay-bot", "LAND START relay-bot z-1110"),
                  (2, "relay-bot", "GRANTED nobody", {"kind": "reply", "replyTo": "missing"}))
        self.assertEqual((q["holders"], q["waiting"]), ([], []))


class QueueCliAndHttpTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        self.r.write_log("alpha", [msg(1, "alpha", "TURN ASK alpha a-1108", 1),
                                   msg(2, "alpha", "LAND START alpha a-1108", 3)])
        self.r.write_log("beta", [msg(1, "beta", "TURN ASK beta b-1108", 2)])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
        return subprocess.run([sys.executable, str(self.r.root / "scripts" / "relay.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def test_cli_prints_the_queue_and_next(self):
        p = self.run_cli("queue")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual([h["project"] for h in out["holders"]], ["alpha"])
        self.assertEqual(out["next"], "beta")
        self.assertEqual(out["slots_free"], 1)
        p = self.run_cli("queue", "--next")
        self.assertEqual((p.returncode, p.stdout), (0, "beta\n"))

    def test_http_queue(self):
        with RunningServer(self.r.root) as srv:
            with urllib.request.urlopen(srv.url + "/api/queue") as resp:
                body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body["next"], "beta")
        self.assertEqual(body["holders"][0]["branches"], ["a-1108"])



class CitationsAndCoordinatorTests(unittest.TestCase):
    def test_a_cited_message_id_is_not_a_branch(self):
        self.assertEqual(
            branches_of("LAND START alp (answers relay-bot-20261108T222035231071Z) x-1108: go", "LAND START"),
            ["x-1108"])

    def test_a_coordinator_reply_that_opens_with_a_land_word_claims_nothing(self):
        docs = {("messages", "m1"): {"from": "relay-bot", "t": "2026-11-08T22:07:00Z",
                                     "summary": "TURN ASK x-1108: queued fifth"}}
        self.assertEqual(land_queue(docs, coordinator="relay-bot")["waiting"], [])


if __name__ == "__main__":
    unittest.main()
