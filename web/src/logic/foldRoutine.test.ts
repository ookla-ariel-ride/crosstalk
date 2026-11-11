import { describe, expect, test } from "vitest";
import { foldRoutine } from "./foldRoutine";
import type { Thread } from "./types";

const checkIn = (root: string, last_t: string): Thread => ({
  root,
  kind: "check-in",
  title: "Checked in",
  projects: ["alpha"],
  last_t,
  routine: true,
});
const talk = (root: string, last_t: string): Thread => ({
  root,
  kind: "note",
  title: "A question about the build",
  projects: ["alpha", "beta"],
  last_t,
  routine: false,
});

describe("foldRoutine", () => {
  test("folds routine threads that follow each other into one item", () => {
    const a = checkIn("alpha-0001", "2030-01-01T10:00:00Z");
    const b = { ...checkIn("beta-0001", "2030-01-01T10:10:00Z"), projects: ["beta"] };
    const c = {
      root: "alpha-0002",
      kind: "note",
      title: "LAND START alpha v2-task-10",
      projects: ["alpha"],
      last_t: "2030-01-01T10:20:00Z",
      routine: true,
    };
    expect(foldRoutine([a, b, c])).toEqual([
      { kind: "routine", threads: [a, b, c], counts: { "check-in": 2, "LAND START": 1 } },
    ]);
  });

  test("keeps the order it was given and wraps each other thread", () => {
    const t1 = talk("beta-0002", "2030-01-01T12:00:00Z");
    const r1 = checkIn("alpha-0003", "2030-01-01T11:50:00Z");
    const t2 = talk("beta-0003", "2030-01-01T11:40:00Z");
    expect(foldRoutine([t1, r1, t2])).toEqual([
      { kind: "thread", thread: t1 },
      { kind: "routine", threads: [r1], counts: { "check-in": 1 } },
      { kind: "thread", thread: t2 },
    ]);
  });

  test("foldRoutine ends a run at 60 minutes", () => {
    const a = checkIn("alpha-0001", "2030-01-01T10:00:00Z");
    const b = checkIn("alpha-0002", "2030-01-01T10:59:00Z");
    const c = checkIn("alpha-0003", "2030-01-01T11:00:00Z");
    const d = checkIn("alpha-0004", "2030-01-01T11:01:00Z");
    const items = foldRoutine([a, b, c, d]);
    // a to c is exactly 60 minutes: still one run. a to d is more.
    expect(items.map((i) => (i.kind === "routine" ? i.threads.map((t) => t.root) : []))).toEqual([
      ["alpha-0001", "alpha-0002", "alpha-0003"],
      ["alpha-0004"],
    ]);
  });

  test("a run in newest-first order ends at 60 minutes too", () => {
    const a = checkIn("alpha-0004", "2030-01-01T12:00:00Z");
    const b = checkIn("alpha-0003", "2030-01-01T10:59:00Z");
    expect(foldRoutine([a, b])).toHaveLength(2);
  });

  test("foldRoutine keeps a routine thread with a second participant's reply out of the run", () => {
    const a = checkIn("alpha-0001", "2030-01-01T10:00:00Z");
    // The server sets routine to false once a second participant replies.
    const replied = { ...checkIn("alpha-0002", "2030-01-01T10:05:00Z"), routine: false, projects: ["alpha", "beta"] };
    // And a row that says routine but shows two participants is not folded either.
    const mixed = { ...checkIn("alpha-0003", "2030-01-01T10:06:00Z"), projects: ["alpha", "beta"] };
    const b = checkIn("alpha-0004", "2030-01-01T10:10:00Z");
    expect(foldRoutine([a, replied, mixed, b])).toEqual([
      { kind: "routine", threads: [a], counts: { "check-in": 1 } },
      { kind: "thread", thread: replied },
      { kind: "thread", thread: mixed },
      { kind: "routine", threads: [b], counts: { "check-in": 1 } },
    ]);
  });

  test("counts each routine kind", () => {
    const rows: Thread[] = [
      { root: "a-1", kind: "checkin", title: "x", last_t: "2030-01-01T10:00:00Z", routine: true },
      { root: "a-2", kind: "sync", title: "x", last_t: "2030-01-01T10:01:00Z", routine: true },
      { root: "a-3", kind: "note", title: "LAND END alpha", last_t: "2030-01-01T10:02:00Z", routine: true },
      { root: "a-4", kind: "note", title: "GRANTED alpha", last_t: "2030-01-01T10:03:00Z", routine: true },
      { root: "a-5", kind: "note", title: " TURN ASK alpha", last_t: "2030-01-01T10:04:00Z", routine: true },
      { root: "a-6", kind: "__proto__", title: "x", last_t: "2030-01-01T10:05:00Z", routine: true },
    ];
    const [item] = foldRoutine(rows);
    expect(item).toEqual({
      kind: "routine",
      threads: rows,
      counts: { "check-in": 1, sync: 1, "LAND END": 1, GRANTED: 1, "TURN ASK": 1, other: 1 },
    });
  });

  test("a routine thread with a bad time is a run of its own", () => {
    const a = checkIn("alpha-0001", "2030-01-01T10:00:00Z");
    const bad = checkIn("alpha-0002", "not a date");
    const c = checkIn("alpha-0003", "2030-01-01T10:01:00Z");
    expect(foldRoutine([a, bad, c])).toHaveLength(3);
  });

  test("bad input gives a plain result", () => {
    expect(foldRoutine([])).toEqual([]);
    expect(foldRoutine(undefined as never)).toEqual([]);
    expect(foldRoutine([{}])).toEqual([{ kind: "thread", thread: {} }]);
    expect(foldRoutine([null as never])).toEqual([]);
  });
});
