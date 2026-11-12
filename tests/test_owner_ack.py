"""POST /api/ack: the owner's "No action needed" on a request that names the owner."""
import unittest

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
        ])
        self.srv = RunningServer(self.r.root, tokens={"rev": token_hash("rev-token")})
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
        status, body = self.ack({"item": "alpha-s1"})
        self.assertEqual(status, 400)
        self.assertIn("not a request", body["error"])

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


if __name__ == "__main__":
    unittest.main()
