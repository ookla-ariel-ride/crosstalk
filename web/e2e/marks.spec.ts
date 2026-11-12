// The marks line of a lesson that twelve participants marked, on the shared fixture
// server. The test only reads: it needs no state from another test and leaves none.
import { FIXTURE, MARKED } from "./global-setup";
import { expect, signIn, test } from "./fixtures";

test("an open thread shows the marks line of a marked lesson, and the line wraps at 360 px", async ({ page }) => {
  await signIn(page);
  await page.goto("/v2/#/conversations?sort=new");
  const row = page.getByRole("button", { name: FIXTURE.marked });
  // Ten owe a mark (Beta and the nine projects); seven hold an acknowledged one.
  await expect(row).toContainText("7 of 10 acknowledged");
  await row.click();
  const line = page.locator("article [data-marks]");
  await expect(line).toBeVisible();
  await expect(line.getByRole("img", { name: "acknowledged" })).toHaveAttribute("title", "acknowledged");
  await expect(line).toHaveText(
    "✅ Delta, Epsilon, Gamma, Relay bot, Zeta 🔧 Eta, Theta ➖ Iota, Owner 👀 Alpha › Beta, Kappa ❓ Lambda not yet: Alpha › Beta, Kappa, Lambda",
  );

  // Playwright's docs: set the size before the page loads; a hash change alone loads nothing, so reload.
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto(`/v2/#/conversations?sort=new&open=${MARKED.id}`);
  await page.reload();
  await expect(line).toBeVisible();
  const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);
  const box = await line.boundingBox();
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(360);
});
