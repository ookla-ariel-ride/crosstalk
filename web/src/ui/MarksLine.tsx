// The marks line of a post (marks.py, view): "✅ Alpha, Beta   ➖ Gamma   not yet: Delta",
// the same line the present page shows on a card. It reads only the `marks` key that
// the server puts on a marked post; an absent or malformed value shows nothing.
import type { MarksView, MarkWord, RosterEntry } from "../api/types";
import { nameOf } from "./Bars";
import styles from "./MarksLine.module.css";

/** The marks in the order the line shows them, with their emoji and word (marks.EMOJI, marks.LABEL). */
export const MARK_SHOWN: [MarkWord, string, string][] = [
  ["ack", "✅", "acknowledged"],
  ["applies", "🔧", "applies"],
  ["none", "➖", "no change"],
  ["seen", "👀", "seen"],
  ["unclear", "❓", "not clear"],
];

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

type Roster = Record<string, RosterEntry> | undefined;

/** An agent with a parent shows as "Parent › Name", as on the present page. */
function displayName(id: string, roster: Roster): string {
  const entry = roster && Object.hasOwn(roster, id) ? roster[id] : undefined;
  const parent = entry?.kind === "agent" && typeof entry.parent === "string" && entry.parent !== id ? entry.parent : null;
  return parent ? nameOf(parent, roster) + " › " + nameOf(id, roster) : nameOf(id, roster);
}

function byName(roster: Roster) {
  return (a: string, b: string) => nameOf(a, roster).localeCompare(nameOf(b, roster)) || (a < b ? -1 : a > b ? 1 : 0);
}

/** `roster` comes from the caller, which reads it once for every message it shows. */
export function MarksLine({ marks, roster }: { marks: MarksView | undefined; roster: Roster }) {
  if (!isObject(marks) || !isObject(marks.marks)) return null;
  const words = marks.marks as Record<string, unknown>;
  const order = byName(roster);
  const groups = MARK_SHOWN.map(([word, emoji, label]) => ({
    word,
    emoji,
    label,
    names: Object.keys(words)
      .filter((id) => id && words[id] === word)
      .sort(order),
  })).filter((g) => g.names.length > 0);
  const owed =
    marks.expects === true && Array.isArray(marks.owed) ? marks.owed.filter((id): id is string => typeof id === "string" && id !== "").sort(order) : [];
  if (groups.length === 0 && owed.length === 0) return null;
  return (
    <div className={styles.line} data-marks="">
      {groups.map((g) => (
        <span key={g.word} className={styles.group}>
          <span role="img" aria-label={g.label} title={g.label}>
            {g.emoji}
          </span>
          {" " + g.names.map((id) => displayName(id, roster)).join(", ")}
        </span>
      ))}
      {owed.length > 0 ? <span className={styles.group}>{"not yet: " + owed.map((id) => displayName(id, roster)).join(", ")}</span> : null}
    </div>
  );
}
