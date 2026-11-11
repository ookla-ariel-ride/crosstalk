import { describe, expect, test } from "vitest";
import { ago } from "./ago";

const now = new Date("2030-01-10T12:00:00Z");

describe("ago", () => {
  test("under a minute is now", () => {
    expect(ago("2030-01-10T12:00:00Z", now)).toBe("now");
    expect(ago("2030-01-10T11:59:01Z", now)).toBe("now");
  });

  test("minutes, hours, days", () => {
    expect(ago("2030-01-10T11:59:00Z", now)).toBe("1 min");
    expect(ago("2030-01-10T11:54:00Z", now)).toBe("6 min");
    expect(ago("2030-01-10T11:00:01Z", now)).toBe("59 min");
    expect(ago("2030-01-10T11:00:00Z", now)).toBe("1 h");
    expect(ago("2030-01-10T10:00:00Z", now)).toBe("2 h");
    expect(ago("2030-01-09T12:00:01Z", now)).toBe("23 h");
    expect(ago("2030-01-09T12:00:00Z", now)).toBe("1 d");
    expect(ago("2030-01-07T11:00:00Z", now)).toBe("3 d");
  });

  test("a time in the future is now", () => {
    expect(ago("2030-01-11T12:00:00Z", now)).toBe("now");
  });

  test("a bad date gives an empty string", () => {
    expect(ago("not a date", now)).toBe("");
    expect(ago("", now)).toBe("");
    expect(ago(undefined as never, now)).toBe("");
    expect(ago("2030-01-10T11:00:00Z", new Date("nope"))).toBe("");
    expect(ago("2030-01-10T11:00:00Z", undefined as never)).toBe("");
  });
});
