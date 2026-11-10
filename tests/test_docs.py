"""The docs keep up with the code: these fail when a doc goes stale."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = sorted((REPO / "docs").glob("*.md"))
# docs/goal.md is a session's working note, not a document for readers.
READER_DOCS = [p for p in DOCS if p.name != "goal.md"]
PROSE = [REPO / "README.md", REPO / "PROTOCOL.md", REPO / "AGENTS.md"] + READER_DOCS


def text(path):
    return path.read_text(encoding="utf-8")


def commands():
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "relay.py"), "--help"],
                         capture_output=True, text=True, check=True).stdout
    return re.search(r"\{([a-z,-]+)\}", out).group(1).split(",")


class DocsTests(unittest.TestCase):
    def test_every_relay_command_is_in_the_docs(self):
        reference = text(REPO / "docs" / "reference.md")
        missing = [c for c in commands() if "`%s`" % c not in reference]
        self.assertEqual(missing, [], "add a row for each to the command table in docs/reference.md")

    def test_the_readme_links_every_doc(self):
        readme = text(REPO / "README.md")
        missing = [p.name for p in READER_DOCS if "docs/%s" % p.name not in readme]
        self.assertEqual(missing, [], "add each to the Documentation table in README.md")

    def test_every_plugin_folder_has_a_readme(self):
        folders = [d for parent in (REPO / "scripts").glob("*-plugin") for d in parent.iterdir() if d.is_dir()]
        self.assertTrue(folders)
        self.assertEqual([str(d.relative_to(REPO)) for d in folders if not (d / "README.md").exists()], [])

    def test_the_docs_hold_no_em_dash(self):
        plugin_readmes = list((REPO / "scripts").glob("*-plugin/*/README.md"))
        found = ["%s:%d" % (p.relative_to(REPO), n)
                 for p in PROSE + plugin_readmes
                 for n, line in enumerate(text(p).splitlines(), 1) if "—" in line]
        self.assertEqual(found, [], "rewrite each sentence without the em-dash")


if __name__ == "__main__":
    unittest.main()
