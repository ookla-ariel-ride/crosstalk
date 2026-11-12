// The stream of threads that fold (spec, "Threads"): one row for each thread,
// opened in place; routine runs and muted threads folded into one line each.
// The server sorts; the page never sorts a list itself.
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { memo, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import { keys, useSection, useThread, useThreads, useUnreadCount } from "../../api/queries";
import type { ReadMark, SectionRow, Thread, ThreadDetail } from "../../api/types";
import { afterSummary } from "../../logic/afterSummary";
import { ago } from "../../logic/ago";
import { foldRoutine } from "../../logic/foldRoutine";
import { href, type Route, route as routeOf, type Sort } from "../../logic/route";
import { Body } from "../../ui/Body";
import { Guard } from "../../ui/Guard";
import { Row } from "../../ui/Row";
import { RowList } from "../../ui/RowList";
import { useNow } from "../../ui/useNow";
import { isLater, isUnread, OWNER_ID, unreadThreads, useReading, useUnreadTotal } from "./reading";
import { FoldLine, RoutineLine } from "./RoutineLine";
import { SortBar } from "./SortBar";
import styles from "./Stream.module.css";
import { type RenderActions, ThreadOpen } from "./ThreadOpen";

/** The route keeps this many open threads at most, the newest opened. */
export const MAX_OPEN = 20;
const ROUTE_KEY = "crosstalk.route";
/** How long the status line of "Mark all read" stays, in ms. */
export const STATUS_MS = 5000;

const NO_ROWS = new Map<string, Thread>();

/** What the last "Mark all read" did: how many threads it marked, or that the write failed. */
type MarkStatus = { ok: true; count: number } | { ok: false } | null;

type Item =
  | { type: "thread"; thread: Thread }
  | { type: "routine"; threads: Thread[]; counts: Parameters<typeof RoutineLine>[0]["counts"] }
  | { type: "muted"; threads: Thread[] }
  | { type: "more" };

const keyOf = (item: Item): string =>
  item.type === "thread" ? "t:" + item.thread.root : item.type === "routine" ? "r:" + (item.threads[0]?.root ?? "") : item.type;

interface ThreadRowProps {
  thread: Thread;
  open: boolean;
  onToggle: (root: string) => void;
  now: Date;
  renderActions?: RenderActions;
  rowActions?: RowActions;
}

/** The owner's controls under a folded thread row (Needs you). */
export type RowActions = (thread: Thread) => ReactNode;

const ThreadRow = memo(function ThreadRow({ thread, open, onToggle, now, renderActions, rowActions }: ThreadRowProps) {
  const reading = useReading();
  // The owner's count comes from the server; a viewer sees whether anything is new.
  const fresh = isUnread(thread, reading.state, reading.ready) ? (thread.unread !== undefined ? `${thread.unread} new` : "new") : "";
  const note = (
    <>
      <span className={styles.noteLine}>{ago(thread.last_t, now)}</span>
      {thread.needs_owner ? <span className={styles.needs}>needs you</span> : null}
      {fresh ? <span className={styles.fresh}>{fresh}</span> : null}
    </>
  );
  return (
    <Row
      projects={thread.projects}
      title={thread.title}
      text={thread.preview}
      note={note}
      open={open}
      onOpen={() => onToggle(thread.root)}
      actions={rowActions?.(thread)}
    >
      {open ? <ThreadOpen thread={thread} now={now} renderActions={renderActions} /> : null}
    </Row>
  );
});

interface LinkedProps {
  id: string;
  now: Date;
  onToggle: (id: string) => void;
  /** The id names a message whose thread has another root: open that root in its place. */
  onResolve: (id: string, root: string) => void;
  renderActions?: RenderActions;
}

/** An open thread that no loaded page holds, as an id link opens it: the thread that holds `id`. */
function LinkedThread({ id, now, onToggle, onResolve, renderActions }: LinkedProps) {
  const query = useThread(id);
  const root = query.data?.root;
  useEffect(() => {
    if (root !== undefined && root !== id) onResolve(id, root);
  }, [id, root, onResolve]);
  if (query.isError) {
    const gone = query.error instanceof ApiError && query.error.status === 404;
    return (
      <p className={styles.state}>
        {gone ? `No thread holds ${id}.` : `The thread of ${id} did not load. Fold it and open it again.`}{" "}
        <button type="button" className={styles.action} onClick={() => onToggle(id)}>
          Fold
        </button>
      </p>
    );
  }
  const thread = query.data?.thread;
  if (!thread || root !== id) return <p className={styles.state}>Loading.</p>;
  return <ThreadRow thread={thread} open onToggle={onToggle} now={now} renderActions={renderActions} />;
}

/** The open waiting rows (the present page's Waiting on you): each opens in place to its body and actions. */
function WaitingList({ rows, now, actions }: { rows: SectionRow[]; now: Date; actions?: (row: SectionRow) => ReactNode }) {
  const [open, setOpen] = useState<string[]>([]);
  const str = (v: unknown) => (typeof v === "string" ? v : "");
  return (
    <RowList
      short
      label="Waiting on you"
      items={rows}
      itemKey={(row) => row.id}
      estimateSize={64}
      renderRow={(row) => {
        const shown = open.includes(row.id);
        const rest = afterSummary(str(row.body), str(row.summary));
        return (
          <Row
            projects={str(row.project) ? [str(row.project)] : []}
            title={str(row.summary) || row.id}
            text={rest.split("\n")[0]}
            note={<span className={styles.noteLine}>{"waiting since " + ago(str(row.since), now)}</span>}
            open={shown}
            onOpen={() => setOpen(shown ? open.filter((id) => id !== row.id) : [...open, row.id])}
          >
            {shown ? (
              <div className={styles.thread}>
                <Body text={rest} />
                <div className={styles.end}>{actions?.(row)}</div>
              </div>
            ) : null}
          </Row>
        );
      }}
    />
  );
}

/** Draws the next page when the list nears its end; the button is the keyboard path. */
function LoadMore({ load, busy }: { load: () => void; busy: boolean }) {
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => loadRef.current(), []);
  return (
    <div className={styles.more}>
      <button type="button" className={styles.action} onClick={load} disabled={busy}>
        {busy ? "Loading more." : "Load more"}
      </button>
    </div>
  );
}

export interface StreamProps {
  sort: Sort;
  open: string[];
  onOpenChange: (open: string[]) => void;
  /** The Needs you page: only the threads that need the owner, no sort bar. */
  needsOnly?: boolean;
  renderActions?: RenderActions;
  rowActions?: RowActions;
  /** What shows above the heading, such as the owner's "Start a thread". */
  top?: ReactNode;
  /** What shows first under the heading, such as the open waiting rows; the stream is then never "empty". */
  lead?: ReactNode;
}

export function Stream({ sort, open, onOpenChange, needsOnly = false, renderActions, rowActions, top, lead }: StreamProps) {
  const query = useThreads(sort);
  const reading = useReading();
  const now = useNow();
  const qc = useQueryClient();
  const total = useUnreadTotal();

  const pages = query.data?.pages;
  const muted = reading.state.muted;
  const loaded = useMemo(() => pages?.flatMap((p) => p.threads) ?? [], [pages]);
  // The open ids, each once. An id that no loaded thread has as its root
  // shows above the list, once the list is in: a thread on a later page, or a
  // message an id link names.
  const shownOpen = useMemo(() => [...new Set(open)], [open]);
  const openSet = useMemo(() => new Set(shownOpen), [shownOpen]);

  // The view New lists only the unread threads. A row listed in this visit
  // stays while it is open, so reading it never pulls it out from under the
  // reader; it leaves on the next load of the page or a change of the sort.
  const unreadOnly = sort === "new" && !needsOnly;
  const kept = useRef<{ sort: Sort; rows: Map<string, Thread> }>({ sort, rows: new Map() });
  const { state: readState, ready } = reading;
  const list = useMemo(() => {
    const base = needsOnly ? loaded.filter((t) => t.needs_owner) : loaded;
    if (!unreadOnly) return base;
    const rows = kept.current.sort === sort ? kept.current.rows : NO_ROWS;
    const listed = base.filter((t) => isUnread(t, readState, ready) || (openSet.has(t.root) && rows.has(t.root)));
    const roots = new Set(listed.map((t) => t.root));
    const extra = [...rows.values()].filter((t) => openSet.has(t.root) && !roots.has(t.root));
    // Newest first, as the server sorts New: by last_t, then last_id.
    const out = extra.length
      ? [...listed, ...extra].sort((a, b) => (isLater({ t: a.last_t, id: a.last_id }, { t: b.last_t, id: b.last_id }) ? -1 : 1))
      : listed;
    return out;
  }, [loaded, needsOnly, unreadOnly, readState, ready, openSet, sort]);
  // Keep what this visit listed, after the render that listed it.
  useEffect(() => {
    if (kept.current.sort !== sort) kept.current = { sort, rows: new Map() };
    if (unreadOnly) for (const t of list) kept.current.rows.set(t.root, t);
  }, [list, sort, unreadOnly]);

  const linked = useMemo(() => {
    if (!pages) return [];
    // In New a loaded thread that is read is not listed: open, it shows above the list.
    const roots = new Set((unreadOnly ? list : loaded).map((t) => t.root));
    return shownOpen.filter((id) => !roots.has(id));
  }, [pages, loaded, list, unreadOnly, shownOpen]);

  // The set the next toggle starts from. A toggle sets it at once, so a second
  // click before the route changes builds on the first; a new route resets it.
  const openRef = useRef(shownOpen);
  const seenOpen = useRef(shownOpen);
  if (seenOpen.current !== shownOpen) {
    seenOpen.current = shownOpen;
    openRef.current = shownOpen;
  }
  const changeRef = useRef(onOpenChange);
  changeRef.current = onOpenChange;
  // The status line of "Mark all read". A success goes after STATUS_MS, a
  // failure stays; either goes on the next action.
  const [status, setStatus] = useState<MarkStatus>(null);
  const [marking, setMarking] = useState(false);
  const markButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!status?.ok) return;
    const timer = setTimeout(() => setStatus(null), STATUS_MS);
    return () => clearTimeout(timer);
  }, [status]);
  const onToggle = useCallback((root: string) => {
    setStatus(null);
    const current = openRef.current;
    const next = current.includes(root) ? current.filter((r) => r !== root) : [...current, root].slice(-MAX_OPEN);
    openRef.current = next;
    changeRef.current(next);
  }, []);
  const onResolve = useCallback((id: string, root: string) => {
    const next = [...new Set(openRef.current.map((o) => (o === id ? root : o)))];
    openRef.current = next;
    changeRef.current(next);
  }, []);

  // The needs order puts the threads that need the owner first: past them, no page holds more.
  const more = query.hasNextPage && (!needsOnly || loaded.every((t) => t.needs_owner));
  const items = useMemo(() => {
    const quiet = new Set(muted);
    // A muted thread comes back when it needs the owner: someone asks the owner in it.
    const hidden = list.filter((t) => quiet.has(t.root) && !t.needs_owner);
    const shown = hidden.length ? list.filter((t) => !hidden.includes(t)) : list;
    const out: Item[] = foldRoutine(shown).map((item) =>
      item.kind === "thread"
        ? { type: "thread", thread: item.thread as Thread }
        : { type: "routine", threads: item.threads as Thread[], counts: item.counts },
    );
    if (hidden.length) out.push({ type: "muted", threads: hidden });
    if (more) out.push({ type: "more" });
    return out;
  }, [list, muted, more]);

  const renderThread = useCallback(
    (thread: Thread) => (
      <ThreadRow
          thread={thread}
          open={seenOpen.current.includes(thread.root)}
          onToggle={onToggle}
          now={now}
          renderActions={renderActions}
          rowActions={rowActions}
        />
    ),
    [onToggle, now, renderActions, rowActions],
  );
  const openKey = (threads: Thread[]) => threads.filter((t) => openSet.has(t.root)).map((t) => t.root).join("\n");
  const fetchNext = query.fetchNextPage;
  const busy = query.isFetchingNextPage;
  const loadMore = useCallback(() => {
    if (!busy) void fetchNext();
  }, [fetchNext, busy]);

  const renderRow = (item: Item) => {
    switch (item.type) {
      case "thread":
        return (
          <Guard what={"Thread " + item.thread.root} data={item.thread}>
            <ThreadRow
              thread={item.thread}
              open={openSet.has(item.thread.root)}
              onToggle={onToggle}
              now={now}
              renderActions={renderActions}
              rowActions={rowActions}
            />
          </Guard>
        );
      case "routine":
        return <RoutineLine counts={item.counts} threads={item.threads} now={now} renderThread={renderThread} openKey={openKey(item.threads)} />;
      case "muted":
        return (
          <FoldLine
            title={`${item.threads.length} muted`}
            text="Muted threads. They come back when someone asks you in them."
            label="Muted threads"
            threads={item.threads}
            now={now}
            renderThread={renderThread}
            openKey={openKey(item.threads)}
          />
        );
      case "more":
        return <LoadMore load={loadMore} busy={busy} />;
    }
  };

  // The newest server time of the pages shown: "Mark all read" never reaches past what was loaded.
  const serverTime = pages?.reduce((top, p) => (p.server_time > top ? p.server_time : top), "") ?? "";

  // A message dated after the server time (a writer's clock ahead) stays past
  // all_before, so each thread shown with such a message gets its own mark at
  // its newest message: the list row, or the open thread's row when that one
  // is later. A message that comes after the click stays new everywhere else.
  // The threads opened by id that no loaded page holds: their rows count too.
  const linkedRows = useQueries({ queries: linked.map((id) => ({ queryKey: keys.thread(id), queryFn: () => api.getThread(id) })) });
  // The owner's count is the server's, loaded or not; a viewer's (and the
  // owner's until the server's loads) counts the loaded threads and the
  // threads opened by id.
  const owner = reading.self === OWNER_ID;
  const server = useUnreadCount(owner).data;
  const loadedUnread = unreadThreads([...list, ...linkedRows.flatMap((q) => (q.data?.thread ? [q.data.thread] : []))], reading.state, reading.ready).length;
  const unread = owner && server !== undefined ? server : loadedUnread;
  const off = !serverTime || !reading.ready || marking || unread === 0;

  const markAll = () => {
    // The button stays focusable while off (aria-disabled): a click then does nothing.
    if (off) return;
    const marks: Record<string, ReadMark> = {};
    const add = (row: Thread | undefined) => {
      if (!row || row.last_t <= serverTime) return;
      const mark = { t: row.last_t, id: row.last_id };
      if (isLater(mark, marks[row.root])) marks[row.root] = mark;
    };
    for (const t of loaded) add(t);
    for (const id of [...new Set([...loaded.map((t) => t.root), ...linked])]) add(qc.getQueryData<ThreadDetail>(keys.thread(id))?.thread);
    const count = unread;
    // A click on Retry removes it: the focus goes to the button it repeats.
    if (markButton.current && document.activeElement !== markButton.current) markButton.current.focus();
    setStatus(null);
    setMarking(true);
    void reading.markAll(serverTime, marks).then((ok) => {
      setMarking(false);
      setStatus(ok ? { ok: true, count } : { ok: false });
    });
  };

  let body;
  if (query.isError) {
    const denied = query.error instanceof ApiError && (query.error.status === 401 || query.error.status === 403);
    body = (
      <p className={styles.state}>
        {denied
          ? "The server needs you to sign in to read the threads. Sign in on the Roster page, then come back."
          : "The threads did not load. Check that the relay server runs, then reload the page."}
      </p>
    );
  } else if (query.isPending) {
    body = <p className={styles.state}>Loading.</p>;
  } else if (items.length === 0 && unreadOnly) {
    body = <p className={styles.state}>All read. New posts show here.</p>;
  } else if (items.length === 0 && lead) {
    body = null;
  } else if (items.length === 0) {
    body = (
      <p className={styles.state}>
        {needsOnly ? "Nothing needs you now." : "No threads yet. A thread shows here when a session posts."}
      </p>
    );
  } else {
    body = (
      <RowList
        label={needsOnly ? "Needs you" : "Conversations"}
        items={items}
        itemKey={keyOf}
        estimateSize={64}
        renderRow={renderRow}
      />
    );
  }

  return (
    <>
      {top ? <div className={styles.top}>{top}</div> : null}
      <div className={styles.head}>
        <h1 className={styles.heading}>{needsOnly ? "Needs you" : "Conversations"}</h1>
        {needsOnly ? null : <SortBar sort={sort} open={open} unread={total} />}
        <div className={styles.tools}>
          <button
            type="button"
            className={styles.action}
            onClick={() => {
              setStatus(null);
              onOpenChange([]);
            }}
            disabled={shownOpen.length === 0}
          >
            Fold all
          </button>
          <button ref={markButton} type="button" className={styles.action} onClick={markAll} aria-disabled={off}>
            {unread === 0 ? "All read" : `Mark all read (${unread})`}
          </button>
          {/* Always in the page, so a screen reader hears each change; it floats, so the head keeps its height. */}
          <p role="status" aria-live="polite" className={styles.status}>
            {status?.ok === true ? `Marked ${status.count} thread${status.count === 1 ? "" : "s"} read` : null}
            {status?.ok === false ? (
              <>
                Not saved.{" "}
                <button type="button" className={styles.retry} onClick={markAll}>
                  Retry
                </button>
              </>
            ) : null}
          </p>
        </div>
      </div>
      {lead}
      {linked.length > 0 ? (
        <RowList
          short
          label="Opened threads"
          items={linked}
          itemKey={(id) => id}
          estimateSize={64}
          renderRow={(id) => <LinkedThread id={id} now={now} onToggle={onToggle} onResolve={onResolve} renderActions={renderActions} />}
        />
      ) : null}
      {body}
    </>
  );
}

/** The Conversations page: the sort order and the open threads live in the route and in this tab's sessionStorage. */
export function Conversations({
  route,
  renderActions,
  top,
}: {
  route: Extract<Route, { name: "conversations" }>;
  renderActions?: RenderActions;
  top?: ReactNode;
}) {
  const open = useMemo(() => [...new Set(route.open)].slice(-MAX_OPEN), [route.open]);

  // A tab that comes back to the page with no hash goes to its last route.
  useEffect(() => {
    if (window.location.hash.replace(/^#\/?/, "") !== "") return;
    let saved: string | null = null;
    try {
      saved = window.sessionStorage.getItem(ROUTE_KEY);
    } catch {
      // No storage: stay on the default route.
    }
    if (saved && routeOf(saved).name === "conversations") window.location.replace(saved);
  }, []);

  // This effect runs after the one above has read the saved route.
  const hash = href({ ...route, open });
  useEffect(() => {
    try {
      window.sessionStorage.setItem(ROUTE_KEY, hash);
    } catch {
      // The route still lives in the URL.
    }
  }, [hash]);

  const onOpenChange = useCallback(
    (next: string[]) => {
      window.location.hash = href({ name: "conversations", sort: route.sort, open: next.slice(-MAX_OPEN) });
    },
    [route.sort],
  );
  return <Stream sort={route.sort} open={open} onOpenChange={onOpenChange} renderActions={renderActions} top={top} />;
}

/**
 * The Needs you page: the open waiting rows, then the stream in the needs
 * order with only the threads that need the owner.
 */
export function NeedsYou({
  renderActions,
  rowActions,
  waitingActions,
}: {
  renderActions?: RenderActions;
  rowActions?: RowActions;
  waitingActions?: (row: SectionRow) => ReactNode;
}) {
  const [open, setOpen] = useState<string[]>([]);
  const now = useNow();
  const waiting = useSection("waiting");
  const pages = waiting.data?.pages;
  // A page whose cursor shifted (a row added or closed between two fetches)
  // can repeat a row: each id shows once.
  const rows = useMemo(() => {
    const seen = new Set<string>();
    return (pages?.flatMap((p) => p.rows) ?? []).filter((r) => !r.done && !seen.has(r.id) && !!seen.add(r.id));
  }, [pages]);
  // The server sends the open rows first, 50 to a page, and counts them. Once a
  // done row is loaded no open one is left; until then the button stays while
  // the server has a next page, with the count when it is above 0.
  const openCount = pages?.[pages.length - 1]?.open ?? 0;
  const more = waiting.hasNextPage && !pages?.some((p) => p.rows.some((r) => r.done));
  const left = Math.max(0, openCount - rows.length);
  const lead =
    rows.length || more ? (
      <>
        {rows.length ? <WaitingList rows={rows} now={now} actions={waitingActions} /> : null}
        {more ? (
          <div className={styles.more}>
            <button
              type="button"
              className={styles.action}
              disabled={waiting.isFetchingNextPage}
              onClick={() => void waiting.fetchNextPage({ cancelRefetch: false })}
            >
              {left ? `Show ${left} more waiting row${left === 1 ? "" : "s"}` : "Show more waiting rows"}
            </button>
            {waiting.isFetchNextPageError ? (
              <span role="alert" className={styles.error}>
                The next waiting rows did not load. Press the button to try again.
              </span>
            ) : null}
          </div>
        ) : null}
      </>
    ) : null;
  return <Stream sort="needs" open={open} onOpenChange={setOpen} needsOnly renderActions={renderActions} rowActions={rowActions} lead={lead} />;
}
