// The page's own hash router (spec, "Stack": no router library). The 12 nav
// entries are Needs you, Conversations, the 9 sections and People.

export const SORTS = ["needs", "new", "hot", "unanswered"] as const;
export type Sort = (typeof SORTS)[number];

export const SECTIONS = [
  "queue", "progress", "reviews", "schedules",
  "decisions", "agreements", "lessons", "claims", "adoption",
] as const;
export type Section = (typeof SECTIONS)[number];

export type Route =
  | { name: "needs" }
  | { name: "people" }
  /** The stream, its sort order, and the threads open in it. */
  | { name: "conversations"; sort: Sort; open: string[] }
  /** One section with the text of its search box. */
  | { name: "section"; section: Section; q: string };

const isSort = (value: unknown): value is Sort => (SORTS as readonly unknown[]).includes(value);
const isSection = (value: unknown): value is Section => (SECTIONS as readonly unknown[]).includes(value);

const home = (): Route => ({ name: "conversations", sort: "needs", open: [] });

// A lone surrogate makes encodeURIComponent throw: it becomes U+FFFD.
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g;
const encode = (text: string): string => encodeURIComponent(text.replace(LONE_SURROGATE, "�"));

function decode(text: string): string | null {
  try {
    return decodeURIComponent(text);
  } catch {
    return null;
  }
}

/** The route a location hash names; an unknown hash gives the stream sorted by "needs". */
export function route(hash: string): Route {
  if (typeof hash !== "string") return home();
  const body = hash.replace(/^#/, "").replace(/^\//, "");
  const cut = body.indexOf("?");
  const path = (cut === -1 ? body : body.slice(0, cut)).replace(/\/$/, "");
  const params: [string, string][] = [];
  if (cut !== -1) {
    for (const pair of body.slice(cut + 1).split("&")) {
      const eq = pair.indexOf("=");
      if (eq === -1) continue;
      const value = decode(pair.slice(eq + 1));
      if (value !== null) params.push([pair.slice(0, eq), value]);
    }
  }
  const first = (key: string): string | undefined => params.find(([k]) => k === key)?.[1];
  if (path === "needs") return { name: "needs" };
  if (path === "people") return { name: "people" };
  if (isSection(path)) return { name: "section", section: path, q: first("q") ?? "" };
  if (path === "conversations") {
    const sort = first("sort");
    const open = params.filter(([k, v]) => k === "open" && v !== "").map(([, v]) => v);
    return { name: "conversations", sort: isSort(sort) ? sort : "needs", open };
  }
  return home();
}

/** The hash of a route; `route(href(r))` gives `r` back. */
export function href(r: Route): string {
  switch (r?.name) {
    case "needs":
      return "#/needs";
    case "people":
      return "#/people";
    case "section":
      if (isSection(r.section)) {
        return typeof r.q === "string" && r.q ? `#/${r.section}?q=${encode(r.q)}` : `#/${r.section}`;
      }
      break;
    case "conversations": {
      const open = Array.isArray(r.open) ? r.open.filter((id) => typeof id === "string" && id !== "") : [];
      const sort = isSort(r.sort) ? r.sort : "needs";
      return `#/conversations?sort=${sort}` + open.map((id) => `&open=${encode(id)}`).join("");
    }
  }
  return "#/conversations?sort=needs";
}
