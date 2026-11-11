// File claims, the `locks` collection (index.html, renderLocks): the file and
// its repository, who holds it, why, since when, and whether it is released.
// Open while held.
import { age, body, type Doc, first, line, projects, type RowView, stamp, str, words } from "./fields";

export function lockRow(doc: Doc, now: Date): RowView {
  const project = str(doc.project);
  const holder = str(doc.holder);
  const released = str(doc.released);
  return {
    projects: projects(project),
    title: first(doc, "path", "summary") || str(doc.id),
    text: str(doc.note),
    note: words(doc.released ? "released" : "held", age(doc.since, now)),
    open: !doc.released,
    detail: [
      ...body(str(doc.body)),
      ...line("Repository", str(doc.repo)),
      ...line("Held by", words(project, holder && "(" + holder + ")").join(" ")),
      ...line("Why", str(doc.note)),
      ...line("Since", stamp(doc.since)),
      ...line("Released", stamp(released)),
    ],
  };
}
