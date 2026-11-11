// @vitest-environment jsdom
import { act, fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { RowList } from "./RowList";
import { renderWith } from "./testing";

const ITEMS = Array.from({ length: 10_000 }, (_, i) => ({ id: "alpha-" + i, title: "Row " + i }));

// jsdom has no layout: give the scroller a 600 px view and each row 56 px.
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(780);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(56);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { x: 0, y: 0, top: 0, left: 0, right: 780, bottom: 56, width: 780, height: 56, toJSON: () => ({}) },
  );
  return () => vi.restoreAllMocks();
});

function list() {
  return (
    <RowList
      label="Conversations"
      items={ITEMS}
      itemKey={(item) => item.id}
      estimateSize={56}
      renderRow={(item) => <button type="button">{item.title}</button>}
    />
  );
}

describe("RowList", () => {
  test("RowList draws fewer than 60 rows for 10,000 items", () => {
    renderWith(list());
    const rows = screen.getAllByRole("listitem");
    expect(rows.length).toBeGreaterThan(5);
    expect(rows.length).toBeLessThan(60);
    // A screen reader hears the place of each row in the whole list.
    expect(screen.getByRole("list", { name: "Conversations" })).toBeTruthy();
    expect(rows[0]?.getAttribute("aria-setsize")).toBe("10000");
    expect(rows[0]?.getAttribute("aria-posinset")).toBe("1");
  });

  test("the focused row stays drawn and focused when it scrolls out of view", async () => {
    renderWith(list());
    const first = screen.getByRole("button", { name: "Row 0" });
    first.focus();
    fireEvent.focus(first);
    const scroller = screen.getByRole("list", { name: "Conversations" }).parentElement as HTMLElement;
    await act(async () => {
      scroller.scrollTop = 40_000;
      fireEvent.scroll(scroller);
    });
    expect(screen.getAllByRole("button", { name: /^Row 7\d\d$/ }).length).toBeGreaterThan(0);
    expect(document.activeElement?.textContent).toBe("Row 0");
    expect(screen.getAllByRole("listitem").length).toBeLessThan(60);
  });

  test("a short list draws every row, with the same list semantics", () => {
    renderWith(
      <RowList short label="Land queue" items={ITEMS.slice(0, 3)} estimateSize={44} renderRow={(item) => <a href="#/queue">{item.title}</a>} />,
    );
    expect(screen.getByRole("list", { name: "Land queue" })).toBeTruthy();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Row 0", "Row 1", "Row 2"]);
  });
});
