// React Query hooks over client.ts, and the live updates that keep them fresh.
import { type InvalidateQueryFilters, QueryClient, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ApiError } from "./client";
import { type EventsDeps, type Mode, startEvents } from "./events";
import type { ReadState, RosterEntry, RowRef, SectionName, Snapshot, Sort, ThreadDetail } from "./types";

export const keys = {
  threads: (sort: Sort) => ["threads", sort] as const,
  thread: (root: string) => ["thread", root] as const,
  section: (name: SectionName, q: string) => ["section", name, q] as const,
  vitals: ["vitals"] as const,
  me: ["me"] as const,
  readState: ["read-state"] as const,
  queue: ["queue"] as const,
  roster: ["roster"] as const,
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
  waiting: ["waiting"],
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
    void invalidate(qc, { queryKey: ["threads"] });
    // The land queue is read from the land messages (landqueue.py).
    void invalidate(qc, { queryKey: keys.queue });
    const holders: string[] = [];
    const held = new Set<string>();
    for (const [key, data] of qc.getQueriesData<ThreadDetail>({ queryKey: ["thread"] })) {
      const ids = new Set([String(key[1]), ...(data?.messages ?? []).map((m) => m.id)]);
      const hits = [...messageIds].filter((id) => ids.has(id));
      if (hits.length) holders.push(String(key[1]));
      for (const id of hits) held.add(id);
    }
    if (messageIds.size > held.size) {
      void invalidate(qc, { queryKey: ["thread"] });
    } else {
      for (const root of holders) void invalidate(qc, { queryKey: keys.thread(root), exact: true });
    }
  }

  // An open waiting row makes the thread of a message with its id need the owner.
  if (collections.has("waiting") && !collections.has("messages")) void invalidate(qc, { queryKey: ["threads"] });

  const sections = new Set<SectionName>();
  for (const c of collections) for (const name of SECTIONS_OF[c] ?? []) sections.add(name);
  for (const name of sections) void invalidate(qc, { queryKey: ["section", name] });
}

/**
 * invalidateQueries, plus one more read for each matching query whose first
 * load is in flight. invalidateQueries cancels a fetch in flight only when the
 * query holds data (Query.fetch with cancelRefetch), so it reuses a first
 * load, whose answer can come from before the change that called this. Each
 * such query is read again once that load ends with data, so it ends with a
 * read that started after this call. A first load that fails gets no second
 * read: its error shows, and the next poll or event reads it again. Every
 * invalidation in web/src goes through here.
 */
export function invalidate(qc: QueryClient, filters: InvalidateQueryFilters = {}): Promise<void> {
  const first = qc.getQueryCache().findAll(filters).filter((q) => q.state.data === undefined && q.state.fetchStatus === "fetching");
  return qc
    .invalidateQueries(filters)
    .then(() => Promise.all(first.filter((q) => q.state.data !== undefined).map((q) => qc.invalidateQueries({ queryKey: q.queryKey, exact: true }))))
    .then(() => undefined);
}

/**
 * Invalidate the thread list, every thread, every section and the land queue:
 * what the stream shows. Resolves when the refetches settle.
 */
export function invalidateAll(qc: QueryClient): Promise<void> {
  return Promise.all([["threads"], keys.queue, ["thread"], ["section"]].map((queryKey) => invalidate(qc, { queryKey }))).then(() => undefined);
}

/**
 * Start the live updates for `qc`. The stream has no replay, so each ready
 * event (the first one too: a row written while the page loaded) refetches
 * everything it shows, and so does each poll: the thread list, the open
 * threads, the open section and the land queue. The returned function stops
 * it all.
 */
export function connectLive(qc: QueryClient, onMode: (mode: Mode) => void, deps: EventsDeps = {}): () => void {
  return startEvents(
    (rows) => invalidateForRows(qc, rows),
    onMode,
    {
      ...deps,
      onReady: () => invalidateAll(qc),
      // A poll cannot tell what changed, so it invalidates all that the stream
      // shows. Only the queries an open view watches refetch (React Query's
      // default refetchType "active"); the others refetch when a view opens.
      onPoll: () => invalidateAll(qc),
    },
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

/**
 * The read state to keep: the one with the higher `rev` (readstate.py raises
 * it on each change), so an answer that arrives after a newer one is dropped.
 */
export function newerReadState(cur: ReadState | undefined, next: ReadState): ReadState {
  return cur && (cur.rev ?? 0) > (next.rev ?? 0) ? cur : next;
}

/** The owner's read state; asked only when the caller is the owner (others get 403). */
export function useReadState() {
  const qc = useQueryClient();
  const me = useMe();
  return useQuery({
    queryKey: keys.readState,
    queryFn: () => api.getReadState().then((next) => newerReadState(qc.getQueryData<ReadState>(keys.readState), next)),
    enabled: me.data?.isOwner === true,
  });
}

/** The land queue; a land message changes it, so the live updates refetch it. */
export function useQueue() {
  return useQuery({ queryKey: keys.queue, queryFn: api.getQueue });
}

export interface People {
  roster: Record<string, RosterEntry>;
  /** Each participant's newest check-in time (kind `check-in`, or the older `checkin`). */
  checkIns: Record<string, string>;
}

/** The roster of a snapshot, and the newest check-in of each sender. */
export function rosterOf(snapshot: Snapshot): People {
  const checkIns: Record<string, string> = {};
  for (const doc of Object.values(snapshot.collections?.messages ?? {})) {
    if (!doc || typeof doc !== "object") continue;
    const { from, kind, t } = doc as { from?: unknown; kind?: unknown; t?: unknown };
    if (typeof from !== "string" || typeof t !== "string" || Number.isNaN(Date.parse(t))) continue;
    if (kind !== "check-in" && kind !== "checkin") continue;
    const seen = checkIns[from];
    if (seen === undefined || Date.parse(t) > Date.parse(seen)) checkIns[from] = t;
  }
  return { roster: snapshot.roster ?? {}, checkIns };
}

/**
 * The roster with its colours, and the check-in times. No other route serves
 * participants.json, so this reads the whole snapshot (as the present page
 * does): once a minute, not on each log line.
 */
export function useRoster() {
  return useQuery({ queryKey: keys.roster, queryFn: api.getSnapshot, select: rosterOf, refetchInterval: 60_000 });
}
