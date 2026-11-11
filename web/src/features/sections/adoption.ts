// Adoption records, the `adoptions` collection (index.html, renderAdoption,
// the state cells): the project, the standard, its state and the commit or
// note. A record is never open.
import { age, type Doc, first, line, projects, type RowView, stamp, str, words } from "./fields";

export function adoptionRow(doc: Doc, now: Date): RowView {
  const from = str(doc.from);
  const state = str(doc.state);
  const when = first(doc, "at", "t");
  return {
    projects: projects(from),
    title: str(doc.item) || str(doc.id),
    text: words(from, first(doc, "ref", "note")).join(", "),
    note: words(state || "no state", age(when, now)),
    open: false,
    detail: [
      ...line("Project", from),
      ...line("Standard", str(doc.item)),
      ...line("State", state),
      ...line("Commit", str(doc.ref)),
      ...line("Note", str(doc.note)),
      ...line("Recorded", stamp(when)),
    ],
  };
}
