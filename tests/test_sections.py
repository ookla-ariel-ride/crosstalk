import copy
import json
import unittest
import urllib.parse

from helpers import SPEC_ROSTER, RunningServer, TempRepo, line, plain_roster
from test_serve import request

from crosstalk import sections


def T(minute):
    return "2026-10-21T10:%02d:00Z" % minute


class OpenRuleTests(unittest.TestCase):
    """The open rule of each section, as the present page and commands read it."""

    def open_ids(self, name, docs, roster=None):
        rows, open_count = sections.rows(name, docs, roster or plain_roster("relay-bot", ["alpha", "beta"]))
        return {r["id"] for r in rows[:open_count]}, {r["id"] for r in rows}

    def test_agreements_open_until_landed_both_or_dropped(self):
        docs = {("agreements", "a1"): {"status": "agreed"}, ("agreements", "a2"): {"status": "landed-both"},
                ("agreements", "a3"): {"status": "dropped"}, ("agreements", "a4"): {"status": "held"}}
        self.assertEqual(self.open_ids("agreements", docs), ({"a1", "a4"}, {"a1", "a2", "a3", "a4"}))

    def test_locks_open_while_held(self):
        docs = {("locks", "l1"): {"path": "x"}, ("locks", "l2"): {"path": "y", "released": T(5)}}
        self.assertEqual(self.open_ids("locks", docs), ({"l1"}, {"l1", "l2"}))

    def test_incidents_open_until_ended(self):
        docs = {("incidents", "i1"): {"start": T(1), "end": None}, ("incidents", "i2"): {"start": T(2), "end": T(3)}}
        self.assertEqual(self.open_ids("incidents", docs), ({"i1"}, {"i1", "i2"}))

    def test_schedules_open_until_removed(self):
        docs = {("schedules", "s1"): {"name": "a"}, ("schedules", "s2"): {"name": "b", "removed": T(4)}}
        self.assertEqual(self.open_ids("schedules", docs), ({"s1"}, {"s1", "s2"}))

    def test_reviews_are_the_coordinators_review_rows_and_open_overrides(self):
        base = {"from": "relay-bot", "source": "codex-review", "project": "alpha"}
        docs = {
            ("messages", "r1"): dict(base, kind="review", verdict="ok"),
            ("messages", "o1"): dict(base, kind="disagreement", status="awaiting"),
            ("messages", "o2"): dict(base, kind="disagreement", status="objected"),
            ("messages", "o3"): dict(base, kind="disagreement", status="agreed"),
            ("messages", "o4"): dict(base, kind="disagreement", status="objected", resolution="kept"),
            ("messages", "x1"): dict(base, kind="review", source="other"),
            ("messages", "x2"): dict(base, kind="reply"),
            ("messages", "x3"): dict(base, kind="review", **{"from": "alpha"}),
        }
        self.assertEqual(self.open_ids("reviews", docs), ({"o1", "o2"}, {"r1", "o1", "o2", "o3", "o4"}))

    def test_reviews_are_empty_with_no_coordinator(self):
        docs = {("messages", "r1"): {"from": "", "source": "codex-review", "project": "alpha", "kind": "review"}}
        self.assertEqual(self.open_ids("reviews", docs, plain_roster("", ["alpha"])), (set(), set()))

    def test_sections_with_no_open_state(self):
        for name, coll in (("decisions", "decisions"), ("lessons", "lessons"), ("progress", "progress"),
                           ("adoption", "adoptions")):
            docs = {(coll, "d1"): {"summary": "x"}, ("messages", "m1"): {"summary": "x"}}
            self.assertEqual(self.open_ids(name, docs), (set(), {"d1"}), name)

    def test_unknown_section_is_a_key_error(self):
        with self.assertRaises(KeyError):
            sections.rows("messages", {}, plain_roster("", []))


class OrderTests(unittest.TestCase):
    def test_open_first_then_newest_change_then_id(self):
        docs = {("locks", "a"): {"t": T(1)}, ("locks", "b"): {"t": T(9), "released": T(9)},
                ("locks", "c"): {"t": T(5)}, ("locks", "d"): {"t": T(5)}}
        changed = {("locks", "a"): T(30)}
        rows, open_count = sections.rows("locks", docs, plain_roster("", []), changed=changed.get)
        self.assertEqual([r["id"] for r in rows], ["a", "d", "c", "b"])
        self.assertEqual(open_count, 3)

    def test_rows_are_copies_with_their_id(self):
        doc = {"summary": "s", "id": "stale"}
        rows, _ = sections.rows("lessons", {("lessons", "l1"): doc}, plain_roster("", []))
        self.assertEqual(rows, [{"summary": "s", "id": "l1"}])
        self.assertEqual(doc["id"], "stale")


def section_lines():
    """Six locks: two released. Each lock's last change is a later `update` for l1."""
    out = []
    for n in range(1, 7):
        data = {"path": "f%d" % n, "summary": "lock %d" % n, "body": "Body (a+b)*" if n == 3 else "plain"}
        if n in (2, 5):
            data["released"] = T(n + 30)
        out.append(line(n, "alpha", collection="locks", id_="alpha-l%d" % n, data=data, t=T(n)))
    out.append(line(7, "alpha", collection="locks", id_="alpha-l1", op="update", data={"note": "moved"}, t=T(40)))
    return out


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.repo.write_roster()
        self.repo.write_log("alpha", section_lines())

    def tearDown(self):
        self.repo.__exit__(None, None, None)

    def get(self, srv, query):
        return request(srv.url + "/api/section/" + query)

    def test_section_open_rows_come_first(self):
        with RunningServer(self.repo.root) as srv:
            status, body = self.get(srv, "locks")
        self.assertEqual(status, 200)
        # l1 changed last (the update at :40), then the open ones newest first, then the released.
        self.assertEqual([r["id"] for r in body["rows"]],
                         ["alpha-l1", "alpha-l6", "alpha-l4", "alpha-l3", "alpha-l5", "alpha-l2"])
        self.assertEqual((body["open"], body["total"], body["next"]), (4, 6, None))
        self.assertEqual(body["rows"][0]["note"], "moved")

    def test_section_q_filters(self):
        with RunningServer(self.repo.root) as srv:
            status, body = self.get(srv, "locks?q=LOCK%205")
            self.assertEqual(status, 200)
            self.assertEqual([r["id"] for r in body["rows"]], ["alpha-l5"])
            self.assertEqual((body["open"], body["total"]), (0, 1))
            status, body = self.get(srv, "locks?q=PLAIN")
            self.assertEqual(len(body["rows"]), 5)
            self.assertEqual((body["open"], body["total"]), (3, 5))

    def test_section_q_with_regex_characters_matches_as_plain_text(self):
        with RunningServer(self.repo.root) as srv:
            for q, want in (("(a+b)*", ["alpha-l3"]), (".*", []), ("[", [])):
                status, body = self.get(srv, "locks?q=" + urllib.parse.quote(q, safe=""))
                self.assertEqual(status, 200, q)
                self.assertEqual([r["id"] for r in body["rows"]], want, q)

    def test_unknown_section_is_404(self):
        with RunningServer(self.repo.root) as srv:
            for name in ("nope", "messages", "waiting", "adoptions", ""):
                status, body = self.get(srv, name)
                self.assertEqual(status, 404, name)
                self.assertIn("error", body)

    def test_limit_and_cursor_page_through_with_no_row_lost_or_repeated(self):
        with RunningServer(self.repo.root) as srv:
            _status, whole = self.get(srv, "locks")
            seen, cursor, pages = [], "", 0
            while True:
                status, body = self.get(srv, "locks?limit=4&cursor=" + cursor)
                self.assertEqual(status, 200)
                self.assertEqual((body["open"], body["total"]), (4, 6))
                seen.extend(r["id"] for r in body["rows"])
                pages += 1
                if body["next"] is None:
                    break
                cursor = body["next"]
        self.assertEqual(pages, 2)
        self.assertEqual(seen, [r["id"] for r in whole["rows"]])

    def test_bad_limit_or_cursor_is_400(self):
        with RunningServer(self.repo.root) as srv:
            for query in ("limit=0", "limit=x", "cursor=-2", "cursor=abc"):
                status, body = self.get(srv, "locks?" + query)
                self.assertEqual(status, 400, query)
                self.assertIn("error", body)

    def test_every_section_answers_for_a_viewer(self):
        roster = copy.deepcopy(SPEC_ROSTER)
        roster["relay"] = "rev"
        self.repo.write_roster(roster)
        with RunningServer(self.repo.root) as srv:
            for name in sections.SECTIONS:
                status, body = self.get(srv, name)
                self.assertEqual(status, 200, name)
                self.assertEqual(sorted(body), ["next", "open", "rows", "total"], name)


if __name__ == "__main__":
    unittest.main()
