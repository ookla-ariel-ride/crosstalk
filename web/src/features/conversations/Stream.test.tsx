// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, type QueryKey } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { keys } from "../../api/queries";
import type { Me, Message, ReadState, Thread } from "../../api/types";
import { ago } from "../../logic/ago";
import { renderWith, snapshot, thread, threads } from "../../ui/testing";
import { STORAGE_KEY } from "./reading";
import { type Watch, WatchContext } from "./seen";
import { Conversations, NeedsYou, Stream } from "./Stream";

vi.mock(import("../../logic/ago"), async (importOriginal) => {
  const mod = await importOriginal();
  return { ...mod, ago: vi.fn(mod.ago) };
});

const OWNER: Me = { name: "owner", isOwner: true, canEdit: true, viewer: "owner" };
const VIEWER: Me = { name: null, isOwner: false, canEdit: false, viewer: "viewer" };
const EMPTY_STATE: ReadState = { threads: {}, muted: [], all_before: null };

function message(id: string, t: string, fields: Partial<Message> = {}): Message {
  return { id, t, from: "alpha", summary: "Summary " + id, body: "Body of " + id, depth: 1, quote: null, ...fields };
}

const MESSAGES: Message[] = [
  message("alpha-0001", "2026-11-11T09:00:00Z", { depth: 0, body: "Does the rebuild go on?" }),
  message("beta-0002", "2026-11-11T09:10:00Z", { from: "beta", body: "Read reply" }),
  message("alpha-0003", "2026-11-11T09:20:00Z", {
    depth: 2,
    quote: "First line of the parent\nsecond line",
    body: "Fresh reply",
    harness: "Codex CLI",
    model: "gpt-5",
  }),
  message("beta-0004", "2026-11-11T09:30:00Z", { from: "beta", body: "Newest reply" }),
];
const DETAIL: [QueryKey, unknown] = [keys.thread("alpha-0001"), { root: "alpha-0001", messages: MESSAGES }];
const Q1 = thread("alpha-0001", { title: "Does the rebuild go on?", count: 4, last_t: "2026-11-11T09:30:00Z", last_id: "beta-0004" });

/** A watch that fires only when the test says so. */
function fakeWatch() {
  const watched: { el: Element; onSeen: () => void }[] = [];
  const watch: Watch = (el, onSeen) => {
    const entry = { el, onSeen };
    watched.push(entry);
    return () => watched.splice(watched.indexOf(entry), 1);
  };
  return { watch, watched };
}

/** Replace fetch with a spy that answers each PUT with `state`. */
function spyFetch(state: ReadState = EMPTY_STATE) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    calls.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (init.method === "PUT") return new Response(JSON.stringify(state), { status: 200, headers: { "Content-Type": "application/json" } });
    return new Promise<Response>(() => {});
  });
  return calls;
}

const pad = (n: number) => String(n).padStart(2, "0");
const hhmm = (iso: string) => {
  const d = new Date(iso);
  return pad(d.getHours()) + ":" + pad(d.getMinutes());
};

function base(me: Me, state: ReadState | null, list: Thread[], sort: "needs" | "new" | "hot" | "unanswered" = "needs"): [QueryKey, unknown][] {
  const data: [QueryKey, unknown][] = [snapshot(), [keys.me, me], [keys.threads(sort), threads(...list)], DETAIL];
  if (state) data.push([keys.readState, state]);
  return data;
}

// jsdom has no layout: give the list a 600 px view and each row 56 px.
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(56);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { x: 0, y: 0, top: 0, left: 0, right: 780, bottom: 56, width: 780, height: 56, toJSON: () => ({}) },
  );
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.location.hash = "";
});
afterEach(() => vi.restoreAllMocks());

describe("Stream", () => {
  test("a row opens in place", () => {
    const onOpenChange = vi.fn();
    const view = (open: string[]) => <Stream sort="needs" open={open} onOpenChange={onOpenChange} />;
    const { rerender, qc } = renderWith(view([]), base(OWNER, EMPTY_STATE, [Q1, thread("beta-0009")]));
    const row = screen.getByRole("button", { name: /Does the rebuild go on\?/ });
    expect(row.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(row);
    expect(onOpenChange).toHaveBeenCalledWith(["alpha-0001"]);
    rerender(<QueryClientProvider client={qc}>{view(["alpha-0001"])}</QueryClientProvider>);
    expect(screen.getByRole("button", { name: /Does the rebuild go on\?/ }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Newest reply")).toBeTruthy();
  });

  test("an open thread shows the earlier messages folded, the line new since, then the fresh ones", () => {
    const state = { ...EMPTY_STATE, threads: { "alpha-0001": { t: "2026-11-11T09:10:00Z", id: "beta-0002" } } };
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, base(OWNER, state, [Q1]));
    const show = screen.getByRole("button", { name: "Show 2 earlier messages" });
    expect(screen.queryByText("Read reply")).toBeNull();
    const line = screen.getByText("New since " + hhmm("2026-11-11T09:10:00Z"));
    const fresh = screen.getByText("Fresh reply");
    expect(show.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(line.compareDocumentPosition(fresh) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(show);
    expect(screen.getByText("Read reply")).toBeTruthy();
  });

  test("the owner's thread is marked read once its last fresh message was in view, with one write", async () => {
    const { watch, watched } = fakeWatch();
    renderWith(
      <WatchContext value={watch}>
        <Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />
      </WatchContext>,
      base(OWNER, EMPTY_STATE, [Q1]),
    );
    const calls = spyFetch({ ...EMPTY_STATE, threads: { "alpha-0001": { t: "2026-11-11T09:30:00Z", id: "beta-0004" } } });
    expect(watched).toHaveLength(1);
    expect(watched[0]?.el.textContent).toContain("Newest reply");
    await act(async () => {
      watched[0]?.onSeen();
      watched[0]?.onSeen();
    });
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts).toEqual([
      { url: "/api/read-state", method: "PUT", body: { thread: "alpha-0001", t: "2026-11-11T09:30:00Z", id: "beta-0004" } },
    ]);
  });

  test("no write when the mark would not move forward", async () => {
    const { watch, watched } = fakeWatch();
    const state = { ...EMPTY_STATE, all_before: "2026-11-11T09:40:00Z" };
    renderWith(
      <WatchContext value={watch}>
        <Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />
      </WatchContext>,
      base(OWNER, state, [Q1]),
    );
    const calls = spyFetch();
    expect(screen.getByRole("button", { name: "Show 4 earlier messages" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Mark read" })).toBeNull();
    await act(async () => {
      for (const w of watched) w.onSeen();
    });
    expect(calls).toEqual([]);
  });

  test("mark read sends the owner's mark to the server", async () => {
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, [Q1]));
    const calls = spyFetch();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Mark read" })));
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ thread: "alpha-0001", t: "2026-11-11T09:30:00Z", id: "beta-0004" }]);
  });

  test("a viewer's mark goes to localStorage, not to the server", async () => {
    const { watch, watched } = fakeWatch();
    renderWith(
      <WatchContext value={watch}>
        <Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />
      </WatchContext>,
      base(VIEWER, null, [Q1]),
    );
    const calls = spyFetch();
    await act(async () => watched[0]?.onSeen());
    expect(calls).toEqual([]);
    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null");
    expect(stored.threads["alpha-0001"]).toEqual({ t: "2026-11-11T09:30:00Z", id: "beta-0004" });
  });

  test("a storage error does not break the page", async () => {
    window.localStorage.setItem(STORAGE_KEY, "{not json");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, base(VIEWER, null, [Q1]));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Mark read" })));
    expect(screen.queryByRole("button", { name: "Mark read" })).toBeNull();
  });

  test("fold all closes every thread", () => {
    const onOpenChange = vi.fn();
    renderWith(<Stream sort="needs" open={["alpha-0001", "beta-0009"]} onOpenChange={onOpenChange} />, base(OWNER, EMPTY_STATE, [Q1]));
    fireEvent.click(screen.getByRole("button", { name: "Fold all" }));
    expect(onOpenChange).toHaveBeenCalledWith([]);
  });

  test("mark all read sends all_before with the server time", async () => {
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, [Q1]));
    const calls = spyFetch();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Mark all read" })));
    // testing.tsx's thread pages carry server_time 2026-11-11T10:00:00Z.
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ all_before: "2026-11-11T10:00:00Z" }]);
  });

  test("a muted thread leaves the stream until a message in it addresses the owner", () => {
    const state = { ...EMPTY_STATE, muted: ["beta-0005", "beta-0006"] };
    renderWith(
      <Stream sort="needs" open={[]} onOpenChange={() => {}} />,
      base(OWNER, state, [
        thread("beta-0006", { title: "Asks the owner again", needs_owner: true }),
        Q1,
        thread("beta-0005", { title: "Quiet thread" }),
      ]),
    );
    expect(screen.getByRole("button", { name: /Asks the owner again/ })).toBeTruthy();
    expect(screen.queryByText("Quiet thread")).toBeNull();
    const line = screen.getByRole("button", { name: /1 muted/ });
    fireEvent.click(line);
    expect(screen.getByText("Quiet thread")).toBeTruthy();
  });

  test("each message shows who spoke and its time; a depth 2 message shows its quote", () => {
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, [Q1]));
    const fresh = screen.getByText("Fresh reply").closest("article") as HTMLElement;
    expect(within(fresh).getByText("alpha, Codex CLI, gpt-5")).toBeTruthy();
    const time = fresh.querySelector("time");
    expect(time?.getAttribute("datetime")).toBe("2026-11-11T09:20:00Z");
    expect(time?.getAttribute("title")).toBeTruthy();
    expect(within(fresh).getByText("First line of the parent")).toBeTruthy();
    expect(within(fresh).queryByText(/second line/)).toBeNull();
    const root = screen.getByText("Does the rebuild go on?", { selector: "p" }).closest("article") as HTMLElement;
    expect(within(root).getByText("alpha, model not stated")).toBeTruthy();
  });

  test("renderActions shows under each message and at the end of the thread", () => {
    renderWith(
      <Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} renderActions={(t, m) => <span>{"act " + (m ? m.id : "end:" + t.root)}</span>} />,
      base(OWNER, EMPTY_STATE, [Q1]),
    );
    expect(screen.getByText("act beta-0004")).toBeTruthy();
    expect(screen.getByText("act end:alpha-0001")).toBeTruthy();
  });

  test("a routine item shows as one line with its counts; a click shows its threads", () => {
    renderWith(
      <Stream sort="new" open={[]} onOpenChange={() => {}} />,
      base(OWNER, EMPTY_STATE, [
        thread("alpha-0010", { title: "Check-in of alpha", kind: "check-in", routine: true, last_t: "2026-11-11T09:50:00Z" }),
        thread("beta-0011", { title: "Check-in of beta", kind: "check-in", routine: true, projects: ["beta"], last_t: "2026-11-11T09:45:00Z" }),
        thread("alpha-0012", { title: "LAND START fix-1111", routine: true, last_t: "2026-11-11T09:40:00Z" }),
      ], "new"),
    );
    const line = screen.getByRole("button", { name: /Routine/ });
    expect(line.textContent).toContain("2 check-ins, 1 land start");
    expect(screen.queryByText("Check-in of alpha")).toBeNull();
    fireEvent.click(line);
    expect(screen.getByText("Check-in of alpha")).toBeTruthy();
    expect(screen.getByText("LAND START fix-1111")).toBeTruthy();
  });

  test("hot order asks the server for sort=hot", () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", (url: string) => {
      urls.push(url);
      return new Promise(() => {});
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(keys.me, OWNER);
    render(
      <QueryClientProvider client={qc}>
        <Stream sort="hot" open={[]} onOpenChange={() => {}} />
      </QueryClientProvider>,
    );
    expect(urls).toContain("/api/threads?sort=hot");
    const hot = screen.getByRole("link", { name: "Hot" });
    expect(hot.getAttribute("href")).toBe("#/conversations?sort=hot");
    expect(hot.getAttribute("aria-current")).toBe("page");
  });

  test("an empty stream, a failed load and a 403 each say what happened and what to do", async () => {
    renderWith(<Stream sort="new" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, [], "new"));
    expect(screen.getByText(/No threads yet/)).toBeTruthy();

    for (const [status, text] of [
      [500, /did not load/],
      [403, /sign in/i],
    ] as const) {
      vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: "x" }), { status }));
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const view = render(
        <QueryClientProvider client={qc}>
          <Stream sort="hot" open={[]} onOpenChange={() => {}} />
        </QueryClientProvider>,
      );
      expect(await screen.findByText(text)).toBeTruthy();
      view.unmount();
    }
  });
});

describe("Stream with 5,000 threads", () => {
  const MANY = Array.from({ length: 5000 }, (_, i) => thread("alpha-" + String(i).padStart(5, "0"), { title: "Thread " + i }));

  test("draws fewer than 60 rows, and opening one thread does not draw every row again", () => {
    const view = (open: string[]) => <Stream sort="needs" open={open} onOpenChange={() => {}} />;
    const { rerender, qc } = renderWith(view([]), base(OWNER, EMPTY_STATE, MANY));
    const rows = screen.getAllByRole("listitem");
    expect(rows.length).toBeLessThan(60);
    expect(rows.length).toBeGreaterThan(3);
    const before = vi.mocked(ago).mock.calls.length;
    rerender(<QueryClientProvider client={qc}>{view(["alpha-00001"])}</QueryClientProvider>);
    // Only the opened row draws again (one age); the other rows keep their render.
    expect(vi.mocked(ago).mock.calls.length - before).toBeLessThanOrEqual(1);
    expect(screen.getByText("Thread 1").closest("button")?.getAttribute("aria-expanded")).toBe("true");
  });
});

describe("Conversations", () => {
  test("opening a row puts it in the route and in sessionStorage; the route keeps 20 ids at most", () => {
    const open = Array.from({ length: 20 }, (_, i) => "beta-" + i);
    renderWith(<Conversations route={{ name: "conversations", sort: "new", open }} />, base(OWNER, EMPTY_STATE, [Q1], "new"));
    fireEvent.click(screen.getByRole("button", { name: /Does the rebuild go on\?/ }));
    const hash = decodeURIComponent(window.location.hash);
    expect(hash.startsWith("#/conversations?sort=new")).toBe(true);
    expect(hash).toContain("open=alpha-0001");
    expect(hash).not.toContain("open=beta-0&");
    expect(hash.match(/open=/g)).toHaveLength(20);
    expect(window.sessionStorage.getItem("crosstalk.route")).toContain("sort=new");
  });

  test("a fresh tab with no hash goes back to the last route of this tab", () => {
    window.sessionStorage.setItem("crosstalk.route", "#/conversations?sort=hot&open=alpha-0001");
    renderWith(<Conversations route={{ name: "conversations", sort: "needs", open: [] }} />, base(OWNER, EMPTY_STATE, [Q1]));
    expect(window.location.hash).toBe("#/conversations?sort=hot&open=alpha-0001");
  });
});

describe("NeedsYou", () => {
  test("shows only the threads that need the owner, in the needs order", () => {
    renderWith(
      <NeedsYou />,
      base(OWNER, EMPTY_STATE, [thread("beta-0007", { title: "A question for you", needs_owner: true }), Q1]),
    );
    expect(screen.getByRole("button", { name: /A question for you/ })).toBeTruthy();
    expect(screen.queryByText("Does the rebuild go on?")).toBeNull();
    expect(screen.queryByRole("link", { name: "Hot" })).toBeNull();
  });
});
