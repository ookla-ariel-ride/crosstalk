import { expect, test } from "vitest";
import { grantState } from "./grant";

test("a void or lapsed grant does not read as granted", () => {
  expect(grantState({ granted: "relay-bot-1" })).toBe("granted");
  expect(grantState({ granted: "relay-bot-1", grant_void: "wind-down step 1" })).toBe("grant void: wind-down step 1");
  expect(grantState({ granted: "relay-bot-1", grant_lapsed: true })).toBe("grant lapsed");
  expect(grantState({})).toBeNull();
});
