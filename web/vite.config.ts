/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  // The Python server serves web/dist/ at /v2/.
  base: "/v2/",
  plugins: [react()],
  server: {
    // `npm run dev` talks to a relay server on its usual port.
    proxy: { "/api": "http://127.0.0.1:8585" },
  },
  build: {
    // Every asset is its own file under assets/, named by its content, so
    // the server can cache it for a year. No data: URLs in the CSS or HTML.
    assetsInlineLimit: 0,
    // dist/.vite/manifest.json: the chunk graph that scripts/check-budget.mjs
    // counts. The server never reads it.
    manifest: true,
  },
  test: {
    // e2e/ holds the Playwright tests (playwright.config.ts); Vitest skips them.
    exclude: ["**/node_modules/**", "**/.git/**", "e2e/**"],
  },
});
