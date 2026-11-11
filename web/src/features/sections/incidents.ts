// Incidents (index.html, renderIncidents): the incident, its project, when it
// started and ended, and the fix. Open until it has an end.
import { age, body, type Doc, first, line, projects, refs, type RowView, stamp, str, words } from "./fields";

export function incidentRow(doc: Doc, now: Date): RowView {
  const ended = str(doc.end);
  const fix = str(doc.fix);
  return {
    projects: projects(doc.project),
    title: first(doc, "summary", "title") || str(doc.id),
    text: fix,
    note: words("incident", ended ? "ended" : "open", age(doc.start, now)),
    open: !doc.end,
    detail: [
      ...body(str(doc.body)),
      ...line("Project", str(doc.project)),
      ...line("Started", stamp(doc.start)),
      ...line("Ended", stamp(ended)),
      ...line("Fix", fix),
      ...line("Fix refs", refs(doc.fixRefs)),
      ...line("Refs", refs(doc.refs)),
    ],
  };
}
