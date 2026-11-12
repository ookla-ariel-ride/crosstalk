// The server of the browser tests keeps its files in its temp folder: a sign-in
// writes the sessions file there, never in the home folder of the person who runs the tests.
import { existsSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { expect, signIn, test } from "./fixtures";

/** The size and time of the real home's sessions file, or null; the file is never opened. */
function realSessions(): { size: number; mtimeMs: number } | null {
  const file = join(homedir(), ".crosstalk-sessions.json");
  if (!existsSync(file)) return null;
  const { size, mtimeMs } = statSync(file);
  return { size, mtimeMs };
}

test("a sign-in writes the sessions file inside the temp folder of the run, and not in the real home", async ({ page }) => {
  const before = realSessions();
  await signIn(page);
  expect(existsSync(join(process.env.CROSSTALK_E2E_ROOT ?? "", "home", ".crosstalk-sessions.json"))).toBe(true);
  expect(realSessions()).toEqual(before);
});
