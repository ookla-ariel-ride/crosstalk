// @vitest-environment jsdom
import { fireEvent, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";
import { Row } from "./Row";
import { renderWith, snapshot } from "./testing";

describe("Row", () => {
  test("row opens on Enter and on click", () => {
    const onOpen = vi.fn();
    renderWith(<Row projects={["alpha"]} title="A question" text="One line" note="6 min" onOpen={onOpen} open={false} />);
    const control = screen.getByRole("button", { name: /A question/ });
    expect(control.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(control);
    expect(onOpen).toHaveBeenCalledTimes(1);
    const enter = fireEvent.keyDown(control, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledTimes(2);
    // The key's default is stopped, so a browser sends no second click.
    expect(enter).toBe(false);
    fireEvent.keyDown(control, { key: "a" });
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  test("an open row shows its content and says so", () => {
    function Toggle() {
      const [open, setOpen] = useState(false);
      return (
        <Row projects={["alpha"]} title="A thread" onOpen={() => setOpen(!open)} open={open}>
          <p>The messages</p>
        </Row>
      );
    }
    renderWith(<Toggle />);
    const control = screen.getByRole("button", { name: /A thread/ });
    expect(screen.queryByText("The messages")).toBeNull();
    fireEvent.click(control);
    expect(control.getAttribute("aria-expanded")).toBe("true");
    const region = document.getElementById(control.getAttribute("aria-controls") ?? "");
    expect(region?.textContent).toBe("The messages");
  });

  test("a row with href is one link, with the colour bars of its projects", () => {
    renderWith(<Row projects={["alpha", "beta"]} title="Needs you" note="2 min" href="#/needs" />, [snapshot()]);
    const link = screen.getByRole("link", { name: /Needs you/ });
    expect(link.getAttribute("href")).toBe("#/needs");
    expect(link.hasAttribute("aria-expanded")).toBe(false);
    expect(screen.getByRole("img", { name: "Alpha, Beta" })).toBeTruthy();
  });
});
