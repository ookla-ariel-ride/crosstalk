import { FIXTURE } from "./global-setup";
import { expect, signIn, test } from "./fixtures";

test("No action needed takes a request off Needs you, and it stays off after a reload", async ({ page }) => {
  await signIn(page);
  await page.goto("/v2/#/needs");
  const needsList = page.getByRole("list", { name: "Needs you" });
  await expect(needsList.getByRole("button", { name: FIXTURE.ownerRequest })).toBeVisible();
  const sent = page.waitForResponse((r) => r.url().endsWith("/api/ack") && r.request().method() === "POST");
  await needsList.getByRole("button", { name: "No action needed" }).click();
  expect((await sent).ok()).toBe(true);
  await expect(needsList.getByRole("button", { name: FIXTURE.ownerRequest })).toHaveCount(0);
  await expect(needsList.getByRole("button", { name: FIXTURE.openQuestion })).toBeVisible();

  await page.reload();
  await expect(needsList.getByRole("button", { name: FIXTURE.openQuestion })).toBeVisible();
  await expect(needsList.getByRole("button", { name: FIXTURE.ownerRequest })).toHaveCount(0);

  // The open thread shows the owner's no-change mark on the request, in its marks line.
  await page.goto("/v2/#/conversations?sort=new");
  await page.getByRole("button", { name: FIXTURE.ownerRequest }).click();
  await expect(page.locator("article [data-marks]")).toHaveText("➖ Owner");
  await expect(page.getByRole("button", { name: "No action needed" })).toHaveCount(0);
});
