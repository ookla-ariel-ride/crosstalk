"""The static export: one self-contained HTML file with the log inlined.

The exported page opens from disk with no server and no sibling files: the
replay travels inside the HTML as JSON in a <script> block, because fetch on
file:// is blocked in several browsers.
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TempRepo, line

from crosstalk.exporter import build_snapshot, export_page
from crosstalk.participants import load_roster
from crosstalk.replay import load_all_lines, replay

REPO = Path(__file__).resolve().parent.parent

SNAPSHOT_RE = re.compile(
    r'<script id="crosstalk-snapshot" type="application/json">(.*?)</script>',
    re.S)


def snapshot_in(html):
    """The JSON the exported page carries."""
    m = SNAPSHOT_RE.search(html)
    assert m, "no inlined snapshot in the export"
    return json.loads(m.group(1))


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        shutil.copy(str(REPO / "index.html"), str(self.r.root / "index.html"))

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def test_export_writes_one_self_contained_file(self):
        r = self.r
        r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
        out = r.root / "page.html"
        info = export_page(r.root, out)
        self.assertTrue(out.is_file(), "no export written")
        html = out.read_text(encoding="utf-8")
        self.assertIn("<title>Crosstalk</title>", html)
        # The page's own code is in there, and the snapshot travels with it.
        # The export injects the seam; the retired claude shim stays out.
        self.assertIn("window.__crosstalkDataLayer", html)
        self.assertNotIn("window.claude", html)
        self.assertIn('id="crosstalk-snapshot"', html)
        self.assertNotIn("data.json", html)
        self.assertEqual(info["documents"], 1)

    def test_snapshot_document_count_equals_the_replay(self):
        r = self.r
        r.write_log("codex", [line(1, "codex"),
                              line(2, "codex", collection="waiting", id_="codex-w1",
                                   data={"summary": "w"})])
        r.write_log("bewareilive", [line(1, "bewareilive", collection="messages",
                                         id_="m-0001", data={"summary": "old"})])
        snap = build_snapshot(r.root)
        count = sum(len(v) for v in snap["collections"].values())
        roster = load_roster(r.root / "participants.json")
        self.assertEqual(count, len(replay(load_all_lines(r.root, roster))))
        self.assertEqual(snap["collections"]["messages"]["m-0001"]["summary"], "old")

    def test_export_snapshot_enriches_messages(self):
        # data.from and data.t may be absent from a message row; the export
        # fills both from the writing line, like the live snapshot does.
        r = self.r
        r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
        snap = build_snapshot(r.root)
        doc = snap["collections"]["messages"]["codex-0001"]
        self.assertEqual(doc["from"], "codex")
        self.assertEqual(doc["t"], "2026-10-21T18:00:00Z")

    def test_export_stamps_the_commit_and_the_publish_time(self):
        r = self.r
        out = r.root / "page.html"
        export_page(r.root, out, commit="abc1234", published="2026-10-24T12:00:00Z")
        html = out.read_text(encoding="utf-8")
        self.assertNotIn("__COMMIT__", html)
        self.assertNotIn("__PUBLISHED__", html)
        m = re.search(r'var BUILD = \{ commit: "([^"]*)", published: "([^"]*)" \};', html)
        self.assertIsNotNone(m, html[:400])
        self.assertEqual(m.group(1), "abc1234")
        self.assertEqual(m.group(2), "2026-10-24T12:00:00Z")

    def test_export_declares_its_encoding(self):
        # The export carries UTF-8 (the page's own "·" and any non-ASCII message
        # body). A file opened from disk with no charset is read as windows-1252
        # by some browsers, which mangles both.
        r = self.r
        r.write_log("codex", [line(1, "codex", data={"summary": "café — naïve"})])
        out = r.root / "page.html"
        export_page(r.root, out)
        html = out.read_text(encoding="utf-8")
        self.assertIn('<meta charset="utf-8">', html.lower())
        self.assertLess(html.lower().index("charset"), 1024)

    def test_export_leaves_index_html_alone(self):
        r = self.r
        before = (r.root / "index.html").read_bytes()
        export_page(r.root, r.root / "page.html")
        self.assertEqual((r.root / "index.html").read_bytes(), before)

    def test_export_refuses_to_write_through_a_symlink(self):
        r = self.r
        r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
        target = r.root / "target.html"
        target.write_text("precious", encoding="utf-8")
        link = r.root / "page.html"
        link.symlink_to(target)
        with self.assertRaises(RuntimeError):
            export_page(r.root, link)
        self.assertEqual(target.read_text(encoding="utf-8"), "precious")

    def test_export_carries_no_token_pin_or_cookie_material(self):
        r = self.r
        (r.root / "tokens.json").write_text(
            json.dumps({"codex": "s3cr3t-token-value"}), encoding="utf-8")
        (r.root / "pin").write_text("918273", encoding="utf-8")
        r.write_log("codex", [line(1, "codex")])
        out = r.root / "page.html"
        export_page(r.root, out)
        html = out.read_text(encoding="utf-8")
        for secret in ("s3cr3t-token-value", "918273"):
            self.assertNotIn(secret, html)
        for marker in ("Authorization", "Bearer ", "Set-Cookie", "Cookie:"):
            self.assertNotIn(marker, html)

    def test_export_strips_wake_from_the_roster(self):
        r = self.r
        data = json.loads((r.root / "participants.json").read_text(encoding="utf-8"))
        secret_cwd = str(r.root / "secret-agent-cwd")
        (r.root / "secret-agent-cwd").mkdir()
        for p in data["participants"]:
            if p["id"] == "vectorcraft":
                p["wake"] = {"command": ["secret-agent-cmd"], "cwd": secret_cwd}
        r.write_roster(data)
        snap = build_snapshot(r.root)
        entry = [p for p in snap["roster"]["participants"] if p["id"] == "vectorcraft"][0]
        self.assertNotIn("wake", entry)
        self.assertEqual(entry["name"], "VectorCraft")
        out = r.root / "page.html"
        export_page(r.root, out)
        html = out.read_text(encoding="utf-8")
        self.assertNotIn("secret-agent-cmd", html)
        self.assertNotIn("secret-agent-cwd", html)

    def test_export_refuses_a_log_with_a_fatal_line(self):
        r = self.r
        r.write_log("codex", [line(1, "codex"), "{not json"])
        with self.assertRaises(RuntimeError) as cm:
            export_page(r.root, r.root / "page.html")
        self.assertIn("log/codex.jsonl:2:", str(cm.exception))
        self.assertFalse((r.root / "page.html").exists())

    def test_export_embeds_a_json_object_the_page_can_read(self):
        r = self.r
        r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
        out = r.root / "page.html"
        export_page(r.root, out)
        snap = snapshot_in(out.read_text(encoding="utf-8"))
        self.assertIn("collections", snap)
        self.assertIn("codex-0001", snap["collections"]["messages"])
        self.assertEqual(snap["collections"]["messages"]["codex-0001"]["summary"], "hi")
        # The roster travels too, so the page can name people.
        self.assertEqual(snap["roster"]["participants"][0]["id"], "bewareilive")


class CliExportTests(unittest.TestCase):
    """relay.py export-page <file>"""

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        shutil.copytree(str(REPO / "scripts"), str(self.r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(str(REPO / "index.html"), str(self.r.root / "index.html"))
        self.r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])

    def tearDown(self):
        self.r.__exit__(None, None, None)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.r.root / "scripts" / "relay.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def test_export_page_writes_the_file(self):
        out = self.r.root / "page.html"
        p = self.run_cli("export-page", str(out))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(out.is_file())
        self.assertIn("1 documents", p.stdout)
        self.assertIn('id="crosstalk-snapshot"', out.read_text(encoding="utf-8"))

    def test_export_page_reports_a_broken_log(self):
        self.r.write_log("codex", [line(1, "codex"), "{not json"])
        p = self.run_cli("export-page", str(self.r.root / "page.html"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("log/codex.jsonl:2:", p.stderr)


if __name__ == "__main__":
    unittest.main()
