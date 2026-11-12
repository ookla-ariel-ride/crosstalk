// A reader who is not signed in: the page shows no owner control, the server
// sends no owner data, and every write gets 401 or 403. The spec adds its own
// agreement and waiting row (ids with "viewer") and closes the waiting row after.
import { expect, test } from "./fixtures";
import { FIXTURE, relay } from "./global-setup";

const root = () => process.env.CROSSTALK_E2E_ROOT ?? "";
const AGREEMENT = "Viewer agreement";
const WAITING = { id: "owner-viewer-wait", summary: "Viewer waiting row" };

test.beforeAll(() => {
  relay(root(), "append", "--from", "alpha", "--op", "set", "--collection", "agreements", "--id", "alpha-viewer-agreement",
    "--data", JSON.stringify({ summary: AGREEMENT, body: "One agreement for the viewer.", status: "agreed", builder: "alpha", adopter: "all", commits: {} }));
});

test("a viewer sees no owner controls on the main views", async ({ page }) => {
  // An open waiting row adds to Needs you while this test runs; the finally block closes it.
  relay(root(), "append", "--from", "owner", "--op", "set", "--collection", "waiting", "--id", WAITING.id,
    "--data", JSON.stringify({ since: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"), project: "owner", summary: WAITING.summary, body: "", done: null }));
  try {
    const main = page.getByRole("main");
    await page.goto("/v2/#/conversations?sort=new");
    await expect(main.getByRole("heading", { level: 1 })).toHaveText("Conversations");
    await main.getByRole("button", { name: FIXTURE.thread }).click();
    await expect(main.getByText("The new column is in the branch.")).toBeVisible();
    // Where an action waits, a viewer gets a link to sign in, and nothing else.
    await expect(main.getByRole("link", { name: "Sign in" }).first()).toBeVisible();
    for (const name of ["Start a thread", "Reply", "Record as decision"]) {
      await expect(main.getByRole("button", { name, exact: true }), name).toHaveCount(0);
    }

    await page.goto("/v2/#/needs");
    await expect(main.getByRole("heading", { level: 1 })).toHaveText("Needs you");
    await main.getByRole("button", { name: FIXTURE.openQuestion }).click();
    await expect(main.getByText("The tests pass.")).toBeVisible();
    await expect(main.getByRole("group", { name: "Answer" })).toHaveCount(0);
    await main.getByRole("button", { name: WAITING.summary }).click();
    await expect(main.getByRole("button", { name: "Mark done" })).toHaveCount(0);
    await expect(main.getByRole("button", { name: /^No action needed/ })).toHaveCount(0);

    await page.goto("/v2/#/agreements");
    await main.getByRole("button", { name: AGREEMENT }).click();
    await expect(main.getByText("One agreement for the viewer.")).toBeVisible();
    await expect(main.getByRole("button", { name: /^(Hold|Release hold)$/ })).toHaveCount(0);

    await page.goto("/v2/#/people");
    await expect(main.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
    await expect(main.getByRole("button", { name: "Add participant" })).toHaveCount(0);
    await expect(main.getByText("You are signed in as the owner.")).toHaveCount(0);
  } finally {
    relay(root(), "append", "--from", "owner", "--op", "update", "--collection", "waiting", "--id", WAITING.id,
      "--data", JSON.stringify({ done: new Date().toISOString().replace(/\.\d{3}Z$/, "Z") }));
  }
});

test("a viewer gets no owner data from the API", async ({ page }) => {
  const threads = await page.request.get("/api/threads?sort=needs&unread=1");
  expect(threads.status()).toBe(200);
  const body = (await threads.json()) as { threads: Record<string, unknown>[] };
  expect(body).not.toHaveProperty("unread_total");
  expect(body.threads.length).toBeGreaterThan(0);
  for (const t of body.threads) expect(t).not.toHaveProperty("unread");
  expect((await page.request.get("/api/read-state")).status()).toBe(403);
  const me = (await (await page.request.get("/api/me")).json()) as { isOwner?: boolean };
  expect(me.isOwner).not.toBe(true);
});

test("a viewer's writes get 401 or 403 and change nothing", async ({ page }) => {
  const id = "own-viewer-try";
  const message = { summary: "A viewer's try", body: "", kind: "owner" };
  const tries = [
    page.request.post("/api/append", { data: { from: "owner", op: "set", collection: "messages", id, data: { ...message, from: "owner" } } }),
    page.request.post("/api/append", { data: { from: "alpha", op: "set", collection: "messages", id: "alpha-viewer-try", data: { ...message, from: "alpha", kind: "sync" } } }),
    page.request.post("/api/append", { data: { from: "owner", op: "update", collection: "participants", id: "beta", data: { trust: "approved" } } }),
    page.request.post("/api/ack", { data: { item: "alpha-e2e-runbook" } }),
    page.request.post("/api/onboard", { data: { id: "viewer-made", name: "Viewer made", kind: "tool" } }),
    page.request.put("/api/read-state", { data: { thread: "beta-e2e-schema", muted: true } }),
  ];
  for (const r of await Promise.all(tries)) expect([401, 403], r.url()).toContain(r.status());
  const snap = (await (await page.request.get("/api/snapshot")).json()) as { collections: Record<string, Record<string, unknown>> };
  expect(snap.collections.messages).not.toHaveProperty(id);
  expect(snap.collections.messages).not.toHaveProperty("alpha-viewer-try");
});
