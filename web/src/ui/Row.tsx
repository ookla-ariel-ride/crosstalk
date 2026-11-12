// The one row shape of every list (spec, "The shared list"): the colour bars
// of the projects that spoke, a title, one line of text and a short note on
// the right. The whole row is one control. With `href` it is a link; else a
// button that calls `onOpen`, and `children` show below it while `open`.
import { type KeyboardEvent, type ReactNode, useId } from "react";
import { Bars } from "./Bars";
import styles from "./Row.module.css";

export interface RowProps {
  projects: string[];
  title: ReactNode;
  text?: ReactNode;
  note?: ReactNode;
  open?: boolean;
  onOpen?: () => void;
  href?: string;
  /** What the row shows in place while open. */
  children?: ReactNode;
  /** Controls under a folded row, outside its button (a button holds no other control). */
  actions?: ReactNode;
}

export function Row({ projects, title, text, note, open = false, onOpen, href, children, actions }: RowProps) {
  const contentId = useId();
  const inside = (
    <>
      <Bars projects={projects} />
      <span className={styles.main}>
        <span className={styles.title}>{title}</span>
        {text ? <span className={styles.text}>{text}</span> : null}
      </span>
      {note ? <span className={styles.note}>{note}</span> : null}
    </>
  );

  if (href !== undefined) {
    return (
      <div className={styles.row}>
        <a className={styles.control} href={href}>
          {inside}
        </a>
      </div>
    );
  }

  // A button opens on Enter and Space by itself. Enter is also handled here,
  // with its default stopped so no second click follows; a held key opens once.
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (e.repeat) return;
    onOpen?.();
  };
  const shown = open && children != null;
  return (
    <div className={styles.row} data-open={open || undefined}>
      <button
        type="button"
        className={styles.control}
        aria-expanded={open}
        aria-controls={shown ? contentId : undefined}
        onClick={() => onOpen?.()}
        onKeyDown={onKeyDown}
      >
        {inside}
      </button>
      {actions != null && !open ? <div className={styles.actions}>{actions}</div> : null}
      {shown ? (
        <div id={contentId} className={styles.content}>
          {children}
        </div>
      ) : null}
    </div>
  );
}
