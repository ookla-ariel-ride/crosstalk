// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { keys } from "../../api/queries";
import type { SectionName, SectionRow } from "../../api/types";
import { renderWith } from "../../ui/testing";
import { SectionPage } from "./SectionPage";

// jsdom has no layout: give the scroller a 600 px view and each row 56 px.
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(780);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(56);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { x: 0, y: 0, top: 0, left: 0, right: 780, bottom: 56, width: 780, height: 56, toJSON: () => ({}) },
  );
  window.location.hash = "";
  return () => vi.restoreAllMocks();
});
afterEach(() => vi.useRealTimers());

const page = (rows: SectionRow[], fields: { next?: string | null; open?: number; total?: number } = {}) => ({
  pages: [{ rows, next: fields.next ?? null, open: fields.open ?? 0, total: fields.total ?? rows.length }],
  pageParams: [""],
});
const section = (name: SectionName, data: unknown, q = ""): [readonly unknown[], unknown] => [keys.section(name, q), data];

const LOCKS: SectionRow[] = [
  { id: "alpha-l1", path: "docs/a.md", project: "alpha", note: "edit the intro", since: "2026-11-11T09:00:00Z" },
  { id: "beta-l2", path: "docs/b.md", project: "beta", note: "fix a link", since: "2026-11-11T08:00:00Z" },
  { id: "alpha-l3", path: "docs/c.md", project: "alpha", note: "old", since: "2026-11-10T08:00:00Z", released: "2026-11-10T09:00:00Z" },
];

const rowTitles = () => screen.getAllByRole("listitem").map((li) => within(li).getAllByRole("button")[0]?.textContent ?? "");

/** Render with `fetch` answered by `answer`, and no data cached. */
function renderFetching(ui: ReactNode, answer: (url: string) => Response) {
  vi.stubGlobal("fetch", (url: string) => Promise.resolve(answer(url)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
const sectionCalls = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map((c) => String(c[0])).filter((url) => url.startsWith("/api/section/"));
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("SectionPage", () => {
  test("open rows show first, in the server's order, each with a word for its state", () => {
    renderWith(<SectionPage section="claims" q="" />, [section("locks", page(LOCKS, { open: 2 }))]);
    expect(screen.getByRole("heading", { level: 1, name: "File claims" })).toBeTruthy();
    expect(screen.getByText("2 open of 3")).toBeTruthy();
    const titles = rowTitles();
    expect(titles.map((t) => t.match(/docs\/\w\.md/)?.[0])).toEqual(["docs/a.md", "docs/b.md", "docs/c.md"]);
    expect(titles[0]).toMatch(/held/);
    expect(titles[2]).toMatch(/released/);
  });

  test("a section with no open row says None open", () => {
    renderWith(<SectionPage section="decisions" q="" />, [section("decisions", page([{ id: "own-1", summary: "Use one queue" }]))]);
    expect(screen.getByText("None open of 1")).toBeTruthy();
  });

  test("search box sends q to the route after a 300 ms pause", () => {
    vi.useFakeTimers();
    window.location.hash = "#/claims";
    renderWith(<SectionPage section="claims" q="" />, [section("locks", page(LOCKS))]);
    const box = screen.getByRole("searchbox", { name: "Search file claims" });
    fireEvent.change(box, { target: { value: "intro" } });
    act(() => vi.advanceTimersByTime(299));
    expect(window.location.hash).toBe("#/claims");
    act(() => vi.advanceTimersByTime(1));
    expect(window.location.hash).toBe("#/claims?q=intro");
  });

  test("the q of the route goes to the server and fills the box", () => {
    renderWith(<SectionPage section="claims" q="intro" />, [section("locks", page(LOCKS.slice(0, 1), { open: 1 }), "intro")]);
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("intro");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  test("an empty result names the text searched and offers to clear it", () => {
    window.location.hash = "#/claims?q=zzz";
    renderWith(<SectionPage section="claims" q="zzz" />, [section("locks", page([]), "zzz")]);
    expect(screen.getByText('No row matches "zzz".')).toBeTruthy();
    expect(screen.getByText("None open")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear the search" }));
    expect(window.location.hash).toBe("#/claims");
  });

  test("section list draws fewer than 60 rows for 6,000 docs", () => {
    const rows = Array.from({ length: 6000 }, (_, i) => ({ id: "alpha-l" + i, path: "f" + i, project: "alpha" }));
    renderWith(<SectionPage section="claims" q="" />, [section("locks", page(rows, { open: 6000 }))]);
    const drawn = screen.getAllByRole("listitem");
    expect(drawn.length).toBeGreaterThan(5);
    expect(drawn.length).toBeLessThan(60);
  });

  test("a row opens in place with its detail; opening another closes it", () => {
    renderWith(<SectionPage section="claims" q="" />, [section("locks", page(LOCKS, { open: 2 }))]);
    const [first, second] = screen.getAllByRole("button", { name: /docs\/[ab]\.md/ });
    fireEvent.click(first as HTMLElement);
    expect(first?.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Why: edit the intro")).toBeTruthy();
    fireEvent.click(second as HTMLElement);
    expect(first?.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Why: edit the intro")).toBeNull();
    expect(screen.getByText("Why: fix a link")).toBeTruthy();
  });

  test("Load more asks for the next page", () => {
    const rows = Array.from({ length: 6000 }, (_, i) => ({ id: "alpha-l" + i, path: "f" + i }));
    renderWith(<SectionPage section="claims" q="" />, [section("locks", page(rows, { next: "6000" }))]);
    const fetchSpy = vi.fn(() => new Promise(() => {}));
    vi.stubGlobal("fetch", fetchSpy);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(sectionCalls(fetchSpy)).toEqual(["/api/section/locks?cursor=6000"]);
  });

  test("the list loads the next page by itself near its end", async () => {
    const fetchSpy = vi.fn(() => new Promise(() => {}));
    vi.stubGlobal("fetch", fetchSpy);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(keys.section("locks", ""), page(LOCKS, { next: "3" }));
    render(
      <QueryClientProvider client={qc}>
        <SectionPage section="claims" q="" />
      </QueryClientProvider>,
    );
    // The colour bars read the roster from /api/snapshot; the list asks once for its next page.
    expect(sectionCalls(fetchSpy)).toEqual(["/api/section/locks?cursor=3"]);
    expect(await screen.findByRole("button", { name: "Loading more" })).toBeTruthy();
  });

  test("a 403 says what happened and what to do", async () => {
    renderFetching(<SectionPage section="claims" q="" />, () => json(403, { error: "forbidden" }));
    expect(await screen.findByText(/no access to this section/)).toBeTruthy();
  });

  test("a failed load says so and offers to try again", async () => {
    let calls = 0;
    renderFetching(<SectionPage section="claims" q="" />, () => (calls++ === 0 ? json(500, { error: "boom" }) : json(200, page(LOCKS).pages[0])));
    expect(await screen.findByText(/did not load/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/docs\/a\.md/)).toBeTruthy();
  });

  test("a section and its search render rows with fields of the wrong type", async () => {
    const bad = { id: "own-9", summary: 7, body: { why: "speed" }, from: null, to: "beta", kind: ["x"], t: 5, replyTo: {} };
    renderFetching(<SectionPage section="decisions" q="" />, () => json(200, { rows: [bad], next: null, open: 0, total: 1 }));
    expect(await screen.findByRole("button", { name: /^7/ })).toBeTruthy();
    cleanup();
    renderFetching(<SectionPage section="decisions" q="speed" />, () => json(200, { rows: [bad], next: null, open: 0, total: 1 }));
    fireEvent.click(await screen.findByRole("button", { name: /^7/ }));
    expect(screen.getByText('{"why":"speed"}')).toBeTruthy();
  });

  test("an unknown section name says so", () => {
    renderWith(<SectionPage section="nope" q="" />);
    expect(screen.getByText(/There is no section named "nope"/)).toBeTruthy();
  });

  test("the land queue shows the runs, then the asks that wait", () => {
    const queue = {
      holders: [{ project: "alpha", id: "alpha-0100", since: "2026-11-11T09:40:00Z", word: "LAND START", branches: ["fix-a"], ask: null }],
      waiting: [{ project: "beta", id: "beta-0200", t: "2026-11-11T09:50:00Z", branches: ["feat-b"], holding: false }],
      next: "beta", slots_free: 0, may_start: false, max_holders: 1, notes: ["beta asked twice"],
    };
    renderWith(<SectionPage section="queue" q="" />, [[keys.queue, queue]]);
    expect(screen.getByRole("heading", { level: 1, name: "Land queue" })).toBeTruthy();
    expect(screen.getByText("1 landing, 1 waiting")).toBeTruthy();
    expect(screen.getByText("beta asked twice")).toBeTruthy();
    expect(rowTitles().map((t) => t.match(/alpha: fix-a|Next: beta: feat-b/)?.[0])).toEqual(["alpha: fix-a", "Next: beta: feat-b"]);
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  test("Lessons and incidents shows both collections in one list by time", () => {
    renderWith(<SectionPage section="lessons" q="" />, [
      section("lessons", page([{ id: "alpha-l1", summary: "Lesson new", t: "2026-11-11T09:00:00Z" }, { id: "alpha-l2", summary: "Lesson old", t: "2026-11-09T09:00:00Z" }])),
      section("incidents", page([{ id: "beta-i1", summary: "Incident open", start: "2026-11-01T09:00:00Z" }, { id: "beta-i2", summary: "Incident ended", start: "2026-11-10T09:00:00Z", end: "2026-11-10T10:00:00Z" }], { open: 1 })),
    ]);
    expect(screen.getByText("1 open of 4")).toBeTruthy();
    expect(rowTitles().map((t) => t.match(/(Lesson|Incident) (new|old|open|ended)/)?.[0])).toEqual(["Incident open", "Lesson new", "Incident ended", "Lesson old"]);
  });

  test("Adoption shows the records and says the gaps come later", () => {
    renderWith(<SectionPage section="adoption" q="" />, [section("adoption", page([{ id: "beta-adopt-x", from: "beta", item: "alpha-0042", state: "adopted" }]))]);
    expect(screen.getByText(/alpha-0042/)).toBeTruthy();
    expect(screen.getByText(/come in a later step/)).toBeTruthy();
  });
  test("an open agreement row shows Hold or Release hold to the owner only", () => {
    const rows = [
      { id: "a-1", summary: "Share one queue", status: "agreed" },
      { id: "a-2", summary: "Keep the old page", status: "agreed", hold: true },
    ];
    const owner: [readonly unknown[], unknown] = [keys.me, { name: "Owner", isOwner: true, canEdit: true, viewer: "owner" }];
    const { unmount } = renderWith(<SectionPage section="agreements" q="" />, [section("agreements", page(rows, { open: 2 })), owner]);
    fireEvent.click(screen.getByRole("button", { name: /Share one queue/ }));
    expect(screen.getByRole("button", { name: "Hold" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Keep the old page/ }));
    expect(screen.getByRole("button", { name: "Release hold" })).toBeTruthy();
    unmount();
    const viewer: [readonly unknown[], unknown] = [keys.me, { name: null, isOwner: false, canEdit: false, viewer: "viewer" }];
    renderWith(<SectionPage section="agreements" q="" />, [section("agreements", page(rows, { open: 2 })), viewer]);
    fireEvent.click(screen.getByRole("button", { name: /Share one queue/ }));
    expect(screen.queryByRole("button", { name: "Hold" })).toBeNull();
  });
});
