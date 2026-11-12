import { describe, expect, test } from "vitest";
import { splitRead } from "./splitRead";

const m1 = { id: "alpha-0001", t: "2030-01-01T10:00:00Z" };
const m2 = { id: "beta-0001", t: "2030-01-01T10:05:00Z" };
const m3 = { id: "beta-0002", t: "2030-01-01T10:05:00Z" };
const m4 = { id: "alpha-0002", t: "2030-01-01T10:09:00Z" };

describe("splitRead", () => {
  test("splitRead with no mark puts all in fresh", () => {
    expect(splitRead([m1, m2], undefined)).toEqual({ earlier: [], fresh: [m1, m2] });
  });

  test("a message later than the mark is fresh", () => {
    expect(splitRead([m1, m2, m4], { t: m2.t, id: m2.id })).toEqual({
      earlier: [m1, m2],
      fresh: [m4],
    });
  });

  test("time and id compare as a pair", () => {
    // m3 has the same time as the mark and a later id.
    expect(splitRead([m1, m2, m3, m4], { t: m2.t, id: m2.id })).toEqual({
      earlier: [m1, m2],
      fresh: [m3, m4],
    });
  });

  test("keeps the order it was given", () => {
    expect(splitRead([m4, m1, m2], { t: m1.t, id: m1.id })).toEqual({
      earlier: [m1],
      fresh: [m4, m2],
    });
  });

  test("bad input gives a plain result", () => {
    expect(splitRead([], { t: m1.t, id: m1.id })).toEqual({ earlier: [], fresh: [] });
    expect(splitRead(undefined as never, undefined)).toEqual({ earlier: [], fresh: [] });
    // A mark with no time is no mark.
    expect(splitRead([m1], {} as never)).toEqual({ earlier: [], fresh: [m1] });
    // A message with no time is not later than a mark.
    expect(splitRead([{ id: "x-0001" }], { t: m1.t, id: m1.id })).toEqual({
      earlier: [{ id: "x-0001" }],
      fresh: [],
    });
    expect(splitRead([null as never, m4], { t: m1.t })).toEqual({ earlier: [], fresh: [m4] });
  });
});
