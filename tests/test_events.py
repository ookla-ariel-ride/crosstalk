import http.client
import json
import socket
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

from helpers import RunningServer, TempRepo, line
from test_serve import request

from crosstalk import events

LIMIT = 5.0


def append(root, pid, ln):
    with open(str(root / "log" / (pid + ".jsonl")), "a", encoding="utf-8") as f:
        f.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")


class SinceTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.root = self.repo.root

    def tearDown(self):
        self.repo.__exit__(None, None, None)

    def test_since_returns_only_new_rows(self):
        self.repo.write_log("alpha", [line(1, "alpha", data={"summary": "old"})])
        mark = events.mark(self.root)
        self.assertEqual(mark, {"alpha": 1})
        append(self.root, "alpha", line(2, "alpha", data={"summary": "a body the stream never sends"}))
        rows, new = events.since(self.root, mark)
        self.assertEqual(rows, [{"from": "alpha", "seq": 2, "collection": "messages", "id": "alpha-0002"}])
        self.assertEqual(new, {"alpha": 2})
        self.assertEqual(events.since(self.root, new), ([], new))

    def test_since_survives_a_new_log_file(self):
        self.repo.write_log("alpha", [line(1, "alpha")])
        mark = events.mark(self.root)
        self.repo.write_log("beta", [line(1, "beta"), line(2, "beta")])
        rows, new = events.since(self.root, mark)
        self.assertEqual([r["id"] for r in rows], ["beta-0001", "beta-0002"])
        self.assertEqual(new, {"alpha": 1, "beta": 2})

    def test_since_starts_again_when_a_file_shrinks_or_goes(self):
        self.repo.write_log("alpha", [line(1, "alpha"), line(2, "alpha"), line(3, "alpha")])
        self.repo.write_log("beta", [line(1, "beta")])
        mark = events.mark(self.root)
        self.repo.write_log("alpha", [line(1, "alpha", id_="alpha-new")])
        (self.root / "log" / "beta.jsonl").unlink()
        rows, new = events.since(self.root, mark)
        self.assertEqual([r["id"] for r in rows], ["alpha-new"])
        self.assertEqual(new, {"alpha": 1})
        self.repo.write_log("beta", [line(1, "beta", id_="beta-back")])
        rows, new = events.since(self.root, new)
        self.assertEqual([r["id"] for r in rows], ["beta-back"])

    def test_since_leaves_a_line_that_is_not_complete(self):
        self.repo.write_log("alpha", [line(1, "alpha")])
        mark = events.mark(self.root)
        half = json.dumps(line(2, "alpha"))
        with open(str(self.root / "log" / "alpha.jsonl"), "a", encoding="utf-8") as f:
            f.write(half[:20])
        rows, new = events.since(self.root, mark)
        self.assertEqual((rows, new), ([], {"alpha": 1}))
        with open(str(self.root / "log" / "alpha.jsonl"), "a", encoding="utf-8") as f:
            f.write(half[20:] + "\n")
        rows, new = events.since(self.root, new)
        self.assertEqual([r["id"] for r in rows], ["alpha-0002"])
        self.assertEqual(new, {"alpha": 2})


class Stream:
    """An open /api/events stream; lines() reads it until a deadline."""

    def __init__(self, url):
        parts = urlsplit(url)
        self.conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=LIMIT)
        self.conn.request("GET", "/api/events")
        self.resp = self.conn.getresponse()

    def read_until(self, wanted):
        deadline = time.monotonic() + LIMIT
        seen = []
        while time.monotonic() < deadline:
            raw = self.resp.fp.readline()
            if not raw:
                break
            text = raw.decode("utf-8").rstrip("\n")
            seen.append(text)
            if text == wanted:
                return seen
        raise AssertionError("no %r within %ss; saw %r" % (wanted, LIMIT, seen))

    def close(self):
        self.conn.close()


class EventsRouteTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.repo.write_roster()
        self.repo.write_log("alpha", [line(1, "alpha", data={"kind": "reply", "summary": "old"})])
        self.srv = RunningServer(self.repo.root).__enter__()
        self.srv.state.events_check_sec = 0.05

    def tearDown(self):
        self.srv.__exit__(None, None, None)
        self.repo.__exit__(None, None, None)

    def test_events_route_sends_rows_event(self):
        stream = Stream(self.srv.url)
        try:
            self.assertEqual(stream.resp.status, 200)
            self.assertEqual(stream.resp.getheader("Content-Type"), "text/event-stream")
            stream.read_until("event: ready")
            append(self.repo.root, "alpha", line(2, "alpha", data={"kind": "reply", "summary": "secret body"}))
            seen = stream.read_until("event: rows")
            data = stream.resp.fp.readline().decode("utf-8")
        finally:
            stream.close()
        self.assertNotIn("event: rows", seen[:-1])
        self.assertTrue(data.startswith("data: "), data)
        self.assertEqual(json.loads(data[len("data: "):]),
                         {"rows": [{"from": "alpha", "seq": 2, "collection": "messages", "id": "alpha-0002"}]})
        self.assertNotIn("secret body", data)

    def test_a_lone_surrogate_in_an_id_still_sends_the_rows_event(self):
        stream = Stream(self.srv.url)
        try:
            stream.read_until("event: ready")
            append(self.repo.root, "alpha", line(2, "alpha", id_="alpha-\ud800", data={"kind": "reply"}))
            stream.read_until("event: rows")
            data = stream.resp.fp.readline().decode("utf-8")
        finally:
            stream.close()
        self.assertEqual(json.loads(data[len("data: "):])["rows"][0]["id"], "alpha-\ud800")

    def test_ready_comes_after_the_mark_and_a_row_after_the_mark_follows_it(self):
        # The client refetches on `ready`; a row written before the mark is in
        # that refetch, and a row written after it must come as an event.
        real_mark = events.mark

        def mark_then_write(root):
            out = real_mark(root)
            append(self.repo.root, "alpha", line(2, "alpha", data={"kind": "reply", "summary": "late"}))
            return out

        with mock.patch.object(events, "mark", mark_then_write):
            stream = Stream(self.srv.url)
            try:
                before = stream.read_until("event: ready")
                seen = stream.read_until("event: rows")
                data = stream.resp.fp.readline().decode("utf-8")
            finally:
                stream.close()
        self.assertNotIn("event: rows", before)
        self.assertIn("alpha-0002", data)
        self.assertNotIn("event: ready", seen)

    def test_no_byte_reaches_the_client_before_the_mark(self):
        # The browser's own open event fires on the headers, so the headers
        # must wait for the mark too.
        release = threading.Event()
        real_mark = events.mark

        def slow_mark(root):
            release.wait(LIMIT)
            return real_mark(root)

        parts = urlsplit(self.srv.url)
        with mock.patch.object(events, "mark", slow_mark):
            sock = socket.create_connection((parts.hostname, parts.port), timeout=0.5)
            try:
                sock.sendall(b"GET /api/events HTTP/1.1\r\nHost: x\r\n\r\n")
                with self.assertRaises(socket.timeout):
                    sock.recv(1)
                release.set()
                sock.settimeout(LIMIT)
                got = b""
                while b"event: ready" not in got:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    got += chunk
            finally:
                sock.close()
        self.assertTrue(got.startswith(b"HTTP/1."), got[:40])
        self.assertIn(b"event: ready", got)

    def test_events_route_sends_ping(self):
        self.srv.state.events_ping_sec = 0.2
        stream = Stream(self.srv.url)
        try:
            stream.read_until(": ping")
        finally:
            stream.close()

    def test_other_requests_work_while_stream_is_open(self):
        stream = Stream(self.srv.url)
        try:
            stream.read_until("event: ready")
            status, body = request(self.srv.url + "/api/messages")
            self.assertEqual(status, 200)
            self.assertIn("alpha-0001", body["docs"])
        finally:
            stream.close()

    def test_server_stops_with_a_stream_open(self):
        stream = Stream(self.srv.url)
        try:
            stream.read_until("event: ready")
            done = threading.Event()

            def stop():
                self.srv.close()
                done.set()
            threading.Thread(target=stop, daemon=True).start()
            self.assertTrue(done.wait(LIMIT), "the server did not stop with a stream open")
            # The stream ends too: the handler sees the stop and returns.
            deadline = time.monotonic() + LIMIT
            while time.monotonic() < deadline and stream.resp.fp.readline():
                pass
            self.assertLess(time.monotonic(), deadline)
        finally:
            stream.close()


if __name__ == "__main__":
    unittest.main()
