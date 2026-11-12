// Progress reports (index.html, renderProgress and progRow): the item, how
// much is built and verified of how many rows, and when. A report is never open.
import { age, count, type Doc, first, line, projects, type RowView, stamp, str, words } from "./fields";

export function progressRow(doc: Doc, now: Date): RowView {
  // A progress row names what it measures in `item` (older rows: `game`).
  const item = first(doc, "item", "game");
  const pct = count(doc.pct);
  const verified = count(doc.verified);
  const total = count(doc.rows) || count(doc.applicable) || null;
  const when = first(doc, "t", "updated", "date");
  const verifiedText = verified === null ? "" : verified + (total ? " of " + total : "") + " verified";
  return {
    projects: projects(doc.project),
    title: item || str(doc.id),
    text: words(pct === null ? "" : pct + "% built", verifiedText).join(", "),
    note: words(age(when, now)),
    open: false,
    detail: [
      ...line("Project", str(doc.project)),
      ...line("Built", pct === null ? "" : pct + "%"),
      ...line("Verified", verified === null ? "" : String(verified)),
      ...line("Rows", total === null ? "" : String(total)),
      ...line("Note", str(doc.note)),
      ...line("Reported", stamp(when)),
    ],
  };
}
