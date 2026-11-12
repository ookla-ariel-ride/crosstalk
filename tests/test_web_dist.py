"""The built v2 page in web/dist/ matches its source: this fails when a source
file of web/ (see the hash rule below) changes and nobody ran `npm run build`
in web/."""
import hashlib
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"

# The build hash. web/scripts/build-hash.mjs computes the same value; keep the
# two rules identical.
# 1. Inputs: web/package.json, web/package-lock.json, web/index.html,
#    web/vite.config.ts, web/tsconfig.json, and every file under web/src/ and
#    web/public/ (when it exists), skipping any file or folder whose name
#    starts with ".".
# 2. Each path is relative to web/ with "/" between parts (src/ui/tokens.css).
# 3. Order: by the UTF-8 bytes of the path, smallest first.
# 4. Content: the file's bytes with every CRLF replaced by LF.
# 5. For each file, feed SHA-256 with: the path in UTF-8, one 0x00 byte, the
#    content length in bytes as ASCII decimal digits, one 0x00 byte, the content.
# 6. The hash is the hex digest. web/dist/.build-hash holds it and a newline.
HASH_ROOTS = ("package.json", "package-lock.json", "index.html", "vite.config.ts", "tsconfig.json")
HASH_DIRS = ("src", "public")


def hash_inputs(web):
    paths = [name for name in HASH_ROOTS if (web / name).is_file()]
    for top in HASH_DIRS:
        if not (web / top).is_dir():
            continue
        for p in (web / top).rglob("*"):
            rel = p.relative_to(web)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts):
                paths.append("/".join(rel.parts))
    return sorted(paths, key=lambda s: s.encode("utf-8"))


def build_hash(web):
    h = hashlib.sha256()
    for rel in hash_inputs(web):
        data = (web / rel).read_bytes().replace(b"\r\n", b"\n")
        h.update(rel.encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    return h.hexdigest()


class BuildHashTests(unittest.TestCase):
    def make_web(self, root):
        (root / "src" / "ui").mkdir(parents=True)
        (root / "package.json").write_bytes(b"{}\n")
        (root / "package-lock.json").write_bytes(b"{}\n")
        (root / "src" / "main.tsx").write_bytes(b"a\nb\n")
        (root / "src" / "ui" / "tokens.css").write_bytes(b":root {}\n")
        (root / "public").mkdir()
        (root / "public" / "robots.txt").write_bytes(b"x\n")
        (root / "index.html").write_bytes(b"<!doctype html>\n")
        (root / "vite.config.ts").write_bytes(b"export default {};\n")
        (root / "tsconfig.json").write_bytes(b"{}\n")
        return root

    def test_hash_follows_content_and_not_line_endings(self):
        with tempfile.TemporaryDirectory() as d:
            web = self.make_web(Path(d))
            first = build_hash(web)
            (web / "src" / "main.tsx").write_bytes(b"a\r\nb\r\n")
            self.assertEqual(build_hash(web), first)
            (web / "src" / ".DS_Store").write_bytes(b"x")
            self.assertEqual(build_hash(web), first)
            (web / "src" / "main.tsx").write_bytes(b"a\nc\n")
            self.assertNotEqual(build_hash(web), first)

    def test_hash_inputs_are_sorted_relative_paths(self):
        with tempfile.TemporaryDirectory() as d:
            web = self.make_web(Path(d))
            self.assertEqual(hash_inputs(web),
                             ["index.html", "package-lock.json", "package.json", "public/robots.txt",
                              "src/main.tsx", "src/ui/tokens.css", "tsconfig.json", "vite.config.ts"])

    def test_page_and_build_config_count(self):
        with tempfile.TemporaryDirectory() as d:
            web = self.make_web(Path(d))
            first = build_hash(web)
            for rel in ("index.html", "vite.config.ts", "tsconfig.json", "public/robots.txt"):
                old = (web / rel).read_bytes()
                (web / rel).write_bytes(old + b"// changed\n")
                self.assertNotEqual(build_hash(web), first, rel)
                (web / rel).write_bytes(old)
            self.assertEqual(build_hash(web), first)

    def test_known_hash(self):
        # The same tree gives this value in web/scripts/build-hash.test.mjs.
        with tempfile.TemporaryDirectory() as d:
            web = self.make_web(Path(d))
            self.assertEqual(build_hash(web), KNOWN_HASH)


# The hash of the tree that make_web() writes.
KNOWN_HASH = "4c2c071f28ee37a37c894fa1bbc27c5e760eb77047cd6c549394cdb574b4fbe7"


@unittest.skipUnless(WEB.is_dir(), "no web/ folder")
class WebDistTests(unittest.TestCase):
    def test_dist_matches_source(self):
        mark = WEB / "dist" / ".build-hash"
        self.assertTrue(mark.is_file(), "web/dist/.build-hash is missing: run `npm run build` in web/")
        self.assertEqual(mark.read_text(encoding="ascii").strip(), build_hash(WEB),
                         "web/dist/ is older than its source: run `npm run build` in web/ and commit web/dist/")

    def test_the_built_page_names_its_build(self):
        # The page compares it with /api/vitals page_build from its first render.
        page = (WEB / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="crosstalk-build" content="%s"' % build_hash(WEB), page)


if __name__ == "__main__":
    unittest.main()
