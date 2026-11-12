import { QueryClient, QueryObserver, type QueryKey } from "@tanstack/react-query";
import { beforeEach, describe, expect, test } from "vitest";
import { ApiError } from "./client";
import { RETRY_MS } from "./events";
import { FakeClock, FakeEventSource, fakeDeps, flush } from "./fakes";
import { connectLive, createQueryClient, invalidateAll, invalidateForRows, keys, ROWS_MS, rosterOf, shouldRetry } from "./queries";
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

  test("a waiting row invalidates the thread list: an open one makes the thread of its id need the owner", () => {
    invalidateForRows(qc, [row("waiting", "alpha-w1")]);
    expect(invalidated()).toEqual([JSON.stringify(keys.threads("new")), JSON.stringify(keys.threads("hot"))]);
  });

  test("a mark refreshes the thread list, the open thread of its item and the decisions, lessons and reviews", () => {
    invalidateForRows(qc, [row("acks", "owner--beta-0002")]);
    expect(invalidated()).toEqual([
      JSON.stringify(keys.threads("new")),
      JSON.stringify(keys.threads("hot")),
      JSON.stringify(keys.thread("alpha-0001")),
      JSON.stringify(keys.section("decisions", "")),
      JSON.stringify(keys.section("reviews", "codex")),
    ]);
  });

  test("a mark on a lesson refreshes the open thread of its message", () => {
    invalidateForRows(qc, [row("acks", "beta--lesson-beta-0009")]);
    const got = invalidated();
    expect(got).toContain(JSON.stringify(keys.thread("beta-0009")));
    expect(got).not.toContain(JSON.stringify(keys.thread("alpha-0001")));
  });

  test("a mark on an item no open thread holds, or with an id that names no item, refreshes every open thread", () => {
    for (const id of ["beta--owner-d1", "owner-d1", "--alpha-0001"]) {
      qc = new QueryClient();
      for (const [key, data] of CACHED) qc.setQueryData(key, data);
      invalidateForRows(qc, [row("acks", id)]);
      expect(invalidated(), id).toEqual(
        expect.arrayContaining([JSON.stringify(keys.thread("alpha-0001")), JSON.stringify(keys.thread("beta-0009"))]),
      );
    }
  });

  test("a row with no collection invalidates nothing", () => {
    invalidateForRows(qc, [{ from: "alpha", seq: null, collection: null, id: null }]);
    expect(invalidated()).toEqual([]);
  });

  test("the first ready event and each one after a drop refetch the thread list, every thread and every section", async () => {
    const clock = new FakeClock();
    const modes: string[] = [];
    const stop = connectLive(qc, (mode) => modes.push(mode), fakeDeps(clock));
    // The first ready event refetches too: a row written while the page loaded is in it.
    FakeEventSource.latest().emit("ready");
    expect(invalidated()).toEqual(CACHED.filter(([k]) => k[0] !== "vitals").map(([k]) => JSON.stringify(k)));
    for (const [key, data] of CACHED) qc.setQueryData(key, data);
    expect(invalidated()).toEqual([]);
    FakeEventSource.latest().emit("error");

    // A poll in poll mode invalidates what the stream shows (and the land queue, not cached here).
    clock.advance(15_000);
    await flush();
    expect(invalidated()).toEqual(CACHED.filter(([k]) => k[0] !== "vitals").map(([k]) => JSON.stringify(k)));

    clock.advance(RETRY_MS - 15_000);
    FakeEventSource.latest().emit("ready");
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

  test("a first load still pending at ready gets a second read that starts after ready, and that one shows", async () => {
    const fresh = new QueryClient();
    const answers: ((d: ThreadDetail) => void)[] = [];
    const key = keys.thread("gamma-0001");
    const observer = new QueryObserver(fresh, {
      queryKey: key,
      queryFn: () => new Promise<ThreadDetail>((resolve) => answers.push(resolve)),
      staleTime: Infinity,
    });
    const seen: unknown[] = [];
    const unsubscribe = observer.subscribe((r) => seen.push(r.status));
    await flush();
    expect(answers).toHaveLength(1);
    const clock = new FakeClock();
    const stop = connectLive(fresh, () => {}, fakeDeps(clock));
    FakeEventSource.latest().emit("ready");
    await flush();
    // The first read answers with the snapshot from before the server's mark.
    answers[0]?.(thread("gamma-0001"));
    await flush();
    expect(answers).toHaveLength(2);
    answers[1]?.(thread("gamma-0001", "beta-0002"));
    await flush();
    expect(fresh.getQueryData<ThreadDetail>(key)?.messages.map((m) => m.id)).toEqual(["gamma-0001", "beta-0002"]);
    expect(seen).not.toContain("error");
    stop();
    unsubscribe();
  });

  test("a rows event that finds a first load still pending gets a second read, and that one shows", async () => {
    const fresh = new QueryClient();
    const answers: ((d: ThreadDetail) => void)[] = [];
    const key = keys.thread("gamma-0001");
    const observer = new QueryObserver(fresh, {
      queryKey: key,
      queryFn: () => new Promise<ThreadDetail>((resolve) => answers.push(resolve)),
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => {});
    await flush();
    // The row is new to every thread, so every thread is invalidated.
    invalidateForRows(fresh, [row("messages", "beta-0002")]);
    answers[0]?.(thread("gamma-0001"));
    await flush();
    expect(answers).toHaveLength(2);
    answers[1]?.(thread("gamma-0001", "beta-0002"));
    await flush();
    expect(fresh.getQueryData<ThreadDetail>(key)?.messages).toHaveLength(2);
    unsubscribe();
  });

  test("rows events within ROWS_MS make one invalidation, with every row of them", async () => {
    const clock = new FakeClock();
    const stop = connectLive(qc, () => {}, fakeDeps(clock));
    const es = FakeEventSource.latest();
    es.emit("ready");
    for (const [key, data] of CACHED) qc.setQueryData(key, data);
    let calls = 0;
    const real = qc.invalidateQueries.bind(qc);
    qc.invalidateQueries = ((...args: Parameters<typeof real>) => {
      calls += 1;
      return real(...args);
    }) as typeof qc.invalidateQueries;
    es.emit("rows", JSON.stringify({ rows: [row("agreements", "a-parser")] }));
    clock.advance(ROWS_MS - 1);
    es.emit("rows", JSON.stringify({ rows: [row("decisions", "d-one")] }));
    expect(invalidated()).toEqual([]);
    expect(calls).toBe(0);
    clock.advance(1);
    expect(invalidated()).toEqual([JSON.stringify(keys.section("agreements", "")), JSON.stringify(keys.section("decisions", ""))]);
    expect(calls).toBe(2);
    // A ready event refetches everything: a batch still waiting is dropped.
    es.emit("rows", JSON.stringify({ rows: [row("adoptions", "x")] }));
    es.emit("ready");
    calls = 0;
    clock.advance(ROWS_MS);
    expect(calls).toBe(0);
    stop();
  });

  test("a first load that fails gets no chained read", async () => {
    const fresh = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const fails: ((e: Error) => void)[] = [];
    const observer = new QueryObserver(fresh, {
      queryKey: keys.thread("gamma-0001"),
      queryFn: () => new Promise<ThreadDetail>((_resolve, reject) => fails.push(reject)),
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => {});
    await flush();
    const done = invalidateAll(fresh);
    fails[0]?.(new Error("down"));
    await done;
    expect(fails).toHaveLength(1);
    unsubscribe();
  });

  test("the same holds for a poll that finds a first load still pending", async () => {
    const fresh = new QueryClient();
    const answers: ((d: ThreadDetail) => void)[] = [];
    const key = keys.thread("gamma-0001");
    const observer = new QueryObserver(fresh, {
      queryKey: key,
      queryFn: () => new Promise<ThreadDetail>((resolve) => answers.push(resolve)),
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => {});
    await flush();
    const ready = invalidateAll(fresh);
    answers[0]?.(thread("gamma-0001"));
    await flush();
    expect(answers).toHaveLength(2);
    answers[1]?.(thread("gamma-0001", "beta-0002"));
    await ready;
    expect(fresh.getQueryData<ThreadDetail>(key)?.messages).toHaveLength(2);
    unsubscribe();
  });

  test("each poll refetches the open thread and the open section, and nothing that no view shows", async () => {
    const fetched: string[] = [];
    const watch = (key: QueryKey) =>
      new QueryObserver(qc, { queryKey: key, queryFn: () => (fetched.push(JSON.stringify(key)), qc.getQueryData(key)), staleTime: Infinity }).subscribe(() => {});
    const stops = [watch(keys.thread("alpha-0001")), watch(keys.section("agreements", ""))];
    await flush();
    fetched.length = 0;
    const clock = new FakeClock();
    const stop = connectLive(qc, () => {}, fakeDeps(clock));
    FakeEventSource.latest().emit("error");
    clock.advance(15_000);
    await flush();
    expect(fetched.sort()).toEqual([JSON.stringify(keys.section("agreements", "")), JSON.stringify(keys.thread("alpha-0001"))]);
    // A thread and a section that no view shows are only marked stale.
    expect(qc.getQueryState(keys.thread("beta-0009"))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(keys.section("decisions", ""))?.isInvalidated).toBe(true);
    stop();
    for (const s of stops) s();
  });

  test("a return to push and each poll refetch the land queue", async () => {
    qc.setQueryData(keys.queue, { holders: [], waiting: [] });
    const clock = new FakeClock();
    const stop = connectLive(qc, () => {}, fakeDeps(clock));
    FakeEventSource.latest().emit("ready");
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
