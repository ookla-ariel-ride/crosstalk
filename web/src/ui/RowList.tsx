// The one list of the page: it draws only the rows in view (spec, "The shared
// list"), measures each drawn row, and keeps the row that holds the keyboard
// focus drawn while it is out of view, so the focus is never lost.
import { defaultRangeExtractor, type Range, useVirtualizer } from "@tanstack/react-virtual";
import { type FocusEvent, type ReactNode, useCallback, useMemo, useRef, useState } from "react";
import styles from "./RowList.module.css";

export interface RowListProps<T> {
  items: T[];
  renderRow: (item: T, index: number) => ReactNode;
  /** A row's height before it is measured, in px. */
  estimateSize: number | ((index: number) => number);
  /** A stable key for each item: a row keeps its node, its measure and its focus when the list changes. */
  itemKey: (item: T, index: number) => string;
  /** The list's name for a screen reader. */
  label: string;
  /**
   * A short list (the right column): it draws every row in the flow of the
   * page, with the same markup, instead of a box that scrolls on its own.
   */
  short?: boolean;
}

export function RowList<T>({ items, renderRow, estimateSize, itemKey, label, short = false }: RowListProps<T>) {
  const scroller = useRef<HTMLDivElement>(null);
  // The key of the item that holds the focus, not its index: an insert or a
  // reorder moves the item, and the row must stay drawn where it now is.
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const focused = useMemo(() => {
    if (focusedKey === null) return null;
    const index = items.findIndex((item, i) => itemKey(item, i) === focusedKey);
    return index === -1 ? null : index;
  }, [items, itemKey, focusedKey]);

  const virtualizer = useVirtualizer({
    count: short ? 0 : items.length,
    getScrollElement: () => scroller.current,
    estimateSize: typeof estimateSize === "number" ? () => estimateSize : estimateSize,
    getItemKey: (index) => {
      const item = items[index];
      return item !== undefined ? itemKey(item, index) : index;
    },
    rangeExtractor: useCallback(
      (range: Range) => {
        const shown = defaultRangeExtractor(range);
        if (focused === null || focused >= range.count || shown.includes(focused)) return shown;
        return [...shown, focused].sort((a, b) => a - b);
      },
      [focused],
    ),
  });

  const onFocus = (e: FocusEvent<HTMLDivElement>) => {
    const index = Number((e.target as HTMLElement).closest("[data-index]")?.getAttribute("data-index"));
    const item = Number.isInteger(index) ? items[index] : undefined;
    setFocusedKey(item !== undefined ? itemKey(item, index) : null);
  };
  const onBlur = (e: FocusEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocusedKey(null);
  };

  if (short) {
    return (
      <div role="list" aria-label={label}>
        {items.map((item, index) => (
          <div key={itemKey(item, index)} role="listitem">
            {renderRow(item, index)}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div ref={scroller} className={styles.scroller} onFocus={onFocus} onBlur={onBlur}>
      <div role="list" aria-label={label} className={styles.inner} style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((row) => {
          const item = items[row.index];
          if (item === undefined) return null;
          return (
            <div
              key={row.key}
              role="listitem"
              aria-setsize={items.length}
              aria-posinset={row.index + 1}
              data-index={row.index}
              ref={virtualizer.measureElement}
              className={styles.item}
              style={{ transform: `translateY(${row.start}px)` }}
            >
              {renderRow(item, row.index)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
