import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, test } from "vitest";
import { buildHash, hashInputs } from "./build-hash.mjs";

// tests/test_web_dist.py builds the same tree and expects the same value.
const KNOWN_HASH = "4c2c071f28ee37a37c894fa1bbc27c5e760eb77047cd6c549394cdb574b4fbe7";

let dirs = [];
function makeWeb() {
  const web = mkdtempSync(join(tmpdir(), "crosstalk-web-"));
  dirs.push(web);
  mkdirSync(join(web, "src", "ui"), { recursive: true });
  writeFileSync(join(web, "package.json"), "{}\n");
  writeFileSync(join(web, "package-lock.json"), "{}\n");
  writeFileSync(join(web, "src", "main.tsx"), "a\nb\n");
  writeFileSync(join(web, "src", "ui", "tokens.css"), ":root {}\n");
  mkdirSync(join(web, "public"));
  writeFileSync(join(web, "public", "robots.txt"), "x\n");
  writeFileSync(join(web, "index.html"), "<!doctype html>\n");
  writeFileSync(join(web, "vite.config.ts"), "export default {};\n");
  writeFileSync(join(web, "tsconfig.json"), "{}\n");
  return web;
}
afterEach(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true });
  dirs = [];
});

describe("build hash", () => {
  test("matches the Python rule", () => {
    expect(buildHash(makeWeb())).toBe(KNOWN_HASH);
  });

  test("lists sorted paths relative to web/", () => {
    expect(hashInputs(makeWeb())).toEqual([
      "index.html", "package-lock.json", "package.json", "public/robots.txt",
      "src/main.tsx", "src/ui/tokens.css", "tsconfig.json", "vite.config.ts",
    ]);
  });

  test("counts the page and the build config", () => {
    const web = makeWeb();
    const first = buildHash(web);
    for (const rel of ["index.html", "vite.config.ts", "tsconfig.json", "public/robots.txt"]) {
      const file = join(web, ...rel.split("/"));
      const old = readFileSync(file);
      writeFileSync(file, Buffer.concat([old, Buffer.from("// changed\n")]));
      expect(buildHash(web), rel).not.toBe(first);
      writeFileSync(file, old);
    }
    expect(buildHash(web)).toBe(first);
  });

  test("follows content, not line endings or dot files", () => {
    const web = makeWeb();
    writeFileSync(join(web, "src", "main.tsx"), "a\r\nb\r\n");
    writeFileSync(join(web, "src", ".DS_Store"), "x");
    expect(buildHash(web)).toBe(KNOWN_HASH);
    writeFileSync(join(web, "src", "main.tsx"), "a\nc\n");
    expect(buildHash(web)).not.toBe(KNOWN_HASH);
  });
});
