// Agreements (index.html, renderAgreements): the summary, status, who builds
// and who adopts, the commits and the last update. Open until landed-both or dropped.
import { age, body, type Doc, first, line, list, projects, refs, type RowView, stamp, str, words } from "./fields";

const STATUSES = ["proposed", "agreed", "building", "landed-builder", "ready-to-adopt", "landed-both", "held", "dropped"];

/** Whether the agreement is held: the `hold` flag, or the older status "held". */
export const isHeld = (doc: Doc): boolean => doc.hold === true || str(doc.status) === "held";

export function agreementRow(doc: Doc, now: Date): RowView {
  const status = str(doc.status);
  const held = isHeld(doc);
  // A long free-text status shows as "agreed", with the text in the detail, as the present tab does.
  const long = !held && !STATUSES.includes(status) && status.length > 20;
  const shown = held ? "held" : long ? "agreed" : status || "proposed";
  const builder = first(doc, "builder", "owner");
  const adopter = str(doc.adopter);
  const commits = doc.commits && typeof doc.commits === "object" && !Array.isArray(doc.commits) ? (doc.commits as Record<string, unknown>) : {};
  const when = first(doc, "updated", "t");
  return {
    projects: projects(builder, adopter),
    title: first(doc, "summary", "title") || str(doc.id),
    text: words(builder && "built by " + builder, adopter && "adopted by " + adopter).join(", "),
    note: words(shown, age(when, now)),
    open: status !== "landed-both" && status !== "dropped",
    detail: [
      ...body(first(doc, "body", "detail")),
      ...line("Status", long ? status : ""),
      ...line("Hold", held ? str(doc.holdNote) : ""),
      ...line("Builds", builder),
      ...line("Adopts", adopter),
      ...Object.keys(commits).flatMap((p) => line("Commits of " + p, list(commits[p]).map(str).filter(Boolean).join(", "))),
      ...line("Refs", refs(doc.refs)),
      ...line("Updated", stamp(when)),
    ],
  };
}
