import { describe, expect, test } from "vitest";
import { family } from "./family";

describe("family", () => {
  test("reads the table", () => {
    expect(family("claude-opus-4-5")).toBe("Anthropic");
    expect(family("gpt-5")).toBe("OpenAI");
    expect(family("o1")).toBe("OpenAI");
    expect(family("o3-pro")).toBe("OpenAI");
    expect(family("openai/o4-mini")).toBe("OpenAI");
    expect(family("glm-4.6")).toBe("Zhipu");
    expect(family("kimi-k2")).toBe("Moonshot");
    expect(family("gemini-2.5-pro")).toBe("Google");
  });

  test("family is case blind", () => {
    expect(family("Claude-Sonnet-4-5")).toBe("Anthropic");
    expect(family("GPT-5")).toBe("OpenAI");
    expect(family("O3")).toBe("OpenAI");
  });

  test("o1, o3 and o4 match only at the start of a name part", () => {
    expect(family("llama-pro3")).toBe("Other");
    expect(family("mistral-pro1")).toBe("Other");
    expect(family("neo4j")).toBe("Other");
  });

  test("no match gives Other", () => {
    expect(family("llama-3.1-70b")).toBe("Other");
  });

  test("no model gives Not stated", () => {
    expect(family(undefined)).toBe("Not stated");
    expect(family("")).toBe("Not stated");
    expect(family("   ")).toBe("Not stated");
    expect(family(42 as unknown as string)).toBe("Not stated");
  });

  test("extra pairs are checked first, as plain substrings", () => {
    expect(family("gpt-oss-120b", [["gpt-oss", "Open weights"]])).toBe("Open weights");
    expect(family("Qwen3-Coder", [["qwen", "Alibaba"]])).toBe("Alibaba");
    // A pattern is not a regex: "." matches only a dot.
    expect(family("glm-4x6", [["glm-4.6", "Z"]])).toBe("Zhipu");
  });

  test("bad extra pairs are skipped", () => {
    const bad = [["", "Empty"], [undefined, "X"], ["qwen"]] as unknown as [string, string][];
    expect(family("qwen3", bad)).toBe("Other");
    expect(family("gpt-5", null as unknown as [string, string][])).toBe("OpenAI");
  });
});
