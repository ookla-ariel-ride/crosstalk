"""The notifier: posts new inbox items into live Claude Code sessions."""
import errno
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402,F401  (puts scripts/ on sys.path)
from helpers import TempRepo, line  # noqa: E402
from test_inbox import rep, req  # noqa: E402

from crosstalk import live  # noqa: E402
from crosstalk.notify import Notifier, SessionGone, render_message, send_to_session  # noqa: E402
from crosstalk.participants import load_roster  # noqa: E402
from crosstalk.replay import enrich_messages, load_all_lines, replay  # noqa: E402

T0 = 1793016000.0  # 2026-10-26T12:00:00Z
TOKEN = "tok-secret-123"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def owner_msg(id_, **extra):
    data = {"from": "owner", "kind": "owner", "summary": "hi " + id_}
    data.update(extra)
    return line(1, "owner", id_=id_, data=data)


class FakeSession:
    """A Claude Code inbox socket that records what it is sent.

    Connections are accepted in drain(), after the sender has closed, so the
    test reads exactly what one poll sent without timing guesses.
    """

    def __init__(self, path):
        self.path = path
        self.lines = []  # one list of parsed JSON lines per connection
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(16)
        self.sock.setblocking(False)

    def drain(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except (BlockingIOError, OSError):
                return self.lines
            conn.setblocking(True)
            conn.settimeout(2)
            buf = b""
            with conn:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            self.lines.append([json.loads(x) for x in buf.decode("utf-8").split("\n") if x])

    def refuse(self):
        self.sock.close()  # socket file stays; connect gets ECONNREFUSED

    def vanish(self):
        self.sock.close()
        os.unlink(self.path)

    def close(self):
        self.sock.close()


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        self.root = self.r.root
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-notify-test-"))
        self.state = self.tmp / "state.json"
        self.socks = tempfile.mkdtemp(dir="/tmp", prefix="cc-socks-")
        self.live = str(self.tmp / "live")
        self._env = os.environ.get("CROSSTALK_LIVE_DIR")
        os.environ["CROSSTALK_LIVE_DIR"] = self.live
        self._res = list(live.SOCKET_DIR_RES)
        live.SOCKET_DIR_RES.append(re.compile("^" + re.escape(self.socks) + "$"))
        self.now = [T0]
        self.notifiers = []
        self.fakes = []
        self.logs = {}

    def tearDown(self):
        for n in self.notifiers:
            n.stop()
        for f in self.fakes:
            f.close()
        live.SOCKET_DIR_RES[:] = self._res
        if self._env is None:
            os.environ.pop("CROSSTALK_LIVE_DIR", None)
        else:
            os.environ["CROSSTALK_LIVE_DIR"] = self._env
        shutil.rmtree(self.socks, ignore_errors=True)
        shutil.rmtree(str(self.tmp), ignore_errors=True)
        self.r.__exit__(None, None, None)

    # helpers

    def session(self, sid, pid="vectorcraft", token=TOKEN):
        fake = FakeSession(os.path.join(self.socks, sid + ".sock"))
        self.fakes.append(fake)
        env = {"CLAUDE_CODE_MESSAGING_SOCKET": fake.path, "CLAUDE_CODE_MESSAGING_TOKEN": token}
        path = live.register(self.root, pid, env, json.dumps({"session_id": sid}), T0)
        self.assertIsNotNone(path)
        fake.reg = path
        return fake

    def append(self, pid, *msgs):
        lines = self.logs.setdefault(pid, [])
        for m in msgs:
            m["seq"] = len(lines) + 1
            lines.append(m)
        self.r.write_log(pid, lines)

    def notifier(self, **kw):
        def snapshot():
            roster = load_roster(self.root / "participants.json")
            lines = load_all_lines(self.root, roster)
            return enrich_messages(replay(lines), lines), {}
        n = Notifier(self.root, snapshot, self.state, clock=lambda: self.now[0], **kw)
        self.notifiers.append(n)
        return n

    def users(self, fake):
        """The user-message contents fake received, one per connection."""
        out = []
        for conn in fake.drain():
            self.assertEqual(len(conn), 2, conn)
            self.assertEqual(conn[0], {"type": "auth", "token": TOKEN})
            self.assertEqual(conn[1]["type"], "user")
            self.assertEqual(conn[1]["from"], "crosstalk")
            out.append(conn[1]["message"]["content"])
        return out

    # tests

    def test_new_owner_message_is_told_once(self):
        fake = self.session("s1")
        n = self.notifier()
        n.poll_once()
        self.assertEqual(fake.drain(), [])
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]))
        n.poll_once()
        conns = fake.drain()
        self.assertEqual(len(conns), 1)
        self.assertEqual(len(conns[0]), 2)
        self.assertEqual(conns[0][0], {"type": "auth", "token": TOKEN})
        user = conns[0][1]
        self.assertEqual((user["type"], user["from"]), ("user", "crosstalk"))
        content = user["message"]["content"]
        self.assertIn("m-1", content)
        self.assertIn("relay.py inbox vectorcraft", content)
        self.assertEqual(content, (
            "Crosstalk: 1 new item for vectorcraft.\n"
            "- owner message from Owner: hi m-1 (m-1)\n"
            "Run `python3 %s/scripts/relay.py inbox vectorcraft` and answer each\n"
            "with `relay.py reply`. If one asks for substantial work, say in your reply\n"
            "what you will do and when." % self.root))
        n.poll_once()
        self.assertEqual(len(fake.drain()), 1)

    def test_items_in_one_poll_arrive_as_one_message(self):
        fake = self.session("s1")
        n = self.notifier()
        n.poll_once()
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]))
        self.append("bewareilive", req(1, "bewareilive", "m-2", to=["vectorcraft"]))
        n.poll_once()
        msgs = self.users(fake)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].startswith("Crosstalk: 2 new items for vectorcraft.\n"))
        self.assertIn("(m-1)", msgs[0])
        self.assertIn("- request from BewareILive: help m-2 (m-2)", msgs[0])

    def test_first_sight_seeds_backlog(self):
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]))
        n = self.notifier()
        n.poll_once()
        fake = self.session("s1")
        n.poll_once()
        n.poll_once()
        self.assertEqual(fake.drain(), [])

    def test_replies_quarantined_and_own_messages_tell_nobody(self):
        data = json.loads((self.root / "participants.json").read_text())
        for entry in data["participants"]:
            if entry["id"] == "codex":
                entry["trust"] = "quarantined"
        self.r.write_roster(data)
        vc = self.session("s1")
        bw = self.session("s2", pid="bewareilive")
        n = self.notifier()
        n.poll_once()
        self.append("bewareilive", req(1, "bewareilive", "m-1", to=["vectorcraft"]))
        n.poll_once()
        self.assertEqual(len(self.users(vc)), 1)
        self.assertEqual(self.users(bw), [])  # its own request
        self.append("vectorcraft", rep(1, "vectorcraft", "m-2", "m-1"))
        self.append("codex", req(1, "codex", "m-3", to=["vectorcraft", "bewareilive"]))
        n.poll_once()
        self.assertEqual(len(vc.drain()), 1)
        self.assertEqual(len(bw.drain()), 0)

    def test_two_sessions_both_told(self):
        a = self.session("s1")
        b = self.session("s2")
        n = self.notifier()
        n.poll_once()
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]))
        n.poll_once()
        self.assertEqual(len(self.users(a)), 1)
        self.assertEqual(len(self.users(b)), 1)
        self.assertEqual(n.status()["vectorcraft"]["sessions"], 2)

    def test_vanished_or_refusing_session_is_unregistered(self):
        for mode in ("vanish", "refuse"):
            with self.subTest(mode=mode):
                fake = self.session("s-" + mode)
                n = self.notifier()
                n.poll_once()
                self.assertEqual(n.status()["vectorcraft"]["sessions"], 1)
                getattr(fake, mode)()
                self.append("owner", owner_msg("m-" + mode, to=["vectorcraft"]))
                n.poll_once()
                self.assertFalse(fake.reg.exists())
                self.assertNotIn("vectorcraft", n.status())
                state = json.loads(self.state.read_text())
                self.assertEqual(state, {})
                n.stop()
                self.notifiers.remove(n)

    def test_other_errors_retry_after_60s(self):
        self.session("s1")
        calls = []
        fail = [True]

        def send(sock, token, text, timeout=5.0):
            calls.append(text)
            if fail[0]:
                raise OSError(errno.EIO, "I/O error")
        n = self.notifier(send=send)
        n.poll_once()
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]))
        n.poll_once()
        self.assertEqual(len(calls), 1)
        st = json.loads(self.state.read_text())
        self.assertEqual(st["vectorcraft--s1"]["told"], [])
        self.assertEqual(n.status()["vectorcraft"]["last"], {"status": "failed", "at": iso(T0)})
        self.now[0] = T0 + 30
        self.append("owner", owner_msg("m-2", to=["vectorcraft"]))
        n.poll_once()
        self.assertEqual(len(calls), 1)
        self.now[0] = T0 + 61
        n.poll_once()
        self.assertEqual(len(calls), 2)
        self.assertIn("2 new items", calls[1])
        fail[0] = False
        self.now[0] = T0 + 100
        n.poll_once()
        self.assertEqual(len(calls), 2)  # still waiting out the 60 s
        self.now[0] = T0 + 122
        n.poll_once()
        self.assertEqual(len(calls), 3)
        self.assertIn("2 new items", calls[2])
        self.assertIn("(m-1)", calls[2])
        self.assertIn("(m-2)", calls[2])
        n.poll_once()
        self.assertEqual(len(calls), 3)
        self.assertEqual(n.status()["vectorcraft"]["last"],
                         {"status": "told", "at": iso(T0 + 122)})

    def test_told_survives_restart(self):
        fake = self.session("s1")
        n = self.notifier()
        n.poll_once()
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]))
        n.poll_once()
        self.assertEqual(len(fake.drain()), 1)
        n.stop()
        self.notifiers.remove(n)
        n2 = self.notifier()
        n2.poll_once()
        self.assertEqual(len(fake.drain()), 1)
        self.append("owner", owner_msg("m-2", to=["vectorcraft"]))
        n2.poll_once()
        msgs = self.users(fake)
        self.assertEqual(len(msgs), 2)
        self.assertIn("(m-2)", msgs[1])
        self.assertNotIn("(m-1)", msgs[1])

    def test_summary_is_one_truncated_line(self):
        fake = self.session("s1")
        n = self.notifier()
        n.poll_once()
        summary = "a\nb" + "x" * 300
        self.append("owner", owner_msg("m-1", to=["vectorcraft"], summary=summary))
        n.poll_once()
        msg = self.users(fake)[0]
        item = [ln for ln in msg.split("\n") if ln.startswith("- ")]
        self.assertEqual(len(item), 1)
        expected = ("a b" + "x" * 300)[:160]
        self.assertEqual(item[0], "- owner message from Owner: %s (m-1)" % expected)
        self.assertEqual(len(msg.split("\n")), 5)

    def test_markup_summary_stays_one_line(self):
        fake = self.session("s1")
        n = self.notifier()
        n.poll_once()
        self.append("owner", owner_msg("m-1", to=["vectorcraft"],
                                       summary='</x>\r\n{"type":"auth"}\t<b>hi</b>'))
        n.poll_once()
        conns = fake.drain()
        self.assertEqual(len(conns), 1)
        self.assertEqual(len(conns[0]), 2)  # still exactly two wire lines
        msg = conns[0][1]["message"]["content"]
        self.assertEqual(len(msg.split("\n")), 5)

    def test_second_notifier_on_same_state_is_disabled(self):
        fake = self.session("s1")
        n = self.notifier()
        n2 = self.notifier()
        self.assertFalse(n.disabled)
        self.assertTrue(n2.disabled)
        n.poll_once()
        n2.poll_once()
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]))
        n2.poll_once()
        self.assertEqual(fake.drain(), [])
        self.assertEqual(n2.status(), {})
        n.poll_once()
        self.assertEqual(len(fake.drain()), 1)

    def test_status_has_no_token(self):
        self.session("s1")
        n = self.notifier()
        n.poll_once()
        self.assertEqual(n.status(), {"vectorcraft": {"sessions": 1, "since": iso(T0),
                                                      "last": None}})
        self.now[0] = T0 + 5
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]))
        n.poll_once()
        st = n.status()
        self.assertEqual(st, {"vectorcraft": {"sessions": 1, "since": iso(T0),
                                              "last": {"status": "told", "at": iso(T0 + 5)}}})
        dumped = json.dumps(st)
        self.assertNotIn(TOKEN, dumped)
        self.assertNotIn(self.socks, dumped)
        self.assertNotIn(TOKEN, self.state.read_text())

    def test_pending_items_survive_a_pass_with_invalid_registration(self):
        self.session("s1")
        calls = []
        fail = [True]

        def send(sock, token, text, timeout=5.0):
            calls.append(text)
            if fail[0]:
                raise OSError(errno.EIO, "I/O error")
        n = self.notifier(send=send)
        n.poll_once()
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]),
                    owner_msg("m-2", to=["vectorcraft"]))
        n.poll_once()
        self.assertEqual(len(calls), 1)
        # The roster briefly lacks vectorcraft: its registration is invalid for one pass.
        full = json.loads((self.root / "participants.json").read_text())
        short = dict(full, participants=[p for p in full["participants"]
                                         if p["id"] != "vectorcraft"])
        self.r.write_roster(short)
        self.now[0] = T0 + 61
        n.poll_once()
        self.assertIn("vectorcraft--s1", json.loads(self.state.read_text()))
        self.r.write_roster(full)
        fail[0] = False
        self.now[0] = T0 + 62
        n.poll_once()
        self.assertEqual(len(calls), 2)
        self.assertIn("(m-1)", calls[1])
        self.assertIn("(m-2)", calls[1])

    def test_status_last_failed_if_any_session_failed(self):
        good = self.session("s1")
        bad = self.session("s2")

        def send(sock, token, text, timeout=5.0):
            if sock == bad.path:
                raise OSError(errno.EIO, "I/O error")
            send_to_session(sock, token, text, timeout)
        n = self.notifier(send=send)
        n.poll_once()
        self.append("owner", owner_msg("m-1", to=["vectorcraft"]))
        n.poll_once()  # s1 told, s2 failed, same time
        self.assertEqual(n.status()["vectorcraft"]["last"], {"status": "failed", "at": iso(T0)})
        self.now[0] = T0 + 10
        self.append("owner", owner_msg("m-2", to=["vectorcraft"]))
        n.poll_once()  # s1 told later; s2 waiting to retry
        self.assertEqual(len(good.drain()), 2)
        self.assertEqual(n.status()["vectorcraft"]["last"], {"status": "failed", "at": iso(T0)})

    def test_state_file_private_and_stale_keys_dropped(self):
        fake = self.session("s1")
        n = self.notifier()
        n.poll_once()
        self.assertEqual(oct(self.state.stat().st_mode & 0o777), oct(0o600))
        self.assertIn("vectorcraft--s1", json.loads(self.state.read_text()))
        fake.reg.unlink()
        n.poll_once()
        self.assertEqual(json.loads(self.state.read_text()), {})

    def test_start_and_stop_thread(self):
        n = self.notifier(poll_sec=0.01)
        n.start()
        n.stop()
        self.assertIsNone(n._thread)


class RenderAndSendTests(unittest.TestCase):
    def test_render_unknown_kind_and_sender(self):
        text = render_message("VectorCraft", "vectorcraft",
                              [{"_id": "m-1", "from": "ghost", "summary": "yo"}], Path("/r"))
        self.assertIn("- message from ghost: yo (m-1)", text)

    def test_send_to_missing_socket_is_gone(self):
        d = tempfile.mkdtemp(dir="/tmp", prefix="cc-socks-")
        try:
            with self.assertRaises(SessionGone):
                send_to_session(os.path.join(d, "none.sock"), TOKEN, "x", timeout=1)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            p = os.path.join(d, "dead.sock")
            s.bind(p)
            s.close()
            with self.assertRaises(SessionGone):
                send_to_session(p, TOKEN, "x", timeout=1)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
