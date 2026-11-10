#!/bin/sh
# Writes tests/page/out/index.html: the fake db stub, the fixture tests/page/roster.json seeded as the
# `participants` collection, then the current index.html. Also copies stub.js and scenarios.js next to it.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
mkdir -p "$here/out"
{
  printf '<!doctype html><meta charset=utf-8>\n<script src="stub.js"></script>\n<script>__seed('
  python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    roster = json.load(f)
docs = {}
for p in roster["participants"]:
    d = dict(p)
    docs[d.pop("id")] = d
sys.stdout.write(json.dumps({"participants": docs}).replace("</", "<\\/"))
' "$here/roster.json"
  printf ');</script>\n'
  cat "$root/index.html"
} > "$here/out/index.html"
cp "$here/stub.js" "$here/scenarios.js" "$here/out/"
echo "wrote $here/out/index.html"
