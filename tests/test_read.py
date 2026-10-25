"""relay.py read: the replay as JSON on stdout, for any session that speaks JSON."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TempRepo, line

REPO = Path(__file__).resolve().parent.parent


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(self.r.root / "scripts" / "relay.py")

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def test_read_prints_the_replay_as_json(self):
        self.r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"}),
                                   line(2, "codex", collection="waiting", id_="codex-w1",
                                        data={"summary": "w", "since": "2026-10-21T00:00:00Z"})])
        p = self.run_cli("read")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(sorted(out["collections"]), ["messages", "waiting"])
        self.assertEqual(out["collections"]["messages"]["codex-0001"]["summary"], "hi")
        self.assertEqual(out["collections"]["waiting"]["codex-w1"]["summary"], "w")

    def test_read_collection_filter(self):
        self.r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"}),
                                   line(2, "codex", collection="waiting", id_="codex-w1",
                                        data={"summary": "w"})])
        p = self.run_cli("read", "--collection", "messages")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(list(out["collections"]), ["messages"])
        self.assertIn("codex-0001", out["collections"]["messages"])

    def test_read_filter_may_name_several_collections(self):
        self.r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"}),
                                   line(2, "codex", collection="waiting", id_="codex-w1",
                                        data={"summary": "w"}),
                                   line(3, "codex", collection="locks", id_="codex-l1",
                                        data={"path": "a.txt"})])
        p = self.run_cli("read", "--collection", "messages", "--collection", "locks")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(sorted(out["collections"]), ["locks", "messages"])

    def test_read_of_an_empty_log_is_an_empty_object(self):
        p = self.run_cli("read")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["collections"], {})
        self.assertEqual(out["service"]["name"], "Crosstalk")

    def test_read_exits_1_on_a_fatal_line(self):
        self.r.write_log("codex", [line(1, "codex"), "{not json"])
        p = self.run_cli("read")
        self.assertEqual(p.returncode, 1)
        self.assertIn("log/codex.jsonl:2:", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_read_exits_1_on_a_trust_violation(self):
        # A message whose data.from is not its writer is fatal (ruling R9).
        self.r.write_log("codex", [line(1, "codex", data={"from": "owner"})])
        p = self.run_cli("read")
        self.assertEqual(p.returncode, 1)
        self.assertIn("log/codex.jsonl:1:", p.stderr)

    def test_read_gives_the_documents_as_the_page_sees_them(self):
        self.r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
        p = self.run_cli("read")
        out = json.loads(p.stdout)
        doc = out["collections"]["messages"]["codex-0001"]
        self.assertEqual(doc["summary"], "hi")
        self.assertNotIn("data", doc)

    def test_read_carries_the_service_block(self):
        self.r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
        p = self.run_cli("read")
        self.assertEqual(p.returncode, 0, p.stderr)
        svc = json.loads(p.stdout)["service"]
        self.assertEqual(svc["name"], "Crosstalk")
        for key in ("purpose", "capabilities", "commands", "protocol"):
            self.assertIn(key, svc)


if __name__ == "__main__":
    unittest.main()
