// Mark all read on the shared fixture server. The test sets the read state it
// needs and puts the old one back after, so the order of the tests does not
// matter and no other test sees its marks.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { FIXTURE } from "./global-setup";
import { expect, signIn, test } from "./fixtures";

const file = () => join(process.env.CROSSTALK_E2E_ROOT ?? "", "read-state.json");
let saved = "";

/** Write `state` with a `rev` above every earlier one, so the page keeps it. */
function write(state: Record<string, unknown>): void {
  writeFileSync(file(), JSON.stringify({ ...state, rev: Date.now() + 1000 }));
}

test.beforeEach(() => {
  saved = readFileSync(file(), "utf8");
  // Nothing read: the linked thread has its two messages new.
  write({ threads: {}, muted: [], all_before: null });
});

test.afterEach(() => {
  write(JSON.parse(saved) as Record<string, unknown>);
});

test("mark all read clears the count of a thread opened by its id, and it stays clear after a reload", async ({ page }) => {
  await signIn(page);
  // The unanswered order leaves the thread out, so it shows as a thread opened by its id.
  await page.goto("/v2/#/conversations?sort=unanswered&open=beta-e2e-cache");
  const row = page.getByRole("list", { name: "Opened threads" }).getByRole("button", { name: FIXTURE.linked });
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
