// React Query hooks over client.ts, and the live updates that keep them fresh.
import { QueryClient, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ApiError } from "./client";
import { type EventsDeps, type Mode, startEvents } from "./events";
import type { RowRef, SectionName, Sort, ThreadDetail } from "./types";

export const keys = {
  threads: (sort: Sort) => ["threads", sort] as const,
  thread: (root: string) => ["thread", root] as const,
  section: (name: SectionName, q: string) => ["section", name, q] as const,
  vitals: ["vitals"] as const,
  me: ["me"] as const,
  readState: ["read-state"] as const,
};

/** The sections that read each collection (sections.COLLECTION, turned around). */
const SECTIONS_OF: Record<string, SectionName[]> = {
  agreements: ["agreements"],
  decisions: ["decisions"],
  lessons: ["lessons"],
  incidents: ["incidents"],
  locks: ["locks"],
  progress: ["progress"],
  schedules: ["schedules"],
  messages: ["reviews"],
  adoptions: ["adoption"],
};

/** Retry up to three times, but never a 401 or 403: a retry would only hide it. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return false;
  return failureCount < 3;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: shouldRetry } } });
}

/**
 * Invalidate what the new log lines change: the thread list and the open
 * threads for `messages`, and the sections that read each named collection.
 *
 * A row names only its own id. A changed message invalidates the open thread
 * that holds it; a message that no open thread holds may be a new reply to any
 * of them, so it invalidates every open thread.
 */
export function invalidateForRows(qc: QueryClient, rows: RowRef[]): void {
  const collections = new Set<string>();
  const messageIds = new Set<string>();
  for (const row of rows) {
    if (typeof row.collection !== "string") continue;
    collections.add(row.collection);
    if (row.collection === "messages" && typeof row.id === "string") messageIds.add(row.id);
  }

  if (collections.has("messages")) {
    void qc.invalidateQueries({ queryKey: ["threads"] });
    const holders: string[] = [];
    const held = new Set<string>();
    for (const [key, data] of qc.getQueriesData<ThreadDetail>({ queryKey: ["thread"] })) {
      const ids = new Set([String(key[1]), ...(data?.messages ?? []).map((m) => m.id)]);
      const hits = [...messageIds].filter((id) => ids.has(id));
      if (hits.length) holders.push(String(key[1]));
      for (const id of hits) held.add(id);
    }
    if (messageIds.size > held.size) {
      void qc.invalidateQueries({ queryKey: ["thread"] });
    } else {
      for (const root of holders) void qc.invalidateQueries({ queryKey: keys.thread(root), exact: true });
    }
  }

  const sections = new Set<SectionName>();
  for (const c of collections) for (const name of SECTIONS_OF[c] ?? []) sections.add(name);
  for (const name of sections) void qc.invalidateQueries({ queryKey: ["section", name] });
}

/** Invalidate the thread list, every thread and every section: what the stream shows. */
export function invalidateAll(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: ["threads"] });
  void qc.invalidateQueries({ queryKey: ["thread"] });
  void qc.invalidateQueries({ queryKey: ["section"] });
}

/**
 * Start the live updates for `qc`. The stream has no replay, so a return from
 * poll to push refetches everything it shows; each poll refetches the thread
 * list. The returned function stops it all.
 */
export function connectLive(qc: QueryClient, onMode: (mode: Mode) => void, deps: EventsDeps = {}): () => void {
  let mode: Mode | null = null;
  return startEvents(
    (rows) => invalidateForRows(qc, rows),
    (next) => {
      if (mode === "poll" && next === "push") invalidateAll(qc);
      mode = next;
      onMode(next);
    },
    { ...deps, onPoll: () => void qc.invalidateQueries({ queryKey: ["threads"] }) },
  );
}

/** Keep the queries live while mounted; the mode is null until the stream answers. */
export function useLive(): Mode | null {
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode | null>(null);
  useEffect(() => connectLive(qc, setMode), [qc]);
  return mode;
}

export function useThreads(sort: Sort) {
  return useInfiniteQuery({
    queryKey: keys.threads(sort),
    queryFn: ({ pageParam }) => api.getThreads(sort, pageParam),
    initialPageParam: "",
    getNextPageParam: (last) => last.next,
  });
}

export function useThread(root: string) {
  return useQuery({ queryKey: keys.thread(root), queryFn: () => api.getThread(root) });
}

export function useSection(name: SectionName, q = "") {
  return useInfiniteQuery({
    queryKey: keys.section(name, q),
    queryFn: ({ pageParam }) => api.getSection(name, { cursor: pageParam, q }),
    initialPageParam: "",
    getNextPageParam: (last) => last.next,
  });
}

/** The health numbers; no log line changes them, so they refresh each minute. */
export function useVitals() {
  return useQuery({ queryKey: keys.vitals, queryFn: api.getVitals, refetchInterval: 60_000 });
}

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: api.getMe });
}

/** The owner's read state; asked only when the caller is the owner (others get 403). */
export function useReadState() {
  const me = useMe();
  return useQuery({ queryKey: keys.readState, queryFn: api.getReadState, enabled: me.data?.isOwner === true });
}
