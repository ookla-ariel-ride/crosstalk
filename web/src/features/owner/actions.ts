// One function for each owner action. Each sends the body the present page
// (index.html) sends for the same action: `append` and `user.login` and
// `user.onboard` of its fetch layer, and the click handlers that call them.
import { api } from "../../api/client";
import type { AppendBody, OnboardBody } from "../../api/types";

/** The time the present page writes: ISO, without the milliseconds. */
export const stamp = (): string => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

/** A new owner message id, as newOwnId of the present page. */
export const newOwnId = (): string =>
  "own-" + stamp().replace(/[^0-9]/g, "").slice(0, 14) + "-" + Math.random().toString(36).slice(2, 6);

const row = (op: AppendBody["op"], collection: string, id: string, data: Record<string, unknown>) =>
  api.append({ from: "owner", op, collection, id, data });

/** The six-digit startup PIN goes as `pin`; anything else is the owner token. */
export function signIn(pinOrToken: string) {
  const v = pinOrToken.trim();
  return api.login(/^\d{6}$/.test(v) ? { pin: v } : { token: v });
}

/** The kinds of the present composer: its select values. */
export const KINDS = {
  owner: "Note",
  request: "Question or request",
  decision: "Decision",
  agreement: "Propose agreement",
  alert: "Urgent alert",
  lesson: "Record a lesson",
  waiting: "Something I need to do",
  incident: "Build or host incident",
} as const;
export type Kind = keyof typeof KINDS;

/** Summary and body of a post: the subject, else the first line of the text (the composer's submit). */
export function splitPost(subject: string, text: string) {
  const subj = subject.replace(/[\r\n]+/g, " ").trim();
  const t = text.trim();
  if (subj) return { summary: subj.slice(0, 160), body: t };
  const summary = (t.split("\n")[0] ?? "").slice(0, 160);
  return { summary, body: t !== summary.trim() ? t : "" };
}

/** Summary and body of an answer (sendAnswer). */
export function splitAnswer(text: string) {
  const lines = text.trim().split("\n");
  const first = (lines[0] ?? "").trim();
  return { summary: first.slice(0, 160), body: first.length > 160 ? text.trim() : lines.slice(1).join("\n").trim() };
}

/** A new thread: the message, then the row its kind adds, all on the same id so a retry rewrites them. */
export async function startThread(to: string[], kind: Kind, summary: string, body: string, id = newOwnId()) {
  const t = stamp();
  const msgKind = kind === "decision" || kind === "waiting" ? "owner" : kind === "incident" ? "alert" : kind;
  const msg: Record<string, unknown> = { t, from: "owner", kind: msgKind, summary, body };
  if (kind === "alert" || kind === "incident") msg.important = true;
  if (to.length) msg.to = to;
  await row("set", "messages", id, msg);
  const project = to.length === 1 ? to[0] : "owner";
  if (kind === "decision")
    await row("set", "decisions", id, { t, by: "owner", summary, body: body || summary, project: to.length === 1 ? to[0] : "all", source: "Crosstalk page" });
  if (kind === "waiting") await row("set", "waiting", id, { since: t, project, summary, body, done: null });
  if (kind === "agreement")
    await row("set", "agreements", "a-" + id, { t, updated: t, summary, body, status: "agreed", builder: to[0] ?? "owner", adopter: "all", commits: {} });
  if (kind === "lesson") await row("set", "lessons", "lesson-" + id, { t, project, summary, body: body || "", fix: body || summary });
  if (kind === "incident") await row("set", "incidents", "i-" + id, { start: t, end: null, project, summary, body, fix: "" });
}

/** A reply to `toId`; `to` names its author when the roster holds it. */
export function reply(toId: string, summary: string, body: string, to: string[] = [], id = newOwnId()) {
  const data: Record<string, unknown> = { t: stamp(), from: "owner", kind: "owner", summary, body, replyTo: toId };
  if (to.length) data.to = to;
  return row("set", "messages", id, data);
}

/** The answer to a question: a reply that names the asker, as sendAnswer writes it. */
export function answer(questionId: string, text: string, asker?: string, id = newOwnId()) {
  const { summary, body } = splitAnswer(text);
  return reply(questionId, summary, body, asker ? [asker] : [], id);
}

export const markDone = (id: string) => row("update", "waiting", id, { done: stamp() });

export const recordDecision = (m: { id: string; summary?: string; body?: string }) =>
  row("set", "decisions", "d-from-" + m.id, {
    t: stamp(),
    by: "owner",
    project: "all",
    summary: m.summary || "(no summary)",
    body: m.body || m.summary || "",
    source: "Crosstalk page",
  });

/** Hold or release an agreement; a release of the old status "held" sets it back to "agreed". */
export function holdAgreement(a: { id: string; status?: unknown }, hold: boolean) {
  const patch: Record<string, unknown> = { hold, holdNote: hold ? "held by owner" : null, updated: stamp() };
  if (!hold && a.status === "held") patch.status = "agreed";
  return row("update", "agreements", a.id, patch);
}

export const approveParticipant = (pid: string) => row("update", "participants", pid, { trust: "approved" });

export interface AddForm {
  id: string;
  name: string;
  kind: string;
  repo: string;
  parent: string;
  harness: string;
  checkInMin: string;
}

/** Add a participant: the Add form's fields, the empty ones left out. The answer holds the token and the kit. */
export function addParticipant(form: AddForm) {
  const fields: OnboardBody = { id: form.id.trim(), name: form.name.trim(), kind: form.kind };
  const repo = form.repo.trim();
  const parent = form.parent.trim();
  const check = form.checkInMin.trim();
  if (repo) fields.repo = repo;
  if (parent) fields.parent = parent;
  if (form.harness) fields.harness = form.harness;
  if (check !== "") fields.checkInMin = Number.isInteger(Number(check)) ? Number(check) : check;
  return api.onboard(fields);
}
