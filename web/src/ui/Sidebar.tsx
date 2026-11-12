// The sidebar (spec, "Layout"): the mark, the name and the tagline; the 12
// entries; the projects with the age of each last check-in; the health lines.
import { POLL_MS, type Mode } from "../api/events";
import { useQueue, useRoster, useSection, useThreads, useVitals } from "../api/queries";
import type { SectionName, Thread, Vitals } from "../api/types";
import { ago } from "../logic/ago";
import { href, type Route, type Section } from "../logic/route";
import markUrl from "./mark.svg";
import { colourStyle, nameOf } from "./Bars";
import styles from "./Sidebar.module.css";
import { useNow } from "./useNow";

export const TAGLINE = "Where your agent sessions talk, agree, and ask you.";

/** Disk under this many GB shows in amber. */
export const DISK_LOW_GB = 60;

interface Entry {
  label: string;
  route: Route;
}

const section = (name: Section, label: string): Entry => ({ label, route: { name: "section", section: name, q: "" } });

/** Needs you and Conversations, then the three groups of the spec. */
export const TOP: Entry[] = [
  { label: "Needs you", route: { name: "needs" } },
  { label: "Conversations", route: { name: "conversations", sort: "needs", open: [] } },
];
export const GROUPS: { label: string; entries: Entry[] }[] = [
  {
    label: "Work",
    entries: [section("queue", "Land queue"), section("progress", "Progress"), section("reviews", "Reviews"), section("schedules", "Schedules")],
  },
  {
    label: "Record",
    entries: [
      section("decisions", "Decisions"),
      section("agreements", "Agreements"),
      section("lessons", "Lessons and incidents"),
      section("claims", "File claims"),
      section("adoption", "Adoption"),
    ],
  },
  { label: "People", entries: [{ label: "Roster", route: { name: "people" } }] },
];

/** The nav label of a route: the heading of its page. */
export function labelOf(route: Route): string {
  for (const entry of [...TOP, ...GROUPS.flatMap((g) => g.entries)]) if (isCurrent(entry.route, route)) return entry.label;
  return "Conversations";
}

function isCurrent(entry: Route, route: Route): boolean {
  if (entry.name !== route.name) return false;
  return entry.name !== "section" || (route.name === "section" && entry.section === route.section);
}

/** The sections whose open rows the sidebar counts, and the server section each reads. */
const OPEN_COUNTS: Partial<Record<Section, SectionName>> = {
  reviews: "reviews",
  agreements: "agreements",
  lessons: "incidents",
  claims: "locks",
};

function useOpenCount(name: SectionName): number {
  return useSection(name).data?.pages[0]?.open ?? 0;
}

interface Count {
  text: string;
  /** What a screen reader hears when it differs from `text`. */
  spoken?: string;
  needs?: boolean;
}

/** The counts of open or new items; an entry with none is left out. */
function useCounts(): Map<string, Count> {
  // The first page of the needs sort: threads that need the owner come first.
  const first = useThreads("needs").data?.pages[0];
  const threads: Thread[] = first?.threads ?? [];
  const queue = useQueue().data;
  const open: Record<string, number> = {
    reviews: useOpenCount("reviews"),
    agreements: useOpenCount("agreements"),
    incidents: useOpenCount("incidents"),
    locks: useOpenCount("locks"),
  };
  const counts = new Map<string, Count>();
  // Open waiting rows need the owner too (Needs you lists them first).
  const todo = useOpenCount("waiting");
  const needs = threads.filter((t) => t.needs_owner).length;
  // A next page whose first page needs the owner throughout may hold more.
  const more = first?.next != null && needs === threads.length;
  const waiting = queue ? queue.holders.length + queue.waiting.length : 0;
  const total = needs + todo;
  if (total) counts.set("Needs you", more ? { text: total + "+", spoken: total + " or more", needs: true } : { text: String(total), needs: true });
  // Conversations shows no count: an unread total needs a server route that gives it.
  if (waiting) counts.set("Land queue", { text: String(waiting) });
  for (const g of GROUPS) {
    for (const entry of g.entries) {
      if (entry.route.name !== "section") continue;
      const server = OPEN_COUNTS[entry.route.section];
      const n = server ? (open[server] ?? 0) : 0;
      if (n) counts.set(entry.label, { text: n + " open" });
    }
  }
  return counts;
}

function NavList({ entries, route, counts, onNavigate }: { entries: Entry[]; route: Route; counts: Map<string, Count>; onNavigate?: () => void }) {
  return (
    <ul className={styles.entries}>
      {entries.map((entry) => {
        const count = counts.get(entry.label);
        return (
          <li key={entry.label}>
            <a
              className={styles.entry}
              href={href(entry.route)}
              aria-current={isCurrent(entry.route, route) ? "page" : undefined}
              aria-label={count?.spoken ? entry.label + " " + count.spoken : undefined}
              onClick={onNavigate}
            >
              <span>{entry.label}</span>
              {count ? (
                <span data-count className={count.needs ? styles.needsCount : styles.count}>
                  {count.text}
                </span>
              ) : null}
            </a>
          </li>
        );
      })}
    </ul>
  );
}

function Projects() {
  const people = useRoster().data;
  const now = useNow();
  const roster = people?.roster ?? {};
  const projects = Object.keys(roster)
    .filter((id) => roster[id]?.kind === "project")
    .sort((a, b) => nameOf(a, roster).localeCompare(nameOf(b, roster)));
  if (projects.length === 0) return null;
  return (
    <section className={styles.block} aria-labelledby="sidebar-projects">
      <h2 id="sidebar-projects" className={styles.groupHead}>
        Projects
      </h2>
      <ul className={styles.projects} aria-label="Projects">
        {projects.map((id) => {
          const t = people?.checkIns[id];
          return (
            <li key={id} className={styles.project}>
              <i className={styles.projectBar} style={colourStyle(roster[id])} aria-hidden="true" />
              <span className={styles.projectName}>{nameOf(id, roster)}</span>
              {t ? <span className={styles.muted}>{ago(t, now)}</span> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

interface Line {
  label: string;
  value: string;
  /** Amber, and a word that says so. */
  warn?: boolean;
  /** No value: muted text. */
  unknown?: boolean;
}

/** The four health lines; a value the server gives as null is "not known", never 0. */
export function healthLines(vitals: Vitals | undefined, pending: boolean, mode: Mode | null): Line[] {
  const feed: Line = {
    label: "Feed",
    value: mode === "push" ? "Live" : mode === "poll" ? `Polling every ${POLL_MS / 1000} s` : "Connecting",
  };
  if (!vitals) {
    const value = pending ? "loading" : "not known";
    return [feed, ...["Disk", "Weekly usage", "Build host"].map((label) => ({ label, value, unknown: true }))];
  }
  const low = vitals.disk_free_gb < DISK_LOW_GB;
  const disk: Line = { label: "Disk", value: `${vitals.disk_free_gb} GB free${low ? ", low" : ""}`, warn: low };
  let usage: Line = { label: "Weekly usage", value: "not known", unknown: true };
  if (vitals.usage !== null) {
    const limit = vitals.usage_limit;
    const at = limit !== null && vitals.usage >= limit;
    usage = {
      label: "Weekly usage",
      value: (limit === null ? String(vitals.usage) : `${vitals.usage} of ${limit}`) + (at ? ", at the limit" : ""),
      warn: at,
    };
  }
  const host = vitals.build_host;
  const build: Line = host
    ? { label: "Build host", value: host.reachable ? "Reachable" : "Not reachable" }
    : { label: "Build host", value: "not known", unknown: true };
  return [feed, disk, usage, build];
}

function Health({ mode }: { mode: Mode | null }) {
  const vitals = useVitals();
  return (
    <dl className={styles.health} aria-label="Health">
      {healthLines(vitals.data, vitals.isPending, mode).map((line) => (
        <div key={line.label} className={styles.healthLine}>
          <dt>{line.label}</dt>
          <dd
            data-warn={line.warn || undefined}
            className={line.warn ? styles.warn : line.unknown ? styles.muted : undefined}
          >
            {line.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export interface SidebarProps {
  route: Route;
  mode: Mode | null;
  /** Called when an entry is followed: the menu closes under 900 px. */
  onNavigate?: () => void;
}

export function Sidebar({ route, mode, onNavigate }: SidebarProps) {
  const counts = useCounts();
  return (
    <>
      <div className={styles.brand}>
        <img className={styles.mark} src={markUrl} alt="" />
        <div>
          <p className={styles.name}>Crosstalk</p>
          <p className={styles.tagline}>{TAGLINE}</p>
        </div>
      </div>
      <nav aria-label="Sections" className={styles.nav}>
        <NavList entries={TOP} route={route} counts={counts} onNavigate={onNavigate} />
        {GROUPS.map((g) => (
          <div key={g.label} role="group" aria-label={g.label} className={styles.block}>
            <h2 className={styles.groupHead} aria-hidden="true">
              {g.label}
            </h2>
            <NavList entries={g.entries} route={route} counts={counts} onNavigate={onNavigate} />
          </div>
        ))}
      </nav>
      <Projects />
      <Health mode={mode} />
    </>
  );
}
