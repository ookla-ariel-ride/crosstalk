import json
import unittest
from unittest import mock
from pathlib import Path

from helpers import RunningServer, TempRepo, line
from test_serve import request

from crosstalk import readstate
from crosstalk import serve as serve_mod
from crosstalk.tokens import token_hash

OWNER_TOKEN = "owner-test-token"
BETA_TOKEN = "beta-test-token"
NOW_MS = 1_800_000_000_000
EMPTY = {"threads": {}, "muted": [], "all_before": None, "rev": NOW_MS}


def fixed_clock(test):
    """Hold readstate's clock at NOW_MS; test.clock[0] moves it."""
    test.clock = [NOW_MS]
    patcher = mock.patch.object(readstate, "_now_ms", side_effect=lambda: test.clock[0])
    patcher.start()
    test.addCleanup(patcher.stop)


def msg(did, frm, t):
    return {"id": did, "from": frm, "t": t}


THREAD = {"root": "a"}
MESSAGES = [msg("a", "alpha", "2026-10-21T10:00:00Z"), msg("b", "beta", "2026-10-21T10:05:00Z")]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.root = self.repo.root
        fixed_clock(self)

    def tearDown(self):
        self.repo.__exit__(None, None, None)

    def test_missing_file_is_empty_state(self):
        self.assertFalse((self.root / readstate.FILE).exists())
        self.assertEqual(readstate.load(self.root), EMPTY)
        self.assertEqual(readstate.unread(THREAD, MESSAGES, readstate.load(self.root)), 2)

    def test_corrupt_file_is_empty_state_and_is_not_overwritten_on_read(self):
        path = self.root / readstate.FILE
        for raw in ("{not json", "[1, 2]", '{"threads": 3, "muted": "x", "all_before": 5}'):
            path.write_text(raw, encoding="utf-8")
            self.assertEqual(readstate.load(self.root), EMPTY, raw)
            self.assertEqual(path.read_text(encoding="utf-8"), raw)

    def test_mark_then_unread_is_zero(self):
        self.assertEqual(readstate.unread(THREAD, MESSAGES, readstate.load(self.root)), 2)
        readstate.mark(self.root, "a", "2026-10-21T10:05:00Z", "b")
        state = readstate.load(self.root)
        self.assertEqual(state["threads"], {"a": {"t": "2026-10-21T10:05:00Z", "id": "b"}})
        self.assertEqual(readstate.unread(THREAD, MESSAGES, state), 0)

    def test_new_reply_counts_one(self):
        readstate.mark(self.root, "a", "2026-10-21T10:05:00Z", "b")
        state = readstate.load(self.root)
        more = MESSAGES + [msg("c", "beta", "2026-10-21T10:10:00Z"),
                           msg("d", "owner", "2026-10-21T10:11:00Z")]  # the owner's own post is never new
        self.assertEqual(readstate.unread(THREAD, more, state), 1)
        # A message in the same second sorts after the mark by its id.
        same = MESSAGES + [msg("bz", "alpha", "2026-10-21T10:05:00Z")]
        self.assertEqual(readstate.unread(THREAD, same, state), 1)

    def test_mark_never_moves_backwards(self):
        readstate.mark(self.root, "a", "2026-10-21T10:05:00Z", "b")
        readstate.mark(self.root, "a", "2026-10-21T10:00:00Z", "a")
        self.assertEqual(readstate.load(self.root)["threads"]["a"], {"t": "2026-10-21T10:05:00Z", "id": "b"})
        readstate.mark_all(self.root, "2026-10-21T12:00:00Z")
        readstate.mark_all(self.root, "2026-10-21T11:00:00Z")
        self.assertEqual(readstate.load(self.root)["all_before"], "2026-10-21T12:00:00Z")

    def test_mark_all_clears_every_thread(self):
        other = [msg("x", "alpha", "2026-10-21T09:00:00Z"), msg("y", "beta", "2026-10-21T11:00:00Z")]
        state = readstate.load(self.root)
        self.assertEqual(readstate.unread(THREAD, MESSAGES, state), 2)
        self.assertEqual(readstate.unread({"root": "x"}, other, state), 2)
        readstate.mark_all(self.root, "2026-10-21T11:00:00Z")
        state = readstate.load(self.root)
        self.assertEqual(state["all_before"], "2026-10-21T11:00:00Z")
        self.assertEqual(readstate.unread(THREAD, MESSAGES, state), 0)
        self.assertEqual(readstate.unread({"root": "x"}, other, state), 0)
        later = other + [msg("z", "beta", "2026-10-21T11:00:01Z")]
        self.assertEqual(readstate.unread({"root": "x"}, later, state), 1)

    def test_mute_round_trip(self):
        readstate.set_muted(self.root, "a", True)
        readstate.set_muted(self.root, "a", True)
        readstate.set_muted(self.root, "x", True)
        self.assertEqual(readstate.load(self.root)["muted"], ["a", "x"])
        readstate.set_muted(self.root, "a", False)
        self.assertEqual(readstate.load(self.root)["muted"], ["x"])

    def test_each_change_raises_rev_and_a_no_op_keeps_it(self):
        self.assertEqual(readstate.mark(self.root, "a", "2026-10-21T10:05:00Z", "b")["rev"], NOW_MS + 1)
        self.assertEqual(readstate.mark(self.root, "a", "2026-10-21T10:00:00Z", "a")["rev"], NOW_MS + 1)
        self.assertEqual(readstate.mark_all(self.root, "2026-10-21T12:00:00Z")["rev"], NOW_MS + 2)
        self.assertEqual(readstate.set_muted(self.root, "a", True)["rev"], NOW_MS + 3)
        self.assertEqual(readstate.set_muted(self.root, "a", True)["rev"], NOW_MS + 3)
        self.assertEqual(readstate.load(self.root)["rev"], NOW_MS + 3)

    def test_a_bad_rev_in_the_file_reads_as_the_present_time(self):
        path = self.root / readstate.FILE
        for rev in ('"7"', "-1", "true", "1.5", "null"):
            path.write_text('{"threads": {}, "muted": [], "all_before": null, "rev": %s}' % rev, encoding="utf-8")
            self.assertEqual(readstate.load(self.root)["rev"], NOW_MS, rev)

    def test_a_lost_file_gives_a_rev_above_one_handed_out_before(self):
        before = [readstate.mark(self.root, "a", "2026-10-21T10:0%d:00Z" % i, "b")["rev"] for i in range(3)]
        (self.root / readstate.FILE).unlink()
        self.clock[0] += 10
        self.assertGreater(readstate.load(self.root)["rev"], max(before))
        self.assertGreater(readstate.mark(self.root, "x", "2026-10-21T10:00:00Z", "b")["rev"], max(before))
        # A file replaced by an old copy with a low rev: the next write starts from the present time.
        (self.root / readstate.FILE).write_text('{"threads": {}, "muted": [], "all_before": null, "rev": 2}',
                                                encoding="utf-8")
        self.assertGreater(readstate.mark(self.root, "y", "2026-10-21T10:00:00Z", "b")["rev"], max(before))

    def test_file_is_written_whole_and_git_ignored(self):
        readstate.mark(self.root, "a", "2026-10-21T10:05:00Z", "b")
        raw = json.loads((self.root / readstate.FILE).read_text(encoding="utf-8"))
        self.assertEqual(raw, {"threads": {"a": {"t": "2026-10-21T10:05:00Z", "id": "b"}},
                               "muted": [], "all_before": None, "rev": NOW_MS + 1})
        ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(readstate.FILE, ignore.splitlines())


def rows():
    return [
        line(1, "alpha", id_="alpha-root", data={"kind": "note", "summary": "root"}, t="2026-10-21T10:00:00Z"),
        line(2, "alpha", id_="alpha-other", data={"kind": "note", "summary": "other"}, t="2026-10-21T10:30:00Z"),
    ]


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.repo.write_roster()
        self.repo.write_log("alpha", rows())
        self.repo.write_log("beta", [
            line(1, "beta", id_="beta-reply", data={"kind": "reply", "summary": "reply", "replyTo": "alpha-root"},
                 t="2026-10-21T10:05:00Z"),
        ])
        self.tokens = {"owner": token_hash(OWNER_TOKEN), "beta": token_hash(BETA_TOKEN)}
        fixed_clock(self)

    def tearDown(self):
        self.repo.__exit__(None, None, None)

    def server(self):
        return RunningServer(self.repo.root, tokens=self.tokens)

    @staticmethod
    def auth(token):
        return {"Authorization": "Bearer " + token}

    def put(self, srv, body, token=OWNER_TOKEN):
        headers = self.auth(token) if token else {}
        return request(srv.url + "/api/read-state", "PUT", body, headers)

    def unread_by_root(self, srv, token=OWNER_TOKEN):
        status, body = request(srv.url + "/api/threads", headers=self.auth(token) if token else {})
        self.assertEqual(status, 200)
        return {t["root"]: t.get("unread") for t in body["threads"]}

    def test_read_state_put_needs_owner(self):
        good = {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "beta-reply"}
        with self.server() as srv:
            for token in (None, BETA_TOKEN):
                for body in (good, {"nonsense": 1}):
                    status, reply = self.put(srv, body, token)
                    self.assertEqual(status, 403, (token, body))
                    self.assertIn("error", reply)
                status, _ = request(srv.url + "/api/read-state", headers=self.auth(token) if token else {})
                self.assertEqual(status, 403, token)
            self.assertFalse((self.repo.root / readstate.FILE).exists())
            status, reply = self.put(srv, good)
            self.assertEqual(status, 200)
            self.assertEqual(reply["threads"], {"alpha-root": {"t": "2026-10-21T10:05:00Z", "id": "beta-reply"}})

    def test_put_rejects_a_bad_body(self):
        bad = [
            [1], {}, {"thread": "alpha-root"}, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z"},
            {"thread": "", "t": "2026-10-21T10:05:00Z", "id": "x"},
            {"thread": "alpha-root", "t": "yesterday", "id": "x"},
            {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": 3},
            {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "x", "muted": True},
            {"all_before": None}, {"all_before": "soon"},
            {"thread": "alpha-root", "muted": "yes"}, {"thread": 5, "muted": True},
        ]
        with self.server() as srv:
            for body in bad:
                status, reply = self.put(srv, body)
                self.assertEqual(status, 400, body)
                self.assertIn("error", reply)
        self.assertFalse((self.repo.root / readstate.FILE).exists())

    def test_read_state_survives_a_restart(self):
        with self.server() as srv:
            self.assertEqual(self.put(srv, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z",
                                            "id": "beta-reply"})[0], 200)
            self.assertEqual(self.put(srv, {"thread": "alpha-other", "muted": True})[0], 200)
            self.assertEqual(self.put(srv, {"all_before": "2026-10-21T09:00:00Z"})[0], 200)
        with self.server() as srv:
            status, body = request(srv.url + "/api/read-state", headers=self.auth(OWNER_TOKEN))
        self.assertEqual(status, 200)
        self.assertEqual(body, {"threads": {"alpha-root": {"t": "2026-10-21T10:05:00Z", "id": "beta-reply"}},
                                "muted": ["alpha-other"], "all_before": "2026-10-21T09:00:00Z", "rev": NOW_MS + 3})

    def test_threads_route_gives_the_owner_unread_counts(self):
        with self.server() as srv:
            self.assertEqual(self.unread_by_root(srv), {"alpha-root": 2, "alpha-other": 1})
            self.assertEqual(self.unread_by_root(srv, None), {"alpha-root": None, "alpha-other": None})
            self.assertEqual(self.unread_by_root(srv, BETA_TOKEN), {"alpha-root": None, "alpha-other": None})
            self.put(srv, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "beta-reply"})
            self.assertEqual(self.unread_by_root(srv), {"alpha-root": 0, "alpha-other": 1})

    def test_unread_filter_lists_only_the_owners_unread_threads_and_counts_them(self):
        def unread_page(srv, query="", token=OWNER_TOKEN):
            headers = self.auth(token) if token else {}
            status, body = request(srv.url + "/api/threads?sort=new&unread=1" + query, headers=headers)
            self.assertEqual(status, 200, body)
            return body

        with self.server() as srv:
            body = unread_page(srv)
            self.assertEqual([t["root"] for t in body["threads"]], ["alpha-other", "alpha-root"])
            self.assertEqual(body["unread_total"], 2)
            # Paging runs over the unread threads only.
            first = unread_page(srv, "&limit=1")
            self.assertEqual(([t["root"] for t in first["threads"]], first["next"]), (["alpha-other"], "1"))
            second = unread_page(srv, "&limit=1&cursor=1")
            self.assertEqual(([t["root"] for t in second["threads"]], second["next"]), (["alpha-root"], None))
            # A muted thread is hidden on the page, so the filter leaves it out too.
            self.put(srv, {"thread": "alpha-other", "muted": True})
            self.assertEqual([t["root"] for t in unread_page(srv)["threads"]], ["alpha-root"])
            self.put(srv, {"thread": "alpha-other", "muted": False})
            self.put(srv, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "beta-reply"})
            self.assertEqual([t["root"] for t in unread_page(srv)["threads"]], ["alpha-other"])
            self.put(srv, {"all_before": "2026-10-21T12:00:00Z"})
            body = unread_page(srv)
            self.assertEqual((body["threads"], body["unread_total"], body["next"]), ([], 0, None))
            # A reader with no server read state gets every thread, with no total.
            for token in (None, BETA_TOKEN):
                body = unread_page(srv, token=token)
                self.assertEqual(len(body["threads"]), 2, token)
                self.assertNotIn("unread_total", body)
            # Without the filter nothing changes.
            status, body = request(srv.url + "/api/threads?sort=new", headers=self.auth(OWNER_TOKEN))
            self.assertEqual(len(body["threads"]), 2)
            self.assertNotIn("unread_total", body)

    def owner_get(self, srv, path):
        status, body = request(srv.url + path, headers=self.auth(OWNER_TOKEN))
        self.assertEqual(status, 200, body)
        return body

    def test_the_unread_counts_are_built_once_for_each_change(self):
        paths = ["/api/threads?sort=new&unread=1", "/api/threads?sort=needs&unread=1&limit=1",
                 "/api/threads?sort=hot", "/api/thread/alpha-root"]
        with self.server() as srv, mock.patch.object(readstate, "load", wraps=readstate.load) as load:
            for _ in range(3):
                for path in paths:
                    load.reset_mock()
                    self.owner_get(srv, path)
                    self.assertEqual(load.call_count, 1, path)
            self.assertEqual(srv.state.unread_builds, 1)

            self.put(srv, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "beta-reply"})
            for path in paths:
                self.owner_get(srv, path)
            self.assertEqual(srv.state.unread_builds, 2)
            self.assertEqual(self.owner_get(srv, paths[0])["unread_total"], 1)

            self.append("beta", line(2, "beta", id_="beta-new", t="2026-10-21T11:05:00Z",
                                     data={"kind": "reply", "summary": "new", "replyTo": "alpha-root"}))
            body = self.owner_get(srv, paths[0])
            self.assertEqual(([t["root"] for t in body["threads"]], body["unread_total"]),
                             (["alpha-root", "alpha-other"], 2))
            for path in paths:
                self.owner_get(srv, path)
            self.assertEqual(srv.state.unread_builds, 3)

    def test_unread_filter_keeps_the_needs_order(self):
        with self.server() as srv:
            needs = [t["root"] for t in self.owner_get(srv, "/api/threads?sort=needs")["threads"]]
            body = self.owner_get(srv, "/api/threads?sort=needs&unread=1")
            self.assertEqual([t["root"] for t in body["threads"]], needs)
            self.assertEqual(body["unread_total"], 2)
            self.put(srv, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "beta-reply"})
            body = self.owner_get(srv, "/api/threads?sort=needs&unread=1")
            self.assertEqual(([t["root"] for t in body["threads"]], body["unread_total"]), (["alpha-other"], 1))

    def test_unread_filter_reads_a_wrong_typed_state_as_empty(self):
        (self.repo.root / readstate.FILE).write_text(json.dumps(
            {"threads": 5, "muted": "alpha-root", "all_before": 7, "rev": "x"}), encoding="utf-8")
        with self.server() as srv:
            body = self.owner_get(srv, "/api/threads?sort=new&unread=1")
        self.assertEqual(([t["root"] for t in body["threads"]], body["unread_total"]),
                         (["alpha-other", "alpha-root"], 2))

    def test_mark_all_with_ahead_marks_only_the_threads_dated_after_the_servers_clock(self):
        # The page loaded at 10:10 (all_before); the click reaches the server at 10:20.
        self.append("beta", line(2, "beta", id_="beta-mid", t="2026-10-21T10:15:00Z",
                                 data={"kind": "note", "summary": "came after the load"}))
        self.append("beta", line(3, "beta", id_="beta-mix", t="2026-10-21T10:12:00Z",
                                 data={"kind": "note", "summary": "mixed"}))
        self.append("alpha", line(3, "alpha", id_="alpha-mix-late", t="2026-10-21T10:50:00Z",
                                  data={"kind": "reply", "summary": "clock ahead", "replyTo": "beta-mix"}))
        with mock.patch.object(serve_mod, "now_iso", return_value="2026-10-21T10:20:00Z"), self.server() as srv:
            status, state = self.put(srv, {"all_before": "2026-10-21T10:10:00Z", "ahead": True})
            self.assertEqual(status, 200, state)
            # alpha-other (10:30) is dated after the clock: marked. beta-mid (10:15) came between
            # the load and the click: it stays new, and so does beta-mix, which holds a post like it.
            self.assertEqual(state["threads"], {"alpha-other": {"t": "2026-10-21T10:30:00Z", "id": "alpha-other"}})
            self.assertEqual(self.unread_by_root(srv),
                             {"alpha-root": 0, "alpha-other": 0, "beta-mid": 1, "beta-mix": 2})
            self.assertEqual(self.owner_get(srv, "/api/threads?sort=new&unread=1")["unread_total"], 2)
            self.append("beta", line(4, "beta", id_="beta-after", t="2026-10-21T10:40:00Z",
                                     data={"kind": "reply", "summary": "after", "replyTo": "alpha-other"}))
            self.assertEqual(self.unread_by_root(srv)["alpha-other"], 1)
            for bad in ("yes", 1, None):
                status, reply = self.put(srv, {"all_before": "2026-10-21T10:10:00Z", "ahead": bad})
                self.assertEqual(status, 400, bad)
                self.assertIn("error", reply)

    def test_all_before_more_than_five_minutes_ahead_of_the_clock_is_refused(self):
        with mock.patch.object(serve_mod, "now_iso", return_value="2026-10-21T10:20:00Z"), self.server() as srv:
            for t in ("2026-10-21T10:25:01Z", "9999-12-31T23:59:59Z"):
                status, reply = self.put(srv, {"all_before": t})
                self.assertEqual(status, 400, t)
                self.assertIn("error", reply)
                status, reply = self.put(srv, {"all_before": t, "ahead": True})
                self.assertEqual(status, 400, t)
            self.assertFalse((self.repo.root / readstate.FILE).exists())
            status, state = self.put(srv, {"all_before": "2026-10-21T10:25:00Z"})
            self.assertEqual((status, state["all_before"]), (200, "2026-10-21T10:25:00Z"))

    def test_unread_filter_refuses_a_bad_value(self):
        with self.server() as srv:
            status, body = request(srv.url + "/api/threads?sort=new&unread=yes", headers=self.auth(OWNER_TOKEN))
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_edit_of_old_message_does_not_count(self):
        with self.server() as srv:
            self.put(srv, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "beta-reply"})
            self.assertEqual(self.unread_by_root(srv)["alpha-root"], 0)
            path = self.repo.root / "log" / "beta.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line(2, "beta", id_="beta-reply", op="update", data={"summary": "reply, edited"},
                                        t="2026-10-21T11:00:00Z")) + "\n")
            # The edit moves the replayed `t`; /api/thread sends the created time, the one the page marks by.
            _status, body = request(srv.url + "/api/thread/alpha-root")
            edited = [m for m in body["messages"] if m["id"] == "beta-reply"][0]
            self.assertEqual((edited["summary"], edited["t"]), ("reply, edited", "2026-10-21T10:05:00Z"))
            self.assertEqual(self.unread_by_root(srv)["alpha-root"], 0)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line(3, "beta", id_="beta-new", data={"kind": "reply", "summary": "new",
                                                                         "replyTo": "alpha-root"},
                                        t="2026-10-21T11:05:00Z")) + "\n")
            self.assertEqual(self.unread_by_root(srv)["alpha-root"], 1)

    def append(self, who, row):
        with (self.repo.root / "log" / (who + ".jsonl")).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def test_update_of_a_root_keeps_its_place_and_is_not_new(self):
        """One created time: an update moves neither the order, last_t, the preview, nor the split."""
        with self.server() as srv:
            self.put(srv, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "beta-reply"})
            self.append("alpha", line(3, "alpha", id_="alpha-root", op="update",
                                      data={"resolution": {"choice": "a"}}, t="2026-10-21T11:00:00Z"))
            _status, body = request(srv.url + "/api/thread/alpha-root")
            self.assertEqual([(m["id"], m["t"]) for m in body["messages"]],
                             [("alpha-root", "2026-10-21T10:00:00Z"), ("beta-reply", "2026-10-21T10:05:00Z")])
            _status, page = request(srv.url + "/api/threads?sort=new", headers=self.auth(OWNER_TOKEN))
            self.assertEqual([t["root"] for t in page["threads"]], ["alpha-other", "alpha-root"])
            root = page["threads"][1]
            self.assertEqual((root["last_t"], root["last_id"], root["preview"], root["unread"]),
                             ("2026-10-21T10:05:00Z", "beta-reply", "reply", 0))

    def test_an_earlier_data_t_is_read_after_the_page_marks_it(self):
        """The page marks the newest (t, id) that /api/thread sends; unread then counts 0."""
        self.append("beta", line(2, "beta", id_="beta-late", t="2026-10-21T10:20:00Z",
                                 data={"kind": "reply", "summary": "late", "replyTo": "alpha-root",
                                       "t": "2026-10-21T10:01:00Z"}))
        with self.server() as srv:
            _status, body = request(srv.url + "/api/thread/alpha-root")
            top = max((m["t"], m["id"]) for m in body["messages"])
            self.put(srv, {"thread": "alpha-root", "t": top[0], "id": top[1]})
            self.assertEqual(self.unread_by_root(srv)["alpha-root"], 0)

    def test_thread_route_answers_for_any_message_id(self):
        with self.server() as srv:
            status, body = request(srv.url + "/api/thread/beta-reply", headers=self.auth(OWNER_TOKEN))
            self.assertEqual(status, 200)
            self.assertEqual(body["root"], "alpha-root")
            self.assertEqual([m["id"] for m in body["messages"]], ["alpha-root", "beta-reply"])
            self.assertEqual((body["thread"]["root"], body["thread"]["count"], body["thread"]["unread"]),
                             ("alpha-root", 2, 2))
            _status, viewer = request(srv.url + "/api/thread/beta-reply")
            self.assertNotIn("unread", viewer["thread"])
            status, body = request(srv.url + "/api/thread/nobody-0001")
            self.assertEqual(status, 404)
            self.assertIn("nobody-0001", body["error"])


if __name__ == "__main__":
    unittest.main()
