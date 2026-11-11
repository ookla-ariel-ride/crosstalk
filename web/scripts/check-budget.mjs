// Fails the build when the script of the first load is over 120 KB after
// gzip (level 6, the default of most servers and proxies).
//
// What counts. The build writes dist/.vite/manifest.json (build.manifest in
// vite.config.ts); its chunks are the real graph of the script.
// 1. The entry chunk and every chunk it reaches through static imports
//    always count.
// 2. A chunk behind a dynamic import also counts, unless NOT_AT_FIRST_PAINT
//    names its manifest key. So by default every script chunk of the build
//    counts. A key there must be a chunk of the manifest that no static import
//    of the entry reaches, or the check fails.
// 3. Every .js file under dist/ must be a chunk of the manifest, and
//    dist/index.html may hold only one script tag: type="module" with a src
//    that names the entry chunk. Anything else fails the check.
// The server does not read the manifest; it is committed with dist/ so a
// reviewer can check the count.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { gzipSync } from "node:zlib";

export const LIMIT = 122880;
// Manifest keys of chunks that load only after the first paint. Empty: the
// page has no such chunk yet.
export const NOT_AT_FIRST_PAINT = [];
const BASE = "/v2/";
const MANIFEST = join(".vite", "manifest.json");

const isScript = (chunk) => /\.m?js$/.test(chunk.file);

function entryKey(manifest) {
  const keys = Object.keys(manifest).filter((k) => manifest[k].isEntry);
  if (keys.length !== 1) throw new Error(`the manifest has ${keys.length} entry chunks, not 1`);
  return keys[0];
}

// The files of the chunks that count, the static graph of the entry first.
export function firstLoadChunks(manifest, notAtFirstPaint = NOT_AT_FIRST_PAINT) {
  const reached = [];
  const visit = (key) => {
    if (reached.includes(key)) return;
    if (!manifest[key]) throw new Error(`${key} is imported but not in the manifest`);
    reached.push(key);
    for (const next of manifest[key].imports ?? []) visit(next);
  };
  visit(entryKey(manifest));
  for (const key of notAtFirstPaint) {
    if (!manifest[key]) throw new Error(`NOT_AT_FIRST_PAINT names ${key}, which is not in the manifest`);
    if (reached.includes(key)) throw new Error(`NOT_AT_FIRST_PAINT names ${key}, which a static import of the entry reaches`);
  }
  const rest = Object.keys(manifest)
    .filter((k) => !reached.includes(k) && !notAtFirstPaint.includes(k) && isScript(manifest[k]));
  return [...reached, ...rest].map((k) => manifest[k].file);
}

function attr(tag, name) {
  const m = tag.match(new RegExp(`\\s${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s"'>]+))`, "i"));
  return m ? (m[1] ?? m[2] ?? m[3]) : null;
}

// Throws unless index.html loads exactly the entry chunk, as a module script.
export function checkHtml(html, manifest, base = BASE) {
  const entry = manifest[entryKey(manifest)].file;
  const tags = html.match(/<script\b[^>]*>/gi) ?? [];
  for (const tag of tags) {
    if ((attr(tag, "type") ?? "").toLowerCase() !== "module") throw new Error(`index.html has a script that is not a module: ${tag}`);
    const src = attr(tag, "src");
    if (src === null) throw new Error(`index.html has a module script with no src: ${tag}`);
    if (src !== base + entry) throw new Error(`index.html loads ${src}, not the entry ${base}${entry}`);
  }
  if (tags.length !== 1) throw new Error(`index.html has ${tags.length} script tags, not 1 that loads the entry`);
}

function scriptFiles(dir, rel = "") {
  const out = [];
  for (const e of readdirSync(join(dir, rel), { withFileTypes: true })) {
    const child = rel ? `${rel}/${e.name}` : e.name;
    if (e.isDirectory()) out.push(...scriptFiles(dir, child));
    else if (/\.m?js$/.test(e.name)) out.push(child);
  }
  return out;
}

export function measure(dist, notAtFirstPaint = NOT_AT_FIRST_PAINT) {
  const manifest = JSON.parse(readFileSync(join(dist, MANIFEST), "utf8"));
  checkHtml(readFileSync(join(dist, "index.html"), "utf8"), manifest);
  const known = new Set(Object.values(manifest).map((c) => c.file));
  const stray = scriptFiles(dist).filter((f) => !known.has(f));
  if (stray.length) throw new Error(`script files that the manifest does not name: ${stray.join(", ")}`);
  const files = firstLoadChunks(manifest, notAtFirstPaint);
  const total = files.reduce((sum, f) => sum + gzipSync(readFileSync(join(dist, f))).length, 0);
  return { files, total };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const dist = process.argv[2] ?? join(dirname(dirname(fileURLToPath(import.meta.url))), "dist");
  const { files, total } = measure(dist);
  const line = `budget: first-load script ${total} bytes gzip of ${LIMIT} (${files.length} files)`;
  if (total > LIMIT) {
    console.error(`${line}: over the limit`);
    process.exit(1);
  }
  console.log(line);
}
