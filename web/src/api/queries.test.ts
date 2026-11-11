import { QueryClient, type QueryKey } from "@tanstack/react-query";
import { beforeEach, describe, expect, test } from "vitest";
import { ApiError } from "./client";
import { RETRY_MS } from "./events";
import { FakeClock, FakeEventSource, fakeDeps, flush } from "./fakes";
import { connectLive, createQueryClient, invalidateAll, invalidateForRows, keys, rosterOf, shouldRetry } from "./queries";
import type { Message, RowRef, Snapshot, ThreadDetail } from "./types";

function message(id: string): Message {
  return { id, depth: 0, quote: null, from: "alpha", kind: "note", summary: id };
}

function thread(root: string, ...ids: string[]): ThreadDetail {
  return { root, messages: [root, ...ids].map(message) };
}

const CACHED: [QueryKey, unknown][] = [
  [keys.threads("new"), { pages: [], pageParams: [] }],
  [keys.threads("hot"), { pages: [], pageParams: [] }],
  [keys.thread("alpha-0001"), thread("alpha-0001", "beta-0002")],
  [keys.thread("beta-0009"), thread("beta-0009")],
  [keys.section("agreements", ""), { pages: [], pageParams: [] }],
  [keys.section("decisions", ""), { pages: [], pageParams: [] }],
  [keys.section("reviews", "codex"), { pages: [], pageParams: [] }],
  [keys.section("adoption", ""), { pages: [], pageParams: [] }],
  [keys.vitals, {}],
];

let qc: QueryClient;
beforeEach(() => {
  qc = new QueryClient();
  for (const [key, data] of CACHED) qc.setQueryData(key, data);
});

/** The cached keys that are invalidated now, as JSON for an easy compare. */
function invalidated(): string[] {
  return CACHED.filter(([key]) => qc.getQueryState(key)?.isInvalidated).map(([key]) => JSON.stringify(key));
}

function row(collection: string, id: string): RowRef {
  return { from: "alpha", seq: 1, collection, id };
}

describe("queries", () => {
  test("rows event invalidates only the named collections", () => {
    invalidateForRows(qc, [row("agreements", "a-parser"), row("adoptions", "beta-adopt-a-parser")]);
    expect(invalidated()).toEqual([
      JSON.stringify(keys.section("agreements", "")),
      JSON.stringify(keys.section("adoption", "")),
    ]);
  });

  test("a changed message invalidates the thread list, the thread that holds it and the reviews section", () => {
    invalidateForRows(qc, [row("messages", "beta-0002")]);
    expect(invalidated()).toEqual([
      JSON.stringify(keys.threads("new")),
      JSON.stringify(keys.threads("hot")),
      JSON.stringify(keys.thread("alpha-0001")),
      JSON.stringify(keys.section("reviews", "codex")),
    ]);
  });

  test("a message no open thread holds invalidates every open thread", () => {
    // A new reply names only its own id; its parent may be in any open thread.
    invalidateForRows(qc, [row("messages", "alpha-0010")]);
    expect(invalidated()).toEqual(
      expect.arrayContaining([JSON.stringify(keys.thread("alpha-0001")), JSON.stringify(keys.thread("beta-0009"))]),
    );
  });

  test("a row with no collection invalidates nothing", () => {
    invalidateForRows(qc, [{ from: "alpha", seq: null, collection: null, id: null }]);
    expect(invalidated()).toEqual([]);
  });

  test("the stream opens again after a drop: the thread list, every thread and every section refetch", async () => {
    const clock = new FakeClock();
    const modes: string[] = [];
    const stop = connectLive(qc, (mode) => modes.push(mode), fakeDeps(clock));
    FakeEventSource.latest().emit("open");
    expect(invalidated()).toEqual([]);
    FakeEventSource.latest().emit("error");

    // A poll in poll mode invalidates the thread list (and the land queue, not cached here).
    clock.advance(15_000);
    await flush();
    expect(invalidated()).toEqual([JSON.stringify(keys.threads("new")), JSON.stringify(keys.threads("hot"))]);

    clock.advance(RETRY_MS - 15_000);
    FakeEventSource.latest().emit("open");
    expect(modes).toEqual(["push", "poll", "push"]);
    expect(invalidated()).toEqual(CACHED.filter(([k]) => k[0] !== "vitals").map(([k]) => JSON.stringify(k)));
    stop();
  });

  test("a message invalidates the land queue; another collection does not", () => {
    qc.setQueryData(keys.queue, { holders: [], waiting: [] });
    invalidateForRows(qc, [row("agreements", "a-parser")]);
    expect(qc.getQueryState(keys.queue)?.isInvalidated).toBe(false);
    invalidateForRows(qc, [row("messages", "beta-0003")]);
    expect(qc.getQueryState(keys.queue)?.isInvalidated).toBe(true);
  });

  test("a return to push and each poll refetch the land queue", async () => {
    qc.setQueryData(keys.queue, { holders: [], waiting: [] });
    const clock = new FakeClock();
    const stop = connectLive(qc, () => {}, fakeDeps(clock));
    FakeEventSource.latest().emit("open");
    FakeEventSource.latest().emit("error");
    clock.advance(15_000);
    await flush();
    expect(qc.getQueryState(keys.queue)?.isInvalidated).toBe(true);
    stop();
    qc.setQueryData(keys.queue, { holders: [], waiting: [] });
    invalidateAll(qc);
    expect(qc.getQueryState(keys.queue)?.isInvalidated).toBe(true);
  });

  test("the roster comes from the snapshot with each participant's newest check-in", () => {
    const snapshot: Snapshot = {
      server_time: "2026-11-11T10:00:00Z",
      roster: {
        alpha: { id: "alpha", kind: "project", color: "#2e7d32", colorDark: "#81c784" },
        beta: { id: "beta", kind: "project" },
      },
      collections: {
        messages: {
          m1: { from: "alpha", kind: "check-in", t: "2026-11-11T09:00:00Z" },
          m2: { from: "alpha", kind: "checkin", t: "2026-11-11T09:30:00Z" },
          m3: { from: "alpha", kind: "note", t: "2026-11-11T09:50:00Z" },
          m4: { from: "beta", kind: "check-in", t: "not a time" },
          m5: { from: "relay-bot", kind: "check-in", t: "2026-11-11T09:40:00Z" },
          m6: null,
        },
      },
    };
    const people = rosterOf(snapshot);
    expect(people.roster).toBe(snapshot.roster);
    expect(people.checkIns).toEqual({ alpha: "2026-11-11T09:30:00Z", "relay-bot": "2026-11-11T09:40:00Z" });
    expect(rosterOf({ server_time: "", roster: {}, collections: {} } as Snapshot).checkIns).toEqual({});
  });

  test("401 and 403 are not retried; other errors are, three times", () => {
    expect(shouldRetry(0, new ApiError(403, "no"))).toBe(false);
    expect(shouldRetry(0, new ApiError(401, "no"))).toBe(false);
    expect(shouldRetry(0, new ApiError(0, "offline"))).toBe(true);
    expect(shouldRetry(2, new ApiError(500, "boom"))).toBe(true);
    expect(shouldRetry(3, new ApiError(500, "boom"))).toBe(false);
    expect(createQueryClient().getDefaultOptions().queries?.retry).toBe(shouldRetry);
  });
});
