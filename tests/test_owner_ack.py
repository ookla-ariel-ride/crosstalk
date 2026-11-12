"""POST /api/ack: the owner's "No action needed" on a request that names the owner."""
import json
import unittest
import urllib.error
import urllib.request

from helpers import RunningServer, TempRepo, line
from test_serve import request, request_with_headers

from crosstalk.logs import read_log
from crosstalk.tokens import token_hash


def msg(seq, frm, id_, **data):
    return line(seq, frm, id_=id_, data=dict(data, **{"from": frm}))


class OwnerAckTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        self.r.write_log("alpha", [
            msg(1, "alpha", "alpha-r1", kind="request", summary="Look at this", to=["owner"]),
            msg(2, "alpha", "alpha-q1", kind="question", summary="Which one?", to=["owner"]),
            msg(3, "alpha", "alpha-s1", kind="sync", summary="status"),
            msg(4, "alpha", "alpha-r2", kind="request", summary="For beta", to=["beta"]),
            msg(5, "alpha", "alpha-r3", kind="request", summary="For everyone"),
            msg(6, "alpha", "alpha-r4", kind="request", summary="Answered", to=["owner"]),
        ])
        self.r.write_log("owner", [msg(1, "owner", "own-1", kind="owner", summary="Done", replyTo="alpha-r4")])
        self.srv = RunningServer(self.r.root, tokens={"rev": token_hash("rev-token"), "owner": token_hash("owner-token")})
        self.srv.__enter__()
        _status, _body, headers = request_with_headers(self.srv.url + "/api/login", "POST", {"pin": self.srv.pin})
        self.cookie = headers.get("Set-Cookie", "").split(";")[0]

    def tearDown(self):
        self.srv.__exit__(None, None, None)
        self.r.__exit__(None, None, None)

    def ack(self, body, headers=None):
        return request(self.srv.url + "/api/ack", "POST", body, headers if headers is not None else {"Cookie": self.cookie})

    def acks(self):
        path = self.r.root / "log" / "owner.jsonl"
        return [ln for ln in read_log(path)[0] if ln.collection == "acks"] if path.exists() else []

    def raw(self, data):
        req = urllib.request.Request(self.srv.url + "/api/ack", data=data, method="POST",
                                     headers={"Cookie": self.cookie, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def needs(self):
        status, body = request(self.srv.url + "/api/threads?sort=needs")
        self.assertEqual(status, 200)
        return {t["root"] for t in body["threads"] if t["needs_owner"]}

    def test_the_owner_writes_the_row_and_the_request_needs_the_owner_no_more(self):
        self.assertIn("alpha-r1", self.needs())
        status, body = self.ack({"item": "alpha-r1"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        rows = self.acks()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, "owner--alpha-r1")
        self.assertEqual(rows[0].data["none"], True)
        self.assertEqual(rows[0].data["item"], "alpha-r1")
        self.assertNotIn("alpha-r1", self.needs())
        self.assertIn("alpha-q1", self.needs())

    def test_the_thread_lists_the_owner_requests_and_the_ack(self):
        status, body = request(self.srv.url + "/api/thread/alpha-r1")
        self.assertEqual(body["thread"]["owner_requests"], ["alpha-r1"])
        self.assertNotIn("acked_by", body["messages"][0])
        self.ack({"item": "alpha-r1"})
        status, body = request(self.srv.url + "/api/thread/alpha-r1")
        self.assertEqual(status, 200)
        self.assertEqual(body["thread"]["owner_requests"], [])
        self.assertEqual(body["messages"][0]["acked_by"], ["owner"])

    def test_a_later_mark_through_the_lesson_replaces_the_no_change_mark(self):
        # One source: acked_by and the wait read the acks rows through marks.mark_of,
        # and of two rows of one participant on one post the later one wins.
        self.r.write_log("beta", [line(1, "beta", "lessons", "lesson-alpha-r1",
                                       data={"summary": "A lesson", "t": "2026-10-21T18:00:00Z"})])
        self.r.write_log("owner", [
            msg(1, "owner", "own-1", kind="owner", summary="Done", replyTo="alpha-r4"),
            line(2, "owner", "acks", "owner--alpha-r1", t="2026-10-21T18:01:00Z",
                 data={"from": "owner", "item": "alpha-r1", "mark": "none", "none": True, "t": "2026-10-21T18:01:00Z"}),
            line(3, "owner", "acks", "owner--lesson-alpha-r1", t="2026-10-21T18:02:00Z",
                 data={"from": "owner", "item": "lesson-alpha-r1", "mark": "seen", "t": "2026-10-21T18:02:00Z"}),
        ])
        status, body = request(self.srv.url + "/api/thread/alpha-r1")
        self.assertEqual(status, 200)
        self.assertNotIn("acked_by", body["messages"][0])
        self.assertEqual(body["thread"]["owner_requests"], ["alpha-r1"])
        self.assertIn("alpha-r1", self.needs())

    def test_a_viewer_is_refused(self):
        status, body = self.ack({"item": "alpha-r1"}, headers={})
        self.assertEqual(status, 401)
        self.assertIn("error", body)
        status, body = self.ack({"item": "alpha-r1"}, headers={"Authorization": "Bearer rev-token"})
        self.assertEqual(status, 403)
        self.assertEqual(self.acks(), [])

    def test_a_question_is_refused_with_the_reason(self):
        status, body = self.ack({"item": "alpha-q1"})
        self.assertEqual(status, 400)
        self.assertIn("a question to the owner gets an answer, not an ack", body["error"])
        self.assertEqual(self.acks(), [])

    def test_a_message_that_is_no_request_is_refused(self):
        # The command takes --none on any post; the route takes only a request that waits for the owner.
        status, body = self.ack({"item": "alpha-s1"})
        self.assertEqual(status, 400)
        self.assertIn("not a request", body["error"])
        self.assertEqual(self.acks(), [])

    def test_a_no_change_mark_that_is_already_there_writes_nothing(self):
        # A row with only `mark: "none"` (no `none` field) is the owner's no-change mark too.
        self.r.write_log("owner", [
            msg(1, "owner", "own-1", kind="owner", summary="Done", replyTo="alpha-r4"),
            line(2, "owner", "acks", "owner--alpha-r1", data={"from": "owner", "item": "alpha-r1", "mark": "none"}),
        ])
        status, body = self.ack({"item": "alpha-r1"})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["written"])

    def test_another_mark_of_the_owner_does_not_end_the_wait(self):
        self.r.write_log("owner", [
            msg(1, "owner", "own-1", kind="owner", summary="Done", replyTo="alpha-r4"),
            line(2, "owner", "acks", "owner--alpha-r1", data={"from": "owner", "item": "alpha-r1", "mark": "seen"}),
        ])
        self.assertIn("alpha-r1", self.needs())
        status, body = self.ack({"item": "alpha-r1"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["written"])
        self.assertNotIn("alpha-r1", self.needs())

    def test_an_unknown_id_is_404(self):
        status, body = self.ack({"item": "alpha-nope"})
        self.assertEqual(status, 404)
        self.assertIn("error", body)
        self.assertEqual(self.acks(), [])

    def test_a_second_call_changes_nothing(self):
        self.assertEqual(self.ack({"item": "alpha-r1"})[0], 200)
        status, body = self.ack({"item": "alpha-r1"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(len(self.acks()), 1)

    def test_a_wrong_typed_body_is_400(self):
        for body in ({"item": 5}, {"item": None}, {"item": ["alpha-r1"]}, {}, ["alpha-r1"], "alpha-r1", {"item": ""}):
            status, answer = self.ack(body)
            self.assertEqual(status, 400, (body, answer))
            self.assertIn("error", answer)
        self.assertEqual(self.acks(), [])

    def test_a_request_that_does_not_wait_for_the_owner_is_refused(self):
        # To another participant, a broadcast, and one the owner replied to.
        for item in ("alpha-r2", "alpha-r3", "alpha-r4"):
            status, body = self.ack({"item": item})
            self.assertEqual(status, 400, (item, body))
            self.assertIn("does not wait for the owner", body["error"])
        self.assertEqual(self.acks(), [])

    def test_a_body_that_is_not_json_or_empty_is_400(self):
        for data in (b"{not json", b""):
            status, body = self.raw(data)
            self.assertEqual(status, 400, (data, body))
            self.assertIn("error", body)
        self.assertEqual(self.acks(), [])

    def test_the_owner_token_may_ack(self):
        status, body = self.ack({"item": "alpha-r1"}, headers={"Authorization": "Bearer owner-token"})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.acks()), 1)

    def test_the_row_has_the_shape_of_the_command_row(self):
        # `relay.py ack --from owner <id> --none` writes from, item, mark "none", none true and t.
        self.ack({"item": "alpha-r1"})
        row = self.acks()[0]
        self.assertEqual(row.from_, "owner")
        self.assertEqual(set(row.data), {"from", "item", "mark", "none", "t"})
        self.assertEqual((row.data["from"], row.data["item"], row.data["mark"], row.data["none"]),
                         ("owner", "alpha-r1", "none", True))
        self.assertRegex(row.data["t"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


if __name__ == "__main__":
    unittest.main()
