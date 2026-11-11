// The browser tests: e2e/global-setup.ts builds a fixture data root in a temp
// folder and starts `relay.py serve` on a free port; the tests load /v2/ from
// it. Run `npm run build` first: the server serves web/dist/.
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  // One server and one data root: the tests run one at a time.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  reporter: "list",
  globalSetup: "./e2e/global-setup.ts",
  use: { trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});
