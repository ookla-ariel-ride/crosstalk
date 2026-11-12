// @vitest-environment jsdom
import { act } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { keys } from "../../api/queries";
import type { ReadState } from "../../api/types";
import { renderWith, thread, threads } from "../../ui/testing";
import { type Reading, unreadThreads, useReading, useUnreadTotal } from "./reading";

const OWNER = { name: "Owner", isOwner: true, canEdit: true, viewer: "owner" };
const VIEWER = { name: null, isOwner: false, canEdit: false, viewer: "viewer" };
const START: ReadState = { threads: {}, muted: [], all_before: null, rev: 0 };
const MARK = { t: "2026-11-11T09:30:00Z", id: "beta-0004" };
const ALL = "2026-11-11T09:40:00Z";

let reading: Reading;
function Probe() {
  reading = useReading();
  return null;
}

/** fetch answers held until the test lets each one go, in the order it picks. */
function heldFetch() {
  const held: { method: string; answer: (body: unknown) => void }[] = [];
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) =>
    new Promise<Response>((resolve) => {
      held.push({
        method: init?.method ?? "GET",
        answer: (body) => resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })),
      });
    }),
  );
  return held;
}

describe("the owner's read state", () => {
  test("two writes whose answers arrive in reverse order leave the newer state", async () => {
    const { qc } = renderWith(<Probe />, [[keys.me, OWNER], [keys.readState, START]]);
    const held = heldFetch();
    act(() => reading.markThread("alpha-0001", MARK));
    act(() => void reading.markAll(ALL));
    expect(held.map((h) => h.method)).toEqual(["PUT", "PUT"]);
    const first = { ...START, threads: { "alpha-0001": MARK }, rev: 1 };
    const second = { ...first, all_before: ALL, rev: 2 };
    await act(async () => held[1]?.answer(second));
    await act(async () => held[0]?.answer(first));
    expect(qc.getQueryData(keys.readState)).toEqual(second);
  });

  test("a read that started before a write and ends after it keeps the write", async () => {
    const { qc } = renderWith(<Probe />, [[keys.me, OWNER], [keys.readState, START]]);
    const held = heldFetch();
    act(() => void qc.invalidateQueries({ queryKey: keys.readState }));
    act(() => void reading.markAll(ALL));
    expect(held.map((h) => h.method)).toEqual(["GET", "PUT"]);
    const written = { ...START, all_before: ALL, rev: 1 };
    await act(async () => held[1]?.answer(written));
    await act(async () => held[0]?.answer(START));
    expect(qc.getQueryData(keys.readState)).toEqual(written);
  });
});

describe("unread threads", () => {
  const rows = [
    thread("alpha-0001", { unread: 2, last_t: "2026-11-11T09:40:00Z" }),
    thread("alpha-0002", { unread: 0 }),
    thread("beta-0003", { unread: 1, last_t: "2026-11-11T09:20:00Z" }),
    thread("beta-0004", { unread: 1, needs_owner: true, last_t: "2026-11-11T09:20:00Z" }),
  ];

  test("a row counts when the server says it is unread and the read state does not cover its newest message", () => {
    expect(unreadThreads(rows, START, true).map((t) => t.root)).toEqual(["alpha-0001", "beta-0003", "beta-0004"]);
    // A mark-all time the server kept, before the list loads again.
    const after = { ...START, all_before: "2026-11-11T09:30:00Z" };
    expect(unreadThreads(rows, after, true).map((t) => t.root)).toEqual(["alpha-0001"]);
  });

  test("a hidden muted thread does not count; one that needs the owner does", () => {
    const muted = { ...START, muted: ["beta-0003", "beta-0004"] };
    expect(unreadThreads(rows, muted, true).map((t) => t.root)).toEqual(["alpha-0001", "beta-0004"]);
  });

  test("a viewer's row with no count counts by the marks, once the state is ready", () => {
    const row = thread("alpha-0009", { last_t: "2026-11-11T09:40:00Z" });
    expect(unreadThreads([row], START, true)).toHaveLength(1);
    expect(unreadThreads([row], START, false)).toHaveLength(0);
  });

  test("each root counts once", () => {
    expect(unreadThreads([rows[0]!, rows[0]!], START, true)).toHaveLength(1);
  });

  test("a viewer's total counts the loaded pages of the needs order", () => {
    let total = -1;
    function Total() {
      total = useUnreadTotal();
      return null;
    }
    const data = threads(...rows);
    const two = { ...data, pages: [...data.pages, { threads: [thread("gamma-0005", { unread: 3 })], next: null, server_time: "2026-11-11T10:00:00Z" }] };
    renderWith(<Total />, [[keys.me, VIEWER], [keys.threads("needs", "viewer"), two]]);
    expect(total).toBe(4);
  });

  test("the owner's total is the server's count of unread threads, from its own query", () => {
    let total = -1;
    function Total() {
      total = useUnreadTotal();
      return null;
    }
    renderWith(<Total />, [[keys.me, OWNER], [keys.readState, START], [keys.threads("needs", "owner"), threads(...rows)], [keys.unreadTotal, 37]]);
    expect(total).toBe(37);
  });
});
