import { describe, expect, test } from "vitest";
import { speaker } from "./speaker";

describe("speaker", () => {
  test("names the project, harness and model of the message", () => {
    expect(speaker({ from: "alpha", harness: "Codex CLI", model: "gpt-5" }, {})).toBe(
      "alpha, Codex CLI, gpt-5",
    );
  });

  test("takes the roster's harness when the message has none", () => {
    const roster = { beta: { harness: "claude-code" } };
    expect(speaker({ from: "beta", model: "claude-opus-4-5" }, roster)).toBe(
      "beta, claude-code, claude-opus-4-5",
    );
  });

  test("the message's harness wins over the roster's", () => {
    const roster = { beta: { harness: "claude-code" } };
    expect(speaker({ from: "beta", harness: "opencode", model: "glm-4.6" }, roster)).toBe(
      "beta, opencode, glm-4.6",
    );
  });

  test("says model not stated when there is no model", () => {
    const roster = { alpha: { harness: "codex" } };
    expect(speaker({ from: "alpha" }, roster)).toBe("alpha, codex, model not stated");
  });

  test("leaves out a harness that neither the message nor the roster has", () => {
    expect(speaker({ from: "relay-bot" }, { "relay-bot": { harness: null } })).toBe(
      "relay-bot, model not stated",
    );
  });

  test("the owner is a person: only the name, no model", () => {
    expect(speaker({ from: "owner" }, {})).toBe("owner");
    expect(speaker({ from: "owner", harness: "x", model: "y" }, {})).toBe("owner");
  });

  test("bad input gives a plain result", () => {
    expect(speaker({}, {})).toBe("model not stated");
    expect(speaker(undefined as never, undefined as never)).toBe("model not stated");
    expect(speaker({ from: "alpha", harness: 7, model: [] } as never, { alpha: undefined })).toBe(
      "alpha, model not stated",
    );
  });
});
