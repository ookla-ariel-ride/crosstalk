// The page at 390 px wide, the width of a phone: no main view scrolls
// sideways, the menu button reaches the navigation, and a thread opens. The
// spec only reads the shared fixture, signed out.
import { type Page } from "@playwright/test";
import { expect, test } from "./fixtures";
import { FIXTURE } from "./global-setup";

test.use({ viewport: { width: 390, height: 844 } });

async function noSideScroll(page: Page, what: string): Promise<void> {
  const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(widths.scroll, what).toBeLessThanOrEqual(widths.client);
}

test("at 390 px the main views do not scroll sideways, the menu reaches the navigation, and a thread opens", async ({ page }) => {
  // Signed out: the browser keeps the read state, so every message of a thread shows when it opens.
  const main = page.getByRole("main");
  for (const [hash, heading] of [
    ["#/conversations?sort=needs", "Conversations"],
    ["#/needs", "Needs you"],
    ["#/decisions", "Decisions"],
    ["#/people", "People"],
  ] as const) {
    await page.goto("/v2/" + hash);
    await expect(main.getByRole("heading", { level: 1 })).toHaveText(heading);
    await noSideScroll(page, hash);
  }

  // The sidebar hides behind the menu button; an entry takes you there and closes the menu.
  const menu = page.getByRole("button", { name: "Menu" });
  const nav = page.getByRole("navigation", { name: "Sections" });
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(nav.getByRole("link", { name: /^Needs you/ })).not.toBeInViewport();
  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await expect(nav.getByRole("link", { name: /^Roster/ })).toBeVisible();
  await nav.getByRole("link", { name: /^Conversations/ }).click();
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(main.getByRole("heading", { level: 1 })).toHaveText("Conversations");

  // A thread opens in place, and the open thread does not scroll sideways either.
  const row = main.getByRole("button", { name: FIXTURE.thread });
  await row.click();
  await expect(row).toHaveAttribute("aria-expanded", "true");
  await expect(main.getByText("The new column is in the branch.")).toBeVisible();
  await noSideScroll(page, "open thread");
});
