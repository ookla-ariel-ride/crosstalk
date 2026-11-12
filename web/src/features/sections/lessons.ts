// Lessons (index.html, renderLessons): what happened, what prevents it now,
// from whom and when. A lesson is never open.
import { age, body, type Doc, first, line, marksOf, projects, refs, type RowView, stamp, str, words } from "./fields";

export function lessonRow(doc: Doc, now: Date): RowView {
  const from = first(doc, "project", "from");
  const fix = str(doc.fix);
  return {
    projects: projects(from),
    title: first(doc, "summary", "title") || str(doc.id),
    text: fix,
    note: words("lesson", age(doc.t, now)),
    open: false,
    detail: [
      ...body(first(doc, "body", "detail")),
      ...line("What prevents it now", fix),
      ...line("From", from),
      ...line("Refs", refs(doc.refs)),
      ...line("When", stamp(doc.t)),
    ],
    marks: marksOf(doc),
  };
}
