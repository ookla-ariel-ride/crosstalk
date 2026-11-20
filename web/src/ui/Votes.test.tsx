// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { marksView } from "../api/normalize";
import type { RosterEntry } from "../api/types";
import { renderWith } from "./testing";
import { shownState, Votes } from "./Votes";

const ROSTER: Record<string, RosterEntry> = {
  alpha: { id: "alpha", name: "Alpha", kind: "project" },
  beta: { id: "beta", name: "Beta", kind: "project" },
  gamma: { id: "gamma", name: "Gamma", kind: "project" },
  "beta.helper": { id: "beta.helper", name: "Helper", kind: "agent", parent: "beta" },
};

const DUE = "2026-11-20T12:00:00Z";

/** Render the block for a raw `marks` value from the server, through normalize.ts. */
function show(raw: unknown) {
  return renderWith(<Votes marks={marksView(raw)} roster={ROSTER} />);
}

function marks(votes: unknown) {
  return { marks: {}, owed: [], expects: false, votes };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("Votes", () => {
  test("the ADOPT and OBJECT counts, then each voter with the choice and the note", () => {
    const { container } = show(
      marks({
        counts: { ADOPT: 2, OBJECT: 1 },
        votes: [
          { from: "gamma", vote: "OBJECT", note: "too soon", t: "2026-11-19T11:00:00Z" },
          { from: "beta.helper", vote: "ADOPT", note: null, t: "2026-11-19T11:00:00Z" },
          { from: "alpha", vote: "ADOPT", note: "fits our release", t: "2026-11-19T11:00:00Z" },
        ],
        due: null,
        state: null,
        outcome: null,
      }),
    );
    expect(screen.getByRole("img", { name: "votes" }).textContent).toBe("🗳️");
    expect([...container.querySelectorAll("[data-choice]")].map((e) => e.textContent)).toEqual(["ADOPT 2", "OBJECT 1"]);
    expect([...container.querySelectorAll("li")].map((e) => e.textContent)).toEqual([
      "ADOPT Alpha: fits our release",
      "ADOPT Beta › Helper",
      "OBJECT Gamma: too soon",
    ]);
    expect(container.querySelector("[data-state]")).toBeNull();
  });

  test("OBJECT 0 shows next to ADOPT, and another choice follows them", () => {
    const { container } = show(
      marks({ counts: { ABSTAIN: 1, ADOPT: 1 }, votes: [
        { from: "alpha", vote: "ADOPT" },
        { from: "beta", vote: "ABSTAIN" },
      ] }),
    );
    expect([...container.querySelectorAll("[data-choice]")].map((e) => e.textContent)).toEqual(["ADOPT 1", "OBJECT 0", "ABSTAIN 1"]);
    expect([...container.querySelectorAll("li")].map((e) => e.textContent)).toEqual(["ADOPT Alpha", "ABSTAIN Beta"]);
  });

  test("a due proposal shows its state, and its outcome once the due time passes", () => {
    vi.useFakeTimers({ now: Date.parse("2026-11-20T11:00:00Z") });
    const raw = marks({ counts: {}, votes: [], due: DUE, state: "open", outcome: "passed" });
    const { container, unmount } = show(raw);
    expect(container.textContent).toContain("No votes yet");
    expect(container.querySelector("[data-state]")?.getAttribute("data-state")).toBe("open");
    expect(container.querySelector("[data-state]")?.textContent).toMatch(/^Open until /);
    unmount();

    vi.setSystemTime(Date.parse("2026-11-20T13:00:00Z"));
    const later = show(raw);
    expect(later.container.querySelector("[data-state]")?.getAttribute("data-state")).toBe("passed");
    expect(later.container.querySelector("[data-state]")?.textContent).toMatch(/^Passed at /);
  });

  test("shownState keeps a state the server already settled", () => {
    const after = new Date("2026-11-20T13:00:00Z");
    const base = { counts: {}, votes: [], due: DUE };
    expect(shownState({ ...base, state: "objected", outcome: "objected" }, after)).toBe("objected");
    expect(shownState({ ...base, state: "retracted", outcome: "retracted" }, after)).toBe("retracted");
    expect(shownState({ ...base, state: "open", outcome: "objected" }, new Date("2026-11-20T11:00:00Z"))).toBe("open");
    expect(shownState({ ...base, state: "open", outcome: "objected" }, after)).toBe("objected");
  });

  test("nothing without votes and a due time, or for a malformed value", () => {
    for (const bad of [undefined, null, 5, "x", marks(undefined), marks(7), marks([]), marks({ votes: [{ vote: "ADOPT" }, { from: "alpha" }] })]) {
      const { container, unmount } = show(bad);
      expect(container.textContent).toBe("");
      unmount();
    }
  });

  test("normalize keeps good votes and drops bad counts, notes and states", () => {
    const v = marksView(
      marks({
        counts: { ADOPT: 1, OBJECT: 0, X: 1.5, Y: "2" },
        votes: [{ from: "alpha", vote: "ADOPT", note: 7, t: 3 }, "x", { from: "", vote: "ADOPT" }],
        due: DUE,
        state: "won",
        outcome: "passed",
      }),
    )?.votes;
    expect(v).toEqual({ counts: { ADOPT: 1 }, votes: [{ from: "alpha", vote: "ADOPT", note: null, t: null }], due: DUE, state: null, outcome: "passed" });
    expect(marksView(marks({ counts: {}, votes: [], state: "open", outcome: "passed" }))?.votes).toMatchObject({ due: null, state: null, outcome: null });
  });
});
