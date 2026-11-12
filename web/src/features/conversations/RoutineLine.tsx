// A run of routine posts as one line with its counts; a click, or Enter, shows
// its threads in place. The same fold holds the muted threads.
import { memo, type ReactNode, useState } from "react";
import type { Thread } from "../../api/types";
import { ago } from "../../logic/ago";
import type { RoutineKind } from "../../logic/foldRoutine";
import { Row } from "../../ui/Row";
import { RowList } from "../../ui/RowList";

const WORDS: Record<RoutineKind, [string, string]> = {
  "check-in": ["check-in", "check-ins"],
  sync: ["sync", "syncs"],
  "LAND START": ["land start", "land starts"],
  "LAND END": ["land end", "land ends"],
  GRANTED: ["granted", "granted"],
  "TURN ASK": ["turn ask", "turn asks"],
  other: ["other", "others"],
};

/** "5 check-ins, 2 land starts, 1 granted". */
export function countsText(counts: Partial<Record<RoutineKind, number>>): string {
  return (Object.keys(WORDS) as RoutineKind[])
    .filter((k) => (counts[k] ?? 0) > 0)
    .map((k) => {
      const n = counts[k] ?? 0;
      return `${n} ${WORDS[k][n === 1 ? 0 : 1]}`;
    })
    .join(", ");
}

const newestT = (threads: Thread[]): string =>
  threads.reduce((top, t) => (t.last_t > top ? t.last_t : top), "");

export interface FoldLineProps {
  title: string;
  text: string;
  threads: Thread[];
  now: Date;
  label: string;
  renderThread: (thread: Thread) => ReactNode;
  /** The roots of these threads that are open, joined, so the line draws again only when they change. */
  openKey: string;
}

/** One line that folds a set of threads. */
export const FoldLine = memo(function FoldLine({ title, text, threads, now, label, renderThread }: FoldLineProps) {
  const [shown, setShown] = useState(false);
  const projects = [...new Set(threads.flatMap((t) => t.projects))];
  return (
    <Row projects={projects} title={title} text={text} note={ago(newestT(threads), now)} open={shown} onOpen={() => setShown(!shown)}>
      {shown ? <RowList short label={label} items={threads} itemKey={(t) => t.root} estimateSize={56} renderRow={renderThread} /> : null}
    </Row>
  );
});

export interface RoutineLineProps extends Omit<FoldLineProps, "title" | "text" | "label"> {
  counts: Partial<Record<RoutineKind, number>>;
}

export function RoutineLine({ counts, ...rest }: RoutineLineProps) {
  return <FoldLine title="Routine" text={countsText(counts)} label="Routine posts" {...rest} />;
}
