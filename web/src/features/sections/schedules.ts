// Scheduled jobs (index.html, renderSchedules): the project, the job and its
// cron, the next run, the state, the last runs and the last report. Open until
// removed; an open job's note says scheduled, late, stale or unknown.
import type { Block } from "../../logic/parseBody";
import { age, type Doc, first, line, list, projects, type RowView, stamp, str, words } from "./fields";

// The same rule as status() in scripts/crosstalk/schedules.py and schedState in index.html.
const GRACE_MIN = 5;
const STALE_MIN = 120;
const STALE_NO_PERIOD_MIN = 1440;
const MIN = 60_000;

export function scheduleState(doc: Doc, now: Date): string {
  const next = Date.parse(str(doc.next));
  const reported = Date.parse(str(doc.reported));
  const period = typeof doc.periodMin === "number" && Number.isFinite(doc.periodMin) ? doc.periodMin : 0;
  const window = period ? Math.max(STALE_MIN, 3 * period) : STALE_NO_PERIOD_MIN;
  const ms = now.getTime();
  if ((Number.isNaN(next) || next < ms) && (Number.isNaN(reported) || ms - reported > window * MIN)) return "stale";
  if (Number.isNaN(next)) return "unknown";
  return ms - next > Math.max(GRACE_MIN, period / 2) * MIN ? "late" : "scheduled";
}

export function scheduleRow(doc: Doc, now: Date): RowView {
  const runs = list(doc.runs)
    .filter((r): r is Doc => r !== null && typeof r === "object")
    .slice(-10)
    .reverse();
  const last = runs[0];
  const runBlock: Block[] = runs.length
    ? [{ type: "ul", items: runs.map((r) => [{ type: "text", text: words(stamp(r.at), str(r.result), str(r.note)).join(" ") }]) }]
    : [];
  const cron = str(doc.cron);
  return {
    projects: projects(doc.from),
    title: first(doc, "name") || str(doc.id),
    text: first(doc, "about") || cron,
    note: words(doc.removed ? "removed" : scheduleState(doc, now), age(doc.reported, now)),
    open: !doc.removed,
    detail: [
      ...line("About", str(doc.about)),
      ...line("Cron", cron && cron + " (local time)"),
      ...line("Next run", stamp(doc.next)),
      ...line("Last run", last ? words(str(last.result), stamp(last.at)).join(", ") : "no runs reported"),
      ...runBlock,
      ...line("Last report", stamp(doc.reported)),
      ...line("Removed", stamp(doc.removed)),
    ],
  };
}
