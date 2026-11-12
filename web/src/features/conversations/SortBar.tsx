// The sort orders of the stream, as links: the server sorts, the route keeps the choice.
import { href, type Sort } from "../../logic/route";
import styles from "./Stream.module.css";

const LABELS: [Sort, string][] = [
  ["needs", "Needs you"],
  ["new", "New"],
  ["hot", "Hot"],
  ["unanswered", "Unanswered"],
];

export function SortBar({ sort, open }: { sort: Sort; open: string[] }) {
  return (
    <nav className={styles.sorts} aria-label="Sort order">
      {LABELS.map(([value, label]) => (
        <a
          key={value}
          className={styles.sort}
          href={href({ name: "conversations", sort: value, open })}
          aria-current={value === sort ? "page" : undefined}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}
