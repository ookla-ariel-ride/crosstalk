import { FIXTURE } from "./global-setup";
import { expect, signIn, test } from "./fixtures";

test("an answer to a question closes it", async ({ page }) => {
  await signIn(page);
  await page.goto("/v2/#/needs");
  const needsList = page.getByRole("list", { name: "Needs you" });
  await expect(page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Needs you 2" })).toBeVisible();
  const row = needsList.getByRole("button", { name: FIXTURE.answerQuestion });
  await row.click();
  const answer = page.getByRole("group", { name: "Answer" });
  await expect(answer.getByRole("button", { name: /A free port/ })).toContainText("Recommended");
  const sent = page.waitForResponse((r) => r.url().endsWith("/api/append") && r.request().method() === "POST");
  await answer.getByRole("button", { name: /A free port/ }).click();
  expect((await sent).ok()).toBe(true);
  // The refetched stream no longer lists it as needing the owner (the row
  // leaves at once, with its "Answer sent"); the other question stays.
  await expect(needsList.getByRole("button", { name: FIXTURE.answerQuestion })).toHaveCount(0);
  await expect(needsList.getByRole("button", { name: FIXTURE.openQuestion })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Needs you 1" })).toBeVisible();

  // In Conversations the thread shows the question as answered.
  await page.goto("/v2/#/conversations?sort=new");
  await page.getByRole("button", { name: FIXTURE.answerQuestion }).click();
  await expect(page.getByText("Answered", { exact: true })).toBeVisible();
  await expect(page.getByRole("group", { name: "Answer" })).toHaveCount(0);
});
