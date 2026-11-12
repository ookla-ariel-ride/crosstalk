// @vitest-environment jsdom
import { act } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { keys } from "../../api/queries";
import type { ReadState } from "../../api/types";
import { renderWith } from "../../ui/testing";
import { type Reading, useReading } from "./reading";

const OWNER = { name: "Owner", isOwner: true, canEdit: true, viewer: "owner" };
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
    act(() => reading.markAll(ALL));
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
    act(() => reading.markAll(ALL));
    expect(held.map((h) => h.method)).toEqual(["GET", "PUT"]);
    const written = { ...START, all_before: ALL, rev: 1 };
    await act(async () => held[1]?.answer(written));
    await act(async () => held[0]?.answer(START));
    expect(qc.getQueryData(keys.readState)).toEqual(written);
  });
});
