// The push channel (GET /api/events) and its fallback.
//
// The server takes its mark of the log, then sends `event: ready`, then
// `event: rows` with the ids of the lines written after the mark. The stream
// has no replay, so the caller refetches what it shows on each ready event,
// the first one too (queries.connectLive): a line written before the mark is
// in that refetch. The browser's own open event fires on the headers and says
// nothing about the mark, so it is not used. When the stream fails, the page
// polls GET /api/health every 15 seconds and tries the stream again every 30.
// When the stream opens but no `ready` comes within 10 seconds (a proxy that
// holds the stream back), the page polls and keeps the stream; a later
// `ready` or `rows` event stops the poll.
import { rowRefs } from "./normalize";
import type { RowRef } from "./types";

export const POLL_MS = 15_000;
export const RETRY_MS = 30_000;
export const READY_MS = 10_000;
/** A poll (the health read and the refetches onPoll starts) ends after this. */
export const POLL_TIMEOUT_MS = 10_000;

export type Mode = "push" | "poll";

type TimerId = ReturnType<typeof globalThis.setTimeout> | number;

export interface EventsDeps {
  EventSource?: new (url: string) => Pick<EventSource, "addEventListener" | "close">;
  fetch?: (input: string, init?: RequestInit) => Promise<Response>;
  setTimeout?: (fn: () => void, ms: number) => TimerId;
  clearTimeout?: (id: TimerId | undefined) => void;
  setInterval?: (fn: () => void, ms: number) => TimerId;
  clearInterval?: (id: TimerId | undefined) => void;
  /**
   * Called in poll mode after each read of /api/health that answers. Health
   * gives no sign of a new log line (its server_time changes on every read),
   * so the caller refetches what its open views show each time. A returned
   * promise holds the next poll back until it settles.
   */
  onPoll?: () => unknown;
  /** Called on each `ready` event: the caller refetches what it shows. */
  onReady?: () => void;
}

/** Start the stream; the returned function stops the stream, the poll and every timer. */
export function startEvents(
  onRows: (rows: RowRef[]) => void,
  onMode: (mode: Mode) => void,
  deps: EventsDeps = {},
): () => void {
  const Source = deps.EventSource ?? globalThis.EventSource;
  const fetchFn = deps.fetch ?? ((input: string, init?: RequestInit) => fetch(input, init));
  const later = deps.setTimeout ?? ((fn: () => void, ms: number) => globalThis.setTimeout(fn, ms));
  const cancelLater = deps.clearTimeout ?? ((id) => globalThis.clearTimeout(id));
  const every = deps.setInterval ?? ((fn: () => void, ms: number) => globalThis.setInterval(fn, ms));
  const cancelEvery = deps.clearInterval ?? ((id) => globalThis.clearInterval(id));

  let source: Pick<EventSource, "addEventListener" | "close"> | null = null;
  let mode: Mode | null = null;
  let pollTimer: TimerId | undefined;
  let retryTimer: TimerId | undefined;
  let readyTimer: TimerId | undefined;
  let stopped = false;

  function setMode(next: Mode) {
    if (next === mode) return;
    mode = next;
    onMode(next);
  }

  // A poll does not start while the last one (the health read and the
  // refetches it starts) runs: a slow server never gets a pile of them. A
  // poll ends after POLL_TIMEOUT_MS, and a switch back to push ends it, so a
  // request that never answers cannot stop the polls.
  let pollRun: AbortController | undefined;
  let pollLimit: TimerId | undefined;
  function poll() {
    if (pollRun) return;
    const run = new AbortController();
    pollRun = run;
    pollLimit = later(() => run.abort(), POLL_TIMEOUT_MS);
    const aborted = new Promise<void>((resolve) => run.signal.addEventListener("abort", () => resolve(), { once: true }));
    const work = fetchFn("/api/health", { credentials: "same-origin", cache: "no-store", signal: run.signal }).then((response) =>
      response.ok && !stopped && mode === "poll" && !run.signal.aborted ? deps.onPoll?.() : undefined,
    );
    void Promise.race([work, aborted])
      .catch(() => undefined)
      .then(() => {
        if (pollRun !== run) return;
        cancelLater(pollLimit);
        pollRun = undefined;
        pollLimit = undefined;
      });
  }

  function startPoll() {
    if (pollTimer === undefined) pollTimer = every(poll, POLL_MS);
    setMode("poll");
  }

  function stopPoll() {
    if (pollTimer !== undefined) cancelEvery(pollTimer);
    pollTimer = undefined;
    if (pollLimit !== undefined) cancelLater(pollLimit);
    pollLimit = undefined;
    pollRun?.abort();
    pollRun = undefined;
  }

  function stopWaiting() {
    if (readyTimer !== undefined) cancelLater(readyTimer);
    readyTimer = undefined;
  }

  /** The stream works: stop the poll and the wait for ready. */
  function live() {
    stopWaiting();
    stopPoll();
    setMode("push");
  }

  function open() {
    const es = new Source("/api/events");
    source = es;
    stopWaiting();
    readyTimer = later(() => {
      readyTimer = undefined;
      if (source === es && !stopped) startPoll();
    }, READY_MS);
    es.addEventListener("ready", () => {
      if (source !== es) return;
      live();
      deps.onReady?.();
    });
    es.addEventListener("rows", (event) => {
      if (source !== es) return;
      let data: unknown;
      try {
        data = JSON.parse(String((event as MessageEvent).data));
      } catch {
        return;
      }
      // A rows event shows the stream works, also when its ready was lost.
      if (mode !== "push") live();
      const rows = rowRefs(data);
      if (rows.length) onRows(rows);
    });
    es.addEventListener("error", () => {
      if (source !== es) return;
      // Close it, so the browser does not retry on its own schedule too.
      es.close();
      source = null;
      stopWaiting();
      startPoll();
      retryTimer = later(() => {
        retryTimer = undefined;
        if (!stopped) open();
      }, RETRY_MS);
    });
  }

  open();

  return () => {
    stopped = true;
    stopPoll();
    stopWaiting();
    if (retryTimer !== undefined) cancelLater(retryTimer);
    retryTimer = undefined;
    source?.close();
    source = null;
  };
}
