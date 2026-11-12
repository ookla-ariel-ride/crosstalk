// The owner's actions other than Answer and Mark all read: each test acts
// through the page, then checks the page and the server's state. The spec adds
// its own rows (ids with "actions"), which no other spec reads, and puts back
// each state file it changes: the read state and the roster.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Page, Response } from "@playwright/test";
import { expect, signIn, test } from "./fixtures";
import { relay } from "./global-setup";

const root = () => process.env.CROSSTALK_E2E_ROOT ?? "";
const readState = () => join(root(), "read-state.json");
const who = ["--harness", "codex", "--model", "gpt-5"];
const stamp = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

const THREAD = { root: "beta-actions-thread", title: "Owner actions thread", body: "The thread that the owner acts in." };
const AGREEMENT = { id: "alpha-actions-agreement", title: "Owner actions agreement" };
let saved = "";

test.beforeAll(() => {
  // Two participants, so the thread is not folded as routine.
  relay(root(), "append", ...who, "--from", "beta", "--op", "set", "--collection", "messages", "--id", THREAD.root,
    "--data", JSON.stringify({ from: "beta", kind: "sync", summary: THREAD.title, body: THREAD.body, to: ["alpha"] }));
  relay(root(), "reply", ...who, "--from", "alpha", "--to-id", THREAD.root, "--summary", "Seen", "--body", "Alpha read it.");
  relay(root(), "append", "--from", "alpha", "--op", "set", "--collection", "agreements", "--id", AGREEMENT.id,
    "--data", JSON.stringify({ summary: AGREEMENT.title, body: "One agreement to hold.", status: "agreed", builder: "alpha", adopter: "all", commits: {} }));
});

test.beforeEach(() => {
  saved = readFileSync(readState(), "utf8");
  // Nothing read: an open thread shows every message.
  writeFileSync(readState(), JSON.stringify({ threads: {}, muted: [], all_before: null, rev: Date.now() + 1000 }));
});

test.afterEach(async ({ page }) => {
  // Leave the page first, so no late PUT of the read state lands after the restore.
  await page.goto("about:blank");
  writeFileSync(readState(), JSON.stringify({ ...(JSON.parse(saved) as Record<string, unknown>), rev: Date.now() + 2000 }));
});

/** One document of the server's replay, read with the owner's session. */
async function doc(page: Page, collection: string, id: string): Promise<Record<string, unknown> | undefined> {
  const r = await page.request.get("/api/snapshot");
  expect(r.ok()).toBe(true);
  const snap = (await r.json()) as { collections: Record<string, Record<string, Record<string, unknown>>> };
  return snap.collections[collection]?.[id];
}

/** The answer to the next POST to /api/append. */
function nextAppend(page: Page): Promise<Response> {
  const sent = page.waitForResponse((r) => r.url().endsWith("/api/append") && r.request().method() === "POST");
  // A test that fails before it awaits the answer leaves no unhandled rejection.
  sent.catch(() => undefined);
  return sent;
}

/** The body that the page sent, once the server took it. */
async function appended(sent: Promise<Response>) {
  const r = await sent;
  expect(r.ok()).toBe(true);
  return r.request().postDataJSON() as { id: string; collection: string; data: Record<string, unknown> };
}

async function openThread(page: Page) {
  await page.goto(`/v2/#/conversations?sort=new&open=${THREAD.root}`);
  const main = page.getByRole("main");
  await expect(main.getByRole("button", { name: THREAD.title })).toHaveAttribute("aria-expanded", "true");
  await expect(main.getByText(THREAD.body)).toBeVisible();
  return main;
}

test("Mute thread hides the thread on the server's read state, and Unmute brings it back", async ({ page }) => {
  await signIn(page);
  let main = await openThread(page);
  // The mute's own PUT: the page also PUTs a read mark once the thread was in view.
  const put = page.waitForResponse((r) => {
    if (!r.url().endsWith("/api/read-state") || r.request().method() !== "PUT") return false;
    const sent = r.request().postDataJSON() as { thread?: unknown; muted?: unknown } | null;
    return sent?.thread === THREAD.root && sent?.muted === true;
  });
  await main.getByRole("button", { name: "Mute thread" }).click();
  expect((await put).ok()).toBe(true);
  // The stream folds the thread into one "muted" line at once.
  await expect(main.getByRole("button", { name: /\b1 muted\b/ })).toBeVisible();
  await expect(main.getByRole("button", { name: THREAD.title })).toHaveCount(0);
  const state = async () => ((await (await page.request.get("/api/read-state")).json()) as { muted: string[] }).muted;
  await expect.poll(state).toContain(THREAD.root);

  // The sort Hot folds the muted thread into one line, also after a reload.
  await page.goto("/v2/#/conversations?sort=hot");
  await page.reload();
  await expect(main.getByRole("button", { name: /\b1 muted\b/ })).toBeVisible();
  await expect(main.getByRole("button", { name: THREAD.title })).toHaveCount(0);

  // Opened by its id, the muted thread shows above the list with Unmute.
  main = await openThread(page);
  await main.getByRole("button", { name: "Unmute thread" }).click();
  await expect(main.getByRole("button", { name: "Mute thread" })).toBeVisible();
  await expect.poll(state).not.toContain(THREAD.root);
});

test("Reply posts the owner's message in the thread", async ({ page }) => {
  await signIn(page);
  const main = await openThread(page);
  // The thread's own Reply, at its end, after the one under each message.
  await main.getByRole("button", { name: "Reply", exact: true }).last().click();
  await main.getByLabel("Summary").fill("Owner reply from the browser test");
  await main.getByLabel("Message").fill("The owner answers in the thread.");
  const sent = nextAppend(page);
  await main.getByRole("button", { name: "Send" }).click();
  const body = await appended(sent);
  await expect(main.getByText("The owner answers in the thread.")).toBeVisible();
  await expect(main.getByLabel("Message")).toHaveCount(0);

  const thread = (await (await page.request.get("/api/thread/" + THREAD.root)).json()) as { messages: Record<string, unknown>[] };
  expect(thread.messages.find((m) => m.id === body.id)).toMatchObject({
    from: "owner",
    replyTo: THREAD.root,
    summary: "Owner reply from the browser test",
    body: "The owner answers in the thread.",
  });
});

test("Start a thread posts a new thread to the participants ticked", async ({ page }) => {
  await signIn(page);
  await page.goto("/v2/#/conversations?sort=needs");
  const main = page.getByRole("main");
  await main.getByRole("button", { name: "Start a thread" }).click();
  await main.getByRole("checkbox", { name: "Alpha", exact: true }).check();
  await main.getByLabel("Summary").fill("Owner thread from the browser test");
  await main.getByLabel("Message").fill("A new thread for Alpha.");
  const sent = nextAppend(page);
  await main.getByRole("button", { name: "Send" }).click();
  const body = await appended(sent);
  await expect(main.getByRole("button", { name: "Start a thread" })).toBeVisible();

  expect(await doc(page, "messages", body.id)).toMatchObject({
    from: "owner",
    kind: "owner",
    to: ["alpha"],
    summary: "Owner thread from the browser test",
    body: "A new thread for Alpha.",
  });
  expect((await doc(page, "messages", body.id))?.replyTo).toBeUndefined();
  // The page lists it: opened by its id, it shows as a thread of its own.
  await page.goto(`/v2/#/conversations?sort=new&open=${body.id}`);
  await expect(main.getByRole("button", { name: "Owner thread from the browser test" })).toBeVisible();
});

test("Record as decision writes a decision row for the message", async ({ page }) => {
  await signIn(page);
  const main = await openThread(page);
  const rootMessage = main.locator("article").filter({ hasText: THREAD.body });
  const sent = nextAppend(page);
  await rootMessage.getByRole("button", { name: "Record as decision" }).click();
  const body = await appended(sent);
  expect(body.id).toBe("d-from-" + THREAD.root);
  await expect(rootMessage.getByRole("button", { name: "Recorded" })).toBeDisabled();

  expect(await doc(page, "decisions", "d-from-" + THREAD.root)).toMatchObject({ by: "owner", summary: THREAD.title, body: THREAD.body });
  await page.reload();
  await expect(main.locator("article").filter({ hasText: THREAD.body }).getByRole("button", { name: "Recorded" })).toBeDisabled();
  await page.goto("/v2/#/decisions");
  await expect(main.getByRole("button", { name: THREAD.title })).toBeVisible();
});

test("Mark done closes a waiting row", async ({ page }) => {
  const id = "owner-actions-wait-" + Date.now();
  relay(root(), "append", "--from", "owner", "--op", "set", "--collection", "waiting", "--id", id,
    "--data", JSON.stringify({ since: stamp(), project: "owner", summary: "Owner actions waiting row", body: "Close this row.", done: null }));
  await signIn(page);
  await page.goto("/v2/#/needs");
  const waiting = page.getByRole("main").getByRole("list", { name: "Waiting on you" });
  await waiting.getByRole("button", { name: "Owner actions waiting row" }).click();
  const sent = nextAppend(page);
  await waiting.getByRole("button", { name: "Mark done" }).click();
  expect((await appended(sent)).id).toBe(id);
  await expect(waiting.getByRole("button", { name: "Owner actions waiting row" })).toHaveCount(0);
  expect((await doc(page, "waiting", id))?.done).toEqual(expect.any(String));
  await page.reload();
  await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toHaveText("Needs you");
  await expect(page.getByRole("main").getByRole("button", { name: "Owner actions waiting row" })).toHaveCount(0);
});

test("Hold and Release hold change an agreement's hold", async ({ page }) => {
  await signIn(page);
  await page.goto("/v2/#/agreements");
  const main = page.getByRole("main");
  await main.getByRole("button", { name: AGREEMENT.title }).click();
  await main.getByRole("button", { name: "Hold", exact: true }).click();
  await expect(main.getByRole("button", { name: "Release hold" })).toBeVisible();
  await expect(main.getByText("held by owner")).toBeVisible();
  expect(await doc(page, "agreements", AGREEMENT.id)).toMatchObject({ hold: true, holdNote: "held by owner" });

  await main.getByRole("button", { name: "Release hold" }).click();
  await expect(main.getByRole("button", { name: "Hold", exact: true })).toBeVisible();
  await expect(main.getByText("held by owner")).toHaveCount(0);
  const released = await doc(page, "agreements", AGREEMENT.id);
  expect(released?.hold).toBe(false);
  expect(released?.status).toBe("agreed");
});

test.describe("a participant to approve", () => {
  const roster = () => join(root(), "participants.json");
  let before = "";

  test.beforeEach(() => {
    before = readFileSync(roster(), "utf8");
    // A tool owes no mark on a lesson, so the marks test sees no change while it waits.
    relay(root(), "onboard", "omicron", "--name", "Omicron", "--kind", "tool", "--color", "#0b3d91", "--colorDark", "#6fa8f7");
  });

  test.afterEach(async ({ request }) => {
    writeFileSync(roster(), before);
    // The approval's log line stays; the server must still find the roster sound without its entry.
    expect(((await (await request.get("/api/health")).json()) as { ok: boolean }).ok).toBe(true);
  });

  test("Approve on People approves the participant", async ({ page }) => {
    await signIn(page);
    const main = page.getByRole("main");
    const row = main.getByRole("button", { name: /^Omicron/ });
    await expect(row).toContainText("not approved");
    await row.click();
    const sent = nextAppend(page);
    await main.getByRole("button", { name: "Approve", exact: true }).click();
    expect(await appended(sent)).toMatchObject({ collection: "participants", id: "omicron", data: { trust: "approved" } });
    await expect(row).not.toContainText("not approved");
    const entry = (JSON.parse(readFileSync(roster(), "utf8")) as { participants: { id: string; trust?: string }[] }).participants.find((p) => p.id === "omicron");
    expect(entry?.trust).toBe("approved");
  });
});
