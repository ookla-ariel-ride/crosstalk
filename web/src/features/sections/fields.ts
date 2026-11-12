// What every mapper shares: the row it returns, and readers that never throw
// on a field that is missing or of the wrong type.
import { ago } from "../../logic/ago";
import type { MarksView } from "../../api/types";
import { type Block, parseBody } from "../../logic/parseBody";

/** One row of a section, ready for `Row`; `detail` shows through `Body` when the row opens. */
export interface RowView {
  projects: string[];
  title: string;
  text: string;
  /** Short words for the note on the right; the first one names the state where the section has one. */
  note: string[];
  open: boolean;
  detail: Block[];
  /** The marks of a decision or a lesson (marks.view), shown by MarksLine when the row opens. */
  marks?: MarksView;
}

/** A doc of a section: its fields depend on the collection. */
export type Doc = { id?: unknown; [field: string]: unknown };

/** A string field, a finite number as text, else "". */
export function str(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

/** The first field of `keys` that holds text. */
export function first(doc: Doc, ...keys: string[]): string {
  for (const key of keys) {
    const value = str(doc[key]);
    if (value.trim()) return value;
  }
  return "";
}

export const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

/** A count that is a finite number of 0 or more, else null. */
export function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** The project ids for the colour bars, each once; "all" and "both" name no one project. */
export function projects(...ids: unknown[]): string[] {
  const out: string[] = [];
  for (const id of ids) {
    if (typeof id !== "string" || !id || id === "all" || id === "both" || out.includes(id)) continue;
    out.push(id);
  }
  return out;
}

/** A time as "2026-11-11 10:00 UTC"; text that is no time stays as written. */
export function stamp(value: unknown): string {
  const text = str(value);
  const ms = Date.parse(text);
  if (!text || Number.isNaN(ms)) return text;
  return new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

/** How long ago, "" for no time. */
export const age = (value: unknown, now: Date): string => ago(str(value), now);

/** A doc's `refs`: one string, or a list of strings. */
export function refs(value: unknown): string {
  if (typeof value === "string") return value;
  return list(value).map(str).filter(Boolean).join(", ");
}

/** The body as blocks. */
export const body = (text: string): Block[] => parseBody(text);

/** A short labelled line, as blocks; none when the value is empty. */
export function line(label: string, value: string): Block[] {
  return value.trim() ? parseBody(label + ": " + value) : [];
}

/** The parts that are not empty, in order. */
export const words = (...parts: string[]): string[] => parts.filter((p) => p !== "");

/** The `marks` key that normalize.ts kept on a row (marks.view); MarksLine checks its shape. */
export function marksOf(doc: Doc): MarksView | undefined {
  return typeof doc.marks === "object" && doc.marks !== null ? (doc.marks as MarksView) : undefined;
}
