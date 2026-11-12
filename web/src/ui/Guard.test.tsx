// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Guard } from "./Guard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => vi.restoreAllMocks());

function Row({ row }: { row: { title: unknown } }) {
  if (typeof row.title !== "string") throw new Error("not text");
  return <p>{row.title}</p>;
}

test("a failed Guard shows its row again when the row's data changes", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const bad = { title: 7 };
  const { rerender } = render(
    <Guard what="Row alpha-1" data={bad}>
      <Row row={bad} />
    </Guard>,
  );
  expect(screen.getByRole("alert").textContent).toMatch(/Row alpha-1 could not be shown/);
  // The same data again: the Guard stays failed and does not loop.
  rerender(
    <Guard what="Row alpha-1" data={bad}>
      <Row row={bad} />
    </Guard>,
  );
  expect(screen.getByRole("alert")).toBeTruthy();
  const fixed = { title: "Fixed" };
  rerender(
    <Guard what="Row alpha-1" data={fixed}>
      <Row row={fixed} />
    </Guard>,
  );
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("Fixed")).toBeTruthy();
});
