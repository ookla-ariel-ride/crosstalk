import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from crosstalk import inboxstate  # noqa: E402
from crosstalk.logs import Line  # noqa: E402

T1 = "2026-11-11T22:17:00Z"
T2 = "2026-11-11T22:18:00Z"

# Each child process moves one participant's place 50 times, with rising numbers.
CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from crosstalk import inboxstate
for i in range(1, 51):
    inboxstate.move(Path(sys.argv[2]), sys.argv[3], {"beta": i})
"""


def ln(frm, seq, t=T1, id_=None):
    return Line(seq=seq, t=t, from_=frm, op="set", collection="messages",
                id=id_ or "%s-%04d" % (frm, seq))


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
        for raw in ("{not json", "[1, 2]", '{"alpha": {"beta": "3"}}', '{"alpha": {"beta": 0}}',
                    '{"alpha": {"beta": true}}', '{"alpha": {"Beta": 3}}', '{"alpha": [3]}',
                    '{"alpha": {"t": "yesterday", "seen": []}}'):
            path.write_text(raw, encoding="utf-8")
            self.assertEqual(inboxstate.load(self.root, []), {}, raw)
            self.assertEqual(path.read_text(encoding="utf-8"), raw)
        place = {"beta": 42}
        self.assertEqual(inboxstate.move(self.root, "alpha", place), place)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"alpha": place})

    def test_clean_place_takes_one_whole_number_per_writer(self):
        self.assertEqual(inboxstate.clean_place({"beta": 3, "gamma": 1}), {"beta": 3, "gamma": 1})
        self.assertEqual(inboxstate.clean_place({}), {})
        for bad in ("x", [], {"beta": 0}, {"beta": -1}, {"beta": 1.5}, {"beta": False},
                    {"": 1}, {"Beta": 1}, {"t": T1, "seen": []}):
            self.assertIsNone(inboxstate.clean_place(bad), bad)

    def test_old_form_needs_the_exact_log_time_form(self):
        # An old-form `t` without zero padding sorts wrongly as a string, so it reads as empty.
        for t in ("2026-11-1T22:17:00Z", "2026-1-11T22:17:00Z", "2026-11-11T2:17:00Z",
                  "2026-11-11T22:7:00Z", "2026-11-11T22:17:0Z"):
            (self.root / inboxstate.FILE).write_text(
                json.dumps({"alpha": {"t": t, "seen": []}}), encoding="utf-8")
            self.assertEqual(inboxstate.load(self.root, [ln("beta", 1)]), {}, t)

    def test_old_form_without_the_log_reads_as_empty(self):
        (self.root / inboxstate.FILE).write_text(
            json.dumps({"alpha": {"t": T1, "seen": []}}), encoding="utf-8")
        self.assertEqual(inboxstate.load(self.root), {})

    def test_convert_stops_at_the_first_line_the_old_place_did_not_cover(self):
        old = {"t": T1, "seen": ["beta-0002"]}
        lines = [ln("beta", 1, "2026-11-11T22:16:00Z"), ln("beta", 2, T1), ln("beta", 3, T1),
                 ln("beta", 4, "2026-11-11T22:10:00Z"), ln("gamma", 1, T2), ln("gamma", 2, T1)]
        self.assertEqual(inboxstate.convert(old, lines), {"beta": 2})

    def test_deeply_nested_file_reads_as_empty(self):
        (self.root / inboxstate.FILE).write_text("[" * 200000, encoding="utf-8")
        self.assertEqual(inboxstate.load(self.root), {})

    def test_move_never_goes_back(self):
        inboxstate.move(self.root, "alpha", {"beta": 5})
        self.assertEqual(inboxstate.move(self.root, "alpha", {"beta": 3}), {"beta": 5})
        self.assertEqual(inboxstate.load(self.root), {"alpha": {"beta": 5}})

    def test_merge_takes_the_higher_number_of_each_writer(self):
        inboxstate.move(self.root, "alpha", {"beta": 5, "gamma": 1})
        stored = inboxstate.move(self.root, "alpha", {"beta": 3, "gamma": 4, "owner": 2})
        self.assertEqual(stored, {"beta": 5, "gamma": 4, "owner": 2})
        self.assertEqual(inboxstate.merge(None, None), None)
        self.assertEqual(inboxstate.merge(None, stored), stored)

    def test_is_new_rules(self):
        place = {"beta": 4}
        self.assertTrue(inboxstate.is_new(ln("beta", 1), None))
        self.assertFalse(inboxstate.is_new(ln("beta", 4), place))
        self.assertTrue(inboxstate.is_new(ln("beta", 5), place))
        self.assertTrue(inboxstate.is_new(ln("gamma", 1), place))
        # The line time plays no part.
        self.assertTrue(inboxstate.is_new(ln("beta", 5, "2020-01-01T00:00:00Z"), place))

    def test_newest_takes_the_highest_number_of_each_writer(self):
        self.assertIsNone(inboxstate.newest([]))
        lines = [ln("beta", 1, T2), ln("beta", 2, T1), ln("gamma", 1, T1)]
        self.assertEqual(inboxstate.newest(lines), {"beta": 2, "gamma": 1})

    def test_two_processes_keep_both_places(self):
        procs = [subprocess.Popen([sys.executable, "-c", CHILD, str(SCRIPTS), str(self.root), pid])
                 for pid in ("alpha", "beta")]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        self.assertEqual(inboxstate.load(self.root), {"alpha": {"beta": 50}, "beta": {"beta": 50}})


if __name__ == "__main__":
    unittest.main()
