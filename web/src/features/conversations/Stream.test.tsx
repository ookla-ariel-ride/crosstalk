// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, type QueryKey } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { keys } from "../../api/queries";
import type { Me, Message, ReadState, Thread } from "../../api/types";
import { ago } from "../../logic/ago";
import { renderWith, snapshot, thread, threads } from "../../ui/testing";
import { STORAGE_KEY } from "./reading";
import { SortBar } from "./SortBar";
import { type Watch, WatchContext } from "./seen";
import { Conversations, NeedsYou, STATUS_MS, Stream } from "./Stream";

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
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    // testing.tsx's thread pages carry server_time 2026-11-11T10:00:00Z.
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ all_before: "2026-11-11T10:00:00Z" }]);
  });

  test("mark all read also marks each shown thread whose newest message is dated after the server time", async () => {
    const ahead = thread("beta-0007", { last_t: "2026-11-11T10:05:00Z", last_id: "beta-0008", unread: 1 });
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, [Q1, ahead]));
    const calls = spyFetch();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([
      { all_before: "2026-11-11T10:00:00Z" },
      { thread: "beta-0007", t: "2026-11-11T10:05:00Z", id: "beta-0008" },
    ]);
  });

  test("a viewer's mark all read keeps the time and the marks of the threads dated ahead", async () => {
    const ahead = thread("beta-0007", { last_t: "2026-11-11T10:05:00Z", last_id: "beta-0008" });
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(VIEWER, null, [Q1, ahead]));
    expect(screen.getAllByText("new")).toHaveLength(2);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null");
    expect(stored.all_before).toBe("2026-11-11T10:00:00Z");
    expect(stored.threads).toEqual({ "beta-0007": { t: "2026-11-11T10:05:00Z", id: "beta-0008" } });
    expect(screen.queryAllByText("new")).toHaveLength(0);
  });

  test("after mark all read the open thread shows no line new since and keeps its messages in view", async () => {
    const state = { ...EMPTY_STATE, threads: { "alpha-0001": { t: "2026-11-11T09:10:00Z", id: "beta-0002" } } };
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, base(OWNER, state, [Q1]));
    expect(screen.getByText("New since " + hhmm("2026-11-11T09:10:00Z"))).toBeTruthy();
    spyFetch({ ...state, all_before: "2026-11-11T10:00:00Z" });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    expect(screen.queryByText(/New since/)).toBeNull();
    expect(screen.getByText("Newest reply")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /earlier message/ })).toBeNull();
  });

  test("a failed mark all read write leaves the line new since", async () => {
    const state = { ...EMPTY_STATE, threads: { "alpha-0001": { t: "2026-11-11T09:10:00Z", id: "beta-0002" } } };
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, base(OWNER, state, [Q1]));
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit = {}) =>
      init.method === "PUT" ? new Response(JSON.stringify({ error: "down" }), { status: 500 }) : new Promise<Response>(() => {}),
    );
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    expect(screen.getByText("New since " + hhmm("2026-11-11T09:10:00Z"))).toBeTruthy();
  });

  test("a row that arrives after mark all read stays new", async () => {
    const { qc } = renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(VIEWER, null, [Q1]));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    expect(screen.queryAllByText("new")).toHaveLength(0);
    const later = thread("beta-0011", { title: "Came after the click", last_t: "2026-11-11T10:02:00Z", last_id: "beta-0011" });
    const reply = { ...Q1, last_t: "2026-11-11T10:03:00Z", last_id: "beta-0012" };
    await act(async () => qc.setQueryData(keys.threads("needs"), { pages: [{ threads: [later, reply], next: null, server_time: "2026-11-11T10:04:00Z" }], pageParams: [""] }));
    await waitFor(() => expect(screen.getAllByText("new")).toHaveLength(2));
  });

  test("mark all read keeps the messages in view mounted, with their actions", async () => {
    const mounts: string[] = [];
    function Probe({ id }: { id: string }) {
      useEffect(() => void mounts.push(id), [id]);
      return null;
    }
    const state = { ...EMPTY_STATE, threads: { "alpha-0001": { t: "2026-11-11T09:10:00Z", id: "beta-0002" } } };
    const actions = (_t: Thread, m: Message | null) => (m ? <Probe id={m.id} /> : null);
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} renderActions={actions} />, base(OWNER, state, [Q1]));
    expect(mounts).toEqual(["alpha-0003", "beta-0004"]);
    spyFetch({ ...state, all_before: "2026-11-11T10:00:00Z" });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    expect(mounts).toEqual(["alpha-0003", "beta-0004", "alpha-0001", "beta-0002"]);
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

  test("a multiline answer keeps its first line after another reply; a body that repeats the summary shows it once", () => {
    const answered = [
      MESSAGES[0] as Message,
      message("own-0002", "2026-11-11T09:10:00Z", { from: "owner", summary: "Keep the log", body: "We revisit it in a month." }),
      message("beta-0003", "2026-11-11T09:20:00Z", { from: "beta", summary: "Agreed", body: "Agreed\n\nI keep the log as it is." }),
    ];
    const data = base(OWNER, EMPTY_STATE, [Q1]).map(([k, v]): [QueryKey, unknown] =>
      k[0] === "thread" ? [k, { root: "alpha-0001", messages: answered }] : [k, v],
    );
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, data);
    const answer = screen.getByText("Keep the log").closest("article") as HTMLElement;
    expect(within(answer).getByText("We revisit it in a month.")).toBeTruthy();
    const reply = screen.getByText("I keep the log as it is.").closest("article") as HTMLElement;
    expect(within(reply).getAllByText("Agreed")).toHaveLength(1);
  });

  test("renderActions shows under each message and at the end of the thread", () => {
    renderWith(
      <Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} renderActions={(t, m) => <span>{"act " + (m ? m.id : "end:" + t.root)}</span>} />,
      base(OWNER, EMPTY_STATE, [Q1]),
    );
    expect(screen.getByText("act beta-0004")).toBeTruthy();
    expect(screen.getByText("act end:alpha-0001")).toBeTruthy();
  });

  test("a request the owner acked shows Owner: no action needed", () => {
    const acked = [message("alpha-0001", "2026-11-11T09:00:00Z", { depth: 0, kind: "request", acked_by: ["owner"] }), MESSAGES[1]!];
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, [
      ...base(OWNER, EMPTY_STATE, [Q1]).filter(([key]) => key !== DETAIL[0]),
      [keys.thread("alpha-0001"), { root: "alpha-0001", messages: acked }],
    ]);
    expect(screen.getAllByText("Owner: no action needed")).toHaveLength(1);
  });

  test("an ack by another participant does not show the owner line", () => {
    const acked = [message("alpha-0001", "2026-11-11T09:00:00Z", { depth: 0, kind: "request", acked_by: ["beta"] })];
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, [
      ...base(OWNER, EMPTY_STATE, [Q1]).filter(([key]) => key !== DETAIL[0]),
      [keys.thread("alpha-0001"), { root: "alpha-0001", messages: acked }],
    ]);
    expect(screen.queryByText("Owner: no action needed")).toBeNull();
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
    renderWith(<Stream sort="hot" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, [], "hot"));
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

describe("the reader's own messages", () => {
  // The owner's mark sits at alpha-0001; `own` lists which later messages the owner wrote.
  function open(own: string[], me: Me = OWNER) {
    const messages = MESSAGES.map((m) => (own.includes(m.id) ? { ...m, from: "owner" } : m));
    const state = { ...EMPTY_STATE, threads: { "alpha-0001": { t: "2026-11-11T09:00:00Z", id: "alpha-0001" } } };
    const data = base(me, me.isOwner ? state : null, [Q1]);
    data.push([keys.thread("alpha-0001"), { root: "alpha-0001", messages }]);
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, data);
  }
  const before = (a: Element, b: Element) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

  test("an own message last is not new; the line sits above the first message by another", () => {
    open(["beta-0004"]);
    const line = screen.getByText("New since " + hhmm("2026-11-11T09:00:00Z"));
    expect(before(line, screen.getByText("Read reply"))).toBe(true);
    expect(screen.getByRole("button", { name: "Mark read" })).toBeTruthy();
  });

  test("an own message first is folded with the read ones; the line sits above the next message by another", () => {
    open(["beta-0002"]);
    expect(screen.getByRole("button", { name: "Show 2 earlier messages" })).toBeTruthy();
    expect(screen.queryByText("Read reply")).toBeNull();
    const line = screen.getByText("New since " + hhmm("2026-11-11T09:00:00Z"));
    expect(before(line, screen.getByText("Fresh reply"))).toBe(true);
  });

  test("an own message between two others stays under the line", () => {
    open(["alpha-0003"]);
    const line = screen.getByText("New since " + hhmm("2026-11-11T09:00:00Z"));
    expect(before(line, screen.getByText("Read reply"))).toBe(true);
    expect(before(line, screen.getByText("Fresh reply"))).toBe(true);
  });

  test("a thread whose only new messages are the reader's own is read: no line, no mark read", () => {
    open(["beta-0002", "alpha-0003", "beta-0004"]);
    expect(screen.getByRole("button", { name: "Show 4 earlier messages" })).toBeTruthy();
    expect(screen.queryByText(/New since/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Mark read" })).toBeNull();
  });

  test("a viewer has no own messages", () => {
    open(["beta-0004"], VIEWER);
    expect(screen.getByRole("button", { name: "Mark read" })).toBeTruthy();
  });
});

describe("the mark race", () => {
  test("a reply that lands after the row was seen and before the write stays new", async () => {
    const { watch, watched } = fakeWatch();
    const { qc } = renderWith(
      <WatchContext value={watch}>
        <Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />
      </WatchContext>,
      base(OWNER, EMPTY_STATE, [Q1]),
    );
    const calls = spyFetch({ ...EMPTY_STATE, threads: { "alpha-0001": { t: "2026-11-11T09:30:00Z", id: "beta-0004" } } });
    const seen = watched[0]?.onSeen;
    // The new reply is drawn; the second that ran out was for beta-0004.
    await act(async () => {
      qc.setQueryData(keys.thread("alpha-0001"), {
        root: "alpha-0001",
        messages: [...MESSAGES, message("beta-0005", "2026-11-11T09:40:00Z", { from: "beta", body: "Late reply" })],
      });
    });
    await screen.findByText("Late reply");
    await act(async () => seen?.());
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([
      { thread: "alpha-0001", t: "2026-11-11T09:30:00Z", id: "beta-0004" },
    ]);
    expect(screen.getByRole("button", { name: "Mark read" })).toBeTruthy();
    expect(watched.at(-1)?.el.textContent).toContain("Late reply");
  });
});

describe("open ids", () => {
  test("mark all read refreshes a thread that an id link opened", async () => {
    const linked = thread("zeta-0001", { title: "A linked thread", unread: 1, last_t: "2026-11-11T09:55:00Z", last_id: "zeta-0002" });
    const msgs = [message("zeta-0001", "2026-11-11T08:00:00Z", { depth: 0 }), message("zeta-0002", "2026-11-11T09:55:00Z", { from: "beta" })];
    const data = base(OWNER, EMPTY_STATE, [Q1]);
    data.push([keys.thread("zeta-0001"), { root: "zeta-0001", thread: linked, messages: msgs }]);
    renderWith(<Stream sort="needs" open={["zeta-0001"]} onOpenChange={() => {}} />, data);
    expect(screen.getByText("1 new")).toBeTruthy();
    const after = { ...EMPTY_STATE, all_before: "2026-11-11T10:00:00Z" };
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
      if (init.method === "PUT") return json(after);
      if (url.startsWith("/api/thread/zeta-0001")) return json({ root: "zeta-0001", thread: { ...linked, unread: 0 }, messages: msgs });
      return new Promise<Response>(() => {});
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    await waitFor(() => expect(screen.queryByText("1 new")).toBeNull());
  });

  test("two fast clicks open two threads", () => {
    const onOpenChange = vi.fn();
    renderWith(<Stream sort="needs" open={[]} onOpenChange={onOpenChange} />, base(OWNER, EMPTY_STATE, [Q1, thread("beta-0009", { title: "Second" })]));
    fireEvent.click(screen.getByRole("button", { name: /Does the rebuild go on\?/ }));
    fireEvent.click(screen.getByRole("button", { name: /Second/ }));
    expect(onOpenChange).toHaveBeenLastCalledWith(["alpha-0001", "beta-0009"]);
  });

  test("an open thread that is not among the loaded ones shows above the list, open", () => {
    const onOpenChange = vi.fn();
    const linked = thread("zeta-0001", { title: "A thread on a later page" });
    const data = base(OWNER, EMPTY_STATE, [Q1]);
    data.push([keys.thread("zeta-0001"), { root: "zeta-0001", thread: linked, messages: [message("zeta-0001", "2026-11-11T08:00:00Z", { depth: 0, body: "Linked body" })] }]);
    renderWith(<Stream sort="needs" open={["zeta-0001"]} onOpenChange={onOpenChange} />, data);
    const row = screen.getByRole("button", { name: /A thread on a later page/ });
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Linked body")).toBeTruthy();
    const listed = screen.getByRole("button", { name: /Does the rebuild go on\?/ });
    expect(row.compareDocumentPosition(listed) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect((screen.getByRole("button", { name: "Fold all" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(listed);
    expect(onOpenChange).toHaveBeenCalledWith(["zeta-0001", "alpha-0001"]);
    fireEvent.click(row);
    expect(onOpenChange).toHaveBeenLastCalledWith(["alpha-0001"]);
  });

  test("an open id of a reply opens its thread in its place in the route", () => {
    const onOpenChange = vi.fn();
    const data = base(OWNER, EMPTY_STATE, [Q1]);
    data.push([keys.thread("beta-0002"), { root: "alpha-0001", thread: Q1, messages: MESSAGES }]);
    renderWith(<Stream sort="needs" open={["beta-0009", "beta-0002"]} onOpenChange={onOpenChange} />, data);
    expect(onOpenChange).toHaveBeenCalledWith(["beta-0009", "alpha-0001"]);
  });

  test("an id that no thread holds says so, and folds", async () => {
    const onOpenChange = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    for (const [key, value] of base(OWNER, EMPTY_STATE, [Q1])) qc.setQueryData(key, value);
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: "no thread holds the message" }), { status: 404 }));
    render(
      <QueryClientProvider client={qc}>
        <Stream sort="needs" open={["gone-0001"]} onOpenChange={onOpenChange} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("No thread holds gone-0001.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Fold" }));
    expect(onOpenChange).toHaveBeenCalledWith([]);
  });

  test("a duplicate id in the route counts once", () => {
    renderWith(
      <Conversations route={{ name: "conversations", sort: "new", open: ["alpha-0001", "alpha-0001"] }} />,
      base(OWNER, EMPTY_STATE, [Q1], "new"),
    );
    fireEvent.click(screen.getByRole("button", { name: /Does the rebuild go on\?/ }));
    expect(decodeURIComponent(window.location.hash)).toBe("#/conversations?sort=new");
  });
});

describe("Conversations", () => {
  test("opening a row puts it in the route and in sessionStorage; the route keeps 20 ids at most", () => {
    const open = Array.from({ length: 20 }, (_, i) => "beta-" + i);
    renderWith(<Conversations route={{ name: "conversations", sort: "new", open }} />, base(OWNER, EMPTY_STATE, [Q1, ...open.map((id) => thread(id))], "new"));
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
  test("open waiting rows show first, with the slot for their actions", () => {
    const rows = [{ id: "alpha-w0001", summary: "Rotate the key", body: "Before Friday.\n\nThe old one expires.", project: "alpha", since: "2026-11-11T08:00:00Z", done: null }];
    renderWith(
      <NeedsYou waitingActions={(row) => <span>{"done " + row.id}</span>} />,
      [...base(OWNER, EMPTY_STATE, [Q1]), [keys.section("waiting", ""), { pages: [{ rows, next: null, open: 1, total: 1 }], pageParams: [""] }]],
    );
    expect(screen.queryByText(/Nothing needs you/)).toBeNull();
    const row = screen.getByRole("button", { name: /Rotate the key/ });
    expect(screen.queryByText("The old one expires.")).toBeNull();
    fireEvent.click(row);
    expect(screen.queryByText("The old one expires.")).toBeTruthy();
    expect(screen.getByText("done alpha-w0001")).toBeTruthy();
  });

  test("a waiting row whose body repeats its summary shows that line once", () => {
    const rows = [{ id: "alpha-w0002", summary: "Renew the cert", body: "Renew the cert\n\nIt ends on Monday.", project: "alpha", since: "2026-11-11T08:00:00Z", done: null }];
    renderWith(<NeedsYou />, [...base(OWNER, EMPTY_STATE, [Q1]), [keys.section("waiting", ""), { pages: [{ rows, next: null, open: 1, total: 1 }], pageParams: [""] }]]);
    fireEvent.click(screen.getByRole("button", { name: /Renew the cert/ }));
    expect(screen.getAllByText("It ends on Monday.")).toHaveLength(2);
    expect(screen.getAllByText("Renew the cert")).toHaveLength(1);
  });

  test("the 51st open waiting row is one click away", async () => {
    const wait = (n: number) => ({ id: "alpha-w" + String(n).padStart(4, "0"), summary: "Task " + n, project: "alpha", since: "2026-11-11T08:00:00Z", done: null });
    const first = Array.from({ length: 50 }, (_, i) => wait(i + 1));
    // renderWith stubs fetch; the stub of this test comes after it.
    renderWith(<NeedsYou />, [...base(OWNER, EMPTY_STATE, [Q1]), [keys.section("waiting", ""), { pages: [{ rows: first, next: "50", open: 51, total: 51 }], pageParams: [""] }]]);
    const asked: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      asked.push(url);
      if (url.startsWith("/api/section/waiting") && url.includes("cursor=50")) {
        return new Response(JSON.stringify({ rows: [wait(51)], next: null, open: 51, total: 51 }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Promise<Response>(() => {});
    });
    expect(screen.queryByRole("button", { name: /Task 51/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 more waiting row" }));
    expect(await screen.findByRole("button", { name: /Task 51/ })).toBeTruthy();
    expect(asked.filter((u) => u.startsWith("/api/section/waiting"))).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /more waiting/ })).toBeNull();
  });

  test("a row that a shifted page repeats shows once, and the button stays while the server has a next page", () => {
    const wait = (n: number) => ({ id: "alpha-w" + String(n).padStart(4, "0"), summary: "Task " + n, project: "alpha", since: "2026-11-11T08:00:00Z", done: null });
    const pages = [
      { rows: Array.from({ length: 50 }, (_, i) => wait(i + 1)), next: "50", open: 52, total: 52 },
      { rows: [wait(50)], next: "51", open: 52, total: 52 },
    ];
    renderWith(<NeedsYou />, [...base(OWNER, EMPTY_STATE, [Q1]), [keys.section("waiting", ""), { pages, pageParams: ["", "50"] }]]);
    expect(screen.getAllByRole("button", { name: /Task 50(?!\d)/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: /more waiting/ })).toBeTruthy();
  });

  test("a failed load of more waiting rows says so and can be tried again", async () => {
    const wait = (n: number) => ({ id: "alpha-w" + String(n).padStart(4, "0"), summary: "Task " + n, project: "alpha", since: "2026-11-11T08:00:00Z", done: null });
    renderWith(<NeedsYou />, [...base(OWNER, EMPTY_STATE, [Q1]), [keys.section("waiting", ""), { pages: [{ rows: [wait(1)], next: "1", open: 2, total: 2 }], pageParams: [""] }]]);
    let calls = 0;
    vi.stubGlobal("fetch", async (url: string) => {
      if (!url.startsWith("/api/section/waiting")) return new Promise<Response>(() => {});
      calls++;
      const body = calls === 1 ? { error: "cannot read the log" } : { rows: [wait(2)], next: null, open: 2, total: 2 };
      return new Response(JSON.stringify(body), { status: calls === 1 ? 500 : 200, headers: { "Content-Type": "application/json" } });
    });
    fireEvent.click(screen.getByRole("button", { name: "Show 1 more waiting row" }));
    expect((await screen.findByRole("alert")).textContent).toContain("did not load");
    fireEvent.click(screen.getByRole("button", { name: "Show 1 more waiting row" }));
    expect(await screen.findByRole("button", { name: /Task 2/ })).toBeTruthy();
  });

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

describe("rows with fields of the wrong type", () => {
  // What a log line can hold: any field of any JSON type.
  const BAD_THREAD = { root: "alpha-0001", title: 7, preview: null, projects: "alpha", count: "3", last_t: "2026-11-11T09:30:00Z", last_id: "beta-0003", needs_owner: 1, unanswered: false, routine: false, hot: 0, kind: 5, owner_requests: 5 };
  const BAD_MESSAGES = [
    { id: "alpha-0001", depth: 0, quote: null, t: "2026-11-11T09:00:00Z", from: null, kind: 5, summary: 42, body: { plan: "keep" } },
    { id: "beta-0002", depth: 1, quote: null, t: 1731315000, from: "beta", to: "alpha", summary: "Fine", body: ["one", 2], replyTo: ["alpha-0001"] },
    { id: "beta-0003", depth: 2, quote: 9, t: "2026-11-11T09:30:00Z", from: "beta", kind: "request", summary: null, body: null, acked_by: "owner" },
  ];

  test("the stream and the open thread render them through the client", async () => {
    const answers: Record<string, unknown> = {
      "/api/threads?sort=needs": { threads: [BAD_THREAD], next: null, server_time: "2026-11-11T10:00:00Z" },
      "/api/thread/alpha-0001": { root: "alpha-0001", messages: BAD_MESSAGES },
      "/api/me": OWNER,
      "/api/read-state": EMPTY_STATE,
    };
    vi.stubGlobal("fetch", (url: string) =>
      url in answers ? Promise.resolve(new Response(JSON.stringify(answers[url]), { status: 200, headers: { "Content-Type": "application/json" } })) : new Promise(() => {}),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("42")).toBeTruthy();
    expect(screen.getByRole("button", { name: /alpha.*7/ })).toBeTruthy();
    expect(screen.getByText('{"plan":"keep"}')).toBeTruthy();
    expect(screen.getByText('["one",2]')).toBeTruthy();
  });

  test("a message that still fails to render shows its id; the rest of the thread stays", () => {
    const broken = { ...MESSAGES[1], id: "beta-0002", body: { not: "text" } } as unknown as Message;
    const data = base(OWNER, EMPTY_STATE, [Q1]).map(([k, v]): [QueryKey, unknown] =>
      k[0] === "thread" ? [k, { root: "alpha-0001", messages: [MESSAGES[0], broken, MESSAGES[3]] }] : [k, v],
    );
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderWith(<Stream sort="needs" open={["alpha-0001"]} onOpenChange={() => {}} />, data);
    expect(screen.getByText(/beta-0002 could not be shown/)).toBeTruthy();
    expect(screen.getByText("Newest reply")).toBeTruthy();
  });

  test("rowActions shows beside a folded thread row, outside its button, and not while it is open", () => {
    renderWith(
      <NeedsYou rowActions={(t) => <button type="button">{"row " + t.root}</button>} />,
      [...base(OWNER, EMPTY_STATE, [{ ...Q1, needs_owner: true }]), [keys.section("waiting", ""), { pages: [{ rows: [], next: null, open: 0, total: 0 }], pageParams: [""] }]],
    );
    const action = screen.getByRole("button", { name: "row alpha-0001" });
    const row = screen.getByRole("button", { name: /Does the rebuild go on\?/ });
    expect(row.contains(action)).toBe(false);
    fireEvent.click(row);
    expect(screen.queryByRole("button", { name: "row alpha-0001" })).toBeNull();
  });
});

describe("Mark all read feedback", () => {
  const unreadRows = () => [
    thread("alpha-0101", { title: "Old ask", needs_owner: true, unread: 0, last_t: "2026-11-11T08:00:00Z", last_id: "alpha-0101" }),
    thread("alpha-0102", { title: "New ask", needs_owner: true, unread: 2, last_t: "2026-11-11T09:40:00Z", last_id: "alpha-0103" }),
    thread("beta-0104", { title: "A sync", unread: 1, last_t: "2026-11-11T09:45:00Z", last_id: "beta-0104" }),
  ];
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  test("the button shows how many loaded threads are unread; with none it reads All read and is disabled", () => {
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, unreadRows()));
    const button = screen.getByRole("button", { name: "Mark all read (2)" });
    expect(button.hasAttribute("disabled")).toBe(false);
    cleanup();
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, [thread("alpha-0101", { unread: 0 })]));
    expect(screen.getByRole("button", { name: "All read" }).hasAttribute("disabled")).toBe(true);
  });

  test("a muted thread that is hidden does not count", () => {
    const state = { ...EMPTY_STATE, muted: ["beta-0104"] };
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(OWNER, state, unreadRows()));
    expect(screen.getByRole("button", { name: "Mark all read (1)" })).toBeTruthy();
  });

  test("an unread row with the needs you tag shows its count beside the tag", () => {
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, unreadRows()));
    const row = screen.getByRole("button", { name: /New ask/ });
    expect(within(row).getByText("needs you")).toBeTruthy();
    expect(within(row).getByText("2 new")).toBeTruthy();
  });

  test("after a click a polite status line says how many threads were marked, and the counts clear", async () => {
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, unreadRows()));
    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toBe("");
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit = {}) =>
      init.method === "PUT" ? json({ ...EMPTY_STATE, all_before: "2026-11-11T10:00:00Z", rev: 1 }) : new Promise<Response>(() => {}),
    );
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Mark all read (2)" })));
    await waitFor(() => expect(status.textContent).toBe("Marked 2 threads read"));
    // The server kept all_before: the rows read as read before the list loads again.
    expect(screen.queryAllByText(/^\d+ new$/)).toHaveLength(0);
    expect(screen.getByRole("button", { name: "All read" }).hasAttribute("disabled")).toBe(true);
  });

  test("the status line goes after a few seconds", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(VIEWER, null, [Q1]));
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Mark all read (1)" })));
      expect(screen.getByRole("status").textContent).toBe("Marked 1 thread read");
      act(() => vi.advanceTimersByTime(STATUS_MS));
      expect(screen.getByRole("status").textContent).toBe("");
    } finally {
      vi.useRealTimers();
    }
  });

  test("the status line goes on the next action", async () => {
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(VIEWER, null, [Q1]));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Mark all read (1)" })));
    expect(screen.getByRole("status").textContent).toBe("Marked 1 thread read");
    fireEvent.click(screen.getByRole("button", { name: /Does the rebuild go on/ }));
    expect(screen.getByRole("status").textContent).toBe("");
  });

  test("a failed write says Not saved and offers a retry that sends again", async () => {
    renderWith(<Stream sort="needs" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, unreadRows()));
    let puts = 0;
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit = {}) => {
      if (init.method !== "PUT") return new Promise<Response>(() => {});
      puts += 1;
      return puts === 1 ? json({ error: "down" }, 500) : json({ ...EMPTY_STATE, all_before: "2026-11-11T10:00:00Z", rev: 1 });
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Mark all read (2)" })));
    const status = screen.getByRole("status");
    await waitFor(() => expect(status.textContent).toMatch(/^Not saved/));
    await act(async () => fireEvent.click(within(status).getByRole("button", { name: "Retry" })));
    expect(puts).toBe(2);
    await waitFor(() => expect(status.textContent).toBe("Marked 2 threads read"));
  });
});

describe("the view New", () => {
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  const READ = thread("beta-0201", { title: "Already read", unread: 0, last_t: "2026-11-11T09:00:00Z", last_id: "beta-0201" });
  const FRESH = thread("alpha-0202", { title: "Fresh one", unread: 1, last_t: "2026-11-11T09:40:00Z", last_id: "alpha-0202" });

  test("the owner's list asks the server for the unread threads only", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", (url: string) => {
      urls.push(url);
      return new Promise(() => {});
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(keys.me, OWNER);
    render(
      <QueryClientProvider client={qc}>
        <Stream sort="new" open={[]} onOpenChange={() => {}} />
      </QueryClientProvider>,
    );
    expect(urls).toContain("/api/threads?sort=new&unread=1");
  });

  test("a viewer's list asks for every thread and shows only the unread ones", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", (url: string) => {
      urls.push(url);
      return new Promise(() => {});
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(keys.me, VIEWER);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ threads: {}, muted: [], all_before: "2026-11-11T09:30:00Z" }));
    render(
      <QueryClientProvider client={qc}>
        <Stream sort="new" open={[]} onOpenChange={() => {}} />
      </QueryClientProvider>,
    );
    expect(urls).toContain("/api/threads?sort=new");
    await act(async () => qc.setQueryData(keys.threads("new"), threads(FRESH, READ)));
    expect(await screen.findByText("Fresh one")).toBeTruthy();
    expect(screen.queryByText("Already read")).toBeNull();
  });

  test("after mark all read the list is empty with its empty state, and a thread with a new post comes back", async () => {
    const { qc } = renderWith(<Stream sort="new" open={[]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, [FRESH, READ], "new"));
    expect(screen.getByText("Fresh one")).toBeTruthy();
    expect(screen.queryByText("Already read")).toBeNull();
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit = {}) =>
      init.method === "PUT" ? json({ ...EMPTY_STATE, all_before: "2026-11-11T10:00:00Z", rev: 1 }) : new Promise<Response>(() => {}),
    );
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    await waitFor(() => expect(screen.getByText("All read. New posts show here.")).toBeTruthy());
    expect(screen.queryByText("Fresh one")).toBeNull();
    const later = { ...FRESH, unread: 1, last_t: "2026-11-11T10:05:00Z", last_id: "beta-0203" };
    await act(async () => qc.setQueryData(keys.threads("new"), { pages: [{ threads: [later], next: null, server_time: "2026-11-11T10:06:00Z" }], pageParams: [""] }));
    await waitFor(() => expect(screen.getByText("Fresh one")).toBeTruthy());
    expect(screen.queryByText("All read. New posts show here.")).toBeNull();
  });

  test("an open thread stays listed in its place after it is read, until the view loads again", async () => {
    const { qc } = renderWith(<Stream sort="new" open={["alpha-0202"]} onOpenChange={() => {}} />, base(OWNER, EMPTY_STATE, [FRESH, READ], "new"));
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit = {}) =>
      init.method === "PUT" ? json({ ...EMPTY_STATE, all_before: "2026-11-11T10:00:00Z", rev: 1 }) : new Promise<Response>(() => {}),
    );
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /^Mark all read/ })));
    // The server's next list leaves the read thread out; the open row stays where it was.
    await act(async () => qc.setQueryData(keys.threads("new"), { pages: [{ threads: [], next: null, server_time: "2026-11-11T10:01:00Z" }], pageParams: [""] }));
    await act(() => new Promise((ok) => setTimeout(ok, 20)));
    const list = screen.getByRole("list", { name: "Conversations" });
    expect(within(list).getByRole("button", { name: /Fresh one/ }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.queryByRole("list", { name: "Opened threads" })).toBeNull();
  });

  test("the tab New shows the unread count, and plain New at zero", () => {
    renderWith(<SortBar sort="needs" open={[]} unread={12} />);
    expect(screen.getByRole("link", { name: "New (12)" })).toBeTruthy();
    cleanup();
    renderWith(<SortBar sort="needs" open={[]} unread={0} />);
    expect(screen.getByRole("link", { name: "New" })).toBeTruthy();
  });
});
