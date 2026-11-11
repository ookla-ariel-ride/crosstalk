// What the component tests share: a query client filled by hand, and no
// network. Import it only from a test file that runs under jsdom.
import { QueryClient, QueryClientProvider, type QueryKey } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, vi } from "vitest";
import { keys } from "../api/queries";
import type { RosterEntry, Snapshot, Thread } from "../api/types";

// Vitest runs with no globals, so React and Testing Library need these set by hand.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Render `ui` with the given query data cached; a query with no data waits for ever. */
export function renderWith(ui: ReactNode, data: [QueryKey, unknown][] = []) {
  vi.stubGlobal("fetch", () => new Promise(() => {}));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  for (const [key, value] of data) qc.setQueryData(key, value);
  return { qc, ...render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>) };
}

export const ROSTER: Record<string, RosterEntry> = {
  alpha: { id: "alpha", name: "Alpha", kind: "project", color: "#2e7d32", colorDark: "#81c784" },
  beta: { id: "beta", name: "Beta", kind: "project", color: "#ad1457", colorDark: "#f48fb1" },
  "relay-bot": { id: "relay-bot", kind: "tool", color: "#5d4037", colorDark: "#a1887f" },
};

export function snapshot(messages: Record<string, unknown> = {}): [QueryKey, Snapshot] {
  return [keys.roster, { server_time: "2026-11-11T10:00:00Z", roster: ROSTER, collections: { messages } }];
}

export function thread(root: string, fields: Partial<Thread> = {}): Thread {
  return {
    root,
    title: "Title of " + root,
    preview: "Preview of " + root,
    projects: ["alpha"],
    count: 1,
    last_t: "2026-11-11T09:50:00Z",
    last_id: root,
    needs_owner: false,
    unanswered: false,
    routine: false,
    hot: 0,
    kind: "",
    ...fields,
  };
}

/** A one-page infinite query of threads. */
export function threads(...items: Thread[]) {
  return { pages: [{ threads: items, next: null, server_time: "2026-11-11T10:00:00Z" }], pageParams: [""] };
}
