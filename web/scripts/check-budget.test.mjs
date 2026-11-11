import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { afterEach, describe, expect, test } from "vitest";
import { checkHtml, firstLoadChunks, LIMIT, measure, NOT_AT_FIRST_PAINT } from "./check-budget.mjs";

const SCRIPT = fileURLToPath(new URL("./check-budget.mjs", import.meta.url));

// Shaped like the manifest Vite 8 writes (ManifestChunk in vite's index.d.ts).
const STATIC_CHAIN = {
  "index.html": { file: "assets/index-a.js", name: "index", src: "index.html", isEntry: true,
    imports: ["_react-b.js"], css: ["assets/index-c.css"], assets: ["assets/font-d.woff2"] },
  "_react-b.js": { file: "assets/react-b.js", name: "react", imports: ["_scheduler-e.js"] },
  "_scheduler-e.js": { file: "assets/scheduler-e.js", name: "scheduler" },
  "src/font.woff2": { file: "assets/font-d.woff2", src: "src/font.woff2" },
};

const WITH_DYNAMIC = {
  "index.html": { file: "assets/index-a.js", src: "index.html", isEntry: true,
    imports: ["_shared-s.js"], dynamicImports: ["src/views/Record.tsx"] },
  "_shared-s.js": { file: "assets/shared-s.js" },
  "src/views/Record.tsx": { file: "assets/Record-r.js", src: "src/views/Record.tsx", isDynamicEntry: true,
    imports: ["_shared-s.js"], css: ["assets/Record-q.css"] },
};

let dirs = [];
afterEach(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true });
  dirs = [];
});

// A dist/ folder with the given files under assets/, a manifest and an index.html.
function makeDist(manifest, files) {
  const dist = mkdtempSync(join(tmpdir(), "crosstalk-dist-"));
  dirs.push(dist);
  mkdirSync(join(dist, "assets"));
  mkdirSync(join(dist, ".vite"));
  writeFileSync(join(dist, ".vite", "manifest.json"), JSON.stringify(manifest));
  for (const [name, data] of Object.entries(files)) writeFileSync(join(dist, name), data);
  const entry = Object.values(manifest).find((c) => c.isEntry).file;
  writeFileSync(join(dist, "index.html"), `<script type="module" crossorigin src="/v2/${entry}"></script>`);
  return dist;
}

describe("script budget", () => {
  test("the limit is 120 KB and no chunk is marked as loaded after first paint", () => {
    expect(LIMIT).toBe(122880);
    expect(NOT_AT_FIRST_PAINT).toEqual([]);
  });

  test("counts the entry and every chunk it reaches through static imports", () => {
    expect(firstLoadChunks(STATIC_CHAIN)).toEqual(["assets/index-a.js", "assets/react-b.js", "assets/scheduler-e.js"]);
  });

  test("counts a chunk behind a dynamic import unless the list marks it", () => {
    expect(firstLoadChunks(WITH_DYNAMIC)).toEqual(["assets/index-a.js", "assets/shared-s.js", "assets/Record-r.js"]);
    expect(firstLoadChunks(WITH_DYNAMIC, ["src/views/Record.tsx"])).toEqual(["assets/index-a.js", "assets/shared-s.js"]);
  });

  test("refuses to mark a chunk that a static import reaches, or one not in the build", () => {
    expect(() => firstLoadChunks(WITH_DYNAMIC, ["_shared-s.js"])).toThrow(/static import/);
    expect(() => firstLoadChunks(WITH_DYNAMIC, ["src/views/Gone.tsx"])).toThrow(/not in the manifest/);
  });

  test("index.html may only load the entry, as a module script with a src", () => {
    expect(() => checkHtml(`<script type='module' src='/v2/assets/index-a.js'></script>`, STATIC_CHAIN)).not.toThrow();
    expect(() => checkHtml(`<script src="/v2/assets/index-a.js"></script>`, STATIC_CHAIN)).toThrow(/module/);
    expect(() => checkHtml(`<script type="module">alert(1)</script>`, STATIC_CHAIN)).toThrow(/src/);
    expect(() => checkHtml(`<script type="module" src="/v2/assets/other.js"></script>`, STATIC_CHAIN)).toThrow(/entry/);
    expect(() => checkHtml(`<p>no script</p>`, STATIC_CHAIN)).toThrow(/entry/);
  });

  test("the total is the sum of the gzip sizes of the counted files", () => {
    const files = { "assets/index-a.js": "a".repeat(5000), "assets/react-b.js": "react ".repeat(900),
      "assets/scheduler-e.js": "s", "assets/index-c.css": "x".repeat(99999), "assets/font-d.woff2": "f" };
    const dist = makeDist(STATIC_CHAIN, files);
    const want = ["assets/index-a.js", "assets/react-b.js", "assets/scheduler-e.js"]
      .reduce((sum, f) => sum + gzipSync(files[f]).length, 0);
    expect(measure(dist).total).toBe(want);
  });

  test("a script file that the manifest does not name fails the check", () => {
    const dist = makeDist(STATIC_CHAIN, { "assets/index-a.js": "a", "assets/react-b.js": "b",
      "assets/scheduler-e.js": "c", "assets/stray-z.js": "z" });
    expect(() => measure(dist)).toThrow(/stray-z\.js/);
  });

  test("the script exits 1 over the limit and 0 under it", () => {
    const big = makeDist(STATIC_CHAIN, { "assets/index-a.js": randomBytes(LIMIT + 1000),
      "assets/react-b.js": "b", "assets/scheduler-e.js": "c" });
    const over = spawnSync(process.execPath, [SCRIPT, big], { encoding: "utf8" });
    expect(over.status).toBe(1);
    expect(over.stderr).toMatch(/^budget: first-load script \d+ bytes gzip of 122880 \(3 files\): over the limit/);
    const small = makeDist(STATIC_CHAIN, { "assets/index-a.js": "a", "assets/react-b.js": "b", "assets/scheduler-e.js": "c" });
    const under = spawnSync(process.execPath, [SCRIPT, small], { encoding: "utf8" });
    expect(under.status).toBe(0);
    expect(under.stdout).toMatch(/^budget: first-load script \d+ bytes gzip of 122880 \(3 files\)\n$/);
  });
});
