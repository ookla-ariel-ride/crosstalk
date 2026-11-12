// Each fixture is the body the present page (index.html) sends for the same
// action, field for field; the values are made up. The comment above each
// names the function of index.html it comes from.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  addParticipant,
  answer,
  approveParticipant,
  holdAgreement,
  markDone,
  recordDecision,
  reply,
  signIn,
  splitAnswer,
  splitPost,
  startThread,
} from "./actions";

const T = "2026-11-11T10:00:00Z";
const ID = "own-20261111100000-ab12";
let sent: { url: string; body: unknown }[] = [];

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-11T10:00:00.123Z"));
  sent = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    sent.push({ url, body: JSON.parse(String(init.body)) });
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("owner actions send the present page's bodies", () => {
  test("signIn: user.login sends a six-digit PIN as pin, anything else as token", async () => {
    await signIn(" 123456 ");
    await signIn("example-owner-token");
    expect(sent).toEqual([
      { url: "/api/login", body: { pin: "123456" } },
      { url: "/api/login", body: { token: "example-owner-token" } },
    ]);
  });

  test("startThread: composer submit writes the message, then the row of its kind", async () => {
    await startThread(["alpha"], "decision", "Use one queue", "Details.", ID);
    await startThread([], "alert", "Disk is full", "", ID);
    expect(sent.map((s) => s.body)).toEqual([
      { from: "owner", op: "set", collection: "messages", id: ID, data: { t: T, from: "owner", kind: "owner", summary: "Use one queue", body: "Details.", to: ["alpha"] } },
      { from: "owner", op: "set", collection: "decisions", id: ID, data: { t: T, by: "owner", summary: "Use one queue", body: "Details.", project: "alpha", source: "Crosstalk page" } },
      { from: "owner", op: "set", collection: "messages", id: ID, data: { t: T, from: "owner", kind: "alert", summary: "Disk is full", body: "", important: true } },
    ]);
  });

  test("startThread: the extra rows of waiting, agreement, lesson and incident", async () => {
    await startThread(["alpha"], "waiting", "S", "B", ID);
    await startThread(["alpha", "beta"], "agreement", "S", "B", ID);
    await startThread([], "lesson", "S", "", ID);
    await startThread(["beta"], "incident", "S", "B", ID);
    await startThread([], "request", "S", "B", ID);
    const rows = sent.map((s) => s.body as { collection: string; id: string; data: Record<string, unknown> });
    expect(rows.map((r) => [r.collection, r.id, r.data.kind])).toEqual([
      ["messages", ID, "owner"],
      ["waiting", ID, undefined],
      ["messages", ID, "agreement"],
      ["agreements", "a-" + ID, undefined],
      ["messages", ID, "lesson"],
      ["lessons", "lesson-" + ID, undefined],
      ["messages", ID, "alert"],
      ["incidents", "i-" + ID, undefined],
      ["messages", ID, "request"],
    ]);
    expect(rows[1]?.data).toEqual({ since: T, project: "alpha", summary: "S", body: "B", done: null });
    expect(rows[3]?.data).toEqual({ t: T, updated: T, summary: "S", body: "B", status: "agreed", builder: "alpha", adopter: "all", commits: {} });
    expect(rows[5]?.data).toEqual({ t: T, project: "owner", summary: "S", body: "", fix: "S" });
    expect(rows[6]?.data).toMatchObject({ important: true, to: ["beta"] });
    expect(rows[7]?.data).toEqual({ start: T, end: null, project: "beta", summary: "S", body: "B", fix: "" });
  });

  test("reply: composer submit with draft.replyTo names the parent and its author", async () => {
    await reply("beta-0002", "Yes", "Go ahead.", ["beta"], ID);
    expect(sent).toEqual([
      {
        url: "/api/append",
        body: { from: "owner", op: "set", collection: "messages", id: ID, data: { t: T, from: "owner", kind: "owner", summary: "Yes", body: "Go ahead.", replyTo: "beta-0002", to: ["beta"] } },
      },
    ]);
  });

  test("answer: sendAnswer splits the first line off as the summary", async () => {
    await answer("alpha-0003", "b. Keep both\nThey serve different readers.", "alpha", ID);
    expect(sent[0]?.body).toEqual({
      from: "owner",
      op: "set",
      collection: "messages",
      id: ID,
      data: { t: T, from: "owner", kind: "owner", summary: "b. Keep both", body: "They serve different readers.", replyTo: "alpha-0003", to: ["alpha"] },
    });
  });

  test("markDone: renderWaiting's Mark done stamps done", async () => {
    await markDone("own-1");
    expect(sent[0]?.body).toEqual({ from: "owner", op: "update", collection: "waiting", id: "own-1", data: { done: T } });
  });

  test("recordDecision: msgCard's Record as decision writes d-from-<id>", async () => {
    await recordDecision({ id: "beta-0004", summary: "Ship on Friday" });
    expect(sent[0]?.body).toEqual({
      from: "owner",
      op: "set",
      collection: "decisions",
      id: "d-from-beta-0004",
      data: { t: T, by: "owner", project: "all", summary: "Ship on Friday", body: "Ship on Friday", source: "Crosstalk page" },
    });
  });

  test("holdAgreement: renderAgreements' Hold and Release hold", async () => {
    await holdAgreement({ id: "a-1" }, true);
    await holdAgreement({ id: "a-2", status: "held" }, false);
    expect(sent.map((s) => s.body)).toEqual([
      { from: "owner", op: "update", collection: "agreements", id: "a-1", data: { hold: true, holdNote: "held by owner", updated: T } },
      { from: "owner", op: "update", collection: "agreements", id: "a-2", data: { hold: false, holdNote: null, updated: T, status: "agreed" } },
    ]);
  });

  test("approveParticipant: rosterEntry's Approve sets trust", async () => {
    await approveParticipant("beta");
    expect(sent[0]?.body).toEqual({ from: "owner", op: "update", collection: "participants", id: "beta", data: { trust: "approved" } });
  });

  test("addParticipant: renderOnboard's form sends only the fields given", async () => {
    await addParticipant({ id: " beta ", name: "Beta", kind: "agent", repo: "", parent: "alpha", harness: "codex", checkInMin: "30" });
    await addParticipant({ id: "relay-bot", name: "Relay bot", kind: "tool", repo: "example/repo", parent: "", harness: "", checkInMin: "" });
    expect(sent).toEqual([
      { url: "/api/onboard", body: { id: "beta", name: "Beta", kind: "agent", parent: "alpha", harness: "codex", checkInMin: 30 } },
      { url: "/api/onboard", body: { id: "relay-bot", name: "Relay bot", kind: "tool", repo: "example/repo" } },
    ]);
  });
});

describe("the time a row carries", () => {
  test("append's TS_KEYS rule: the page makes t with toISOString and sends it without the milliseconds", async () => {
    // index.html append(): `if (HAS.call(TS_KEYS, k) && typeof v === "string") v = v.replace(/\.\d{3}Z$/, "Z")`
    // for t, updated, since, done, start, end and released. The clock reads 10:00:00.123Z.
    await reply("beta-0002", "Yes", "", [], ID);
    await markDone("own-1");
    await holdAgreement({ id: "a-1" }, true);
    await startThread([], "incident", "S", "", ID);
    await startThread([], "waiting", "S", "", ID);
    const times = sent.flatMap((s) => {
      const d = (s.body as { data: Record<string, unknown> }).data;
      return ["t", "updated", "since", "done", "start"].filter((k) => k in d).map((k) => d[k]);
    });
    // Seven times; the new waiting row's `done: null` is no time.
    expect(times.filter((t) => t !== null)).toHaveLength(7);
    for (const t of times.filter((t) => t !== null)) expect(t).toBe("2026-11-11T10:00:00Z");
  });
});

describe("summary and body", () => {
  test("splitPost: a subject is the summary; else the first line, and the body only when it says more", () => {
    expect(splitPost("Subject", "Text")).toEqual({ summary: "Subject", body: "Text" });
    expect(splitPost("", "One line")).toEqual({ summary: "One line", body: "" });
    expect(splitPost("", "First\nSecond")).toEqual({ summary: "First", body: "First\nSecond" });
  });

  test("splitAnswer: a first line over 160 characters keeps the whole text as the body", () => {
    const long = "x".repeat(170);
    expect(splitAnswer(long)).toEqual({ summary: "x".repeat(160), body: long });
  });
});
