"""The install-service.sh plist substitution must be safe for any path."""
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RENDER = REPO / "scripts" / "launchd" / "render_plist.py"
TEMPLATE = REPO / "scripts" / "launchd" / "crosstalk.plist"


class RenderPlistTests(unittest.TestCase):
    def test_a_path_with_xml_specials_yields_a_well_formed_plist(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.plist"
            repo = "/tmp/a&b<c>d"
            home = "/tmp/h&i"
            p = subprocess.run([sys.executable, str(RENDER), str(TEMPLATE), str(dest),
                                repo, home],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 0, p.stderr)
            tree = ET.parse(str(dest))  # raises if the plist is not well-formed
            strings = [e.text for e in tree.iter() if e.tag == "string"]
            self.assertIn(repo + "/scripts/relay.py", strings)
            self.assertIn(repo, strings)
            self.assertIn(home + "/Library/Logs/crosstalk.log", strings)


if __name__ == "__main__":
    unittest.main()
