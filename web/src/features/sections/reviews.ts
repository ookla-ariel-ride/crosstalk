// Reviews (index.html, renderReviews and overrideCard): the coordinator's
// Codex review rows. A branch review shows its tip, tier, verdict and
// findings; an override shows its state, headlines, reason and who agrees or
// objects. An override is open until it is settled or the asked project agrees.
import type { Block } from "../../logic/parseBody";
import { age, body, count, type Doc, line, list, projects, type RowView, stamp, str, words } from "./fields";

const shortTip = (tip: unknown): string => str(tip).slice(0, 7);

function overrideRow(doc: Doc, now: Date): RowView {
  const status = str(doc.status);
  const state = doc.resolution ? "settled" : status === "objected" || status === "agreed" ? status : "awaiting";
  const heads = list(doc.headlines).map(str).filter(Boolean);
  const findings = count(doc.findings);
  const more = findings !== null ? findings - heads.length : 0;
  const items = [...heads, ...(more > 0 ? ["and " + more + " more"] : [])];
  const headBlock: Block[] = items.length ? [{ type: "ul", items: items.map((text) => [{ type: "text", text }]) }] : [];
  const objections = list(doc.objections).flatMap((ob) => {
    const o = ob && typeof ob === "object" ? (ob as Doc) : {};
    return line("Objection from " + (str(o.from) || "a project"), str(o.summary) || "objects");
  });
  const positions = doc.positions && typeof doc.positions === "object" ? (doc.positions as Record<string, unknown>) : {};
  return {
    projects: projects(doc.project),
    title: str(doc.summary) || "Override of " + shortTip(doc.tip),
    text: str(doc.reason) || heads[0] || "",
    note: words("override", state, age(doc.t, now)),
    open: doc.kind === "disagreement" && !doc.resolution && status !== "agreed",
    detail: [
      ...headBlock,
      ...body(str(doc.body)),
      ...line("Reason", str(doc.reason)),
      ...line("Asked", str(doc.reviewer)),
      ...line("Agrees", list(doc.agreed).map(str).filter(Boolean).join(", ")),
      ...objections,
      ...Object.keys(positions).flatMap((p) => line("Position of " + p, str(positions[p]))),
      ...line("Report", str(doc.report)),
      ...line("Tip", str(doc.tip)),
      ...line("When", stamp(doc.t)),
    ],
  };
}

function branchReviewRow(doc: Doc, now: Date): RowView {
  const findings = count(doc.findings);
  return {
    projects: projects(doc.project),
    title: str(doc.summary) || "Review of " + (shortTip(doc.tip) || str(doc.id)),
    text: words(str(doc.tier) && "tier " + str(doc.tier), findings === null ? "" : findings + (findings === 1 ? " finding" : " findings")).join(", "),
    note: words("review", str(doc.verdict) || "unknown", age(doc.t, now)),
    open: false,
    detail: [
      ...body(str(doc.body)),
      ...line("Tip", str(doc.tip)),
      ...line("Tier", str(doc.tier)),
      ...line("Verdict", str(doc.verdict)),
      ...line("Findings", findings === null ? "" : String(findings)),
      ...line("When", stamp(doc.t)),
    ],
  };
}

export function reviewRow(doc: Doc, now: Date): RowView {
  return doc.kind === "disagreement" ? overrideRow(doc, now) : branchReviewRow(doc, now);
}
