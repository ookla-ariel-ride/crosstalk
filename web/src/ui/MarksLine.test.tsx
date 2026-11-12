// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { RosterEntry } from "../api/types";
import { MarksLine } from "./MarksLine";
import { renderWith } from "./testing";

const ROSTER: Record<string, RosterEntry> = {
  alpha: { id: "alpha", name: "Alpha", kind: "project" },
  beta: { id: "beta", name: "Beta", kind: "project" },
  gamma: { id: "gamma", name: "Gamma", kind: "project" },
  delta: { id: "delta", name: "Delta", kind: "project" },
  epsilon: { id: "epsilon", name: "Epsilon", kind: "project" },
  "beta.helper": { id: "beta.helper", name: "Helper", kind: "agent", parent: "beta" },
};

function show(marks: unknown) {
  return renderWith(<MarksLine marks={marks as never} roster={ROSTER} />);
}

describe("MarksLine", () => {
  test("each mark with its names, in the order ack, applies, none, seen, unclear, then not yet", () => {
    const { container } = show({
      marks: { beta: "ack", alpha: "ack", gamma: "none", delta: "seen", epsilon: "unclear", "beta.helper": "applies" },
      owed: ["delta", "epsilon"],
      expects: true,
    });
    expect(container.textContent).toBe("✅ Alpha, Beta🔧 Beta › Helper➖ Gamma👀 Delta❓ Epsilonnot yet: Delta, Epsilon");
  });

  test("each emoji has its word as a title and as its accessible name", () => {
    show({ marks: { alpha: "ack", beta: "applies", gamma: "none", delta: "seen", epsilon: "unclear" }, owed: [], expects: false });
    for (const [emoji, word] of [
      ["✅", "acknowledged"],
      ["🔧", "applies"],
      ["➖", "no change"],
      ["👀", "seen"],
      ["❓", "not clear"],
    ]) {
      const img = screen.getByRole("img", { name: word });
      expect(img.textContent).toBe(emoji);
      expect(img.getAttribute("title")).toBe(word);
    }
  });

  test("not yet shows only on a post that expects marks", () => {
    show({ marks: { alpha: "seen" }, owed: ["alpha", "beta"], expects: false });
    expect(screen.queryByText(/not yet/)).toBeNull();
    expect(screen.getByText(/Alpha/)).toBeTruthy();
  });

  test("a post that expects marks and has none shows only not yet", () => {
    const { container } = show({ marks: {}, owed: ["beta", "alpha"], expects: true });
    expect(container.textContent).toBe("not yet: Alpha, Beta");
  });

  test("nothing for an absent or malformed marks value", () => {
    for (const bad of [undefined, null, 5, "ack", [], { marks: [] }, { marks: "x" }, { marks: {}, owed: [], expects: false }, { marks: { alpha: "great" } }]) {
      const { container, unmount } = show(bad);
      expect(container.textContent).toBe("");
      expect(container.querySelector("[data-marks]")).toBeNull();
      unmount();
    }
  });

  test("a name not in the roster shows as its id", () => {
    const { container } = show({ marks: { zeta: "ack" }, owed: [], expects: false });
    expect(container.textContent).toBe("✅ zeta");
  });
});
