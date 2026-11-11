import { describe, expect, test } from "vitest";
import { firstLoadScripts, LIMIT } from "./check-budget.mjs";

describe("script budget", () => {
  test("the limit is 120 KB", () => {
    expect(LIMIT).toBe(122880);
  });

  test("counts the entry script and its preloaded static imports", () => {
    const html = `<!doctype html><head>
      <script type="module" crossorigin src="/v2/assets/index-abc.js"></script>
      <link rel="modulepreload" crossorigin href="/v2/assets/react-def.js">
      <link rel="stylesheet" crossorigin href="/v2/assets/index-123.css">
      <link rel="icon" href="/v2/assets/mark-1.svg"></head>`;
    expect(firstLoadScripts(html, "/v2/")).toEqual(["assets/index-abc.js", "assets/react-def.js"]);
  });
});
