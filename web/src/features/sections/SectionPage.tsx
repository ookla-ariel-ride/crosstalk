// Every section of the page on the one shared list (spec, "The shared list"):
// a heading with the server's counts, one search box, and the rows in the
// server's order. A row opens in place and shows its detail. The one exception
// to the server's order is "Lessons and incidents", which merges two
// collections by time (merge.ts).
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { useQueue, useSection } from "../../api/queries";
import type { SectionName, SectionPage as Page } from "../../api/types";
import { href, SECTIONS, type Section } from "../../logic/route";
import { Body } from "../../ui/Body";
import { Row } from "../../ui/Row";
import { RowList } from "../../ui/RowList";
import { labelOf } from "../../ui/Sidebar";
import { useNow } from "../../ui/useNow";
import { adoptionRow } from "./adoption";
import { agreementRow } from "./agreements";
import { decisionRow } from "./decisions";
import type { Doc, RowView } from "./fields";
import { incidentRow } from "./incidents";
import { lessonRow } from "./lessons";
import { lockRow } from "./locks";
import { mergeByTime } from "./merge";
import { progressRow } from "./progress";
import { queueEntries, queueRow } from "./queue";
import { reviewRow } from "./reviews";
import { scheduleRow } from "./schedules";
import styles from "./SectionPage.module.css";

export type Mapper = (doc: Doc, now: Date) => RowView;

/** The sections that read one collection: the server's name for it and its mapper. */
const SINGLE: Partial<Record<Section, { server: SectionName; map: Mapper; notes?: string[] }>> = {
  progress: { server: "progress", map: progressRow },
  reviews: { server: "reviews", map: reviewRow },
  schedules: { server: "schedules", map: scheduleRow },
  decisions: { server: "decisions", map: decisionRow },
  agreements: { server: "agreements", map: agreementRow },
  claims: { server: "locks", map: lockRow },
  adoption: {
    server: "adoption",
    map: adoptionRow,
    notes: ["These are the records the projects made. The gaps (a standard with no record) come in a later step."],
  },
};

interface Item {
  key: string;
  view: RowView;
}

interface More {
  has: boolean;
  loading: boolean;
  load: () => void;
}

export function countText(open: number, total: number): string {
  if (total <= 0) return "None open";
  return (open > 0 ? `${open} open` : "None open") + ` of ${total}`;
}

/** What went wrong, and what to do. */
function failure(error: unknown): { text: string; retry: boolean } {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return { text: "You have no access to this section. Sign in on the Roster page, then reload.", retry: false };
  }
  if (error instanceof ApiError && error.status === 404) {
    return { text: "The server has no such section. Update the server, then reload.", retry: false };
  }
  const why = error instanceof Error && error.message ? ` (${error.message})` : "";
  return { text: `The rows did not load${why}. Check that the server runs, then try again.`, retry: true };
}

/** Put `q` in the route: a reload and a shared link keep it. It replaces the history entry. */
function search(section: Section, q: string): void {
  window.location.replace(href({ name: "section", section, q }));
}

function SearchBox({ section, q, label }: { section: Section; q: string; label: string }) {
  const id = useId();
  const [text, setText] = useState(q);
  // The q this box last sent; a q from elsewhere (a link, Clear) fills the box.
  const sent = useRef(q);
  useEffect(() => {
    if (q === sent.current) return;
    sent.current = q;
    setText(q);
  }, [q]);
  useEffect(() => {
    if (text === sent.current) return;
    const timer = setTimeout(() => {
      sent.current = text;
      search(section, text);
    }, 300);
    return () => clearTimeout(timer);
  }, [text, section]);
  return (
    <div className={styles.search}>
      <label htmlFor={id}>Search {label.toLowerCase()}</label>
      <input id={id} type="search" className={styles.input} value={text} onChange={(e) => setText(e.target.value)} />
    </div>
  );
}

/** Mounted with the last row: the list has reached its end, so the next page loads. */
function NearEnd({ onReach }: { onReach: () => void }) {
  useEffect(() => onReach(), []); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

interface SectionListProps {
  label: string;
  count: string | null;
  items: Item[];
  pending: boolean;
  error: unknown;
  retry: () => void;
  more?: More;
  /** The search box, for a section the server can search. */
  find?: { section: Section; q: string };
  notes?: string[];
}

/** The page of one section: heading and counts, search, notes, then the list or a line that says why there is none. */
export function SectionList({ label, count, items, pending, error, retry, more, find, notes = [] }: SectionListProps) {
  // One row open at a time. The route has no place for it, so it is component state.
  const [openKey, setOpenKey] = useState<string | null>(null);
  const empty = !pending && items.length === 0;
  const loadMore = more?.has ? more.load : null;
  useEffect(() => {
    if (empty && !error && loadMore) loadMore();
  }, [empty, error, loadMore]);

  let body;
  const problem = error ? failure(error) : null;
  if (pending) body = <p className={styles.muted}>Loading.</p>;
  else if (problem && items.length === 0) body = null;
  else if (empty && find?.q)
    body = (
      <p className={styles.line}>
        No row matches "{find.q}".{" "}
        <button type="button" className={styles.button} onClick={() => search(find.section, "")}>
          Clear the search
        </button>
      </p>
    );
  else if (empty) body = more?.has ? null : <p className={styles.muted}>This section has no rows yet.</p>;
  else
    body = (
      <div className={styles.list}>
        <RowList
          label={label}
          items={items}
          itemKey={(item) => item.key}
          estimateSize={60}
          renderRow={(item, index) => (
            <>
              <Row
                projects={item.view.projects}
                title={item.view.title}
                text={item.view.text}
                note={item.view.note.join(", ")}
                open={openKey === item.key}
                onOpen={() => setOpenKey((k) => (k === item.key ? null : item.key))}
              >
                {item.view.detail.length ? <Body blocks={item.view.detail} /> : <p className={styles.muted}>No more detail.</p>}
              </Row>
              {loadMore && index === items.length - 1 ? <NearEnd onReach={loadMore} /> : null}
            </>
          )}
        />
      </div>
    );

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <h1 className={styles.heading}>{label}</h1>
        {count !== null ? <p className={styles.count}>{count}</p> : null}
      </div>
      {find ? <SearchBox section={find.section} q={find.q} label={label} /> : null}
      {notes.map((note) => (
        <p key={note} className={styles.muted}>
          {note}
        </p>
      ))}
      {problem ? (
        <p className={styles.line} role="alert">
          {problem.text}{" "}
          {problem.retry ? (
            <button type="button" className={styles.button} onClick={retry}>
              Try again
            </button>
          ) : null}
        </p>
      ) : null}
      {body}
      {more?.has && !pending && !problem ? (
        <p className={styles.more}>
          <button type="button" className={styles.button} disabled={more.loading} onClick={more.load}>
            {more.loading ? "Loading more" : "Load more"}
          </button>
        </p>
      ) : null}
    </div>
  );
}

/** The rows of all loaded pages, each id once (a page boundary may move while rows change). */
function rowsOf(pages: Page[] | undefined): Doc[] {
  const seen = new Set<string>();
  const out: Doc[] = [];
  for (const page of pages ?? []) {
    for (const row of page.rows ?? []) {
      if (seen.has(row.id)) continue;
      seen.add(row.id);
      out.push(row);
    }
  }
  return out;
}

type SectionQuery = ReturnType<typeof useSection>;

function moreOf(query: SectionQuery): More {
  return {
    has: query.hasNextPage,
    loading: query.isFetchingNextPage,
    load: () => {
      if (query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage({ cancelRefetch: false });
    },
  };
}

function SingleSection({ section, server, map, q, notes }: { section: Section; server: SectionName; map: Mapper; q: string; notes?: string[] }) {
  const query = useSection(server, q);
  const now = useNow();
  const items = useMemo(() => rowsOf(query.data?.pages).map((doc) => ({ key: String(doc.id), view: map(doc, now) })), [query.data, map, now]);
  const first = query.data?.pages[0];
  return (
    <SectionList
      label={labelOf({ name: "section", section, q: "" })}
      count={first ? countText(first.open, first.total) : null}
      items={items}
      pending={query.isPending}
      error={query.error}
      retry={() => void query.refetch()}
      more={moreOf(query)}
      find={{ section, q }}
      notes={notes}
    />
  );
}

/** Lessons and incidents: both collections, paged each on its own, in one list by time. */
function LessonsAndIncidents({ q }: { q: string }) {
  const lessons = useSection("lessons", q);
  const incidents = useSection("incidents", q);
  const now = useNow();
  const merged = useMemo(
    () =>
      mergeByTime(
        {
          lessons: { rows: rowsOf(lessons.data?.pages), done: !lessons.hasNextPage },
          incidents: { rows: rowsOf(incidents.data?.pages), done: !incidents.hasNextPage },
        },
        (source, doc) => source === "incidents" && !doc.end,
      ),
    [lessons.data, incidents.data, lessons.hasNextPage, incidents.hasNextPage],
  );
  const items = useMemo(
    () =>
      merged.rows.map(({ source, doc }) => ({
        key: source + ":" + String(doc.id),
        view: source === "lessons" ? lessonRow(doc, now) : incidentRow(doc, now),
      })),
    [merged, now],
  );
  const a = lessons.data?.pages[0];
  const b = incidents.data?.pages[0];
  const queries = { lessons, incidents };
  return (
    <SectionList
      label={labelOf({ name: "section", section: "lessons", q: "" })}
      count={a && b ? countText(a.open + b.open, a.total + b.total) : null}
      items={items}
      pending={lessons.isPending || incidents.isPending}
      error={lessons.error ?? incidents.error}
      retry={() => {
        void lessons.refetch();
        void incidents.refetch();
      }}
      more={{
        has: merged.need.length > 0,
        loading: lessons.isFetchingNextPage || incidents.isFetchingNextPage,
        load: () => {
          for (const source of merged.need) moreOf(queries[source]).load();
        },
      }}
      find={{ section: "lessons", q }}
    />
  );
}

function LandQueueSection() {
  const query = useQueue();
  const now = useNow();
  const queue = query.data;
  const items = useMemo(
    () => queueEntries(queue).map((entry) => ({ key: entry.role + ":" + String(entry.id), view: queueRow(entry, now) })),
    [queue, now],
  );
  const holders = Array.isArray(queue?.holders) ? queue.holders.length : 0;
  const waiting = Array.isArray(queue?.waiting) ? queue.waiting.length : 0;
  const notes = queue
    ? [
        ...(typeof queue.slots_free === "number" && typeof queue.max_holders === "number"
          ? [`${queue.slots_free} of ${queue.max_holders} ${queue.max_holders === 1 ? "slot" : "slots"} free.`]
          : []),
        ...(Array.isArray(queue.notes) ? queue.notes.filter((n): n is string => typeof n === "string") : []),
      ]
    : [];
  return (
    <SectionList
      label={labelOf({ name: "section", section: "queue", q: "" })}
      count={queue ? (holders + waiting ? `${holders} landing, ${waiting} waiting` : "None open") : null}
      items={items}
      pending={query.isPending}
      error={query.error}
      retry={() => void query.refetch()}
      notes={notes}
    />
  );
}

const isSection = (name: string): name is Section => (SECTIONS as readonly string[]).includes(name);

/** The page of the route `section`: one section on the shared list. */
export function SectionPage({ section, q }: { section: string; q: string }) {
  if (section === "queue") return <LandQueueSection />;
  if (section === "lessons") return <LessonsAndIncidents q={q} />;
  const one = isSection(section) ? SINGLE[section] : undefined;
  if (isSection(section) && one) return <SingleSection key={section} section={section} q={q} {...one} />;
  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <h1 className={styles.heading}>No such section</h1>
      </div>
      <p className={styles.line}>There is no section named "{section}". Pick a section from the menu.</p>
    </div>
  );
}
