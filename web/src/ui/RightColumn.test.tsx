// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { keys } from "../api/queries";
import type { LandQueue } from "../api/types";
import { RightColumn } from "./RightColumn";
import { renderWith, snapshot, thread, threads } from "./testing";

const QUEUE: LandQueue = {
  holders: [{ project: "alpha", id: "alpha-0007", since: "2026-11-11T09:30:00Z", word: "LAND START", branches: ["fix-1111"], ask: null }],
  waiting: [
    { project: "beta", id: "beta-0008", t: "2026-11-11T09:35:00Z", branches: ["docs-1111"], holding: false },
    { project: "relay-bot", id: "relay-bot-0009", t: "2026-11-11T09:36:00Z", branches: [], holding: false },
  ],
  next: "beta",
  slots_free: 0,
  may_start: false,
  max_holders: 1,
  notes: [],
};

describe("RightColumn", () => {
  test("needs you lists the threads that need the owner, each a link that opens it", () => {
    renderWith(<RightColumn />, [
      snapshot(),
      [keys.threads("needs"), threads(
        thread("alpha-0001", { title: "Does the rebuild go on?", needs_owner: true }),
        thread("beta-0002", { title: "Only news" }),
      )],
      [keys.queue, QUEUE],
    ]);
    const needs = screen.getByRole("region", { name: "Needs you" });
    const links = within(needs).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]?.textContent).toContain("Does the rebuild go on?");
    expect(links[0]?.getAttribute("href")).toBe("#/conversations?sort=needs&open=alpha-0001");
  });

  test("the land queue shows who holds it, then who is next", () => {
    renderWith(<RightColumn />, [snapshot(), [keys.threads("needs"), threads()], [keys.queue, QUEUE]]);
    const queue = screen.getByRole("region", { name: "Land queue" });
    const text = within(queue).getAllByRole("link").map((a) => a.textContent);
    expect(text[0]).toContain("Alpha, fix-1111");
    expect(text[1]).toContain("Next: Beta, docs-1111");
    expect(text[2]).toContain("Then: relay-bot");
    expect(within(screen.getByRole("region", { name: "Needs you" })).getByText("Nothing needs you now.")).toBeTruthy();
  });

  test("an empty queue says so", () => {
    renderWith(<RightColumn />, [
      [keys.threads("needs"), threads()],
      [keys.queue, { ...QUEUE, holders: [], waiting: [], next: null }],
    ]);
    expect(screen.getByText("No land runs and no one waiting.")).toBeTruthy();
  });
});
