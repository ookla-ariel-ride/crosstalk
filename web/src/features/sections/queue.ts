// The land queue (GET /api/queue, landqueue.py; the right column shows the same
// rows): the runs that hold the queue, then the asks that wait, in the
// server's order. Every entry is open: it leaves the queue when it ends.
import type { LandHolder, LandQueue, LandWaiting } from "../../api/types";
import { age, list, line, projects, type RowView, stamp, str, words } from "./fields";

export type QueueEntry = ({ role: "holder" } & LandHolder) | ({ role: "waiting"; place: number } & LandWaiting);

/** The holders, then the waiting asks; a missing list counts as empty. */
export function queueEntries(queue: LandQueue | undefined): QueueEntry[] {
  if (!queue) return [];
  return [
    ...list(queue.holders).map((h) => ({ role: "holder" as const, ...(h as LandHolder) })),
    ...list(queue.waiting).map((w, place) => ({ role: "waiting" as const, place, ...(w as LandWaiting) })),
  ];
}

const branchList = (value: unknown): string => list(value).map(str).filter(Boolean).join(", ");

export function queueRow(entry: QueueEntry, now: Date): RowView {
  const project = str(entry.project);
  const branches = branchList(entry.branches);
  if (entry.role === "holder") {
    return {
      projects: projects(project),
      title: words(project, branches).join(": "),
      text: words(str(entry.word), str(entry.since) && "since " + stamp(entry.since)).join(", "),
      note: words("landing", age(entry.since, now)),
      open: true,
      detail: [...line("Branches", branches), ...line("Started by", str(entry.id)), ...line("Answers the ask", str(entry.ask))],
    };
  }
  const state = entry.granted ? "granted" : entry.grant_lapsed ? "grant lapsed" : "waiting";
  return {
    projects: projects(project),
    title: (entry.place === 0 ? "Next: " : "Then: ") + words(project, branches).join(": "),
    text: entry.holding ? "already holds a run" : "",
    note: words(state, age(entry.t, now)),
    open: true,
    detail: [
      ...line("Branches", branches),
      ...line("Asked in", str(entry.id)),
      ...line("Asked again in", str(entry.updated)),
      ...line("Granted in", str(entry.granted)),
      ...line("Granted at", stamp(entry.granted_t)),
      ...line("Granted branches", branchList(entry.granted_branches)),
    ],
  };
}
