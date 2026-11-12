// The sort orders of the stream, as links: the server sorts, the route keeps the choice.
import { href, type Sort } from "../../logic/route";
import styles from "./Stream.module.css";

const LABELS: [Sort, string][] = [
  ["needs", "Needs you"],
  ["new", "New"],
  ["hot", "Hot"],
  ["unanswered", "Unanswered"],
];

/** `unread`: how many threads are unread; the tab New shows it, as "New (12)", and plain "New" at 0. */
export function SortBar({ sort, open, unread = 0 }: { sort: Sort; open: string[]; unread?: number }) {
  return (
    <nav className={styles.sorts} aria-label="Sort order">
      {LABELS.map(([value, label]) => (
        <a
          key={value}
          className={styles.sort}
          href={href({ name: "conversations", sort: value, open })}
          aria-current={value === sort ? "page" : undefined}
        >
          {value === "new" && unread > 0 ? `${label} (${unread})` : label}
        </a>
      ))}
    </nav>
  );
}
