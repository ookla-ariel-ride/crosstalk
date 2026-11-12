// One function for each server route the page reads. Every URL is relative to
// the page's own host, so the page works under /v2/ and under the dev proxy.
import type {
  AppendBody,
  AppendResult,
  Health,
  LandQueue,
  LoginBody,
  LoginResult,
  Me,
  OnboardBody,
  OnboardResult,
  ReadState,
  ReadStatePut,
  SectionName,
  SectionPage,
  Snapshot,
  Sort,
  ThreadDetail,
  ThreadsPage,
  Vitals,
} from "./types";
import { sectionPage, threadDetail, threadsPage } from "./normalize";

/** A failed call: the HTTP status (0 when the server was not reached) and the server's message. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? "?" + text : "";
}

export function createApi(fetchFn: Fetch = (input, init) => fetch(input, init)) {
  async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      response = await fetchFn(path, { ...init, credentials: "same-origin" });
    } catch (e) {
      throw new ApiError(0, e instanceof Error ? e.message : String(e));
    }
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new ApiError(response.status, response.ok ? "the response is not JSON" : "HTTP " + response.status);
    }
    if (!response.ok) {
      const error = (body as { error?: unknown } | null)?.error;
      throw new ApiError(response.status, typeof error === "string" ? error : "HTTP " + response.status);
    }
    return body as T;
  }

  function send<T>(method: string, path: string, body: unknown): Promise<T> {
    return call<T>(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }

  return {
    getThreads: (sort: Sort, cursor?: string, limit?: number, unread?: boolean) =>
      call<unknown>("/api/threads" + query({ sort, unread: unread ? 1 : undefined, cursor, limit })).then(threadsPage),
    getThread: (root: string) => call<unknown>("/api/thread/" + encodeURIComponent(root)).then(threadDetail),
    getSection: (name: SectionName, opts: { cursor?: string; limit?: number; q?: string } = {}) =>
      call<unknown>("/api/section/" + name + query(opts)).then(sectionPage),
    getVitals: () => call<Vitals>("/api/vitals"),
    getHealth: () => call<Health>("/api/health"),
    getQueue: () => call<LandQueue>("/api/queue"),
    /** The whole replay; the page reads the roster and the check-in times from it. */
    getSnapshot: () => call<Snapshot>("/api/snapshot"),
    getMe: () => call<Me>("/api/me"),
    getReadState: () => call<ReadState>("/api/read-state"),
    putReadState: (body: ReadStatePut) => send<ReadState>("PUT", "/api/read-state", body),
    login: (body: LoginBody) => send<LoginResult>("POST", "/api/login", body),
    append: (body: AppendBody) => send<AppendResult>("POST", "/api/append", body),
    onboard: (body: OnboardBody) => send<OnboardResult>("POST", "/api/onboard", body),
    /** The owner's "No action needed" on the request `item`. */
    ack: (item: string) => send<{ ok: boolean }>("POST", "/api/ack", { item }),
  };
}

export type Api = ReturnType<typeof createApi>;

export const api: Api = createApi();
