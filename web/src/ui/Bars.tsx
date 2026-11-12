// The colour bars: each project is a line in its own colour, and a row shows
// the lines that crossed in it (spec, "Visual language").
import type { CSSProperties } from "react";
import { useRoster } from "../api/queries";
import type { RosterEntry } from "../api/types";
import styles from "./Bars.module.css";

export const MAX_BARS = 5;

// Roster fields are untrusted: only a hex colour or a plain colour name reaches a style.
const okColour = (c: unknown): c is string => typeof c === "string" && /^(#[0-9a-f]{3,8}|[a-z]+)$/i.test(c);

/** The custom properties a bar reads: `--c` in the light theme, `--c-dark` in the dark one. */
export function colourStyle(entry: RosterEntry | undefined): CSSProperties {
  const light = okColour(entry?.color) ? entry.color : "var(--muted)";
  const dark = okColour(entry?.colorDark) ? entry.colorDark : light;
  return { "--c": light, "--c-dark": dark } as CSSProperties;
}

/** The name the page shows for a participant: its roster name, else its id. */
export function nameOf(id: string, roster: Record<string, RosterEntry> | undefined): string {
  const name = roster && Object.hasOwn(roster, id) ? roster[id]?.name : undefined;
  return typeof name === "string" && name ? name : id;
}

export function Bars({ projects }: { projects: string[] }) {
  const roster = useRoster().data?.roster;
  if (projects.length === 0) return <span className={styles.bars} aria-hidden="true" />;
  return (
    <span className={styles.bars} role="img" aria-label={projects.map((id) => nameOf(id, roster)).join(", ")}>
      {projects.slice(0, MAX_BARS).map((id) => (
        <i key={id} className={styles.bar} style={colourStyle(roster && Object.hasOwn(roster, id) ? roster[id] : undefined)} />
      ))}
    </span>
  );
}
