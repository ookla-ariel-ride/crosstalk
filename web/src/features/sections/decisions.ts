// Decisions (index.html, renderDecisions): the decision, by whom, for which
// project, where it was said and when. A decision is never open.
import { age, body, type Doc, first, line, marksOf, projects, refs, type RowView, stamp, str, words } from "./fields";

export function decisionRow(doc: Doc, now: Date): RowView {
  // Older rows give the day as `date` instead of `t`.
  const when = first(doc, "t", "date");
  const by = str(doc.by);
  const project = str(doc.project);
  const forWhom = project === "all" || project === "both" ? "all projects" : project;
  const source = str(doc.source);
  return {
    projects: projects(by),
    title: first(doc, "summary", "title") || str(doc.id),
    text: words(forWhom && "for " + forWhom, source && "said in " + source).join(", "),
    note: words(age(when, now)),
    open: false,
    detail: [
      ...body(str(doc.body)),
      ...line("By", by),
      ...line("For", forWhom),
      ...line("Where said", source),
      ...line("Refs", refs(doc.refs)),
      ...line("When", stamp(when)),
    ],
    marks: marksOf(doc),
  };
}
