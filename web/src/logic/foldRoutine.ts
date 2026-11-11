import type { Thread } from "./types";

/** What the routine line counts: one key for each kind of routine post. */
export type RoutineKind = "check-in" | "sync" | "LAND START" | "LAND END" | "GRANTED" | "TURN ASK" | "other";

export type StreamItem =
  | { kind: "thread"; thread: Thread }
  | { kind: "routine"; threads: Thread[]; counts: Partial<Record<RoutineKind, number>> };

// The prefixes and kinds of scripts/crosstalk/threads.py.
const PREFIXES = ["LAND START", "LAND END", "GRANTED", "TURN ASK"] as const;
const RUN_MS = 60 * 60 * 1000;

function routineKind(thread: Thread): RoutineKind {
  const title = typeof thread.title === "string" ? thread.title.trimStart() : "";
  for (const prefix of PREFIXES) if (title.startsWith(prefix)) return prefix;
  if (thread.kind === "check-in" || thread.kind === "checkin") return "check-in";
  if (thread.kind === "sync") return "sync";
  return "other";
}

// The server sets `routine` only while one participant wrote in the thread;
// a row that lists two is kept out of a run all the same.
const isRoutine = (thread: Thread): boolean =>
  thread.routine === true && !(Array.isArray(thread.projects) && thread.projects.length > 1);

const timeOf = (thread: Thread): number =>
  typeof thread.last_t === "string" ? Date.parse(thread.last_t) : Number.NaN;

/**
 * The stream in the order given, with each run of routine threads folded into
 * one item. A run holds routine threads with no other thread between them,
 * and ends before a thread whose `last_t` is more than 60 minutes from the
 * run's first. A single routine thread is a run of one.
 */
export function foldRoutine(threads: Thread[]): StreamItem[] {
  const items: StreamItem[] = [];
  if (!Array.isArray(threads)) return items;
  let run: { threads: Thread[]; counts: Partial<Record<RoutineKind, number>>; first: number } | null = null;
  for (const thread of threads) {
    if (!thread || typeof thread !== "object") continue;
    if (!isRoutine(thread)) {
      run = null;
      items.push({ kind: "thread", thread });
      continue;
    }
    const when = timeOf(thread);
    if (!run || Number.isNaN(run.first) || Number.isNaN(when) || Math.abs(when - run.first) > RUN_MS) {
      run = { threads: [], counts: {}, first: when };
      items.push({ kind: "routine", threads: run.threads, counts: run.counts });
    }
    run.threads.push(thread);
    const key = routineKind(thread);
    run.counts[key] = (run.counts[key] ?? 0) + 1;
  }
  return items;
}
