import { describe, expect, test } from "vitest";
import { SECTIONS, href, route } from "./route";
import type { Route } from "./route";

const home: Route = { name: "conversations", sort: "needs", open: [] };

describe("route and href", () => {
  test("every route survives a round trip", () => {
    const routes: Route[] = [
      { name: "needs" },
      { name: "people" },
      home,
      { name: "conversations", sort: "new", open: [] },
      { name: "conversations", sort: "hot", open: ["alpha-0001"] },
      { name: "conversations", sort: "unanswered", open: ["alpha-0001", "beta-20301010T101010000000Z"] },
      ...SECTIONS.map((section): Route => ({ name: "section", section, q: "" })),
      ...SECTIONS.map((section): Route => ({ name: "section", section, q: "disk full" })),
    ];
    for (const r of routes) expect(route(href(r))).toEqual(r);
  });

  test("the nav has 12 entries: needs you, conversations, 9 sections, people", () => {
    expect(SECTIONS).toEqual([
      "queue", "progress", "reviews", "schedules",
      "decisions", "agreements", "lessons", "claims", "adoption",
    ]);
  });

  test("hrefs are plain hashes", () => {
    expect(href({ name: "needs" })).toBe("#/needs");
    expect(href(home)).toBe("#/conversations?sort=needs");
    expect(href({ name: "conversations", sort: "hot", open: ["alpha-0001", "beta-0002"] })).toBe(
      "#/conversations?sort=hot&open=alpha-0001&open=beta-0002",
    );
    expect(href({ name: "section", section: "queue", q: "" })).toBe("#/queue");
    expect(href({ name: "section", section: "lessons", q: "a b" })).toBe("#/lessons?q=a%20b");
    expect(href({ name: "people" })).toBe("#/people");
  });

  test("ids and query text are encoded", () => {
    const odd: Route = { name: "conversations", sort: "new", open: ["a&open=b", "c/../d?#%"] };
    expect(href(odd)).toBe("#/conversations?sort=new&open=a%26open%3Db&open=c%2F..%2Fd%3F%23%25");
    expect(route(href(odd))).toEqual(odd);
    const q: Route = { name: "section", section: "decisions", q: "x&q=y #1 100% <b>" };
    expect(route(href(q))).toEqual(q);
  });

  test("a bad sort gives needs", () => {
    expect(route("#/conversations?sort=oldest")).toEqual(home);
    expect(route("#/conversations")).toEqual(home);
  });

  test("an unknown hash gives the conversations route", () => {
    for (const h of ["", "#", "#/", "#/nowhere", "#/__proto__", "#/toString", "#/constructor", "#//queue", "#/queue/extra"]) {
      expect(route(h)).toEqual(home);
    }
  });

  test("a trailing slash and a missing slash are read", () => {
    expect(route("#/people/")).toEqual({ name: "people" });
    expect(route("#queue")).toEqual({ name: "section", section: "queue", q: "" });
  });

  test("bad input gives a plain result", () => {
    expect(route(undefined as never)).toEqual(home);
    expect(route("#/conversations?sort=hot&open=%E0%A4%A&open=alpha-0001&open=")).toEqual({
      name: "conversations",
      sort: "hot",
      open: ["alpha-0001"],
    });
    expect(route("#/queue?q=%")).toEqual({ name: "section", section: "queue", q: "" });
    expect(href(undefined as never)).toBe("#/conversations?sort=needs");
    expect(href({ name: "conversations" } as never)).toBe("#/conversations?sort=needs");
    expect(href({ name: "section", section: "queue", q: "\uD800" })).toBe("#/queue?q=%EF%BF%BD");
    expect(href({ name: "section", section: "nope" } as never)).toBe("#/conversations?sort=needs");
  });
});
