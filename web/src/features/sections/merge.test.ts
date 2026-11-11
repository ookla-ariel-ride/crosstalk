import { describe, expect, test } from "vitest";
import type { Doc } from "./fields";
import { changeTime, mergeByTime, type Stream } from "./merge";

const at = (minute: number) => `2026-11-11T09:${String(minute).padStart(2, "0")}:00Z`;
const lesson = (n: number): Doc => ({ id: "alpha-l" + n, t: at(n) });
const incident = (n: number, end = true): Doc => ({ id: "beta-i" + n, start: at(n), end: end ? at(n) : null });
const isOpen = (source: string, doc: Doc) => source === "incidents" && !doc.end;
const ids = (m: { rows: { doc: Doc }[] }) => m.rows.map((r) => r.doc.id);

/** Page through both streams as the page does: load the next page of each collection that is needed. */
function pageThrough(lessons: Doc[], incidents: Doc[], size: number) {
  const all = { lessons, incidents };
  const loaded = { lessons: size, incidents: size };
  const streams = (): Record<"lessons" | "incidents", Stream> => ({
    lessons: { rows: all.lessons.slice(0, loaded.lessons), done: loaded.lessons >= all.lessons.length },
    incidents: { rows: all.incidents.slice(0, loaded.incidents), done: loaded.incidents >= all.incidents.length },
  });
  let merged = mergeByTime(streams(), isOpen);
  let rounds = 0;
  while (merged.need.length) {
    for (const source of merged.need) loaded[source] += size;
    merged = mergeByTime(streams(), isOpen);
    rounds++;
  }
  return { merged, rounds };
}

describe("mergeByTime", () => {
  test("open rows first, then the newest change, across both collections", () => {
    const merged = mergeByTime(
      {
        lessons: { rows: [lesson(50), lesson(20)], done: true },
        incidents: { rows: [incident(5, false), incident(40), incident(10)], done: true },
      },
      isOpen,
    );
    expect(ids(merged)).toEqual(["beta-i5", "alpha-l50", "beta-i40", "alpha-l20", "beta-i10"]);
    expect(merged.need).toEqual([]);
  });

  test("it stops where a collection with more pages runs out, and names it", () => {
    const merged = mergeByTime(
      { lessons: { rows: [lesson(50), lesson(30)], done: false }, incidents: { rows: [incident(40), incident(10)], done: true } },
      isOpen,
    );
    expect(ids(merged)).toEqual(["alpha-l50", "beta-i40", "alpha-l30"]);
    expect(merged.need).toEqual(["lessons"]);
  });

  test("paging both collections loses no row and shows none twice when one ends first", () => {
    const lessons = Array.from({ length: 23 }, (_, i) => lesson(59 - i * 2));
    const incidents = Array.from({ length: 5 }, (_, i) => incident(58 - i * 2));
    const { merged, rounds } = pageThrough(lessons, incidents, 4);
    const got = ids(merged);
    expect(new Set(got).size).toBe(got.length);
    expect([...got].sort()).toEqual([...lessons, ...incidents].map((d) => d.id).sort());
    expect(got.slice(0, 4)).toEqual(["alpha-l59", "beta-i58", "alpha-l57", "beta-i56"]);
    expect(rounds).toBeGreaterThan(1);
  });

  test("a row that a moved page boundary repeats shows once", () => {
    const merged = mergeByTime(
      { lessons: { rows: [lesson(50), lesson(40), lesson(40)], done: true }, incidents: { rows: [], done: true } },
      isOpen,
    );
    expect(ids(merged)).toEqual(["alpha-l50", "alpha-l40"]);
  });

  test("the change time is the newest time a doc names", () => {
    expect(changeTime({ start: at(1), end: at(9) })).toBe(Date.parse(at(9)));
    expect(changeTime({ t: "nonsense" })).toBe(-Infinity);
  });
});
