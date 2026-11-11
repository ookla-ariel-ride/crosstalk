import { FIXTURE } from "./global-setup";
import { expect, test } from "./fixtures";

const HEIGHT = 900;

test.describe("at 1440 by 900 px", () => {
  test.use({ viewport: { width: 1440, height: HEIGHT } });

  test("the first screen shows Needs you and the first thread within 900 px", async ({ page }) => {
    await page.goto("/v2/");
    const needs = page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: /^Needs you/ });
    await expect(needs).toBeInViewport({ ratio: 1 });
    // The home stream is sorted by needs: a thread that needs the owner comes first.
    const first = page.getByRole("list", { name: "Conversations" }).getByRole("listitem").first();
    await expect(first).toContainText("needs you");
    await expect(first).toContainText(FIXTURE.openQuestion);
    // The whole row, not a part of it, is on the first screen.
    await expect(first).toBeInViewport({ ratio: 1 });
    const box = await first.boundingBox();
    expect(box).not.toBeNull();
    expect((box?.y ?? HEIGHT) + (box?.height ?? 0)).toBeLessThanOrEqual(HEIGHT);
  });
});

test("the pages load nothing from another host and log no error", async ({ page }) => {
  // The fixture in fixtures.ts fails the test on a request to another host or a console error.
  await page.goto("/v2/");
  await expect(page.getByRole("heading", { name: "Conversations", level: 1 })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Sections" });
  for (const [link, heading] of [
    [/^Needs you/, "Needs you"],
    [/^Roster/, "People"],
    [/^Conversations/, "Conversations"],
  ] as const) {
    await nav.getByRole("link", { name: link }).click();
    await expect(page.getByRole("heading", { level: 1 }).first()).toContainText(heading);
  }
  await page.getByRole("button", { name: FIXTURE.thread }).click();
  await expect(page.getByText("The new column is in the branch.")).toBeVisible();
});
