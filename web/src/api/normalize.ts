// The one place where rows from the API enter the page. Many agents write the
// log, so any field of a row can hold any JSON type. Each field the views read
// gets the type they expect here, as the present page does with String(text):
// text stays text, a number or a boolean becomes its text, an object or a list
// becomes its JSON, and null drops the field. The views then need no guards.
import type { Message, RowRef, SectionPage, SectionRow, Thread, ThreadDetail, ThreadsPage } from "./types";

type Raw = Record<string, unknown>;

const isObject = (v: unknown): v is Raw => typeof v === "object" && v !== null && !Array.isArray(v);

/** A field as text, or undefined for null and a missing field. */
export function text(v: unknown): string | undefined {
  if (v === null || v === undefined) return undefined;
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v) ?? String(v);
  } catch {
    return String(v);
  }
}

/** A list of text: a list keeps its text items (others as text), one value becomes a list of one. */
function texts(v: unknown): string[] | undefined {
  if (v === null || v === undefined) return undefined;
  const items = Array.isArray(v) ? v : [v];
  return items.map(text).filter((t): t is string => t !== undefined);
}

/** Set `key` of `out` to `value`, or drop it when the value is undefined. */
function put(out: Raw, key: string, value: unknown): void {
  if (value === undefined) delete out[key];
  else out[key] = value;
}

/** The message fields that hold text (types.MessageDoc). */
const MESSAGE_TEXT = ["t", "from", "kind", "summary", "body", "replyTo", "harness", "model", "source", "project", "status"];

/** The fields every row of the log may hold that the views read as text, or as a list for `to`. */
const ROW_TEXT = ["t", "from", "kind", "summary", "body", "replyTo"];

function message(raw: unknown): Message | null {
  if (!isObject(raw)) return null;
  const out: Raw = { ...raw, id: text(raw.id) ?? "" };
  for (const key of MESSAGE_TEXT) put(out, key, text(raw[key]));
  put(out, "to", texts(raw.to));
  put(out, "options", Array.isArray(raw.options) ? texts(raw.options) : undefined);
  put(out, "refs", typeof raw.refs === "string" ? raw.refs : Array.isArray(raw.refs) ? texts(raw.refs) : undefined);
  put(out, "recommend", typeof raw.recommend === "number" && Number.isInteger(raw.recommend) ? raw.recommend : undefined);
  put(out, "important", typeof raw.important === "boolean" ? raw.important : undefined);
  put(out, "positions", isObject(raw.positions) ? raw.positions : undefined);
  put(out, "resolution", isObject(raw.resolution) ? raw.resolution : undefined);
  put(out, "acked_by", Array.isArray(raw.acked_by) ? texts(raw.acked_by) : undefined);
  const depth = Number(raw.depth);
  out.depth = depth === 1 || depth === 2 ? depth : 0;
  out.quote = text(raw.quote) ?? null;
  return out as unknown as Message;
}

export function threadDetail(raw: unknown): ThreadDetail {
  const r = isObject(raw) ? raw : {};
  const messages = (Array.isArray(r.messages) ? r.messages : []).map(message).filter((m): m is Message => m !== null);
  const out: ThreadDetail = { root: text(r.root) ?? "", messages };
  if (isObject(r.thread)) out.thread = thread(r.thread);
  return out;
}

function thread(raw: Raw): Thread {
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
  const out: Thread = {
    root: text(raw.root) ?? "",
    title: text(raw.title) ?? "",
    preview: text(raw.preview) ?? "",
    projects: texts(raw.projects) ?? [],
    count: num(raw.count),
    last_t: text(raw.last_t) ?? "",
    last_id: text(raw.last_id) ?? "",
    needs_owner: raw.needs_owner === true,
    unanswered: raw.unanswered === true,
    routine: raw.routine === true,
    hot: num(raw.hot),
    kind: text(raw.kind) ?? "",
  };
  if (typeof raw.unread === "number") out.unread = raw.unread;
  if (Array.isArray(raw.owner_requests)) out.owner_requests = texts(raw.owner_requests);
  return out;
}

export function threadsPage(raw: unknown): ThreadsPage {
  const r = isObject(raw) ? raw : {};
  return {
    threads: (Array.isArray(r.threads) ? r.threads : []).filter(isObject).map(thread),
    next: text(r.next) ?? null,
    server_time: text(r.server_time) ?? "",
  };
}

function sectionRow(raw: Raw): SectionRow {
  const out: Raw = { ...raw, id: text(raw.id) ?? "" };
  for (const key of ROW_TEXT) put(out, key, text(raw[key]));
  put(out, "to", texts(raw.to));
  return out as SectionRow;
}

export function sectionPage(raw: unknown): SectionPage {
  const r = isObject(raw) ? raw : {};
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
  return {
    rows: (Array.isArray(r.rows) ? r.rows : []).filter(isObject).map(sectionRow),
    next: text(r.next) ?? null,
    open: num(r.open),
    total: num(r.total),
  };
}

/** The `rows` of a `rows` event (GET /api/events); an element that is not an object is left out. */
export function rowRefs(raw: unknown): RowRef[] {
  const rows = isObject(raw) && Array.isArray(raw.rows) ? raw.rows : [];
  return rows.filter(isObject).map((r) => ({
    from: text(r.from) ?? "",
    seq: typeof r.seq === "number" ? r.seq : null,
    collection: text(r.collection) ?? null,
    id: text(r.id) ?? null,
  }));
}
