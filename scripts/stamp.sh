#!/bin/sh
# Writes build/index.html: index.html with the commit it was published from
# and the publish time filled in. Refuses if index.html has unsaved changes,
# so the stamped hash always matches the code. Publish build/index.html with
# the Artifact tool, passing the page URL as `url`.
set -eu
cd "$(dirname "$0")/.."

if ! git diff --quiet HEAD -- index.html; then
  echo "stamp: index.html has changes not committed; commit them first" >&2
  exit 1
fi
for token in __COMMIT__ __PUBLISHED__; do
  n=$(grep -o "$token" index.html | wc -l | tr -d ' ')
  if [ "$n" != 1 ]; then
    echo "stamp: expected $token once in index.html, found $n" >&2
    exit 1
  fi
done

commit=$(git rev-parse HEAD)
published=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkdir -p build
sed -e "s/__COMMIT__/$commit/" -e "s/__PUBLISHED__/$published/" index.html > build/index.html

if ! git branch -r --contains "$commit" 2>/dev/null | grep -q .; then
  echo "stamp: note: $(git rev-parse --short HEAD) isn't on GitHub yet; the page's link works once you push" >&2
fi
echo "build/index.html stamped with $(git rev-parse --short HEAD) at $published"
