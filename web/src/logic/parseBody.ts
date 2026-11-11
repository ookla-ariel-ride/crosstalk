// A message body as data for the page to draw: blocks of paragraphs, lists
// and code, each holding inline parts. This is the safety boundary of the
// page: it returns strings and never markup, and `<`, `>`, `&` stay literal
// text. The input may be long and hostile, so every step is one pass over the
// text with indexOf and character tests; no regex runs over unbounded input.

export type Inline =
  | { type: "text"; text: string }
  | { type: "code"; text: string }
  /** An http or https URL; `href` is also the text to show. */
  | { type: "link"; href: string }
  /** What looks like the id of another row; the page decides whether it exists. */
  | { type: "ref"; id: string };

export type Block =
  | { type: "p"; parts: Inline[] }
  | { type: "ol"; start: number; items: Inline[][] }
  | { type: "ul"; items: Inline[][] }
  | { type: "code"; text: string };

const isSpace = (c: string | undefined): boolean => c === " " || c === "\t";
const isDigit = (c: string | undefined): boolean => c !== undefined && c >= "0" && c <= "9";
const isLower = (c: string | undefined): boolean => c !== undefined && c >= "a" && c <= "z";
const isAlnum = (c: string | undefined): boolean =>
  isDigit(c) || isLower(c) || (c !== undefined && c >= "A" && c <= "Z");
const isIdChar = (c: string | undefined): boolean => isAlnum(c) || c === "-" || c === ".";

// ---- inline parts ----

/** Ends a URL: white space, markup characters, quotes and backticks. */
const URL_STOP = new Set([" ", "\t", "\n", "\r", "<", ">", '"', "'", "`", "\\"]);
const TRAILING = new Set([".", ",", ";", ":", "!", "?", "*"]);
const BRACKETS = new Set(["(", ")", "[", "]", "{", "}"]);
const OPENER: Record<string, string> = { ")": "(", "]": "[", "}": "{" };

/**
 * The end of the URL whose scheme ends at `schemeEnd`, with trailing
 * punctuation left out. A closing bracket stays when the URL opens it too,
 * as in https://en.wikipedia.org/wiki/A_(b).
 */
function urlEnd(text: string, schemeEnd: number): number {
  const seen: Record<string, number> = { "(": 0, ")": 0, "[": 0, "]": 0, "{": 0, "}": 0 };
  let end = schemeEnd;
  while (end < text.length && !URL_STOP.has(text[end] as string)) {
    const c = text[end] as string;
    if (BRACKETS.has(c)) seen[c] = (seen[c] as number) + 1;
    end++;
  }
  while (end > schemeEnd) {
    const last = text[end - 1] as string;
    const opener = OPENER[last];
    if (TRAILING.has(last)) {
      end--;
    } else if (opener !== undefined && (seen[last] as number) > (seen[opener] as number)) {
      seen[last] = (seen[last] as number) - 1;
      end--;
    } else {
      break;
    }
  }
  return end;
}

/** The length of the scheme ("http://" or "https://", any case) at `at`, else 0. */
function schemeAt(text: string, at: number): number {
  const head = text.slice(at, at + 8).toLowerCase();
  if (head.startsWith("https://")) return 8;
  if (head.startsWith("http://")) return 7;
  return 0;
}

/**
 * An id of another row: `<participant>-<rest>`, as relay.py, the MCP tools
 * and the owner's page write them (alpha-0042, own-20301110151734-6350,
 * alpha-20301010T101010123456Z, alpha-sync-abc1234, alpha.verifier-0001).
 * It starts with a lower-case letter, holds a "-", and its last "-" part has
 * 4 or more letters and digits with at least one digit. This leaves words
 * such as "check-in", "gpt-5" and "claude-opus-4-5" as text; a false match
 * only gives a link that finds no row.
 */
function isRefId(token: string): boolean {
  if (!isLower(token[0])) return false;
  const dash = token.lastIndexOf("-");
  if (dash <= 0) return false;
  const last = token.slice(dash + 1);
  if (last.length < 4) return false;
  let digit = false;
  for (const c of last) {
    if (!isAlnum(c)) return false;
    if (isDigit(c)) digit = true;
  }
  return digit;
}

class Parts {
  readonly list: Inline[] = [];
  text(t: string): void {
    if (t === "") return;
    const prev = this.list[this.list.length - 1];
    if (prev?.type === "text") prev.text += t;
    else this.list.push({ type: "text", text: t });
  }
  push(part: Inline): void {
    this.list.push(part);
  }
}

/** Where a link may start: an "h" after a character that is not a letter or digit. */
const linkStart = (text: string, i: number): number =>
  (text[i] === "h" || text[i] === "H") && !isAlnum(text[i - 1]) ? schemeAt(text, i) : 0;

/** Text with no code in it: links first, then ids of rows. */
function plain(text: string, out: Parts): void {
  let from = 0; // start of the text not yet emitted
  let i = 0;
  while (i < text.length) {
    const scheme = linkStart(text, i);
    const linkEnd = scheme ? urlEnd(text, i + scheme) : 0;
    if (linkEnd > i + scheme) {
      out.text(text.slice(from, i));
      out.push({ type: "link", href: text.slice(i, linkEnd) });
      from = i = linkEnd;
      continue;
    }
    if (isIdChar(text[i])) {
      // One token of id characters, looked at once and then skipped. It ends
      // where a link starts (after a "-" or "."), so that link is still read.
      let end = i + 1;
      while (end < text.length && isIdChar(text[end]) && !linkStart(text, end)) end++;
      // A token right after "/" or "@" is part of a path or a commit ref.
      const before = text[i - 1];
      if (before !== "/" && before !== "@") {
        let idEnd = end;
        while (idEnd > i && (text[idEnd - 1] === "." || text[idEnd - 1] === "-")) idEnd--;
        const token = text.slice(i, idEnd);
        if (isRefId(token)) {
          out.text(text.slice(from, i));
          out.push({ type: "ref", id: token });
          from = idEnd;
        }
      }
      i = end;
      continue;
    }
    i++;
  }
  out.text(text.slice(from));
}

/** Inline code between single backticks; the rest goes to `plain`. */
function inline(text: string): Inline[] {
  const out = new Parts();
  let from = 0;
  let search = 0;
  while (search < text.length) {
    const open = text.indexOf("`", search);
    if (open === -1) break;
    const close = text.indexOf("`", open + 1);
    if (close === -1) break; // no backtick after this one closes anything
    if (close === open + 1) {
      // An empty pair is text; skip both.
      search = close + 1;
      continue;
    }
    plain(text.slice(from, open), out);
    out.push({ type: "code", text: text.slice(open + 1, close) });
    from = search = close + 1;
  }
  plain(text.slice(from), out);
  return out.list;
}

// ---- blocks ----

const leadingSpace = (line: string): number => {
  let i = 0;
  while (isSpace(line[i])) i++;
  return i;
};

/** A list item line: its kind, number and text, else null. */
function listItem(line: string): { kind: "ol" | "ul"; n: number; text: string } | null {
  let i = leadingSpace(line);
  const c = line[i];
  if ((c === "-" || c === "*" || c === "+") && isSpace(line[i + 1])) {
    return { kind: "ul", n: 0, text: line.slice(i + 2).trim() };
  }
  const digits = i;
  while (isDigit(line[i]) && i - digits < 9) i++;
  if (i > digits && (line[i] === "." || line[i] === ")") && isSpace(line[i + 1])) {
    return { kind: "ol", n: Number(line.slice(digits, i)), text: line.slice(i + 2).trim() };
  }
  return null;
}

const isLangChar = (c: string | undefined): boolean =>
  isAlnum(c) || c === "_" || c === "+" || c === "." || c === "#" || c === "-";

/**
 * A fence is a line that holds only three backticks and an optional language
 * word, with spaces or tabs around them. Any other line that starts with
 * backticks is ordinary text, so no character of it is lost. The fence lines
 * themselves are the only text of the input that no part holds. An opening
 * fence with no closing fence takes the rest of the body as code.
 */
function isFence(line: string): boolean {
  let i = leadingSpace(line);
  if (line.slice(i, i + 3) !== "```") return false;
  i += 3;
  while (isLangChar(line[i])) i++;
  while (isSpace(line[i])) i++;
  return i === line.length;
}

const dropCR = (line: string): string => (line.endsWith("\r") ? line.slice(0, -1) : line);

/** The blocks of a message body. Text that is not a string gives none. */
export function parseBody(text: string): Block[] {
  if (typeof text !== "string") return [];
  // Each line without its "\r"; a code block reads `raw` and keeps them.
  const raw = text.split("\n");
  const lines = raw.map(dropCR);
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { kind: "ol" | "ul"; start: number; items: string[] } | null = null;

  const flush = (): void => {
    if (para.length) blocks.push({ type: "p", parts: inline(para.join("\n")) });
    para = [];
    if (list) {
      const items = list.items.map(inline);
      blocks.push(list.kind === "ol" ? { type: "ol", start: list.start, items } : { type: "ul", items });
    }
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] as string;
    if (isFence(line)) {
      flush();
      const body: string[] = [];
      for (i++; i < lines.length && !isFence(lines[i] as string); i++) body.push(raw[i] as string);
      // The "\r" before a closing fence ends the last line; it is not code.
      if (i < lines.length && body.length) body.push(dropCR(body.pop() as string));
      blocks.push({ type: "code", text: body.join("\n") });
      continue;
    }
    if (line.trim() === "") {
      flush();
      continue;
    }
    const item = listItem(line);
    if (item) {
      if (para.length || (list && list.kind !== item.kind)) flush();
      if (!list) list = { kind: item.kind, start: item.n, items: [] };
      list.items.push(item.text);
      continue;
    }
    if (list && isSpace(line[0])) {
      // An indented line goes on with the list item above it.
      const last = list.items.length - 1;
      list.items[last] += "\n" + line.trim();
      continue;
    }
    if (list) flush();
    para.push(line);
  }
  flush();
  return blocks;
}
