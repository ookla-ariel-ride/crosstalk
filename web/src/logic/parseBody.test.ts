import { describe, expect, test } from "vitest";
import { parseBody } from "./parseBody";

const p = (...parts: unknown[]) => ({ type: "p", parts });
const text = (t: string) => ({ type: "text", text: t });

describe("parseBody", () => {
  test("bodyText renders markup as text", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    expect(parseBody(hostile)).toEqual([p(text(hostile))]);
    const more = '<script>alert("x")</script> &amp; <b>bold</b>';
    expect(parseBody(more)).toEqual([p(text(more))]);
  });

  test("paragraphs split on blank lines and keep line breaks", () => {
    expect(parseBody("one\ntwo\n\n\nthree")).toEqual([p(text("one\ntwo")), p(text("three"))]);
    expect(parseBody("a\r\nb")).toEqual([p(text("a\nb"))]);
  });

  test("numbered and bullet lists", () => {
    expect(parseBody("Steps:\n1. Pull\n2) Build\n   it twice\n\n- one\n* two\n+ three")).toEqual([
      p(text("Steps:")),
      { type: "ol", start: 1, items: [[text("Pull")], [text("Build\nit twice")]] },
      { type: "ul", items: [[text("one")], [text("two")], [text("three")]] },
    ]);
    expect(parseBody("3. third\n4. fourth")).toEqual([
      { type: "ol", start: 3, items: [[text("third")], [text("fourth")]] },
    ]);
  });

  test("a line after a list that is not indented starts a paragraph", () => {
    expect(parseBody("- one\nAfter")).toEqual([{ type: "ul", items: [[text("one")]] }, p(text("After"))]);
  });

  test("a list mark with no space is text", () => {
    expect(parseBody("-1 degrees\n1.5 times")).toEqual([p(text("-1 degrees\n1.5 times"))]);
  });

  test("a code block keeps its text exactly", () => {
    const code = "  <b>x</b> & `y`\n\n- not a list\nhttps://example.com";
    expect(parseBody("Before\n```ts\n" + code + "\n```\nAfter")).toEqual([
      p(text("Before")),
      { type: "code", text: code },
      p(text("After")),
    ]);
  });

  test("a code block with no closing line runs to the end", () => {
    expect(parseBody("```\nx\n\ny")).toEqual([{ type: "code", text: "x\n\ny" }]);
  });

  test("inline code", () => {
    expect(parseBody("run `relay.py ask` now")).toEqual([
      p(text("run "), { type: "code", text: "relay.py ask" }, text(" now")),
    ]);
    expect(parseBody("a ` lone tick")).toEqual([p(text("a ` lone tick"))]);
    expect(parseBody("an `` empty pair")).toEqual([p(text("an `` empty pair"))]);
    expect(parseBody("`<i>` stays")).toEqual([p({ type: "code", text: "<i>" }, text(" stays"))]);
  });

  test("links are made for http and https only", () => {
    expect(parseBody("see https://example.com/a?b=1&c=2#d now")).toEqual([
      p(text("see "), { type: "link", href: "https://example.com/a?b=1&c=2#d" }, text(" now")),
    ]);
    expect(parseBody("HTTP://example.com")).toEqual([p({ type: "link", href: "HTTP://example.com" })]);
    for (const bad of ["javascript:alert(1)", "data:text/html,<b>x</b>", "file:///etc/passwd", "ftp://example.com"]) {
      expect(parseBody(bad)).toEqual([p(text(bad))]);
    }
    expect(parseBody("https:// nothing")).toEqual([p(text("https:// nothing"))]);
    expect(parseBody("xhttps://example.com")).toEqual([p(text("xhttps://example.com"))]);
    expect(parseBody("see.https://example.com")).toEqual([
      p(text("see."), { type: "link", href: "https://example.com" }),
    ]);
  });

  test("trailing punctuation is not part of a link", () => {
    expect(parseBody("Go to https://example.com/x.")).toEqual([
      p(text("Go to "), { type: "link", href: "https://example.com/x" }, text(".")),
    ]);
    expect(parseBody("(see https://example.com/y), then")).toEqual([
      p(text("(see "), { type: "link", href: "https://example.com/y" }, text("), then")),
    ]);
    expect(parseBody("https://en.wikipedia.org/wiki/A_(b)!")).toEqual([
      p({ type: "link", href: "https://en.wikipedia.org/wiki/A_(b)" }, text("!")),
    ]);
    expect(parseBody('"https://example.com/z"')).toEqual([
      p(text('"'), { type: "link", href: "https://example.com/z" }, text('"')),
    ]);
  });

  test("a link stops at markup", () => {
    expect(parseBody("https://example.com/<script>")).toEqual([
      p({ type: "link", href: "https://example.com/" }, text("<script>")),
    ]);
  });

  test("ids of other rows become refs", () => {
    expect(parseBody("Answers alpha-0042 and beta.verifier-20301010T101010123456Z.")).toEqual([
      p(
        text("Answers "),
        { type: "ref", id: "alpha-0042" },
        text(" and "),
        { type: "ref", id: "beta.verifier-20301010T101010123456Z" },
        text("."),
      ),
    ]);
    expect(parseBody("own-20301110151734-6350, alpha-sync-abc1234")).toEqual([
      p({ type: "ref", id: "own-20301110151734-6350" }, text(", "), { type: "ref", id: "alpha-sync-abc1234" }),
    ]);
  });

  test("words that are not ids stay text", () => {
    for (const s of ["check-in", "gpt-5", "claude-opus-4-5", "2030-01-01", "built-in", "Alpha-0042", "x/alpha-0042", "a@alpha-0042"]) {
      expect(parseBody(s)).toEqual([p(text(s))]);
    }
  });

  test("refs and links inside list items", () => {
    expect(parseBody("- alpha-0042\n- https://example.com")).toEqual([
      { type: "ul", items: [[{ type: "ref", id: "alpha-0042" }], [{ type: "link", href: "https://example.com" }]] },
    ]);
  });

  test("100,000 characters of one symbol parse in under 200 ms", () => {
    for (const ch of ["`", "-", "*", "<", "h", "a", "1", " ", "\n", ":", "(", "/", "."]) {
      const input = ch.repeat(100_000);
      const start = performance.now();
      parseBody(input);
      expect(performance.now() - start).toBeLessThan(200);
    }
    for (const tail of [")", ".)", "(", "a-"]) {
      const input = "https://a" + tail.repeat(Math.ceil(100_000 / tail.length));
      const start = performance.now();
      parseBody(input);
      expect(performance.now() - start).toBeLessThan(200);
    }
    for (const unit of ["http://", "a-1", "`a", "1. ", "- ", "```\n", "a-", "https://x)", "a-https://", "-h"]) {
      const input = unit.repeat(Math.ceil(100_000 / unit.length));
      const start = performance.now();
      parseBody(input);
      expect(performance.now() - start).toBeLessThan(200);
    }
  });

  test("bad input gives a plain result", () => {
    expect(parseBody("")).toEqual([]);
    expect(parseBody("\n\n  \n")).toEqual([]);
    expect(parseBody(undefined as never)).toEqual([]);
    expect(parseBody(42 as never)).toEqual([]);
  });
});
