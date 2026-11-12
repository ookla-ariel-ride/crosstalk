// @vitest-environment jsdom
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { keys } from "../../api/queries";
import type { Me, Message, Snapshot, ThreadDetail } from "../../api/types";
import type { ReactNode } from "react";
import { renderWith as renderBare, thread } from "../../ui/testing";
import { drafts } from "./drafts";
import { HoldButton, NewThread, ownerActions, OwnerScope, People, SignIn } from "./index";

/** The page puts every owner control inside one OwnerScope. */
const renderWith = (ui: ReactNode, data?: Parameters<typeof renderBare>[1]) => renderBare(<OwnerScope>{ui}</OwnerScope>, data);

const OWNER: Me = { name: "Owner", isOwner: true, canEdit: true, viewer: "owner" };
const VIEWER: Me = { name: null, isOwner: false, canEdit: false, viewer: "viewer" };
// Made-up secrets: none of them is a real PIN or token.
const PIN = "246810";

const SNAP: Snapshot = {
  server_time: "2026-11-11T10:00:00Z",
  roster: {
    alpha: { id: "alpha", name: "Alpha", kind: "project", color: "#2e7d32", harness: "codex", trust: "approved" },
    beta: { id: "beta", name: "Beta", kind: "agent", parent: "alpha", trust: "quarantined" },
  },
  collections: {
    messages: {
      "alpha-1": { t: "2026-11-11T09:54:00Z", from: "alpha", kind: "check-in", model: "gpt-5.5" },
      "beta-1": { t: "2026-11-11T07:00:00Z", from: "beta", kind: "note" },
    },
    waiting: { "own-7": { since: "2026-11-11T09:00:00Z", summary: "Renew", done: null } },
    decisions: { "d-from-alpha-9": { summary: "Done before" } },
  },
};

const QUESTION: Message = {
  id: "alpha-0001",
  depth: 0,
  quote: null,
  from: "alpha",
  kind: "question",
  summary: "Which store?",
  body: "a. Keep the log\nb. Use SQLite (recommended)",
};
const DETAIL: ThreadDetail = { root: "alpha-0001", messages: [QUESTION] };
const ANSWERED: ThreadDetail = {
  root: "alpha-0001",
  messages: [QUESTION, { id: "own-1", depth: 1, quote: null, from: "owner", kind: "owner", summary: "Keep the log", replyTo: "alpha-0001" }],
};

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/** A fake server: `answers` maps a path to [status, JSON]; anything else waits for ever. */
function serve(answers: Record<string, [number, unknown]>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", (url: string, init: RequestInit = {}) => {
    calls.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
    const hit = answers[url];
    if (!hit) return new Promise(() => {});
    return Promise.resolve(new Response(JSON.stringify(hit[1]), { status: hit[0], headers: { "Content-Type": "application/json" } }));
  });
  return calls;
}

const posts = (calls: Call[], url: string) => calls.filter((c) => c.method === "POST" && c.url === url);

function renderActions(me: Me | undefined, message: Message | null = QUESTION, detail = DETAIL) {
  const data: [readonly unknown[], unknown][] = [
    [keys.roster, SNAP],
    [keys.thread("alpha-0001"), detail],
  ];
  if (me) data.push([keys.me, me]);
  return renderWith(<>{ownerActions(thread("alpha-0001", { kind: "question" }), message)}</>, data);
}

beforeEach(() => {
  drafts.clear();
  localStorage.clear();
  sessionStorage.clear();
  // jsdom has no layout: give the list a 600 px view and each row 56 px.
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(56);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { x: 0, y: 0, top: 0, left: 0, right: 780, bottom: 56, width: 780, height: 56, toJSON: () => ({}) },
  );
  return () => vi.restoreAllMocks();
});

describe("answer buttons", () => {
  test("answer button sends the choice text", async () => {
    renderActions(OWNER);
    const calls = serve({ "/api/append": [201, {}] });
    const recommended = screen.getByRole("button", { name: /Use SQLite/ });
    expect(recommended.textContent).toContain("Recommended");
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(1));
    expect(posts(calls, "/api/append")[0]?.body).toMatchObject({
      collection: "messages",
      data: { from: "owner", kind: "owner", summary: "Keep the log", replyTo: "alpha-0001", to: ["alpha"] },
    });
  });

  test("a double click sends once, and the question shows as answered from the refetched thread", async () => {
    renderActions(OWNER);
    const calls = serve({ "/api/append": [201, {}], "/api/thread/alpha-0001": [200, ANSWERED] });
    const button = screen.getByRole("button", { name: "Keep the log" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText("Answered")).toBeTruthy();
    expect(posts(calls, "/api/append")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Keep the log" })).toBeNull();
  });

  test("write an answer opens the composer", async () => {
    renderActions(OWNER);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Write an answer" }));
    const box = screen.getByLabelText("Your answer");
    expect(document.activeElement).toBe(box);
    fireEvent.change(box, { target: { value: "Neither\nWe wait a week." } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(1));
    expect(posts(calls, "/api/append")[0]?.body).toMatchObject({ data: { summary: "Neither", body: "We wait a week." } });
    // A sent answer leaves no text in the draft store, only the mark that it was sent.
    await waitFor(() => expect(drafts.get("answer:alpha-0001")).toMatchObject({ subject: "", text: "", answered: true }));
  });
});

describe("who sees the controls", () => {
  test("viewer sees no owner controls", () => {
    renderActions(VIEWER);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("#/people");
  });

  test("while the role loads, no control and no link show", () => {
    renderActions(undefined);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });

  test("the people page shows a viewer the sign-in form and no approve or add control", () => {
    renderWith(<People />, [
      [keys.me, VIEWER],
      [keys.roster, SNAP],
    ]);
    expect(screen.getByLabelText("PIN")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Beta/ }));
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add participant" })).toBeNull();
  });
});

describe("reply, record, mark done", () => {
  const NOTE: Message = { id: "own-7", depth: 1, quote: null, from: "beta", kind: "note", summary: "Renew the key" };

  test("a failed reply keeps the draft and shows the error", async () => {
    renderActions(OWNER, NOTE);
    serve({ "/api/append": [400, { error: "to names no participant" }] });
    fireEvent.click(screen.getByRole("button", { name: "Reply" }));
    const box = screen.getByLabelText("Message");
    fireEvent.change(screen.getByLabelText("Summary"), { target: { value: "Agreed" } });
    fireEvent.change(box, { target: { value: "Go ahead." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/to names no participant/)).toBeTruthy();
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("Go ahead.");
    expect((screen.getByLabelText("Summary") as HTMLInputElement).value).toBe("Agreed");
    // Escape keeps a composer that holds a draft.
    fireEvent.keyDown(box, { key: "Escape" });
    expect(screen.getByLabelText("Message")).toBeTruthy();
  });

  test("a 403 shows sign in again next to the control and keeps the draft", async () => {
    renderActions(OWNER, NOTE);
    serve({ "/api/append": [403, { error: "only the owner may write this" }] });
    fireEvent.click(screen.getByRole("button", { name: "Reply" }));
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Later." } });
    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Enter", metaKey: true });
    expect(await screen.findByRole("link", { name: "Sign in again" })).toBeTruthy();
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("Later.");
  });

  test("an empty composer closes on Escape", () => {
    renderActions(OWNER, NOTE);
    fireEvent.click(screen.getByRole("button", { name: "Reply" }));
    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Escape" });
    expect(screen.queryByLabelText("Message")).toBeNull();
  });

  test("record as decision and mark done show where the present page offers them", async () => {
    renderActions(OWNER, NOTE);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Mark done" }));
    fireEvent.click(screen.getByRole("button", { name: "Record as decision" }));
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(2));
    expect(posts(calls, "/api/append").map((c) => (c.body as { collection: string }).collection).sort()).toEqual(["decisions", "waiting"]);
    expect(await screen.findByRole("button", { name: "Recorded" })).toBeTruthy();
  });

  test("a message recorded before shows recorded, and a question shows no record button twice", () => {
    renderActions(OWNER, { ...NOTE, id: "alpha-9" });
    expect((screen.getByRole("button", { name: "Recorded" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Mark done" })).toBeNull();
  });
});

describe("sign in", () => {
  test("sign in with a wrong PIN shows the server message", async () => {
    renderWith(<SignIn />, [[keys.me, VIEWER]]);
    const calls = serve({ "/api/login": [401, { error: "that pin or token is not valid" }] });
    const field = screen.getByLabelText("PIN") as HTMLInputElement;
    expect(field.type).toBe("password");
    expect(field.getAttribute("autocomplete")).toBe("current-password");
    expect(field.getAttribute("inputmode")).toBe("numeric");
    fireEvent.change(field, { target: { value: PIN } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/that pin or token is not valid/)).toBeTruthy();
    expect(posts(calls, "/api/login")[0]?.body).toEqual({ pin: PIN });
    expect(calls.every((c) => !c.url.includes(PIN))).toBe(true);
    expect(field.value).toBe("");
    expect(JSON.stringify({ ...localStorage })).not.toContain(PIN);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(PIN);
  });

  test("a good sign-in refetches the role and holds the secret nowhere", async () => {
    const { qc } = renderWith(<People />, [
      [keys.me, VIEWER],
      [keys.roster, SNAP],
    ]);
    const calls = serve({ "/api/login": [200, { ok: true, viewer: "owner" }], "/api/me": [200, OWNER] });
    fireEvent.click(screen.getByRole("button", { name: "Use the owner token" }));
    const field = screen.getByLabelText("Owner token") as HTMLInputElement;
    expect(field.getAttribute("inputmode")).toBeNull();
    fireEvent.change(field, { target: { value: "example-token-value" } });
    fireEvent.submit(field.closest("form") as HTMLFormElement);
    expect(await screen.findByText("You are signed in as the owner.")).toBeTruthy();
    expect(posts(calls, "/api/login")[0]?.body).toEqual({ token: "example-token-value" });
    // The form, and the field that held the token, are gone.
    expect(screen.queryByLabelText("Owner token")).toBeNull();
    expect(screen.getByRole("button", { name: "Add participant" })).toBeTruthy();
    expect(JSON.stringify(qc.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain("example-token-value");
    expect(JSON.stringify(qc.getMutationCache().getAll().map((m) => m.state))).not.toContain("example-token-value");
    expect(localStorage.length + sessionStorage.length).toBe(0);
  });
});

describe("people", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-11T10:00:00Z"));
    return () => vi.useRealTimers();
  });

  test("the roster shows harness, model family, last seen and trust in words", () => {
    renderWith(<People />, [
      [keys.me, OWNER],
      [keys.roster, SNAP],
      [keys.vitals, { disk_free_gb: 1, usage: null, usage_limit: null, build_host: null, model_families: [["gpt-5", "Example family"]] }],
    ]);
    expect(screen.getByText("project, Codex, Example family")).toBeTruthy();
    expect(screen.getByText("6 min")).toBeTruthy();
    // beta has no harness of its own and takes its parent's.
    expect(screen.getByText("agent, Codex, Not stated, not approved")).toBeTruthy();
  });

  test("the owner approves a participant", async () => {
    renderWith(<People />, [
      [keys.me, OWNER],
      [keys.roster, SNAP],
    ]);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: /Beta/ }));
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(1));
    expect(posts(calls, "/api/append")[0]?.body).toEqual({
      from: "owner",
      op: "update",
      collection: "participants",
      id: "beta",
      data: { trust: "approved" },
    });
  });

  test("the owner adds a participant and sees the kit and the token once", async () => {
    renderWith(<People />, [
      [keys.me, OWNER],
      [keys.roster, SNAP],
    ]);
    const calls = serve({ "/api/onboard": [200, { token: "made-up-token", kit: "kit text for relay-bot" }] });
    fireEvent.click(screen.getByRole("button", { name: "Add participant" }));
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "relay-bot" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Relay bot" } });
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "tool" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
    });
    expect(posts(calls, "/api/onboard")[0]?.body).toEqual({ id: "relay-bot", name: "Relay bot", kind: "tool" });
    expect((screen.getByLabelText("Kit") as HTMLTextAreaElement).value).toBe("kit text for relay-bot");
    expect((screen.getByLabelText("Token (shown once)") as HTMLInputElement).value).toBe("made-up-token");
    expect(localStorage.length + sessionStorage.length).toBe(0);
    // Close and open again: the kit and the token are gone.
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Add participant" }));
    expect(screen.queryByLabelText("Kit")).toBeNull();
    expect(screen.queryByLabelText("Token (shown once)")).toBeNull();
    expect(document.body.textContent).not.toContain("made-up-token");
  });

  test("a failed add keeps the form and shows the server message", async () => {
    renderWith(<People />, [
      [keys.me, OWNER],
      [keys.roster, SNAP],
    ]);
    serve({ "/api/onboard": [400, { error: "id alpha is taken" }] });
    fireEvent.click(screen.getByRole("button", { name: "Add participant" }));
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "alpha" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Alpha" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText(/id alpha is taken/)).toBeTruthy();
    expect((screen.getByLabelText("Id") as HTMLInputElement).value).toBe("alpha");
  });
});

describe("start a thread and hold", () => {
  test("the owner starts a thread to one participant with a kind", async () => {
    renderWith(<NewThread />, [
      [keys.me, OWNER],
      [keys.roster, SNAP],
    ]);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Start a thread" }));
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "request" } });
    fireEvent.click(screen.getByLabelText("Beta"));
    fireEvent.change(screen.getByLabelText("Summary"), { target: { value: "Post your questions" } });
    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(screen.queryByLabelText("Message")).toBeNull());
    expect(posts(calls, "/api/append")[0]?.body).toMatchObject({
      collection: "messages",
      data: { from: "owner", kind: "request", summary: "Post your questions", body: "", to: ["beta"] },
    });
  });

  test("a viewer gets no start button", () => {
    renderWith(<NewThread />, [[keys.me, VIEWER]]);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  test("hold sends the hold patch, and a viewer sees no hold button", async () => {
    const { unmount } = renderWith(<HoldButton agreement={{ id: "a-1" }} held={false} />, [[keys.me, VIEWER]]);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    unmount();
    renderWith(<HoldButton agreement={{ id: "a-1" }} held={false} />, [[keys.me, OWNER]]);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Hold" }));
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(1));
    expect(posts(calls, "/api/append")[0]?.body).toMatchObject({ collection: "agreements", op: "update", data: { hold: true } });
  });
});

describe("one answer for one question", () => {
  test("a click after the send resolved and before the refetch sends nothing", async () => {
    // The thread refetch never lands: the question stays open in the cache.
    renderActions(OWNER);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    expect(await screen.findByText("Answer sent")).toBeTruthy();
    const again = screen.getByRole("button", { name: /Use SQLite/ }) as HTMLButtonElement;
    expect(again.disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Write an answer" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(again);
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await act(async () => {});
    expect(posts(calls, "/api/append")).toHaveLength(1);
  });

  test("an answer written in the composer also locks the choices", async () => {
    renderActions(OWNER);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Write an answer" }));
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "Neither" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Answer sent")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await act(async () => {});
    expect(posts(calls, "/api/append")).toHaveLength(1);
  });

  test("a written answer on its way shuts the choices", async () => {
    renderActions(OWNER);
    const calls = serve({});
    fireEvent.click(screen.getByRole("button", { name: "Write an answer" }));
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "Neither" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(1));
    const choice = screen.getByRole("button", { name: "Keep the log" }) as HTMLButtonElement;
    expect(choice.disabled).toBe(true);
    fireEvent.click(choice);
    await act(async () => {});
    expect(posts(calls, "/api/append")).toHaveLength(1);
  });

  test("a choice on its way shuts the open composer, with no error", async () => {
    renderActions(OWNER);
    const calls = serve({});
    fireEvent.click(screen.getByRole("button", { name: "Write an answer" }));
    const box = screen.getByLabelText("Your answer") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "Neither" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(1));
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
    expect(box.disabled).toBe(true);
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    await act(async () => {});
    expect(posts(calls, "/api/append")).toHaveLength(1);
    expect(posts(calls, "/api/append")[0]?.body).toMatchObject({ data: { summary: "Keep the log" } });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("a failed choice keeps its id when the row mounts again", async () => {
    // renderWith stubs fetch, so each serve comes after a render.
    const first = renderActions(OWNER);
    const failed = serve({ "/api/append": [500, { error: "cannot write the log: disk full" }] });
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await screen.findByRole("alert");
    first.unmount();
    renderActions(OWNER);
    const again = serve({ "/api/append": [201, {}] });
    // Only the id is kept: the composer does not open by itself.
    expect(screen.queryByLabelText("Your answer")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await waitFor(() => expect(posts(again, "/api/append")).toHaveLength(1));
    expect((posts(again, "/api/append")[0]?.body as { id: string }).id).toBe((posts(failed, "/api/append")[0]?.body as { id: string }).id);
  });

  test("a failed written answer, then Cancel, keeps the id for a later choice", async () => {
    const first = renderActions(OWNER);
    const failed = serve({ "/api/append": [500, { error: "cannot write the log: disk full" }] });
    fireEvent.click(screen.getByRole("button", { name: "Write an answer" }));
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "Neither" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    first.unmount();
    renderActions(OWNER);
    const again = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await waitFor(() => expect(posts(again, "/api/append")).toHaveLength(1));
    expect((posts(again, "/api/append")[0]?.body as { id: string }).id).toBe((posts(failed, "/api/append")[0]?.body as { id: string }).id);
  });

  test("a sent answer keeps the controls shut when the row mounts again before the refetch", async () => {
    const first = renderActions(OWNER);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    expect(await screen.findByText("Answer sent")).toBeTruthy();
    first.unmount();
    renderActions(OWNER);
    const again = serve({ "/api/append": [201, {}] });
    expect(screen.getByText("Answer sent")).toBeTruthy();
    const choice = screen.getByRole("button", { name: /Use SQLite/ }) as HTMLButtonElement;
    expect(choice.disabled).toBe(true);
    fireEvent.click(choice);
    await act(async () => {});
    expect(posts(calls, "/api/append").length + posts(again, "/api/append").length).toBe(1);
  });

  test("the mark of a sent answer goes once the thread shows the answer", async () => {
    const first = renderActions(OWNER);
    serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await screen.findByText("Answer sent");
    first.unmount();
    renderActions(OWNER, QUESTION, ANSWERED);
    expect(screen.getByText("Answered")).toBeTruthy();
    await waitFor(() => expect(drafts.has("answer:alpha-0001")).toBe(false));
  });

  test("Ctrl+Enter in the frame of a choice click sends nothing and shows no error", async () => {
    renderActions(OWNER);
    const calls = serve({});
    fireEvent.click(screen.getByRole("button", { name: "Write an answer" }));
    const box = screen.getByLabelText("Your answer");
    fireEvent.change(box, { target: { value: "Neither" } });
    const choice = screen.getByRole("button", { name: "Keep the log" });
    // One act: React draws nothing between the two events.
    act(() => {
      choice.click();
      box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true }));
    });
    await act(async () => {});
    expect(posts(calls, "/api/append")).toHaveLength(1);
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByLabelText("Your answer") as HTMLTextAreaElement).value).toBe("Neither");
  });

  test("a double click on a choice on its way sends once", async () => {
    renderActions(OWNER);
    const calls = serve({});
    const choice = screen.getByRole("button", { name: "Keep the log" });
    fireEvent.click(choice);
    fireEvent.click(choice);
    await act(async () => {});
    expect(posts(calls, "/api/append")).toHaveLength(1);
  });

  test("a choice after a failed written answer goes under the same id", async () => {
    renderActions(OWNER);
    const calls = serve({ "/api/append": [500, { error: "cannot write the log: disk full" }] });
    fireEvent.click(screen.getByRole("button", { name: "Write an answer" }));
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "Neither" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(2));
    const [first, second] = posts(calls, "/api/append").map((c) => (c.body as { id: string }).id);
    expect(second).toBe(first);
  });

  test("a failed answer brings the choices back and shows the error once stopped", async () => {
    renderActions(OWNER);
    const calls = serve({ "/api/append": [400, { error: "Bad reply." }] });
    fireEvent.click(screen.getByRole("button", { name: "Keep the log" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Not sent: Bad reply. Check it");
    expect(alert.textContent).not.toContain("..");
    const button = screen.getByRole("button", { name: "Keep the log" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(2));
  });

  test("mark done, record and approve send once until the refetch lands", async () => {
    const NOTE: Message = { id: "own-7", depth: 1, quote: null, from: "beta", kind: "note", summary: "Renew the key" };
    renderActions(OWNER, NOTE);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: "Mark done" }));
    fireEvent.click(screen.getByRole("button", { name: "Record as decision" }));
    await screen.findByRole("button", { name: "Recorded" });
    await screen.findByRole("button", { name: "Done" });
    fireEvent.click(screen.getByRole("button", { name: "Recorded" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await act(async () => {});
    expect(posts(calls, "/api/append")).toHaveLength(2);
  });

  test("approve sends once until the refetch lands", async () => {
    renderWith(<People />, [
      [keys.me, OWNER],
      [keys.roster, SNAP],
    ]);
    const calls = serve({ "/api/append": [201, {}] });
    fireEvent.click(screen.getByRole("button", { name: /Beta/ }));
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const done = await screen.findByRole("button", { name: "Approved" });
    fireEvent.click(done);
    await act(async () => {});
    expect(posts(calls, "/api/append")).toHaveLength(1);
  });

  test("a failed mark done comes back for another try", async () => {
    const NOTE: Message = { id: "own-7", depth: 1, quote: null, from: "beta", kind: "note", summary: "Renew the key" };
    renderActions(OWNER, NOTE);
    const calls = serve({ "/api/append": [500, { error: "cannot write the log: disk full" }] });
    fireEvent.click(screen.getByRole("button", { name: "Mark done" }));
    expect(await screen.findByText(/cannot write the log: disk full\. Check it/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Mark done" }));
    await waitFor(() => expect(posts(calls, "/api/append")).toHaveLength(2));
  });
});

describe("no answer control while the data loads", () => {
  test("the thread has not loaded", () => {
    renderWith(<>{ownerActions(thread("alpha-0001"), QUESTION)}</>, [
      [keys.me, OWNER],
      [keys.roster, SNAP],
    ]);
    expect(screen.queryByRole("button", { name: "Keep the log" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Write an answer" })).toBeNull();
  });

  test("the roster has not loaded", () => {
    renderWith(<>{ownerActions(thread("alpha-0001"), QUESTION)}</>, [
      [keys.me, OWNER],
      [keys.thread("alpha-0001"), DETAIL],
    ]);
    expect(screen.queryByRole("button", { name: "Keep the log" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Write an answer" })).toBeNull();
  });
});
