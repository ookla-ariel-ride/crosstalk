// What "Mark all read" shows on the default sort (Needs you first). The test
// sets the read state it needs and puts the old one back after, so the order
// of the tests does not matter and no other test sees its marks.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, signIn, test } from "./fixtures";
import { FIXTURE, relay } from "./global-setup";

const file = () => join(process.env.CROSSTALK_E2E_ROOT ?? "", "read-state.json");
let saved = "";

/** Write `state` with a `rev` above every earlier one, so the page keeps it. */
function write(state: Record<string, unknown>): void {
  writeFileSync(file(), JSON.stringify({ ...state, rev: Date.now() + 1000 }));
}

test.beforeEach(() => {
  saved = readFileSync(file(), "utf8");
  // Nothing read: each thread holds a message by another participant.
  write({ threads: {}, muted: [], all_before: null });
});

test.afterEach(() => {
  write(JSON.parse(saved) as Record<string, unknown>);
});

test("on the default sort the button counts the server's unread threads, and a click says what it did and clears the counts", async ({ page }) => {
  await signIn(page);
  await page.goto("/v2/");
  const list = page.getByRole("list", { name: "Conversations" });
  const fresh = list.getByText(/^\d+ new$/);
  await expect(fresh.first()).toBeVisible();
  // A row that needs the owner shows its count too.
  await expect(list.getByRole("button").filter({ hasText: "needs you" }).filter({ hasText: /\d+ new/ }).first()).toBeVisible();

  const button = page.getByRole("button", { name: /^Mark all read \(\d+\)$/ });
  await expect(button).toBeEnabled();
  const n = Number(/\((\d+)\)/.exec((await button.textContent()) ?? "")?.[1]);
  await expect(fresh).toHaveCount(n);
  await expect(page.getByRole("link", { name: `Conversations ${n} unread` })).toBeVisible();
  await expect(page).toHaveTitle(`(${n}) Crosstalk`);

  const status = page.getByRole("status");
  await expect(status).toHaveAttribute("aria-live", "polite");
  await button.click();
  await expect(status).toHaveText(`Marked ${n} thread${n === 1 ? "" : "s"} read`);
  await expect(fresh).toHaveCount(0);
  await expect(page.getByRole("button", { name: "All read" })).toBeDisabled();
  await expect(page).toHaveTitle("Crosstalk");
});

test("on the view New, mark all read leaves the list empty, also after a reload, until a new post comes", async ({ page }) => {
  await signIn(page);
  await page.goto("/v2/#/conversations?sort=new");
  const list = page.getByRole("list", { name: "Conversations" });
  await expect(list.getByText(/^\d+ new$/).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /^New \(\d+\)$/ })).toBeVisible();

  await page.getByRole("button", { name: /^Mark all read \(\d+\)$/ }).click();
  const empty = page.getByText("All read. New posts show here.");
  await expect(empty).toBeVisible();
  await expect(page.getByRole("list", { name: "Conversations" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "New", exact: true })).toBeVisible();

  await page.reload();
  await expect(empty).toBeVisible();
  await expect(page.getByRole("list", { name: "Conversations" })).toHaveCount(0);

  // A new post in the fixture thread that only this test reads. The log stamps whole seconds and the mark-all time covers its
  // own second, so the post comes in the next one.
  await page.waitForTimeout(1100);
  relay(process.env.CROSSTALK_E2E_ROOT ?? "", "reply", "--harness", "codex", "--model", "gpt-5", "--from", "beta",
    "--to-id", "beta-e2e-index", "--summary", "One more", "--body", "The index is rebuilt again.");
  // The live update brings its thread into the view.
  await expect(list.getByRole("button", { name: FIXTURE.feedback })).toBeVisible({ timeout: 20000 });
  await expect(empty).toHaveCount(0);
});
