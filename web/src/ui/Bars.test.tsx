// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { Bars } from "./Bars";
import { renderWith, snapshot } from "./testing";

describe("Bars", () => {
  test("one bar for each project in its roster colours, at most 5, and the names as text", () => {
    renderWith(<Bars projects={["alpha", "beta", "relay-bot", "gamma", "delta", "epsilon"]} />, [snapshot()]);
    const img = screen.getByRole("img");
    expect(img.getAttribute("aria-label")).toBe("Alpha, Beta, relay-bot, gamma, delta, epsilon");
    const bars = [...img.children] as HTMLElement[];
    expect(bars).toHaveLength(5);
    expect(bars[0]?.style.getPropertyValue("--c")).toBe("#2e7d32");
    expect(bars[0]?.style.getPropertyValue("--c-dark")).toBe("#81c784");
    // A project with no roster colour gets the muted token, never a made-up colour.
    expect(bars[3]?.style.getPropertyValue("--c")).toBe("var(--muted)");
  });

  test("a colour that is not a hex colour or a name is not used", () => {
    renderWith(<Bars projects={["alpha"]} />, [
      [["roster"], { server_time: "", roster: { alpha: { id: "alpha", color: "red;background:url(x)" } }, collections: {} }],
    ]);
    const bar = screen.getByRole("img").firstElementChild as HTMLElement;
    expect(bar.style.getPropertyValue("--c")).toBe("var(--muted)");
  });
});
