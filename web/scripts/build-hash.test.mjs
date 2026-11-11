import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, test } from "vitest";
import { buildHash, hashInputs } from "./build-hash.mjs";

// tests/test_web_dist.py builds the same tree and expects the same value.
const KNOWN_HASH = "3e4856d9e3ee22dba54cd6ef1a2631a3ab7a67cfa6e1a9e8f02a84f7ea6880c8";

let dirs = [];
function makeWeb() {
  const web = mkdtempSync(join(tmpdir(), "crosstalk-web-"));
  dirs.push(web);
  mkdirSync(join(web, "src", "ui"), { recursive: true });
  writeFileSync(join(web, "package.json"), "{}\n");
  writeFileSync(join(web, "package-lock.json"), "{}\n");
  writeFileSync(join(web, "src", "main.tsx"), "a\nb\n");
  writeFileSync(join(web, "src", "ui", "tokens.css"), ":root {}\n");
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
      "package-lock.json", "package.json", "src/main.tsx", "src/ui/tokens.css",
    ]);
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
