import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from crosstalk import inboxstate  # noqa: E402

T1 = "2026-11-11T22:17:00Z"
T2 = "2026-11-11T22:18:00Z"

# Each child process moves one participant's place 50 times, with rising times.
CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from crosstalk import inboxstate
for i in range(50):
    inboxstate.move(Path(sys.argv[2]), sys.argv[3], {"t": "2026-11-11T22:%02d:%02dZ" % (i // 60, i % 60), "seen": ["x"]})
"""


class InboxStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_is_empty(self):
        self.assertFalse((self.root / inboxstate.FILE).exists())
        self.assertEqual(inboxstate.load(self.root), {})

    def test_broken_file_reads_as_empty_and_stays_until_a_write(self):
        path = self.root / inboxstate.FILE
        for raw in ("{not json", "[1, 2]", '{"alpha": {"t": "yesterday", "seen": []}}',
                    '{"alpha": {"t": "%s", "seen": ["", 3]}}' % T1):
            path.write_text(raw, encoding="utf-8")
            self.assertEqual(inboxstate.load(self.root), {}, raw)
            self.assertEqual(path.read_text(encoding="utf-8"), raw)
        place = {"t": T1, "seen": ["beta-0042"]}
        self.assertEqual(inboxstate.move(self.root, "alpha", place), place)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"alpha": place})

    def test_clean_place_sorts_and_drops_repeats(self):
        self.assertEqual(inboxstate.clean_place({"t": T1, "seen": ["b", "a", "b"]}), {"t": T1, "seen": ["a", "b"]})
        self.assertIsNone(inboxstate.clean_place({"t": T1}))
        self.assertIsNone(inboxstate.clean_place("x"))

    def test_move_never_goes_back(self):
        later = {"t": T2, "seen": ["b"]}
        inboxstate.move(self.root, "alpha", later)
        self.assertEqual(inboxstate.move(self.root, "alpha", {"t": T1, "seen": ["a"]}), later)
        self.assertEqual(inboxstate.load(self.root), {"alpha": later})

    def test_equal_times_join_seen(self):
        inboxstate.move(self.root, "alpha", {"t": T1, "seen": ["c", "a"]})
        stored = inboxstate.move(self.root, "alpha", {"t": T1, "seen": ["b", "a"]})
        self.assertEqual(stored, {"t": T1, "seen": ["a", "b", "c"]})
        self.assertEqual(inboxstate.later(None, None), None)
        self.assertEqual(inboxstate.later(None, stored), stored)

    def test_is_new_rules(self):
        place = {"t": T1, "seen": ["beta-0042", "gamma-0001"]}
        self.assertTrue(inboxstate.is_new(T1, "x", None))
        self.assertFalse(inboxstate.is_new(T1, "beta-0042", place))
        self.assertTrue(inboxstate.is_new(T1, "delta-0001", place))
        self.assertTrue(inboxstate.is_new(T1, "a-0001", place))  # sorts before every id in seen
        self.assertTrue(inboxstate.is_new(T2, "beta-0042", place))
        self.assertFalse(inboxstate.is_new("2026-11-11T22:16:59Z", "zz", place))

    def test_newest_takes_every_id_of_the_last_second(self):
        self.assertIsNone(inboxstate.newest({}))
        times = {("messages", "b-1"): T2, ("requests", "a-1"): T1, ("acks", "c-1"): T2}
        self.assertEqual(inboxstate.newest(times), {"t": T2, "seen": ["b-1", "c-1"]})

    def test_two_processes_keep_both_places(self):
        procs = [subprocess.Popen([sys.executable, "-c", CHILD, str(SCRIPTS), str(self.root), pid])
                 for pid in ("alpha", "beta")]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        last = {"t": "2026-11-11T22:00:49Z", "seen": ["x"]}
        self.assertEqual(inboxstate.load(self.root), {"alpha": last, "beta": last})


if __name__ == "__main__":
    unittest.main()
