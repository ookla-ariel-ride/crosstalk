import { describe, expect, test } from "vitest";
import { choices } from "./choices";

describe("choices", () => {
  test("uses the options field when present", () => {
    expect(choices({ options: ["Ship it", "Wait a day"], recommend: 1 })).toEqual({
      options: ["Ship it", "Wait a day"],
      recommend: 1,
    });
  });

  test('choices reads "a. " lines', () => {
    const body = "Which build host?\na. The laptop\nb) The server (recommended)\nc. Both\nThanks.";
    expect(choices({ body })).toEqual({
      options: ["The laptop", "The server (recommended)", "Both"],
      recommend: 1,
    });
  });

  test("choices ignores a single lettered line", () => {
    expect(choices({ body: "a. Only this one\nand some text" })).toEqual({ options: [], recommend: null });
  });

  test("options win over lettered lines in the body", () => {
    expect(choices({ options: ["Yes", "No"], body: "a. Left\nb. Right" }).options).toEqual(["Yes", "No"]);
  });

  test("removes a letter prefix from options", () => {
    expect(choices({ options: ["a. Yes", "b) No"] }).options).toEqual(["Yes", "No"]);
  });

  test("recommend falls back to the option marked recommended, case blind", () => {
    expect(choices({ options: ["Yes", "No (Recommended)"] }).recommend).toBe(1);
    expect(choices({ options: ["RECOMMENDED: yes", "No"] }).recommend).toBe(0);
    expect(choices({ options: ["Yes", "No"] }).recommend).toBe(null);
  });

  test("a recommend field out of range or of the wrong type is ignored", () => {
    expect(choices({ options: ["Yes", "No (recommended)"], recommend: 5 }).recommend).toBe(1);
    expect(choices({ options: ["Yes", "No"], recommend: -1 }).recommend).toBe(null);
    expect(choices({ options: ["Yes", "No"], recommend: "0" }).recommend).toBe(null);
    expect(choices({ options: ["Yes", "No"], recommend: 0.5 }).recommend).toBe(null);
  });

  test("a body line needs the letter, the mark and a space", () => {
    expect(choices({ body: "a.No space\nb.No space" }).options).toEqual([]);
    expect(choices({ body: "g. Out of range\nh. Out of range" }).options).toEqual([]);
    expect(choices({ body: "a. One\r\nb. Two\r\n" }).options).toEqual(["One", "Two"]);
  });

  test("bad input gives a plain result", () => {
    const none = { options: [], recommend: null };
    expect(choices({})).toEqual(none);
    expect(choices(undefined as never)).toEqual(none);
    expect(choices({ options: "a, b" })).toEqual(none);
    expect(choices({ options: ["only one"] })).toEqual(none);
    expect(choices({ options: [1, 2] })).toEqual(none);
    expect(choices({ body: 5 as never })).toEqual(none);
  });
});
