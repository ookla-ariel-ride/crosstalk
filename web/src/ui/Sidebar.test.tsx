// @vitest-environment jsdom
import type { Route } from "../logic/route";
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { keys } from "../api/queries";
import type { LandQueue, SectionPage, Vitals } from "../api/types";
import { Sidebar } from "./Sidebar";
import { renderWith, snapshot, thread, threads } from "./testing";

const HOME: Route = { name: "conversations", sort: "needs", open: [] };

function vitals(fields: Partial<Vitals> = {}): Vitals {
  return {
    disk_free_gb: 120,
    usage: 5,
    usage_limit: 96,
    build_host: { reachable: true, load: 1.5, t: "2026-11-11T09:59:00Z" },
    model_families: [],
    ...fields,
  };
}

function section(open: number): { pages: SectionPage[]; pageParams: string[] } {
  return { pages: [{ rows: [], next: null, open, total: open + 2 }], pageParams: [""] };
}

const QUEUE: LandQueue = {
  holders: [{ project: "alpha", id: "alpha-0007", since: "2026-11-11T09:30:00Z", word: "LAND START", branches: ["fix-1111"], ask: null }],
  waiting: [{ project: "beta", id: "beta-0008", t: "2026-11-11T09:35:00Z", branches: ["docs-1111"], holding: false }],
  next: "beta",
  slots_free: 0,
  may_start: false,
  max_holders: 1,
  notes: [],
};

/** The value cell of the health line named `label`. */
function health(label: string): HTMLElement {
  const term = screen.getByText(label, { selector: "dt" });
  return term.nextElementSibling as HTMLElement;
}

describe("Sidebar", () => {
  test("sidebar shows 12 entries in three groups", () => {
    renderWith(<Sidebar route={HOME} mode="push" />);
    const nav = screen.getByRole("navigation", { name: "Sections" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual([
      "Needs you", "Conversations",
      "Land queue", "Progress", "Reviews", "Schedules",
      "Decisions", "Agreements", "Lessons and incidents", "File claims", "Adoption",
      "Roster",
    ]);
    const groups = within(nav).getAllByRole("group");
    expect(groups.map((g) => [g.getAttribute("aria-label"), within(g).getAllByRole("link").length])).toEqual([
      ["Work", 4],
      ["Record", 5],
      ["People", 1],
    ]);
    // Each entry is a real link to its hash route; the current one is marked.
    expect(links[2]?.getAttribute("href")).toBe("#/queue");
    expect(links[1]?.getAttribute("aria-current")).toBe("page");
    expect(links.filter((a) => a.hasAttribute("aria-current"))).toHaveLength(1);
  });

  test("health line turns amber under 60 GB", () => {
    renderWith(<Sidebar route={HOME} mode="push" />, [[keys.vitals, vitals({ disk_free_gb: 59 })]]);
    const disk = health("Disk");
    expect(disk.textContent).toBe("59 GB free, low");
    expect(disk.dataset.warn).toBe("true");
  });

  test("60 GB and usage under the limit are not amber", () => {
    renderWith(<Sidebar route={HOME} mode="push" />, [[keys.vitals, vitals({ disk_free_gb: 60, usage: 95 })]]);
    expect(health("Disk").dataset.warn).toBeUndefined();
    expect(health("Weekly usage").textContent).toBe("95 of 96");
    expect(health("Weekly usage").dataset.warn).toBeUndefined();
  });

  test("usage at the limit is amber, and a null value is not known, never 0", () => {
    renderWith(<Sidebar route={HOME} mode="poll" />, [
      [keys.vitals, vitals({ usage: 96, build_host: null })],
    ]);
    expect(health("Weekly usage").textContent).toBe("96 of 96, at the limit");
    expect(health("Weekly usage").dataset.warn).toBe("true");
    expect(health("Build host").textContent).toBe("not known");
    expect(health("Feed").textContent).toBe("Polling every 15 s");
  });

  test("usage with no limit and no reading shows not known", () => {
    renderWith(<Sidebar route={HOME} mode={null} />, [
      [keys.vitals, vitals({ usage: null, usage_limit: null, build_host: { reachable: false, load: null, t: null } })],
    ]);
    expect(health("Weekly usage").textContent).toBe("not known");
    expect(health("Build host").textContent).toBe("Not reachable");
    expect(health("Feed").textContent).toBe("Connecting");
  });

  test("counts show open or new items, and nothing for 0", () => {
    renderWith(<Sidebar route={HOME} mode="push" />, [
      [keys.threads("needs"), threads(
        thread("alpha-0001", { needs_owner: true, unread: 2 }),
        thread("beta-0002", { needs_owner: true }),
        thread("beta-0003", { unread: 1 }),
        thread("alpha-0004"),
      )],
      [keys.queue, QUEUE],
      [keys.section("agreements", ""), section(16)],
      [keys.section("locks", ""), section(0)],
      [keys.section("incidents", ""), section(1)],
      [keys.section("reviews", ""), section(0)],
    ]);
    const count = (name: string) => screen.getByRole("link", { name: new RegExp("^" + name) }).querySelector("[data-count]")?.textContent ?? null;
    expect(count("Needs you")).toBe("2");
    // No route gives a total of unread threads, so Conversations shows no count.
    expect(count("Conversations")).toBeNull();
    expect(count("Land queue")).toBe("2");
    expect(count("Agreements")).toBe("16 open");
    expect(count("Lessons and incidents")).toBe("1 open");
    expect(count("File claims")).toBeNull();
    expect(count("Reviews")).toBeNull();
    expect(count("Progress")).toBeNull();
  });

  test("open waiting rows count in Needs you", () => {
    renderWith(<Sidebar route={HOME} mode="push" />, [
      [keys.threads("needs"), threads(thread("alpha-0001", { needs_owner: true }), thread("beta-0003"))],
      [keys.section("waiting", ""), section(3)],
    ]);
    const badge = screen.getByRole("link", { name: /^Needs you/ }).querySelector("[data-count]");
    expect(badge?.textContent).toBe("4");
  });

  test("a full first page of the needs sort shows the count with a plus", () => {
    const page = threads(...Array.from({ length: 50 }, (_, i) => thread("alpha-" + (1000 + i), { needs_owner: true })));
    renderWith(<Sidebar route={HOME} mode="push" />, [
      [keys.threads("needs"), { ...page, pages: [{ ...page.pages[0], next: "50" }] }],
    ]);
    const badge = screen.getByRole("link", { name: /^Needs you/ }).querySelector("[data-count]");
    expect(badge?.textContent).toBe("50+");
    expect(screen.getByRole("link", { name: "Needs you 50 or more" })).toBeTruthy();
  });

  test("projects show their colour bar and the age of the last check-in", () => {
    const now = Date.now();
    const sixMinAgo = new Date(now - 6 * 60_000).toISOString();
    renderWith(<Sidebar route={HOME} mode="push" />, [
      snapshot({ m1: { from: "alpha", kind: "check-in", t: sixMinAgo } }),
    ]);
    const list = screen.getByRole("list", { name: "Projects" });
    const items = within(list).getAllByRole("listitem");
    // Only kind "project" entries; relay-bot is a tool.
    expect(items.map((li) => li.textContent)).toEqual(["Alpha6 min", "Beta"]);
  });

  test("the brand shows the mark, the name and the tagline", () => {
    renderWith(<Sidebar route={HOME} mode="push" />);
    expect(screen.getByText("Crosstalk")).toBeTruthy();
    expect(screen.getByText("Where your agent sessions talk, agree, and ask you.")).toBeTruthy();
  });
});
