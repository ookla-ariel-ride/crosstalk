// A reader who is not signed in: the page shows no owner control, the server
// sends no owner data, and every write gets 401 or 403. The spec adds its own
// agreement and waiting row (ids with "viewer") and closes the waiting row after.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
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

interface Snap {
  roster: Record<string, { trust?: string }>;
  collections: Record<string, Record<string, unknown>>;
}

const snapshot = async (page: Page) => (await (await page.request.get("/api/snapshot")).json()) as Snap;

test("a viewer gets no owner data from the API, and the owner does", async ({ page }) => {
  // The server takes this session for a viewer: no owner, no right to edit.
  const me = await page.request.get("/api/me");
  expect(await me.json()).toMatchObject({ isOwner: false, canEdit: false, viewer: "viewer" });

  const threads = await page.request.get("/api/threads?sort=needs&unread=1");
  expect(threads.status()).toBe(200);
  const body = (await threads.json()) as { threads: Record<string, unknown>[] };
  expect(body).not.toHaveProperty("unread_total");
  expect(body.threads.length).toBeGreaterThan(0);
  for (const t of body.threads) expect(t).not.toHaveProperty("unread");
  expect((await page.request.get("/api/read-state")).status()).toBe(403);

  // The control: the same request with the owner's session gets the owner's counts.
  expect((await page.request.post("/api/login", { data: { pin: process.env.CROSSTALK_E2E_PIN } })).ok()).toBe(true);
  const owner = (await (await page.request.get("/api/threads?sort=needs&unread=1")).json()) as { unread_total?: unknown };
  expect(owner.unread_total).toEqual(expect.any(Number));
});

test("a viewer's writes get 401 or 403 and change nothing", async ({ page }) => {
  const roster = join(root(), "participants.json");
  const readState = join(root(), "read-state.json");
  const rosterBefore = readFileSync(roster, "utf8");
  const stateBefore = readFileSync(readState, "utf8");
  // No other spec acks this thread's root for the owner.
  const ackId = "owner--beta-e2e-cache";
  expect((await snapshot(page)).collections.acks).not.toHaveProperty(ackId);

  const message = { summary: "A viewer's try", body: "", kind: "owner" };
  const tries = {
    ownerMessage: page.request.post("/api/append", { data: { from: "owner", op: "set", collection: "messages", id: "own-viewer-try", data: { ...message, from: "owner" } } }),
    alphaMessage: page.request.post("/api/append", { data: { from: "alpha", op: "set", collection: "messages", id: "alpha-viewer-try", data: { ...message, from: "alpha", kind: "sync" } } }),
    trust: page.request.post("/api/append", { data: { from: "owner", op: "update", collection: "participants", id: "beta", data: { trust: "quarantined" } } }),
    ack: page.request.post("/api/ack", { data: { item: "beta-e2e-cache" } }),
    onboard: page.request.post("/api/onboard", { data: { id: "viewer-made", name: "Viewer made", kind: "tool" } }),
    mute: page.request.put("/api/read-state", { data: { thread: "beta-e2e-schema", muted: true } }),
  };
  for (const [name, sent] of Object.entries(tries)) {
    const r = await sent;
    expect([401, 403], name).toContain(r.status());
    // No answer hands out a token or a kit.
    expect(await r.text(), name).not.toMatch(/"(token|kit)"/);
  }

  const snap = await snapshot(page);
  expect(snap.collections.messages).not.toHaveProperty("own-viewer-try");
  expect(snap.collections.messages).not.toHaveProperty("alpha-viewer-try");
  expect(snap.collections.acks).not.toHaveProperty(ackId);
  expect(snap.roster.beta?.trust).toBe("approved");
  expect(snap.roster).not.toHaveProperty("viewer-made");
  // The roster and the read state are the same files, byte for byte.
  expect(readFileSync(roster, "utf8")).toBe(rosterBefore);
  expect(readFileSync(readState, "utf8")).toBe(stateBefore);
  // relay.py knows no such participant, so it has no key to mint or show.
  expect(() => relay(root(), "token", "viewer-made")).toThrow(/unknown participant 'viewer-made'/);
});
