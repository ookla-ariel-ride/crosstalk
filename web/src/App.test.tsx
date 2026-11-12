// @vitest-environment jsdom
// The join of the three feature folders: the owner's controls inside the
// Conversations page, drafts that outlive their row, and mute.
import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { keys } from "./api/queries";
import type { Me, Message, ReadState, Snapshot } from "./api/types";
import { page } from "./App";
import { drafts } from "./features/owner/drafts";
import type { Route } from "./logic/route";
import { renderWith, thread, threads } from "./ui/testing";

const OWNER: Me = { name: "Owner", isOwner: true, canEdit: true, viewer: "owner" };
const VIEWER: Me = { name: null, isOwner: false, canEdit: false, viewer: "viewer" };
const EMPTY: ReadState = { threads: {}, muted: [], all_before: null };

const SNAP: Snapshot = {
  server_time: "2026-11-11T10:00:00Z",
  roster: {
    alpha: { id: "alpha", name: "Alpha", kind: "project", color: "#2e7d32", trust: "approved" },
    beta: { id: "beta", name: "Beta", kind: "agent", parent: "alpha", trust: "quarantined" },
  },
  collections: { messages: {} },
};

const MESSAGES: Message[] = [
  { id: "alpha-0001", t: "2026-11-11T09:00:00Z", depth: 0, quote: null, from: "alpha", kind: "question", summary: "Which store?", body: "Pick one.\n\na. Keep the log\nb. Use SQLite" },
  { id: "beta-0002", t: "2026-11-11T09:10:00Z", depth: 1, quote: null, from: "beta", kind: "note", summary: "I can help", body: "I can help", replyTo: "alpha-0001" },
];
const Q1 = thread("alpha-0001", { title: "Which store?", count: 2, last_t: "2026-11-11T09:10:00Z", last_id: "beta-0002", projects: ["alpha", "beta"] });
const Q2 = thread("beta-0009", { title: "Another thread" });

const route = (open: string[] = ["alpha-0001"]): Route => ({ name: "conversations", sort: "needs", open });

function data(me: Me, state: ReadState = EMPTY): [readonly unknown[], unknown][] {
  const out: [readonly unknown[], unknown][] = [
    [keys.roster, SNAP],
    [keys.me, me],
    [keys.threads("needs"), threads(Q1, Q2)],
    [keys.thread("alpha-0001"), { root: "alpha-0001", messages: MESSAGES }],
  ];
  if (me.isOwner) out.push([keys.readState, state]);
  return out;
}

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/** A fake server: each write takes the next answer of `answers` for its URL; a read waits for ever. */
function serve(answers: Record<string, [number, unknown][]>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const hit = method === "GET" ? undefined : answers[url]?.shift();
    if (!hit) return new Promise(() => {});
    return Promise.resolve(new Response(JSON.stringify(hit[1]), { status: hit[0], headers: { "Content-Type": "application/json" } }));
  });
  return calls;
}
const writes = (calls: Call[], url: string) => calls.filter((c) => c.method !== "GET" && c.url === url);
const article = (text: string) => screen.getByText(text, { selector: "p" }).closest("article") as HTMLElement;

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(56);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { x: 0, y: 0, top: 0, left: 0, right: 780, bottom: 56, width: 780, height: 56, toJSON: () => ({}) },
  );
  localStorage.clear();
  sessionStorage.clear();
  window.location.hash = "";
  drafts.clear();
});
afterEach(() => vi.restoreAllMocks());

describe("owner controls in an open thread", () => {
  test("the owner sees reply, answer and approve on each message", () => {
    renderWith(page(route()), data(OWNER));
    const question = article("Pick one.");
    expect(within(question).getByRole("button", { name: "Keep the log" })).toBeTruthy();
    expect(within(question).getByRole("button", { name: "Write an answer" })).toBeTruthy();
    expect(within(question).getByRole("button", { name: "Reply" })).toBeTruthy();
    const note = article("I can help");
    expect(within(note).getByRole("button", { name: "Reply" })).toBeTruthy();
    expect(within(note).getByRole("button", { name: "Approve Beta" })).toBeTruthy();
  });

  test("a viewer sees no owner control", () => {
    renderWith(page(route()), data(VIEWER));
    for (const name of ["Reply", "Keep the log", "Write an answer", "Approve Beta", "Record as decision", "Start a thread"])
      expect(screen.queryByRole("button", { name })).toBeNull();
  });

  test("approve sends the participant's trust once", async () => {
    renderWith(page(route()), data(OWNER));
    const calls = serve({ "/api/append": [[201, {}]] });
    const approve = within(article("I can help")).getByRole("button", { name: "Approve Beta" });
    fireEvent.click(approve);
    fireEvent.click(approve);
    await waitFor(() => expect(writes(calls, "/api/append")).toHaveLength(1));
    expect(writes(calls, "/api/append")[0]?.body).toMatchObject({ op: "update", collection: "participants", id: "beta", data: { trust: "approved" } });
  });

  test("the owner controls share one roster query, however many messages show", () => {
    const observers = (messages: Message[]) => {
      const list = data(OWNER);
      list[3] = [keys.thread("alpha-0001"), { root: "alpha-0001", messages }];
      const { qc, unmount } = renderWith(page(route()), list);
      expect(screen.getAllByRole("button", { name: "Reply" }).length).toBe(messages.length + 1);
      const n = qc.getQueryCache().find({ queryKey: keys.roster })?.getObserversCount();
      unmount();
      return n;
    };
    const more = Array.from({ length: 8 }, (_, i): Message => ({ ...MESSAGES[1]!, id: `beta-01${i}`, t: `2026-11-11T09:2${i}:00Z`, summary: "Note " + i, body: "Note " + i }));
    expect(observers([...MESSAGES, ...more])).toBe(observers(MESSAGES));
  });
});

describe("drafts", () => {
  test("a reply sends once and its draft is gone after the success", async () => {
    renderWith(page(route()), data(OWNER));
    const calls = serve({ "/api/append": [[201, {}]] });
    fireEvent.click(within(article("I can help")).getByRole("button", { name: "Reply" }));
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Thanks." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.queryByLabelText("Message")).toBeNull());
    expect(writes(calls, "/api/append")).toHaveLength(1);
    expect(writes(calls, "/api/append")[0]?.body).toMatchObject({ data: { replyTo: "beta-0002", summary: "Thanks." } });
    fireEvent.click(within(article("I can help")).getByRole("button", { name: "Reply" }));
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("");
  });

  test("a failed send keeps the draft, and the draft survives an unmount of its row", async () => {
    const { rerender, qc } = renderWith(page(route()), data(OWNER));
    const calls = serve({ "/api/append": [[500, { error: "disk full" }], [201, {}]] });
    fireEvent.click(within(article("I can help")).getByRole("button", { name: "Reply" }));
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Later." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/disk full/)).toBeTruthy();
    // Fold the thread: its rows unmount. Open it again: the composer comes back with the draft.
    rerender(<QueryClientProvider client={qc}>{page(route([]))}</QueryClientProvider>);
    expect(screen.queryByLabelText("Message")).toBeNull();
    rerender(<QueryClientProvider client={qc}>{page(route())}</QueryClientProvider>);
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("Later.");
    // The retry rewrites the same row.
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(writes(calls, "/api/append")).toHaveLength(2));
    const [a, b] = writes(calls, "/api/append").map((c) => (c.body as { id: string }).id);
    expect(b).toBe(a);
  });
  test("a draft of one target does not show in another target's composer", () => {
    renderWith(page(route()), data(OWNER));
    fireEvent.click(within(article("I can help")).getByRole("button", { name: "Reply" }));
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "For beta only." } });
    fireEvent.click(within(article("Pick one.")).getByRole("button", { name: "Reply" }));
    const boxes = screen.getAllByLabelText("Message") as HTMLTextAreaElement[];
    expect(boxes.map((b) => b.value).sort()).toEqual(["", "For beta only."]);
    fireEvent.click(within(article("Pick one.")).getByRole("button", { name: "Write an answer" }));
    expect((screen.getByLabelText("Your answer") as HTMLTextAreaElement).value).toBe("");
  });

  test("an answer sent with a choice leaves no answer draft behind", async () => {
    renderWith(page(route()), data(OWNER));
    serve({ "/api/append": [[201, {}]] });
    fireEvent.click(within(article("Pick one.")).getByRole("button", { name: "Write an answer" }));
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "Half written" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await waitFor(() => expect(screen.getByText("Answer sent")).toBeTruthy());
    // Only the mark that it was sent stays, with no text.
    expect(drafts.get("answer:alpha-0001")).toMatchObject({ subject: "", text: "", answered: true });
  });
});

describe("mute", () => {
  test("mute hides the thread behind the muted line, unmute brings it back, each with one write", async () => {
    renderWith(page(route()), data(OWNER));
    const calls = serve({ "/api/read-state": [[200, { ...EMPTY, muted: ["alpha-0001"] }], [200, EMPTY]] });
    const mute = screen.getByRole("button", { name: "Mute thread" });
    await act(async () => {
      fireEvent.click(mute);
      fireEvent.click(mute);
    });
    const line = await screen.findByRole("button", { name: /1 muted/ });
    expect(screen.queryByRole("button", { name: /Which store\?/ })).toBeNull();
    fireEvent.click(line);
    expect(screen.getByRole("button", { name: /Which store\?/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Unmute thread" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /1 muted/ })).toBeNull());
    expect(screen.getByRole("button", { name: /Which store\?/ })).toBeTruthy();
    expect(writes(calls, "/api/read-state").map((c) => c.body)).toEqual([
      { thread: "alpha-0001", muted: true },
      { thread: "alpha-0001", muted: false },
    ]);
  });

  test("a failed mute says so next to the button and leaves the thread shown", async () => {
    renderWith(page(route()), data(OWNER));
    serve({ "/api/read-state": [[500, { error: "disk full" }]] });
    fireEvent.click(screen.getByRole("button", { name: "Mute thread" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/not muted.*try again/i);
    expect(screen.getByRole("button", { name: "Mute thread" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /1 muted/ })).toBeNull();
  });

  test("a viewer's mute stays in this browser", async () => {
    renderWith(page(route()), data(VIEWER));
    const calls = serve({});
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Mute thread" })));
    expect(writes(calls, "/api/read-state")).toHaveLength(0);
    expect(JSON.parse(localStorage.getItem("crosstalk.read-state") ?? "null").muted).toEqual(["alpha-0001"]);
    expect(screen.getByRole("button", { name: /1 muted/ })).toBeTruthy();
  });
});

describe("new thread", () => {
  test("start a thread shows at the top of Conversations for the owner only", () => {
    renderWith(page(route([])), data(OWNER));
    const start = screen.getByRole("button", { name: "Start a thread" });
    const heading = screen.getByRole("heading", { level: 1, name: "Conversations" });
    expect(start.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test("a viewer gets no start a thread", () => {
    renderWith(page(route([])), data(VIEWER));
    expect(screen.queryByRole("button", { name: "Start a thread" })).toBeNull();
  });
  test("a new thread's draft keeps its kind and recipients across an unmount", async () => {
    const { rerender, qc } = renderWith(page(route([])), data(OWNER));
    const calls = serve({ "/api/append": [[201, {}]] });
    fireEvent.click(screen.getByRole("button", { name: "Start a thread" }));
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "request" } });
    fireEvent.click(screen.getByLabelText("Beta"));
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Can you check the queue?" } });
    rerender(<QueryClientProvider client={qc}>{page({ name: "people" })}</QueryClientProvider>);
    rerender(<QueryClientProvider client={qc}>{page(route([]))}</QueryClientProvider>);
    expect((screen.getByLabelText("Kind") as HTMLSelectElement).value).toBe("request");
    expect((screen.getByLabelText("Beta") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("Can you check the queue?");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(writes(calls, "/api/append")).toHaveLength(1));
    expect(writes(calls, "/api/append")[0]?.body).toMatchObject({ data: { kind: "request", to: ["beta"] } });
  });
});
