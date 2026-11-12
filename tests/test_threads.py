import json
import time
import unittest
import urllib.parse

from helpers import RunningServer, TempRepo, line
from test_serve import request

from crosstalk.serve import ReplayCache
from crosstalk.threads import ThreadIndex, build_threads, page, sort_threads

NOW = "2026-10-21T18:00:00Z"


def msg(frm, t, summary="s", kind="reply", reply_to=None, **extra):
    doc = {"from": frm, "t": t, "summary": summary, "kind": kind}
    if reply_to is not None:
        doc["replyTo"] = reply_to
    doc.update(extra)
    return doc


def by_root(threads):
    return {t["root"]: t for t in threads}


class BuildTests(unittest.TestCase):
    def test_root_is_found_by_reply_links(self):
        threads = build_threads({
            "a": msg("alpha", "2026-10-21T10:00:00Z", "root"),
            "b": msg("beta", "2026-10-21T10:01:00Z", reply_to="a"),
            "c": msg("alpha", "2026-10-21T10:02:00Z", reply_to="b"),
        }, NOW)
        self.assertEqual([t["root"] for t in threads], ["a"])
        self.assertEqual(threads[0]["count"], 3)
        self.assertEqual(threads[0]["projects"], ["alpha", "beta"])

    def test_orphan_reply_is_its_own_thread(self):
        threads = by_root(build_threads({
            "a": msg("alpha", "2026-10-21T10:00:00Z", "root"),
            "o": msg("beta", "2026-10-21T10:01:00Z", "orphan", reply_to="gone"),
        }, NOW))
        self.assertEqual(set(threads), {"a", "o"})
        self.assertEqual(threads["o"]["count"], 1)
        self.assertEqual(threads["o"]["title"], "orphan")

    def test_reply_cycle_ends(self):
        threads = build_threads({
            "x": msg("alpha", "2026-10-21T10:00:00Z", "first", reply_to="y"),
            "y": msg("beta", "2026-10-21T10:01:00Z", "second", reply_to="x"),
            "z": msg("alpha", "2026-10-21T10:02:00Z", "third", reply_to="y"),
        }, NOW)
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["root"], "x")  # the oldest message of the cycle
        self.assertEqual(threads[0]["count"], 3)

    def test_title_is_root_summary_and_preview_is_newest(self):
        t = build_threads({
            "a": msg("alpha", "2026-10-21T10:00:00Z", "the root", kind="request"),
            "b": msg("beta", "2026-10-21T10:05:00Z", "newest", reply_to="a"),
            "c": msg("alpha", "2026-10-21T10:01:00Z", "older", reply_to="a"),
        }, NOW)[0]
        self.assertEqual(t["title"], "the root")
        self.assertEqual(t["preview"], "newest")
        self.assertEqual(t["kind"], "request")

    def test_last_t_and_last_id_come_from_the_newest_message(self):
        t = build_threads({
            "a": msg("alpha", "2026-10-21T10:00:00Z"),
            "c": msg("beta", "2026-10-21T10:05:00Z", reply_to="a"),
            "b": msg("beta", "2026-10-21T10:05:00Z", reply_to="a"),
        }, NOW)[0]
        self.assertEqual((t["last_t"], t["last_id"]), ("2026-10-21T10:05:00Z", "c"))

    def test_routine_by_kind_or_summary_with_one_participant(self):
        threads = by_root(build_threads({
            "k1": msg("alpha", "2026-10-21T10:00:00Z", "hello", kind="check-in"),
            "k2": msg("alpha", "2026-10-21T10:00:00Z", "hello", kind="checkin"),
            "k3": msg("alpha", "2026-10-21T10:00:00Z", "hello", kind="sync"),
            "s1": msg("alpha", "2026-10-21T10:00:00Z", "LAND START alpha"),
            "s2": msg("alpha", "2026-10-21T10:00:00Z", "LAND END alpha"),
            "s3": msg("alpha", "2026-10-21T10:00:00Z", "GRANTED alpha"),
            "s4": msg("alpha", "2026-10-21T10:00:00Z", "TURN ASK alpha", kind="request"),
            "s4r": msg("alpha", "2026-10-21T10:01:00Z", "same author", reply_to="s4"),
            "n1": msg("alpha", "2026-10-21T10:00:00Z", "a real topic"),
        }, NOW))
        for root in ("k1", "k2", "k3", "s1", "s2", "s3", "s4"):
            self.assertTrue(threads[root]["routine"], root)
        self.assertFalse(threads["n1"]["routine"])

    def test_routine_ends_with_a_reply_from_a_second_participant(self):
        t = build_threads({
            "a": msg("alpha", "2026-10-21T10:00:00Z", "LAND START alpha"),
            "b": msg("beta", "2026-10-21T10:01:00Z", "a question", reply_to="a"),
        }, NOW)[0]
        self.assertFalse(t["routine"])

    def test_routine_thread_is_never_hot(self):
        unnamed = msg("alpha", "2026-10-21T17:50:00Z", "no writer", reply_to="k")
        del unnamed["from"]  # a row with no `from` names no second participant
        t = build_threads({
            "k": msg("alpha", "2026-10-21T17:30:00Z", "checking in", kind="check-in"),
            "k2": msg("alpha", "2026-10-21T17:40:00Z", "still here", reply_to="k"),
            "k3": unnamed,
        }, NOW)[0]
        self.assertTrue(t["routine"])
        self.assertEqual(t["hot"], 0)

    def test_hot_counts_the_last_hour_when_two_participants_wrote(self):
        threads = by_root(build_threads({
            "a": msg("alpha", "2026-10-21T16:00:00Z"),  # older than an hour
            "b": msg("beta", "2026-10-21T17:10:00Z", reply_to="a"),
            "c": msg("alpha", "2026-10-21T17:20:00Z", reply_to="b"),
            "d": msg("beta", "2026-10-21T17:30:00Z", reply_to="c"),
            "solo": msg("alpha", "2026-10-21T17:30:00Z"),
            "solo2": msg("alpha", "2026-10-21T17:40:00Z", reply_to="solo"),
        }, NOW))
        self.assertEqual(threads["a"]["hot"], 3)
        self.assertEqual(threads["solo"]["hot"], 0)

    def test_created_times_drive_order_last_t_and_hot(self):
        """An update moves the replayed `t`; the created time keeps the message in its place."""
        messages = {
            "a": msg("alpha", "2026-10-21T17:50:00Z", "root, edited"),  # replayed `t` of an update
            "b": msg("beta", "2026-10-21T17:10:00Z", "reply", reply_to="a"),
        }
        index = ThreadIndex(messages, created={"a": "2026-10-21T15:00:00Z", "b": "2026-10-21T17:10:00Z"})
        t = index.threads_at(NOW)[0]
        self.assertEqual([m["id"] for m in index.thread("a")], ["a", "b"])
        self.assertEqual([m["t"] for m in index.thread("a")], ["2026-10-21T15:00:00Z", "2026-10-21T17:10:00Z"])
        self.assertEqual((t["last_t"], t["last_id"], t["preview"]), ("2026-10-21T17:10:00Z", "b", "reply"))
        self.assertEqual(t["hot"], 0)  # only beta wrote in the last hour

    def test_root_of_any_message(self):
        index = ThreadIndex({"a": msg("alpha", "2026-10-21T10:00:00Z"),
                             "b": msg("beta", "2026-10-21T10:01:00Z", reply_to="a")})
        self.assertEqual((index.root_of.get("b"), index.root_of.get("a"), index.root_of.get("x")), ("a", "a", None))

    def test_needs_owner_for_an_open_waiting_row_of_the_same_id(self):
        threads = by_root(ThreadIndex({
            "w": msg("owner", "2026-10-21T10:00:00Z", "to do", kind="owner"),
            "n": msg("alpha", "2026-10-21T10:00:00Z", "note", kind="note"),
        }, waiting={"w", "gone"}).threads_at(NOW))
        self.assertTrue(threads["w"]["needs_owner"])
        self.assertFalse(threads["n"]["needs_owner"])

    def test_needs_owner_for_an_open_question(self):
        threads = by_root(build_threads({
            "q": msg("alpha", "2026-10-21T10:00:00Z", "which?", kind="question"),
            "q2": msg("alpha", "2026-10-21T10:00:00Z", "which?", kind="question"),
            "q2a": msg("owner", "2026-10-21T10:05:00Z", "this one", reply_to="q2"),
        }, NOW))
        self.assertTrue(threads["q"]["needs_owner"])
        self.assertFalse(threads["q2"]["needs_owner"])

    def test_needs_owner_for_a_request_to_the_owner_until_the_owner_replies(self):
        threads = by_root(build_threads({
            "r": msg("alpha", "2026-10-21T10:00:00Z", "please", kind="request", to=["owner"]),
            "r2": msg("alpha", "2026-10-21T10:00:00Z", "please", kind="request", to=["owner"]),
            "r2a": msg("owner", "2026-10-21T10:05:00Z", "done", reply_to="r2"),
            "peer": msg("alpha", "2026-10-21T10:00:00Z", "peer ask", kind="request", to=["beta"]),
        }, NOW))
        self.assertTrue(threads["r"]["needs_owner"])
        self.assertFalse(threads["r2"]["needs_owner"])
        self.assertFalse(threads["peer"]["needs_owner"])

    def test_an_ack_closes_a_request_as_requests_py_does(self):
        # The same rule as requests.open_requests: an ack row with none: true counts as a reply.
        messages = {
            "r": msg("alpha", "2026-10-21T10:00:00Z", "please", kind="request", to=["owner"]),
            "two": msg("alpha", "2026-10-21T10:00:00Z", "please", kind="request", to=["owner", "beta"]),
        }
        acks = {"owner--r": {"from": "owner", "item": "r", "none": True},
                "owner--two": {"from": "owner", "item": "two", "none": True}}
        threads = by_root(ThreadIndex(messages, acks=acks).threads_at(NOW))
        self.assertFalse(threads["r"]["needs_owner"])
        self.assertFalse(threads["r"]["unanswered"])
        # One of two recipients acked: the request still waits for the other.
        self.assertFalse(threads["two"]["needs_owner"])
        self.assertTrue(threads["two"]["unanswered"])
        acks = {"beta--two": {"from": "beta", "item": "two", "none": True}}
        threads = by_root(build_threads(messages, NOW, acks=acks))
        self.assertTrue(threads["two"]["needs_owner"])
        self.assertTrue(threads["two"]["unanswered"])
        self.assertTrue(threads["r"]["needs_owner"])

    def test_an_ack_row_that_does_not_count_changes_nothing(self):
        messages = {"r": msg("alpha", "2026-10-21T10:00:00Z", "please", kind="request", to=["owner"])}
        for ack_id, ack in (("owner--r", {"from": "owner", "item": "r", "none": False}),
                            ("owner--r", {"from": ["owner"], "item": "r", "none": True}),
                            ("owner--r", {"from": "owner", "item": 7, "none": True}),
                            ("beta--r", {"from": "owner", "item": "r", "none": True}),
                            ("owner--r", "not a row")):
            threads = by_root(build_threads(messages, NOW, acks={ack_id: ack}))
            self.assertTrue(threads["r"]["needs_owner"], ack)
            self.assertTrue(threads["r"]["unanswered"], ack)

    def test_a_request_that_needs_no_answer_waits_for_nobody(self):
        threads = by_root(build_threads({
            "f": msg("alpha", "2026-10-21T10:00:00Z", "fyi", kind="request", to=["owner"], needs_answer=False),
            "s": msg("alpha", "2026-10-21T10:00:00Z", "fyi", kind="request", to=["owner", "beta"],
                     body="Done.\n\nNo answer needed."),
        }, NOW))
        for root in ("f", "s"):
            self.assertFalse(threads[root]["needs_owner"], root)
            self.assertFalse(threads[root]["unanswered"], root)

    def test_unanswered_is_an_open_request(self):
        threads = by_root(build_threads({
            "r": msg("alpha", "2026-10-21T10:00:00Z", "help", kind="request", to=["beta"]),
            "r2": msg("alpha", "2026-10-21T10:00:00Z", "help", kind="request", to=["beta"]),
            "r2a": msg("beta", "2026-10-21T10:05:00Z", "done", reply_to="r2"),
            "n": msg("alpha", "2026-10-21T10:00:00Z", "news", kind="note"),
        }, NOW))
        self.assertTrue(threads["r"]["unanswered"])
        self.assertFalse(threads["r2"]["unanswered"])
        self.assertFalse(threads["n"]["unanswered"])


def thread(root, last_t, needs=False, unanswered=False, hot=0):
    return {"root": root, "last_t": last_t, "last_id": root, "needs_owner": needs,
            "unanswered": unanswered, "hot": hot}


class SortTests(unittest.TestCase):
    T = [
        thread("old-need", "2026-10-21T01:00:00Z", needs=True),
        thread("new-need", "2026-10-21T05:00:00Z", needs=True),
        thread("old", "2026-10-21T02:00:00Z", unanswered=True),
        thread("new", "2026-10-21T06:00:00Z", hot=2),
        thread("mid", "2026-10-21T04:00:00Z", unanswered=True, hot=5),
    ]

    def order(self, name):
        return [t["root"] for t in sort_threads(self.T, name)]

    def test_order_needs(self):
        self.assertEqual(self.order("needs"), ["old-need", "new-need", "new", "mid", "old"])

    def test_order_new(self):
        self.assertEqual(self.order("new"), ["new", "new-need", "mid", "old", "old-need"])

    def test_order_hot(self):
        self.assertEqual(self.order("hot"), ["mid", "new", "new-need", "old", "old-need"])

    def test_order_unanswered(self):
        self.assertEqual(self.order("unanswered"), ["old", "mid"])

    def test_unknown_order_is_an_error(self):
        with self.assertRaises(ValueError):
            sort_threads(self.T, "loud")

    def test_100k_messages_sort_under_one_second(self):
        messages = {}
        for i in range(100000):
            t = "2026-10-%02dT%02d:%02d:%02dZ" % (1 + i // 86400 % 28, i // 3600 % 24, i // 60 % 60, i % 60)
            # A mix like a working log: one request and one question in ten messages.
            kind = ("request", "reply", "reply", "note", "check-in",
                    "reply", "question", "reply", "reply", "note")[i % 10]
            doc = msg("alpha" if i % 2 else "beta", t, "message %d" % i, kind=kind)
            if i % 10:
                doc["replyTo"] = "m%06d" % (i - 1)  # threads of ten
            if i % 7 == 0:
                doc["to"] = ["owner"]
            messages["m%06d" % i] = doc
        best = None
        # CPU time of this process, not wall time: other processes on a loaded
        # machine do not count against it. The best of five takes out the rest.
        for _ in range(5):
            start = time.process_time()
            threads = build_threads(messages, "2026-10-28T00:00:00Z")
            for order in ("needs", "new", "hot", "unanswered"):
                sort_threads(threads, order)
            took = time.process_time() - start
            best = took if best is None else min(best, took)
        self.assertEqual(len(threads), 10000)
        self.assertLess(best, 1.0)


class PageTests(unittest.TestCase):
    def test_page_cursor_walks_all_items_once(self):
        items = list(range(123))
        seen, cursor, calls = [], None, 0
        while True:
            got, cursor = page(items, cursor, 50)
            seen.extend(got)
            calls += 1
            if cursor is None:
                break
        self.assertEqual(seen, items)
        self.assertEqual(calls, 3)

    def test_limit_is_capped_at_200(self):
        got, cursor = page(list(range(500)), None, 1000)
        self.assertEqual(len(got), 200)
        self.assertEqual(cursor, "200")

    def test_limit_defaults_to_50(self):
        got, cursor = page(list(range(60)), None, None)
        self.assertEqual(len(got), 50)
        self.assertEqual(cursor, "50")

    def test_limit_below_one_and_a_bad_cursor_are_errors(self):
        for cursor, limit in ((None, 0), (None, -3), ("x", 10), ("-1", 10)):
            with self.assertRaises(ValueError):
                page([1, 2], cursor, limit)


def rows():
    return [
        line(1, "alpha", id_="alpha-root1", data={"kind": "request", "summary": "first", "to": ["beta"]},
             t="2026-10-21T10:00:00Z"),
        line(2, "alpha", id_="alpha-root2", data={"kind": "note", "summary": "second"}, t="2026-10-21T11:00:00Z"),
        line(3, "alpha", id_="alpha-root3", data={"kind": "question", "summary": "third"}, t="2026-10-21T12:00:00Z"),
        line(4, "alpha", id_="alpha-deep", data={"kind": "reply", "summary": "alpha-deep", "replyTo": "beta-kid"},
             t="2026-10-21T10:30:00Z"),
    ]


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.repo.write_roster()
        self.repo.write_log("alpha", rows())
        self.repo.write_log("beta", [
            line(1, "beta", id_="beta-kid", data={"kind": "reply", "summary": "beta-kid", "replyTo": "alpha-root1"},
                 t="2026-10-21T10:10:00Z"),
        ])

    def tearDown(self):
        self.repo.__exit__(None, None, None)

    def test_threads_route_returns_next_cursor(self):
        with RunningServer(self.repo.root) as srv:
            status, body = request(srv.url + "/api/threads?sort=new&limit=2")
            self.assertEqual(status, 200)
            self.assertEqual([t["root"] for t in body["threads"]], ["alpha-root3", "alpha-root2"])
            self.assertEqual(body["next"], "2")
            self.assertRegex(body["server_time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            status, body = request(srv.url + "/api/threads?sort=new&limit=2&cursor=2")
            self.assertEqual(status, 200)
            self.assertEqual([t["root"] for t in body["threads"]], ["alpha-root1"])
            self.assertIsNone(body["next"])

    def test_an_owner_ack_takes_a_request_off_the_waiting_list(self):
        self.repo.write_log("alpha", rows() + [
            line(5, "alpha", id_="alpha-ask", data={"kind": "request", "summary": "ask", "to": ["owner"]},
                 t="2026-10-21T13:00:00Z")])
        with RunningServer(self.repo.root) as srv:
            def flags():
                _status, body = request(srv.url + "/api/threads?sort=needs")
                row = by_root(body["threads"])["alpha-ask"]
                _status, one = request(srv.url + "/api/thread/alpha-ask")
                self.assertEqual(one["thread"]["needs_owner"], row["needs_owner"])
                return row["needs_owner"], row["unanswered"]
            self.assertEqual(flags(), (True, True))
            self.repo.write_log("owner", [line(1, "owner", collection="acks", id_="owner--alpha-ask",
                                               data={"from": "owner", "item": "alpha-ask", "none": True},
                                               t="2026-10-21T13:05:00Z")])
            self.assertEqual(flags(), (False, False))

    def test_threads_route_serves_each_order(self):
        with RunningServer(self.repo.root) as srv:
            got = {}
            for order in ("needs", "new", "hot", "unanswered"):
                status, body = request(srv.url + "/api/threads?sort=" + order)
                self.assertEqual(status, 200, order)
                got[order] = [t["root"] for t in body["threads"]]
        self.assertEqual(got["needs"], ["alpha-root3", "alpha-root2", "alpha-root1"])
        self.assertEqual(got["new"], ["alpha-root3", "alpha-root2", "alpha-root1"])
        self.assertEqual(got["unanswered"], [])  # beta answered alpha-root1 with `beta-kid`

    def test_threads_route_rejects_a_bad_sort_or_limit(self):
        with RunningServer(self.repo.root) as srv:
            for query in ("sort=loud", "limit=0", "limit=x", "cursor=-2"):
                status, body = request(srv.url + "/api/threads?" + query)
                self.assertEqual(status, 400, query)
                self.assertIn("error", body)

    def test_thread_route_gives_depth_and_quote(self):
        with RunningServer(self.repo.root) as srv:
            status, body = request(srv.url + "/api/thread/alpha-root1")
        self.assertEqual(status, 200)
        self.assertEqual(body["root"], "alpha-root1")
        got = [(m["id"], m["depth"], m["quote"]) for m in body["messages"]]
        self.assertEqual(got, [("alpha-root1", 0, None), ("beta-kid", 1, None), ("alpha-deep", 2, "beta-kid")])

    def test_thread_route_unknown_root_is_404(self):
        with RunningServer(self.repo.root) as srv:
            for root in ("nope", urllib.parse.quote("a/b", safe="")):
                status, _body = request(srv.url + "/api/thread/" + root)
                self.assertEqual(status, 404, root)

    def test_a_second_call_with_no_new_row_does_not_rebuild(self):
        cache = ReplayCache(self.repo.root)
        first = cache.threads()
        self.assertIs(cache.threads(), first)
        self.assertEqual(cache.thread_builds, 1)
        path = self.repo.root / "log" / "beta.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line(2, "beta", id_="beta-more", data={"kind": "note", "summary": "beta-more"},
                                    t="2026-10-21T13:00:00Z")) + "\n")
        self.assertIsNot(cache.threads(), first)
        self.assertEqual(cache.thread_builds, 2)


if __name__ == "__main__":
    unittest.main()
