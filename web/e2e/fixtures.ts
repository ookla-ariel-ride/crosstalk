// The test of the browser tests: the server that global-setup.ts started, and
// for every test a check that the page logs no error and loads nothing from
// another host.
import { test as base, expect, type Page } from "@playwright/test";

export { expect };

export const test = base.extend<{ pageHealth: void }>({
  baseURL: async ({}, use) => {
    await use(process.env.CROSSTALK_E2E_URL);
  },
  pageHealth: [
    async ({ page, baseURL }, use) => {
      const host = new URL(baseURL ?? "").host;
      const problems: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error") problems.push("console error: " + m.text());
      });
      page.on("pageerror", (e) => problems.push("page error: " + e.message));
      page.on("request", (r) => {
        const url = new URL(r.url());
        if ((url.protocol === "http:" || url.protocol === "https:" || url.protocol.startsWith("ws")) && url.host !== host)
          problems.push("request to another host: " + r.url());
      });
      await use();
      expect(problems).toEqual([]);
    },
    { auto: true },
  ],
});

/** Signs in as the owner on the People page with the fixture's PIN. */
export async function signIn(page: Page): Promise<void> {
  await page.goto("/v2/#/people");
  await page.getByLabel("PIN", { exact: true }).fill(process.env.CROSSTALK_E2E_PIN ?? "");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("You are signed in as the owner.")).toBeVisible();
}

/** The row of the thread titled `title` in the stream. */
export function threadRow(page: Page, title: string) {
  return page.getByRole("button", { name: title });
}
