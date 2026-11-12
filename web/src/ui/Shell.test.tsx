// @vitest-environment jsdom
import type { Route } from "../logic/route";
import { act, cleanup, fireEvent, renderHook, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { Shell, useHashRoute } from "./Shell";
import { renderWith } from "./testing";

const HOME: Route = { name: "conversations", sort: "needs", open: [] };

describe("Shell", () => {
  test("lays out the sidebar, the main part with its children, and the right column", () => {
    renderWith(<Shell route={HOME} mode="push"><h1>Main content</h1></Shell>);
    expect(screen.getByRole("navigation", { name: "Sections" })).toBeTruthy();
    expect(screen.getByRole("main").textContent).toBe("Main content");
    expect(screen.getByRole("complementary", { name: "Needs you and the land queue" })).toBeTruthy();
  });

  test("the tab title shows the unread total, and only the name with none", () => {
    renderWith(<Shell route={HOME} mode="push" unread={12}><p>x</p></Shell>);
    expect(document.title).toBe("(12) Crosstalk");
    cleanup();
    renderWith(<Shell route={HOME} mode="push" unread={0}><p>x</p></Shell>);
    expect(document.title).toBe("Crosstalk");
  });

  test("the menu button opens the sidebar; Escape closes it and gives focus back", () => {
    renderWith(<Shell route={HOME} mode="push"><p>x</p></Shell>);
    const button = screen.getByRole("button", { name: "Menu" });
    const sidebar = document.getElementById(button.getAttribute("aria-controls") ?? "");
    expect(sidebar).toBeTruthy();
    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(sidebar?.dataset.open).toBe("true");
    const link = screen.getByRole("link", { name: "Progress" });
    link.focus();
    fireEvent.keyDown(link, { key: "Escape" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(button);
  });

  test("Escape closes the menu when the focus is on the menu button too", () => {
    renderWith(<Shell route={HOME} mode="push"><p>x</p></Shell>);
    const button = screen.getByRole("button", { name: "Menu" });
    fireEvent.click(button);
    button.focus();
    fireEvent.keyDown(button, { key: "Escape" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(button);
  });

  test("a click on an entry closes the menu", () => {
    renderWith(<Shell route={HOME} mode="push"><p>x</p></Shell>);
    const button = screen.getByRole("button", { name: "Menu" });
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("link", { name: "Progress" }));
    expect(button.getAttribute("aria-expanded")).toBe("false");
  });

  test("the route follows the location hash", () => {
    window.location.hash = "#/progress?q=parser";
    const { result } = renderHook(() => useHashRoute());
    expect(result.current).toEqual({ name: "section", section: "progress", q: "parser" });
    act(() => {
      window.location.hash = "#/needs";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(result.current).toEqual({ name: "needs" });
    window.location.hash = "";
  });
});
