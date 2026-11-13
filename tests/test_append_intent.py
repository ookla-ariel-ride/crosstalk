"""The append intent: a torn last line from a writer that died is repaired, never guessed at.

The sidecar `log/<pid>.inflight.json` holds {"offset", "intended_hex"}: the
file size before the write and the exact bytes of the line. Every writer of
the log (relay.py, the server, a project's check-in tool) writes it under the
log's flock before the line and removes it after the line's fsync.
"""
import json
import os
import unittest
from unittest import mock

from helpers import TempRepo, line
from crosstalk import logs
from crosstalk.logs import append_line, check_all, read_log
from crosstalk.participants import load_roster

T = "2026-10-21T18:00:00Z"
BODY = {"body": "café ✓ done"}  # multibyte, so some cuts split a character


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.log = self.r.write_log("rev", [line(1, "rev")])
        self.first = self.log.read_bytes()
        self.sidecar = self.r.root / "log" / "rev.inflight.json"
        # The exact bytes append_line writes as line 2 with these arguments.
        append_line(self.r.root, "rev", "set", "messages", "rev-2", BODY, t=T)
        self.record = self.log.read_bytes()[len(self.first):]
        self.log.write_bytes(self.first)

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def append(self):
        return append_line(self.r.root, "rev", "set", "messages", "rev-2", BODY, t=T)

    def intent(self, offset, intended):
        self.sidecar.write_text(json.dumps({"offset": offset, "intended_hex": intended.hex()}),
                                encoding="utf-8")

    def refused(self, content):
        with self.assertRaises(ValueError) as e:
            self.append()
        msg = str(e.exception)
        self.assertIn("unterminated record", msg)
        self.assertIn(str(self.log), msg)
        self.assertNotIn("Expecting", msg)  # not a JSON decoder message
        self.assertEqual(self.log.read_bytes(), content)  # nothing truncated
        return msg

    def test_a_normal_append_leaves_no_sidecar(self):
        self.assertEqual(self.append().seq, 2)
        self.assertFalse(self.sidecar.exists())
        self.assertEqual(read_log(self.log)[1], [])

    def test_the_intent_is_durable_before_the_line_is_written(self):
        seen = []
        real = os.fsync

        def spy(fd):
            real(fd)
            if os.fstat(fd).st_ino == os.stat(str(self.log)).st_ino:
                seen.append((self.log.stat().st_size,
                             json.loads(self.sidecar.read_text()) if self.sidecar.exists() else None))

        with mock.patch.object(logs.os, "fsync", spy):
            self.append()
        size, sidecar = seen[0]
        self.assertEqual(size, len(self.first) + len(self.record))
        self.assertEqual(sidecar, {"offset": len(self.first), "intended_hex": self.record.hex()})
        self.assertFalse(self.sidecar.exists())

    def test_a_prefix_tail_is_repaired_at_every_cut_point(self):
        for cut in range(1, len(self.record)):
            with self.subTest(cut=cut):
                self.log.write_bytes(self.first + self.record[:cut])
                self.intent(len(self.first), self.record)
                self.assertEqual(self.append().seq, 2)
                self.assertEqual(self.log.read_bytes(), self.first + self.record)
                self.assertFalse(self.sidecar.exists())
                self.log.write_bytes(self.first)

    def test_a_tail_on_an_empty_log_is_repaired(self):
        self.log.write_bytes(b"")
        append_line(self.r.root, "rev", "set", "messages", "rev-2", BODY, t=T)
        rec = self.log.read_bytes()
        self.log.write_bytes(rec[:7])
        self.intent(0, rec)
        self.assertEqual(self.append().seq, 1)
        self.assertEqual(self.log.read_bytes(), rec)

    def test_a_foreign_offset_is_refused(self):
        # The intent names line 1 (complete); the tail after it is not its write.
        content = self.first + self.record[:9]
        self.log.write_bytes(content)
        self.intent(0, self.first)
        msg = self.refused(content)
        self.assertIn("byte %d" % len(self.first), msg)
        self.assertTrue(self.sidecar.exists())

    def test_an_offset_inside_the_tail_is_refused(self):
        content = self.first + self.record[:9]
        self.log.write_bytes(content)
        self.intent(len(self.first) + 2, self.record[2:])
        self.refused(content)

    def test_a_diverging_tail_is_refused(self):
        other = self.record.replace(b"done", b"gone")[:-3]
        content = self.first + other
        self.log.write_bytes(content)
        self.intent(len(self.first), self.record)
        self.refused(content)

    def test_a_tail_without_an_intent_is_refused(self):
        content = self.first + self.record[:5]
        self.log.write_bytes(content)
        self.refused(content)

    def test_an_unreadable_intent_is_refused_like_none(self):
        content = self.first + self.record[:5]
        self.log.write_bytes(content)
        self.sidecar.write_text("{torn", encoding="utf-8")
        self.refused(content)

    def test_a_published_intent_is_synced_before_it_is_cleared(self):
        # The writer died after its write but before its fsync and clear.
        self.log.write_bytes(self.first + self.record)
        self.intent(len(self.first), self.record)
        events = []
        real_fsync, real_unlink, real_replace = os.fsync, os.unlink, os.replace
        log_ino = os.stat(str(self.log)).st_ino

        def fsync(fd):
            real_fsync(fd)
            if os.fstat(fd).st_ino == log_ino:
                events.append("fsync log")

        def unlink(p, *a, **k):
            if str(p) == str(self.sidecar):
                events.append("clear")
            return real_unlink(p, *a, **k)

        def replace(src, dst, *a, **k):
            if str(dst) == str(self.sidecar):
                events.append("new intent")
            return real_replace(src, dst, *a, **k)

        with mock.patch.object(logs.os, "fsync", fsync), \
                mock.patch.object(logs.os, "unlink", unlink), \
                mock.patch.object(logs.os, "replace", replace):
            ln = append_line(self.r.root, "rev", "set", "messages", "rev-3", {}, t=T)
        self.assertEqual(ln.seq, 3)
        self.assertEqual(events[:2], ["fsync log", "clear"])
        self.assertEqual(self.log.read_bytes()[:len(self.first) + len(self.record)],
                         self.first + self.record)
        self.assertFalse(self.sidecar.exists())

    def test_a_stale_intent_never_touches_complete_lines(self):
        # File ends cleanly but does not hold the intent at its offset: the
        # write never started (offset at the end) or the intent is stale. The
        # intent goes; no published byte changes.
        for offset, intended in ((len(self.first), self.record),
                                 (0, self.record),
                                 (len(self.first) + 50, self.record)):
            with self.subTest(offset=offset):
                self.log.write_bytes(self.first)
                self.intent(offset, intended)
                self.assertEqual(self.append().seq, 2)
                self.assertEqual(self.log.read_bytes(), self.first + self.record)
                self.assertFalse(self.sidecar.exists())


class TornReadTests(unittest.TestCase):
    """Readers (check, replay) report a torn last line as a fatal problem; they do not raise."""

    def test_check_reports_a_torn_ascii_tail(self):
        with TempRepo() as r:
            path = r.write_log("rev", [line(1, "rev")])
            with open(str(path), "ab") as f:
                f.write(b'{"seq":2,"t":"20')
            (p,) = check_all(r.root, load_roster(r.write_roster()))
            self.assertEqual((p.file, p.line, p.fatal), ("log/rev.jsonl", 2, True))
            self.assertEqual(len(read_log(path)[0]), 1)

    def test_check_reports_a_torn_multibyte_tail(self):
        with TempRepo() as r:
            path = r.write_log("rev", [line(1, "rev")])
            with open(str(path), "ab") as f:
                f.write('{"seq":2,"data":{"b":"café'.encode("utf-8")[:-1])
            (p,) = check_all(r.root, load_roster(r.write_roster()))
            self.assertEqual((p.file, p.line, p.fatal), ("log/rev.jsonl", 2, True))
            self.assertEqual(len(read_log(path)[0]), 1)  # the replay keeps line 1


if __name__ == "__main__":
    unittest.main()
