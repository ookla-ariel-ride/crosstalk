// The reader's read state: the owner's comes from the server, a viewer's from
// localStorage under one key, in the same shape. A storage error never breaks
// the page: the state then lives in memory for this tab.
import { useQueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { api } from "../../api/client";
import { invalidate, keys, newerReadState, useMe, useReadState, useThreads, useUnreadCount } from "../../api/queries";
import type { ReadMark, ReadState, Thread } from "../../api/types";

export const STORAGE_KEY = "crosstalk.read-state";

const EMPTY: ReadState = { threads: {}, muted: [], all_before: null };

const isMark = (v: unknown): v is ReadMark =>
  !!v && typeof (v as ReadMark).t === "string" && typeof (v as ReadMark).id === "string";

function parse(text: string | null): ReadState {
  try {
    const raw = JSON.parse(text ?? "null") as Partial<ReadState> | null;
    if (!raw || typeof raw !== "object") return EMPTY;
    const threads: Record<string, ReadMark> = {};
    for (const [root, mark] of Object.entries(raw.threads ?? {})) if (isMark(mark)) threads[root] = { t: mark.t, id: mark.id };
    return {
      threads,
      muted: Array.isArray(raw.muted) ? raw.muted.filter((r): r is string => typeof r === "string") : [],
      all_before: typeof raw.all_before === "string" ? raw.all_before : null,
    };
  } catch {
    return EMPTY;
  }
}

let memo: { text: string | null; state: ReadState } = { text: null, state: EMPTY };
/** A state the storage refused to keep, and the stored text it stands over; a change of that text drops it. */
let unsaved: { over: string | null; state: ReadState } | null = null;
const listeners = new Set<() => void>();
/** How many times "Mark all read" ran in this tab: an open thread then drops its line "New since". */
let markAllCount = 0;
const markAllRuns = () => markAllCount;

function storedText(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // No storage: the empty state.
    return null;
  }
}

function localState(): ReadState {
  const text = storedText();
  if (unsaved && unsaved.over === text) return unsaved.state;
  unsaved = null;
  if (text !== memo.text) memo = { text, state: parse(text) };
  return memo.state;
}

function saveLocal(next: ReadState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    unsaved = null;
  } catch {
    unsaved = { over: storedText(), state: next };
  }
  for (const fn of listeners) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  window.addEventListener("storage", fn);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", fn);
  };
}

/** Whether mark `a` is later than `b`, compared as the server does: `t`, then `id`. */
export function isLater(a: ReadMark, b: ReadMark | undefined): boolean {
  return !b || a.t > b.t || (a.t === b.t && a.id > b.id);
}

/** The read position of `root`: its own mark or the mark-all time, whichever is later. */
export function markOf(state: ReadState, root: string): ReadMark | undefined {
  const own = state.threads[root];
  // An id that sorts after every id: each message at or before all_before is read.
  const all = state.all_before ? { t: state.all_before, id: "￿" } : undefined;
  if (!all) return own;
  return own && isLater(own, all) ? own : all;
}

/**
 * Whether `thread` holds a message new to the reader. The owner's row carries
 * the server's `unread`; a read state that already covers the newest message
 * (a mark-all time the server kept before the list loads again) makes it read.
 * A row with no count (a viewer's) counts by the marks once the state is ready.
 */
export function isUnread(thread: Thread, state: ReadState, ready: boolean): boolean {
  if (thread.unread !== undefined ? thread.unread <= 0 : !ready) return false;
  return isLater({ t: thread.last_t, id: thread.last_id }, markOf(state, thread.root));
}

/**
 * The unread threads of `list`, each root once, as the stream shows them: a
 * muted thread is hidden, and so left out, unless it needs the owner.
 */
export function unreadThreads(list: Thread[], state: ReadState, ready: boolean): Thread[] {
  const seen = new Set<string>();
  const muted = new Set(state.muted);
  return list.filter((t) => {
    if (seen.has(t.root) || (muted.has(t.root) && !t.needs_owner) || !isUnread(t, state, ready)) return false;
    seen.add(t.root);
    return true;
  });
}

/**
 * How many threads are unread. For the owner, the server's count (every
 * unread thread, loaded or not); until it loads, and for a viewer, whose state
 * is local, the unread threads on the loaded pages of the needs order.
 */
export function useUnreadTotal(): number {
  const { state, ready, self } = useReading();
  const me = useMe();
  const owner = self === OWNER_ID;
  const server = useUnreadCount(owner).data;
  const pages = useThreads("needs", me.data !== undefined && !owner).data?.pages;
  if (owner && server !== undefined) return server;
  return unreadThreads(pages?.flatMap((p) => p.threads) ?? [], state, ready).length;
}

/** Writes in flight, so one mark is sent once. */
const sending = new Set<string>();

/**
 * The owner's participant id in the log (readstate.OWNER on the server, which
 * leaves the owner's own posts out of `unread`). /api/me names only the role,
 * so the page knows the id from the role.
 */
export const OWNER_ID = "owner";

export interface Reading {
  /** False while the role or the owner's state loads; nothing is written then. */
  ready: boolean;
  /** The reader's own participant id: the owner's, or null for a viewer, who posts nothing. */
  self: string | null;
  state: ReadState;
  markThread(root: string, mark: ReadMark): void;
  /**
   * Mark every message up to `t` read. The owner's server also marks each
   * thread dated after `t`, loaded or not; a viewer's state marks each thread
   * of `marks` up to its mark: the threads shown whose newest message is dated
   * after `t`. False when the owner's write failed.
   */
  markAll(t: string, marks?: Record<string, ReadMark>): Promise<boolean>;
  /** How many times markAll ran in this tab; for the owner, once the server kept its writes. */
  markAllRuns: number;
  /** Mute or unmute a thread: the stream hides a muted one until it needs the owner. False when the write failed. */
  mute(root: string, muted: boolean): Promise<boolean>;
}

export function useReading(): Reading {
  const qc = useQueryClient();
  const me = useMe().data;
  const server = useReadState().data;
  const local = useSyncExternalStore(subscribe, localState, () => EMPTY);
  const runs = useSyncExternalStore(subscribe, markAllRuns, markAllRuns);
  const owner = me?.isOwner === true;
  const ready = me !== undefined && (!owner || server !== undefined);
  const state = owner ? (server ?? EMPTY) : local;

  /** Send one write; false when it failed. A write already in flight counts as sent. */
  const send = (key: string, body: Parameters<typeof api.putReadState>[0]): Promise<boolean> => {
    if (sending.has(key)) return Promise.resolve(true);
    sending.add(key);
    return api
      .putReadState(body)
      .then((next) => {
        // Two writes can answer out of order: keep the newer state.
        qc.setQueryData<ReadState>(keys.readState, (cur) => newerReadState(cur, next));
        // The list rows and the rows of the threads opened by id both carry `unread`.
        void invalidate(qc, { queryKey: ["threads"] });
        void invalidate(qc, { queryKey: ["thread"] });
        return true;
      })
      .catch(() => false)
      .finally(() => sending.delete(key));
  };

  return {
    ready,
    self: owner ? OWNER_ID : null,
    state,
    markThread(root, mark) {
      if (!ready || !isLater(mark, markOf(state, root))) return;
      // A failed mark stays where it was; the next view tries again.
      if (owner) void send(`${root}\n${mark.t}\n${mark.id}`, { thread: root, t: mark.t, id: mark.id });
      else saveLocal({ ...state, threads: { ...state.threads, [root]: mark } });
    },
    markAllRuns: runs,
    markAll(t, marks = {}) {
      if (!ready) return Promise.resolve(false);
      const all = state.all_before === null || t > state.all_before;
      const later = Object.entries(marks).filter(([root, mark]) => isLater(mark, markOf(state, root)));
      const done = () => {
        markAllCount += 1;
        for (const fn of listeners) fn();
      };
      if (owner) {
        // `ahead`: the server also marks each thread dated after its own clock (a
        // writer's clock runs ahead), loaded or not; a post that came after the
        // page loaded, or after the click, stays new.
        return send(`\n${t}\nahead`, { all_before: t, ahead: true }).then((ok) => {
          if (!ok) return false;
          // The count comes from the server's next read (send invalidates it): a post that
          // came after the page loaded stays unread.
          // The open threads drop "New since" only once the server kept the write.
          done();
          return true;
        });
      }
      if (all || later.length) {
        // One write, so the marks and the time do not overwrite each other.
        saveLocal({ ...state, all_before: all ? t : state.all_before, threads: { ...state.threads, ...Object.fromEntries(later) } });
      }
      done();
      return Promise.resolve(true);
    },
    mute(root, muted) {
      if (!ready || state.muted.includes(root) === muted) return Promise.resolve(true);
      if (owner) return send(`${root}\nmuted\n${muted}`, { thread: root, muted });
      saveLocal({ ...state, muted: muted ? [...state.muted, root] : state.muted.filter((r) => r !== root) });
      return Promise.resolve(true);
    },
  };
}
