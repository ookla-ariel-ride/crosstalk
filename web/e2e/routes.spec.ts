// A walk of every route of the page as the owner: the four sorts of
// Conversations, Needs you, the nine sections and People. Each route shows its
// heading and a row of the fixture, with no failed request. The spec adds its
// own rows (ids with "walk"), which no other spec reads, and puts back the read
// state it changes.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, signIn, test } from "./fixtures";
import { FIXTURE, relay } from "./global-setup";

const root = () => process.env.CROSSTALK_E2E_ROOT ?? "";
const file = () => join(root(), "read-state.json");
let saved = "";

const ROWS = {
  decision: "Walk decision",
  agreement: "Walk agreement",
  progress: "walk-progress",
  schedule: "walk-job",
  claim: "docs/walk.md",
  incident: "Walk incident",
  request: "Walk request",
  ask: "TURN ASK alpha walk-branch",
};

test.beforeAll(() => {
  const r = root();
  const set = (collection: string, id: string, data: Record<string, unknown>) =>
    relay(r, "append", "--from", "alpha", "--op", "set", "--collection", collection, "--id", id, "--data", JSON.stringify(data));
  set("decisions", "alpha-walk-decision", { by: "alpha", summary: ROWS.decision, body: "One decision for the walk.", project: "alpha" });
  set("agreements", "alpha-walk-agreement", { summary: ROWS.agreement, body: "One agreement for the walk.", status: "agreed", builder: "alpha", adopter: "all", commits: {} });
  set("progress", "alpha-walk-progress", { project: "alpha", item: ROWS.progress, pct: 40 });
  relay(r, "schedule", "set", "--from", "alpha", "--name", ROWS.schedule, "--cron", "0 9 * * *", "--about", "A job for the walk.");
  relay(r, "claim", "--from", "alpha", "--path", ROWS.claim, "--note", "A claim for the walk.");
  relay(r, "incident", "--from", "alpha", "--slug", "walk", "--summary", ROWS.incident);
  relay(r, "adoption", "set", "--from", "alpha", "--state", "adopted", "--item", "alpha-walk-decision");
  // A land ask, with a reply from Beta so the thread is not folded as routine.
  relay(r, "append", "--harness", "codex", "--model", "gpt-5", "--from", "alpha", "--op", "set", "--collection", "messages", "--id", "alpha-walk-ask",
    "--data", JSON.stringify({ from: "alpha", kind: "sync", summary: ROWS.ask, body: "The walk asks for a turn.", to: ["beta"] }));
  relay(r, "reply", "--harness", "codex", "--model", "gpt-5", "--from", "beta", "--to-id", "alpha-walk-ask", "--summary", "Noted", "--body", "I saw the ask.");
  // A request with no reply, for the sort Unanswered: "No action needed" takes the fixture's request off it.
  relay(r, "append", "--harness", "codex", "--model", "gpt-5", "--from", "beta", "--op", "set", "--collection", "messages", "--id", "beta-walk-request",
    "--data", JSON.stringify({ from: "beta", kind: "request", summary: ROWS.request, body: "The walk asks Alpha.", to: ["alpha"] }));
});

test.beforeEach(() => {
  saved = readFileSync(file(), "utf8");
  // Nothing read, so the sort New lists rows.
  writeFileSync(file(), JSON.stringify({ threads: {}, muted: [], all_before: null, rev: Date.now() + 1000 }));
});

test.afterEach(() => {
  writeFileSync(file(), JSON.stringify({ ...(JSON.parse(saved) as Record<string, unknown>), rev: Date.now() + 2000 }));
});

/**
 * Each route: its hash, its heading, and a row it shows. "any" takes the first
 * row, for an order that other specs change; null: the fixture has no row.
 */
const ROUTES: [string, string, string | null][] = [
  ["#/needs", "Needs you", FIXTURE.openQuestion],
  ["#/conversations?sort=needs", "Conversations", FIXTURE.openQuestion],
  ["#/conversations?sort=new", "Conversations", ROWS.ask],
  ["#/conversations?sort=hot", "Conversations", "any"],
  ["#/conversations?sort=unanswered", "Conversations", ROWS.request],
  ["#/queue", "Land queue", "alpha: walk-branch"],
  ["#/progress", "Progress", ROWS.progress],
  // The reviews section lists only rows that the roster's coordinator posted; the fixture names no coordinator.
  ["#/reviews", "Reviews", null],
  ["#/schedules", "Schedules", ROWS.schedule],
  ["#/decisions", "Decisions", ROWS.decision],
  ["#/agreements", "Agreements", ROWS.agreement],
  ["#/lessons", "Lessons and incidents", ROWS.incident],
  ["#/claims", "File claims", ROWS.claim],
  ["#/adoption", "Adoption", "alpha-walk-decision"],
  ["#/people", "People", "Relay bot"],
];

test("every route loads with its heading and a fixture row, and no request fails", async ({ page }) => {
  const failed: string[] = [];
  page.on("requestfailed", (r) => failed.push(`${r.url()}: ${r.failure()?.errorText ?? ""}`));
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.url()}: HTTP ${r.status()}`);
  });
  await signIn(page);
  const main = page.getByRole("main");
  for (const [hash, heading, row] of ROUTES) {
    await page.goto("/v2/" + hash);
    await expect(main.getByRole("heading", { level: 1 }), hash).toHaveText(heading);
    if (row === null) await expect(main.getByText("This section has no rows yet."), hash).toBeVisible();
    else if (row === "any") await expect(main.getByRole("list", { name: "Conversations" }).getByRole("listitem").first(), hash).toBeVisible();
    else await expect(main.getByRole("button", { name: row }).first(), hash).toBeVisible();
    await expect(main.getByRole("alert"), hash).toHaveCount(0);
  }
  expect(failed).toEqual([]);
});
