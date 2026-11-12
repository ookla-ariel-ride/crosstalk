import { FIXTURE } from "./global-setup";
import { expect, signIn, test } from "./fixtures";

test("a thread opens, shows New since, folds, and its count is 0 after a reload", async ({ page }) => {
  await signIn(page);
  await page.goto("/v2/#/conversations?sort=needs");
  const row = page.getByRole("button", { name: FIXTURE.thread });
  // The owner read the root before the two replies came.
  await expect(row).toContainText("2 new");

  const marked = page.waitForResponse((r) => r.url().endsWith("/api/read-state") && r.request().method() === "PUT" && r.ok());
  await row.click();
  await expect(row).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText(/^New since \d\d:\d\d$/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Show 1 earlier message" })).toBeVisible();
  await expect(page.getByText("The branch is merged.")).toBeVisible();
  // The last new message in view for a second marks the thread read.
  await marked;

  await row.click();
  await expect(row).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText("The branch is merged.")).toHaveCount(0);

  await page.reload();
  await expect(row).toBeVisible();
  await expect(row).not.toContainText(/\d+ new/);
});

// After the test above: Mark all read clears every count of this one server.
test("mark all read clears the count of a thread opened by its id, and it stays clear after a reload", async ({ page }) => {
  await signIn(page);
  await page.goto("/v2/#/conversations?sort=unanswered&open=beta-e2e-cache");
  const opened = page.getByRole("list", { name: "Opened threads" });
  const row = opened.getByRole("button", { name: FIXTURE.linked });
  await expect(row).toContainText("2 new");

  const marked = page.waitForResponse((r) => r.url().endsWith("/api/read-state") && r.request().method() === "PUT" && r.ok());
  await page.getByRole("button", { name: "Mark all read" }).click();
  await marked;
  await expect(row).not.toContainText(/\d+ new/);
  await expect(page.getByText(/^\d+ new$/)).toHaveCount(0);

  await page.reload();
  await expect(row).toBeVisible();
  await expect(row).not.toContainText(/\d+ new/);
  await expect(page.getByText(/^\d+ new$/)).toHaveCount(0);
});
