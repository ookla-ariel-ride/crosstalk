// Writes dist/.build-hash: a hash of the source of the built page, so
// tests/test_web_dist.py can tell when web/dist/ is older than its source.
//
// The rule. tests/test_web_dist.py computes the same value; keep the two
// rules identical.
// 1. Inputs: web/package.json, web/package-lock.json and every file under
//    web/src/, skipping any file or folder whose name starts with ".".
// 2. Each path is relative to web/ with "/" between parts (src/ui/tokens.css).
// 3. Order: by the UTF-8 bytes of the path, smallest first.
// 4. Content: the file's bytes with every CRLF replaced by LF.
// 5. For each file, feed SHA-256 with: the path in UTF-8, one 0x00 byte, the
//    content length in bytes as ASCII decimal digits, one 0x00 byte, the content.
// 6. The hash is the hex digest. web/dist/.build-hash holds it and a newline.
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOTS = ["package.json", "package-lock.json"];
const DIR = "src";

function walk(web, rel, out) {
  for (const entry of readdirSync(join(web, rel), { withFileTypes: true })) {
    if (entry.name.startsWith(".")) continue;
    const child = `${rel}/${entry.name}`;
    if (entry.isDirectory()) walk(web, child, out);
    else if (entry.isFile()) out.push(child);
  }
}

export function hashInputs(web) {
  const paths = ROOTS.filter((name) => existsSync(join(web, name)));
  if (existsSync(join(web, DIR))) walk(web, DIR, paths);
  return paths.sort((a, b) => Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8")));
}

function lf(bytes) {
  const out = Buffer.alloc(bytes.length);
  let n = 0;
  for (let i = 0; i < bytes.length; i++) {
    if (bytes[i] === 0x0d && bytes[i + 1] === 0x0a) continue;
    out[n++] = bytes[i];
  }
  return out.subarray(0, n);
}

export function buildHash(web) {
  const h = createHash("sha256");
  for (const rel of hashInputs(web)) {
    const data = lf(readFileSync(join(web, ...rel.split("/"))));
    h.update(Buffer.concat([Buffer.from(rel, "utf8"), Buffer.from([0]),
      Buffer.from(String(data.length), "ascii"), Buffer.from([0]), data]));
  }
  return h.digest("hex");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const web = dirname(dirname(fileURLToPath(import.meta.url)));
  const hash = buildHash(web);
  writeFileSync(join(web, "dist", ".build-hash"), `${hash}\n`);
  console.log(`build hash: ${hash}`);
}
