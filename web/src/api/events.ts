// The push channel (GET /api/events) and its fallback.
//
// The stream sends `event: rows` with the ids of new log lines. When it fails,
// the page polls GET /api/health every 15 seconds and tries the stream again
// every 30 seconds. The stream has no replay, so a caller that goes from
// "poll" back to "push" must refetch what it shows (queries.connectLive).
import type { RowRef } from "./types";

export const POLL_MS = 15_000;
export const RETRY_MS = 30_000;

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
   * so the caller refetches its thread list each time.
   */
  onPoll?: () => void;
}

function isRows(value: unknown): value is { rows: RowRef[] } {
  return typeof value === "object" && value !== null && Array.isArray((value as { rows?: unknown }).rows);
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
  let stopped = false;

  function setMode(next: Mode) {
    if (next === mode) return;
    mode = next;
    onMode(next);
  }

  function poll() {
    fetchFn("/api/health", { credentials: "same-origin", cache: "no-store" }).then(
      (response) => {
        if (response.ok && !stopped && mode === "poll") deps.onPoll?.();
      },
      () => undefined,
    );
  }

  function stopPoll() {
    if (pollTimer !== undefined) cancelEvery(pollTimer);
    pollTimer = undefined;
  }

  function open() {
    const es = new Source("/api/events");
    source = es;
    es.addEventListener("open", () => {
      if (source !== es) return;
      stopPoll();
      setMode("push");
    });
    es.addEventListener("rows", (event) => {
      if (source !== es) return;
      let data: unknown;
      try {
        data = JSON.parse(String((event as MessageEvent).data));
      } catch {
        return;
      }
      if (isRows(data)) onRows(data.rows);
    });
    es.addEventListener("error", () => {
      if (source !== es) return;
      // Close it, so the browser does not retry on its own schedule too.
      es.close();
      source = null;
      if (pollTimer === undefined) pollTimer = every(poll, POLL_MS);
      setMode("poll");
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
    if (retryTimer !== undefined) cancelLater(retryTimer);
    retryTimer = undefined;
    source?.close();
    source = null;
  };
}
