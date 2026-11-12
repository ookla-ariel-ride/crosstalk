#!/usr/bin/env python3
"""Render the launchd plist template with the repo and home paths substituted.

A line that holds only `__DATA_ROOT_ARGS__` becomes two `<string>` arguments,
`--data-root` and the data folder, when a data root is given; without one the
line goes, so the plist is what it was before the data root existed.

The paths are XML-escaped, so any path (one with `&`, `<`, `>`, quotes) yields a
well-formed plist that still names the literal path. This replaces the old sed
substitution, which corrupted the plist when a path contained `&` (sed's
replacement metacharacter) or XML-special characters.
"""
import re
import sys
from pathlib import Path


def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def render(template: Path, repo: str, home: str, data: str = "") -> str:
    text = Path(template).read_text(encoding="utf-8")
    lines = []
    for ln in text.splitlines(True):
        if ln.strip() != "__DATA_ROOT_ARGS__":
            lines.append(ln)
        elif data:
            indent = ln[:len(ln) - len(ln.lstrip())]
            lines.append("%s<string>--data-root</string>\n%s<string>__DATA__</string>\n" % (indent, indent))
    # One pass, so a path that holds a placeholder's name stays as it is.
    values = {"REPO": repo, "HOME": home, "DATA": data}
    return re.sub(r"__(REPO|HOME|DATA)__", lambda m: xml_escape(values[m.group(1)]), "".join(lines))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (4, 5):
        print("usage: render_plist.py TEMPLATE DEST REPO HOME [DATA]", file=sys.stderr)
        return 2
    template, dest, repo, home = args[:4]
    data = args[4] if len(args) == 5 else ""
    Path(dest).write_text(render(Path(template), repo, home, data), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
