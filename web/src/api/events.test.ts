import { beforeEach, describe, expect, test, vi } from "vitest";
import { POLL_MS, RETRY_MS, startEvents } from "./events";
import { FakeClock, FakeEventSource, fakeDeps, flush } from "./fakes";

let clock: FakeClock;
beforeEach(() => {
  clock = new FakeClock();
});

describe("events", () => {
  test("events calls onRows for a rows event", () => {
    const onRows = vi.fn();
    const onMode = vi.fn();
    startEvents(onRows, onMode, fakeDeps(clock));
    const es = FakeEventSource.latest();
    expect(es.url).toBe("/api/events");
    es.emit("ready");
    expect(onMode).toHaveBeenCalledWith("push");
    const rows = [{ from: "alpha", seq: 7, collection: "messages", id: "alpha-0007" }];
    es.emit("rows", JSON.stringify({ rows }));
    expect(onRows).toHaveBeenCalledWith(rows);
  });

  test("the server's ready event, not the browser's open event, makes the stream ready, each time", () => {
    const onMode = vi.fn();
    const onReady = vi.fn();
    startEvents(vi.fn(), onMode, { ...fakeDeps(clock), onReady });
    const es = FakeEventSource.latest();
    es.emit("open");
    expect(onMode).not.toHaveBeenCalled();
    expect(onReady).not.toHaveBeenCalled();
    es.emit("ready");
    expect(onReady).toHaveBeenCalledTimes(1);
    es.emit("error");
    clock.advance(RETRY_MS);
    FakeEventSource.latest().emit("ready");
    expect(onReady).toHaveBeenCalledTimes(2);
  });

  test("a rows event whose data is not JSON calls nothing", () => {
    const onRows = vi.fn();
    startEvents(onRows, vi.fn(), fakeDeps(clock));
    FakeEventSource.latest().emit("rows", "not json");
    FakeEventSource.latest().emit("rows", JSON.stringify({ rows: "x" }));
    expect(onRows).not.toHaveBeenCalled();
  });

  test("events falls back to polling and recovers", async () => {
    const onMode = vi.fn();
    const onPoll = vi.fn();
    const deps = { ...fakeDeps(clock), onPoll };
    startEvents(vi.fn(), onMode, deps);
    const first = FakeEventSource.latest();
    first.emit("ready");
    first.emit("error");
    expect(first.closed).toBe(true);
    expect(onMode.mock.calls).toEqual([["push"], ["poll"]]);

    // Each 15 seconds: one read of /api/health, and onPoll when it answers.
    clock.advance(POLL_MS);
    await flush();
    expect(deps.fetchCalls).toEqual(["/api/health"]);
    expect(onPoll).toHaveBeenCalledTimes(1);
    clock.advance(POLL_MS - 1);
    expect(FakeEventSource.made).toHaveLength(1);

    // 30 seconds after the error the stream is tried again; it fails.
    clock.advance(1);
    await flush();
    expect(FakeEventSource.made).toHaveLength(2);
    FakeEventSource.latest().emit("error");
    expect(onMode.mock.calls).toEqual([["push"], ["poll"]]);

    // 30 seconds later again; this time it opens and the poll stops.
    clock.advance(RETRY_MS);
    await flush();
    expect(FakeEventSource.made).toHaveLength(3);
    FakeEventSource.latest().emit("ready");
    expect(onMode.mock.calls).toEqual([["push"], ["poll"], ["push"]]);
    expect(clock.pending).toBe(0);
    const polls = deps.fetchCalls.length;
    clock.advance(10 * POLL_MS);
    expect(deps.fetchCalls).toHaveLength(polls);
  });

  test("a failed health read in poll mode calls no onPoll", async () => {
    const onPoll = vi.fn();
    startEvents(vi.fn(), vi.fn(), { ...fakeDeps(clock, 503), onPoll });
    FakeEventSource.latest().emit("error");
    clock.advance(POLL_MS);
    await flush();
    expect(onPoll).not.toHaveBeenCalled();
  });

  test("the returned function stops the stream, the poll and every timer", async () => {
    const deps = { ...fakeDeps(clock), onPoll: vi.fn() };
    const stop = startEvents(vi.fn(), vi.fn(), deps);
    FakeEventSource.latest().emit("error");
    clock.advance(RETRY_MS);
    const retried = FakeEventSource.latest();
    expect(retried.closed).toBe(false);
    stop();
    expect(retried.closed).toBe(true);
    expect(clock.pending).toBe(0);
    retried.emit("error");
    clock.advance(10 * RETRY_MS);
    await flush();
    expect(FakeEventSource.made).toHaveLength(2);
    expect(clock.pending).toBe(0);
  });
});
