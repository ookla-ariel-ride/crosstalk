// The server of the browser tests keeps its files in its temp folder: a sign-in
// writes the sessions file there, never in the home folder of the person who runs the tests.
import { existsSync } from "node:fs";
import { join } from "node:path";
import { expect, signIn, test } from "./fixtures";

test("a sign-in writes the sessions file inside the temp folder of the run", async ({ page }) => {
  await signIn(page);
  expect(existsSync(join(process.env.CROSSTALK_E2E_ROOT ?? "", "home", ".crosstalk-sessions.json"))).toBe(true);
});
