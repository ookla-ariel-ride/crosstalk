#!/usr/bin/env python3
"""Render the launchd plist template with the repo and home paths substituted.

The paths are XML-escaped, so any path (one with `&`, `<`, `>`, quotes) yields a
well-formed plist that still names the literal path. This replaces the old sed
substitution, which corrupted the plist when a path contained `&` (sed's
replacement metacharacter) or XML-special characters.
"""
import sys
from pathlib import Path


def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def render(template: Path, repo: str, home: str) -> str:
    text = Path(template).read_text(encoding="utf-8")
    return text.replace("__REPO__", xml_escape(repo)).replace("__HOME__", xml_escape(home))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 4:
        print("usage: render_plist.py TEMPLATE DEST REPO HOME", file=sys.stderr)
        return 2
    template, dest, repo, home = args
    Path(dest).write_text(render(Path(template), repo, home), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
