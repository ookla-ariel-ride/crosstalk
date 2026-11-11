import json
import unittest
from pathlib import Path

from helpers import RunningServer, TempRepo, line
from test_serve import request

from crosstalk import readstate
from crosstalk.tokens import token_hash

OWNER_TOKEN = "owner-test-token"
BETA_TOKEN = "beta-test-token"
EMPTY = {"threads": {}, "muted": [], "all_before": None}


def msg(did, frm, t):
    return {"id": did, "from": frm, "t": t}


THREAD = {"root": "a"}
MESSAGES = [msg("a", "alpha", "2026-10-21T10:00:00Z"), msg("b", "beta", "2026-10-21T10:05:00Z")]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.root = self.repo.root

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

    def test_file_is_written_whole_and_git_ignored(self):
        readstate.mark(self.root, "a", "2026-10-21T10:05:00Z", "b")
        raw = json.loads((self.root / readstate.FILE).read_text(encoding="utf-8"))
        self.assertEqual(raw, {"threads": {"a": {"t": "2026-10-21T10:05:00Z", "id": "b"}},
                               "muted": [], "all_before": None})
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
                                "muted": ["alpha-other"], "all_before": "2026-10-21T09:00:00Z"})

    def test_threads_route_gives_the_owner_unread_counts(self):
        with self.server() as srv:
            self.assertEqual(self.unread_by_root(srv), {"alpha-root": 2, "alpha-other": 1})
            self.assertEqual(self.unread_by_root(srv, None), {"alpha-root": None, "alpha-other": None})
            self.assertEqual(self.unread_by_root(srv, BETA_TOKEN), {"alpha-root": None, "alpha-other": None})
            self.put(srv, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "beta-reply"})
            self.assertEqual(self.unread_by_root(srv), {"alpha-root": 0, "alpha-other": 1})

    def test_edit_of_old_message_does_not_count(self):
        with self.server() as srv:
            self.put(srv, {"thread": "alpha-root", "t": "2026-10-21T10:05:00Z", "id": "beta-reply"})
            self.assertEqual(self.unread_by_root(srv)["alpha-root"], 0)
            path = self.repo.root / "log" / "beta.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line(2, "beta", id_="beta-reply", op="update", data={"summary": "reply, edited"},
                                        t="2026-10-21T11:00:00Z")) + "\n")
            # The edit moved the message's replayed `t`, so a count by that `t` would see it as new.
            _status, body = request(srv.url + "/api/thread/alpha-root")
            edited = [m for m in body["messages"] if m["id"] == "beta-reply"][0]
            self.assertEqual((edited["summary"], edited["t"]), ("reply, edited", "2026-10-21T11:00:00Z"))
            self.assertEqual(self.unread_by_root(srv)["alpha-root"], 0)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line(3, "beta", id_="beta-new", data={"kind": "reply", "summary": "new",
                                                                         "replyTo": "alpha-root"},
                                        t="2026-10-21T11:05:00Z")) + "\n")
            self.assertEqual(self.unread_by_root(srv)["alpha-root"], 1)


if __name__ == "__main__":
    unittest.main()
