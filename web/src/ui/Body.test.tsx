// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { Body } from "./Body";
import "./testing";

describe("Body", () => {
  test("Body shows a script tag as text", () => {
    const { container } = render(<Body text={'<script>alert("x")</script> and <b>bold</b>'} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toBe('<script>alert("x")</script> and <b>bold</b>');
  });

  test("blocks become paragraphs, lists and code; links open safely in a new tab", () => {
    const text = [
      "See https://example.com/a and `relay.py read`.",
      "",
      "3. three",
      "4. four",
      "",
      "- one",
      "",
      "```sh",
      "<img src=x onerror=alert(1)>",
      "```",
    ].join("\n");
    const { container } = render(<Body text={text} />);
    const link = screen.getByRole("link", { name: "https://example.com/a" });
    expect(link.getAttribute("href")).toBe("https://example.com/a");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(container.querySelector("p code")?.textContent).toBe("relay.py read");
    expect(container.querySelector("ol")?.getAttribute("start")).toBe("3");
    expect(container.querySelectorAll("ol li")).toHaveLength(2);
    expect(container.querySelectorAll("ul li")).toHaveLength(1);
    expect(container.querySelector("pre code")?.textContent).toBe("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
  });

  test("a ref is a link to that id through the hash routes", () => {
    render(<Body text="As in alpha-0042, done." />);
    const ref = screen.getByRole("link", { name: "alpha-0042" });
    expect(ref.getAttribute("href")).toBe("#/conversations?sort=needs&open=alpha-0042");
    expect(ref.hasAttribute("target")).toBe(false);
  });

  test("a click on a ref keeps the present route and adds the id to the open threads", () => {
    window.location.hash = "#/conversations?sort=hot&open=alpha-0001";
    render(<Body text="See beta-0042." />);
    fireEvent.click(screen.getByRole("link", { name: "beta-0042" }));
    expect(window.location.hash).toBe("#/conversations?sort=hot&open=alpha-0001&open=beta-0042");
    window.location.hash = "";
  });

  test("Body draws blocks it is given, as text", () => {
    const { container } = render(
      <Body blocks={[{ type: "p", parts: [{ type: "text", text: "Why: <b>x</b>" }] }, { type: "ul", items: [[{ type: "text", text: "one" }]] }]} />,
    );
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("p")?.textContent).toBe("Why: <b>x</b>");
    expect(container.querySelector("li")?.textContent).toBe("one");
  });
});
