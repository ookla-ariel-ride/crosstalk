import { describe, expect, test } from "vitest";
import { afterSummary } from "./afterSummary";

describe("afterSummary", () => {
  test("a body that starts with the summary line loses that line", () => {
    expect(afterSummary("Agreed\n\nI keep the log.", "Agreed")).toBe("I keep the log.");
  });
  test("a body equal to the summary shows nothing", () => {
    expect(afterSummary(" Agreed \n", "Agreed")).toBe("");
  });
  test("a body that only starts with the same words stays whole", () => {
    expect(afterSummary("Agreed with beta.", "Agreed")).toBe("Agreed with beta.");
  });
  test("another body, or no summary, stays whole", () => {
    expect(afterSummary("We revisit it in a month.", "Keep the log")).toBe("We revisit it in a month.");
    expect(afterSummary("Text", "")).toBe("Text");
  });
});
