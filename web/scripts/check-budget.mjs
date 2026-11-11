// Fails the build when the script of the first load is over 120 KB after
// gzip. The first load is what dist/index.html names: the entry script and
// the static imports Vite lists as modulepreload links.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { gzipSync } from "node:zlib";

export const LIMIT = 122880;
const BASE = "/v2/";

function attr(tag, name) {
  const m = tag.match(new RegExp(`\\s${name}="([^"]*)"`));
  return m ? m[1] : null;
}

// The files under dist/ that index.html loads as script, in page order.
export function firstLoadScripts(html, base = BASE) {
  const files = [];
  for (const tag of html.match(/<(script|link)\b[^>]*>/g) ?? []) {
    let url = null;
    if (tag.startsWith("<script") && attr(tag, "type") === "module") url = attr(tag, "src");
    if (tag.startsWith("<link") && attr(tag, "rel") === "modulepreload") url = attr(tag, "href");
    if (url === null) continue;
    if (!url.startsWith(base)) throw new Error(`script outside ${base}: ${url}`);
    files.push(url.slice(base.length));
  }
  return files;
}

// gzip at the default level (6), the level most servers and proxies use.
export function gzipBytes(buffer) {
  return gzipSync(buffer).length;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const dist = join(dirname(dirname(fileURLToPath(import.meta.url))), "dist");
  const files = firstLoadScripts(readFileSync(join(dist, "index.html"), "utf8"));
  if (files.length === 0) throw new Error("dist/index.html names no module script");
  const total = files.reduce((sum, f) => sum + gzipBytes(readFileSync(join(dist, f))), 0);
  const line = `budget: first-load script ${total} bytes gzip of ${LIMIT} (${files.length} files)`;
  if (total > LIMIT) {
    console.error(`${line}: over the limit`);
    process.exit(1);
  }
  console.log(line);
}
