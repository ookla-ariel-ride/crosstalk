import { describe, expect, test } from "vitest";
import { ApiError, createApi } from "./client";

function answer(status: number, body: string, type = "application/json") {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const api = createApi(async (input, init) => {
    calls.push({ url: String(input), init });
    return new Response(body, { status, headers: { "Content-Type": type } });
  });
  return { api, calls };
}

describe("client", () => {
  test("client throws ApiError on 403", async () => {
    const { api } = answer(403, JSON.stringify({ error: "only the owner has a read state" }));
    const err = await api.getReadState().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 403, message: "only the owner has a read state" });
  });

  test("a body that is not JSON is an ApiError with the status", async () => {
    const { api } = answer(200, "<html>proxy page</html>", "text/html");
    await expect(api.getVitals()).rejects.toMatchObject({ status: 200, name: "ApiError" });
  });

  test("a network failure is an ApiError with status 0", async () => {
    const api = createApi(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(api.getMe()).rejects.toMatchObject({ status: 0, message: "Failed to fetch" });
  });

  test("an error with no JSON body still gives the status", async () => {
    const { api } = answer(502, "Bad Gateway", "text/plain");
    await expect(api.getHealth()).rejects.toMatchObject({ status: 502, message: "HTTP 502" });
  });

  test("each route asks the server with a relative URL and same-origin credentials", async () => {
    const { api, calls } = answer(200, "{}");
    await api.getThreads("hot", "50", 25);
    await api.getThreads("new");
    await api.getThread("alpha-0001/x");
    await api.getSection("agreements", { cursor: "10", q: "a b" });
    await api.getSection("locks");
    await api.getVitals();
    await api.getHealth();
    await api.getMe();
    await api.getReadState();
    await api.getQueue();
    await api.getSnapshot();
    expect(calls.map((c) => c.url)).toEqual([
      "/api/threads?sort=hot&cursor=50&limit=25",
      "/api/threads?sort=new",
      "/api/thread/alpha-0001%2Fx",
      "/api/section/agreements?cursor=10&q=a+b",
      "/api/section/locks",
      "/api/vitals",
      "/api/health",
      "/api/me",
      "/api/read-state",
      "/api/queue",
      "/api/snapshot",
    ]);
    for (const c of calls) expect(c.init?.credentials).toBe("same-origin");
  });

  test("writes send JSON with PUT or POST", async () => {
    const { api, calls } = answer(200, "{}");
    await api.putReadState({ thread: "alpha-0001", t: "2026-01-02T03:04:05Z", id: "beta-0002" });
    await api.login({ pin: "123456" });
    expect(calls.map((c) => [c.url, c.init?.method, c.init?.body])).toEqual([
      ["/api/read-state", "PUT", '{"thread":"alpha-0001","t":"2026-01-02T03:04:05Z","id":"beta-0002"}'],
      ["/api/login", "POST", '{"pin":"123456"}'],
    ]);
    expect(new Headers(calls[0]?.init?.headers).get("Content-Type")).toBe("application/json");
  });

  test("append and onboard POST their JSON body", async () => {
    const { api, calls } = answer(200, "{}");
    await api.append({ from: "owner", op: "update", collection: "waiting", id: "alpha-1", data: { done: "2026-11-11T10:00:00Z" } });
    await api.onboard({ id: "beta", name: "Beta", kind: "project" });
    expect(calls.map((c) => [c.url, c.init?.method, c.init?.body])).toEqual([
      ["/api/append", "POST", '{"from":"owner","op":"update","collection":"waiting","id":"alpha-1","data":{"done":"2026-11-11T10:00:00Z"}}'],
      ["/api/onboard", "POST", '{"id":"beta","name":"Beta","kind":"project"}'],
    ]);
  });

  test("rows of the thread, the threads and the sections come out with the types the views read", async () => {
    const message = { id: 5, depth: "1", quote: 9, t: 1731315000, from: null, to: "beta", kind: 5, summary: 42, body: { plan: "keep" }, replyTo: ["alpha-0001"], options: "a", recommend: "1" };
    const detail = await answer(200, JSON.stringify({ root: "alpha-0001", messages: [message, null] })).api.getThread("alpha-0001");
    expect(detail.messages).toEqual([
      { id: "5", depth: 1, quote: "9", t: "1731315000", to: ["beta"], kind: "5", summary: "42", body: '{"plan":"keep"}', replyTo: '["alpha-0001"]' },
    ]);
    const page = await answer(200, JSON.stringify({ threads: [{ root: "alpha-0001", title: 7, preview: null, projects: "alpha", count: "3", needs_owner: 1 }], next: null, server_time: "x" })).api.getThreads("needs");
    expect(page.threads[0]).toMatchObject({ root: "alpha-0001", title: "7", preview: "", projects: ["alpha"], count: 0, needs_owner: false, kind: "" });
    const rows = await answer(200, JSON.stringify({ rows: [{ id: "own-9", summary: 7, to: "beta", from: null, done: null }], next: null, open: 1, total: 1 })).api.getSection("waiting");
    expect(rows.rows[0]).toEqual({ id: "own-9", summary: "7", to: ["beta"], done: null });
  });
});
