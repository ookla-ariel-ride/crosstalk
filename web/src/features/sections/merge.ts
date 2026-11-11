// "Lessons and incidents" shows two collections as one list: the one place
// where the page orders rows itself. Each collection comes from the server a
// page at a time, open rows first, then the newest change first; this merges
// the pages loaded so far, and says which collection must load its next page
// before more rows can be placed.
import { type Doc, str } from "./fields";

export interface Stream {
  rows: Doc[];
  /** The server has no next page. */
  done: boolean;
}

export interface Placed<K extends string> {
  source: K;
  doc: Doc;
}

export interface Merged<K extends string> {
  rows: Placed<K>[];
  /** The collections whose next page is needed to place the next row. */
  need: K[];
}

const TIME_FIELDS = ["t", "updated", "start", "end", "date", "at"];

/** The newest time a doc names: the closest the page can see to its last change. */
export function changeTime(doc: Doc): number {
  let newest = -Infinity;
  for (const key of TIME_FIELDS) {
    const ms = Date.parse(str(doc[key]));
    if (!Number.isNaN(ms) && ms > newest) newest = ms;
  }
  return newest;
}

/** True when `a` goes before `b`: open first, then the newer change, then the higher id. */
function before(a: { open: boolean; time: number; id: string }, b: { open: boolean; time: number; id: string }): boolean {
  if (a.open !== b.open) return a.open;
  if (a.time !== b.time) return a.time > b.time;
  return a.id > b.id;
}

/**
 * Merge the loaded rows of each stream. A row is placed only while every
 * stream either has a row left to compare or has ended, so a later page never
 * holds a row that belongs above the ones shown. A (source, id) pair shows
 * once, even when a page boundary moved and a row came back.
 */
export function mergeByTime<K extends string>(streams: Record<K, Stream>, isOpen: (source: K, doc: Doc) => boolean): Merged<K> {
  const sources = Object.keys(streams) as K[];
  const at = new Map<K, number>(sources.map((s) => [s, 0]));
  const seen = new Set<string>();
  const rows: Placed<K>[] = [];
  for (;;) {
    let best: { source: K; doc: Doc; open: boolean; time: number; id: string } | null = null;
    const need: K[] = [];
    for (const source of sources) {
      const stream = streams[source];
      const doc = stream.rows[at.get(source) as number];
      if (doc === undefined) {
        if (!stream.done) need.push(source);
        continue;
      }
      const head = { source, doc, open: isOpen(source, doc), time: changeTime(doc), id: str(doc.id) };
      if (best === null || before(head, best)) best = head;
    }
    if (need.length || best === null) return { rows, need };
    at.set(best.source, (at.get(best.source) as number) + 1);
    const key = best.source + "\u0000" + best.id;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ source: best.source, doc: best.doc });
  }
}
