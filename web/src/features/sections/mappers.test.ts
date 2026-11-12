import { describe, expect, test } from "vitest";
import type { Block } from "../../logic/parseBody";
import { adoptionRow } from "./adoption";
import { agreementRow } from "./agreements";
import { decisionRow } from "./decisions";
import type { Doc, RowView } from "./fields";
import { incidentRow } from "./incidents";
import { lessonRow } from "./lessons";
import { lockRow } from "./locks";
import { progressRow } from "./progress";
import { queueEntries, queueRow } from "./queue";
import { reviewRow } from "./reviews";
import { scheduleRow } from "./schedules";

const NOW = new Date("2026-11-11T10:00:00Z");

/** The text of each block, one string a block, for a short expectation. */
function text(blocks: Block[]): string[] {
  return blocks.map((b) => {
    const parts = b.type === "p" ? [b.parts] : b.type === "code" ? [[{ type: "text" as const, text: b.text }]] : b.items;
    return parts
      .map((p) => p.map((x) => (x.type === "link" ? x.href : x.type === "ref" ? x.id : x.text)).join(""))
      .join(" | ");
  });
}

const row = (view: RowView) => ({ ...view, detail: text(view.detail) });

describe("section mappers", () => {
  test("agreement", () => {
    const doc: Doc = {
      id: "alpha-0042", summary: "Share the lint rules", body: "Both use one file.", status: "building",
      builder: "alpha", adopter: "beta", commits: { alpha: ["abc1234"] }, updated: "2026-11-11T08:00:00Z",
    };
    expect(row(agreementRow(doc, NOW))).toEqual({
      projects: ["alpha", "beta"],
      title: "Share the lint rules",
      text: "built by alpha, adopted by beta",
      note: ["building", "2 h"],
      open: true,
      detail: ["Both use one file.", "Builds: alpha", "Adopts: beta", "Commits of alpha: abc1234", "Updated: 2026-11-11 08:00 UTC"],
    });
    expect(agreementRow({ status: "landed-both" }, NOW).open).toBe(false);
    expect(agreementRow({ status: "dropped" }, NOW).open).toBe(false);
    expect(agreementRow({ status: "agreed", hold: true, holdNote: "held by owner" }, NOW).note[0]).toBe("held");
  });

  test("decision", () => {
    const doc: Doc = { id: "own-0001", summary: "Use one queue", by: "alpha", project: "all", source: "the thread", date: "2026-11-10" };
    expect(row(decisionRow(doc, NOW))).toEqual({
      projects: ["alpha"],
      title: "Use one queue",
      text: "for all projects, said in the thread",
      note: ["1 d"],
      open: false,
      detail: ["By: alpha", "For: all projects", "Where said: the thread", "When: 2026-11-10 00:00 UTC"],
    });
  });

  test("lesson", () => {
    const doc: Doc = { id: "alpha-0007", summary: "A hook stole focus", fix: "The hook runs hidden", from: "alpha", t: "2026-11-11T09:00:00Z" };
    expect(row(lessonRow(doc, NOW))).toEqual({
      projects: ["alpha"],
      title: "A hook stole focus",
      text: "The hook runs hidden",
      note: ["lesson", "1 h"],
      open: false,
      detail: ["What prevents it now: The hook runs hidden", "From: alpha", "When: 2026-11-11 09:00 UTC"],
    });
  });

  test("a decision and a lesson carry the marks of their row; other sections do not", () => {
    const marks = { marks: { beta: "ack" }, owed: ["alpha"], expects: true };
    expect(decisionRow({ id: "own-1", marks }, NOW).marks).toEqual(marks);
    expect(lessonRow({ id: "lesson-solo", marks }, NOW).marks).toEqual(marks);
    expect(decisionRow({ id: "own-2" }, NOW).marks).toBeUndefined();
    expect(incidentRow({ id: "beta-1", marks }, NOW).marks).toBeUndefined();
  });

  test("incident", () => {
    const doc: Doc = { id: "beta-0003", summary: "Build host down", project: "beta", start: "2026-11-11T09:30:00Z", end: null };
    expect(row(incidentRow(doc, NOW))).toEqual({
      projects: ["beta"],
      title: "Build host down",
      text: "",
      note: ["incident", "open", "30 min"],
      open: true,
      detail: ["Project: beta", "Started: 2026-11-11 09:30 UTC"],
    });
    expect(incidentRow({ ...doc, end: "2026-11-11T09:45:00Z" }, NOW)).toMatchObject({ open: false, note: ["incident", "ended", "30 min"] });
  });

  test("file claim", () => {
    const doc: Doc = { id: "alpha-l1", path: "docs/a.md", repo: "shared", project: "alpha", holder: "session 2", note: "edit the intro", since: "2026-11-11T09:54:00Z" };
    expect(row(lockRow(doc, NOW))).toEqual({
      projects: ["alpha"],
      title: "docs/a.md",
      text: "edit the intro",
      note: ["held", "6 min"],
      open: true,
      detail: ["Repository: shared", "Held by: alpha (session 2)", "Why: edit the intro", "Since: 2026-11-11 09:54 UTC"],
    });
    expect(lockRow({ ...doc, released: "2026-11-11T09:58:00Z" }, NOW)).toMatchObject({ open: false, note: ["released", "6 min"] });
  });

  test("schedule", () => {
    const doc: Doc = {
      id: "alpha-check-in", from: "alpha", name: "check-in", cron: "*/30 * * * *", periodMin: 30,
      next: "2026-11-11T10:20:00Z", reported: "2026-11-11T09:50:00Z",
      runs: [{ at: "2026-11-11T09:50:00Z", result: "ok" }, "junk"],
    };
    expect(row(scheduleRow(doc, NOW))).toEqual({
      projects: ["alpha"],
      title: "check-in",
      text: "*/30 * * * *",
      note: ["scheduled", "10 min"],
      open: true,
      detail: [
        "Cron: */30 * * * * (local time)",
        "Next run: 2026-11-11 10:20 UTC",
        "Last run: ok, 2026-11-11 09:50 UTC",
        "2026-11-11 09:50 UTC ok",
        "Last report: 2026-11-11 09:50 UTC",
      ],
    });
    expect(scheduleRow({ ...doc, next: "2026-11-11T09:00:00Z" }, NOW).note[0]).toBe("late");
    expect(scheduleRow({ ...doc, removed: "2026-11-11T09:59:00Z" }, NOW)).toMatchObject({ open: false, note: ["removed", "10 min"] });
  });

  test("adoption record", () => {
    const doc: Doc = { id: "beta-adopt-alpha-0042", from: "beta", item: "alpha-0042", state: "adopted", ref: "def5678", at: "2026-11-11T07:00:00Z" };
    expect(row(adoptionRow(doc, NOW))).toEqual({
      projects: ["beta"],
      title: "alpha-0042",
      text: "beta, def5678",
      note: ["adopted", "3 h"],
      open: false,
      detail: ["Project: beta", "Standard: alpha-0042", "State: adopted", "Commit: def5678", "Recorded: 2026-11-11 07:00 UTC"],
    });
  });

  test("progress report", () => {
    const doc: Doc = { id: "alpha-p1", project: "alpha", game: "Parser", pct: 40, verified: 12, rows: 50, t: "2026-11-11T09:00:00Z" };
    expect(row(progressRow(doc, NOW))).toEqual({
      projects: ["alpha"],
      title: "Parser",
      text: "40% built, 12 of 50 verified",
      note: ["1 h"],
      open: false,
      detail: ["Project: alpha", "Built: 40%", "Verified: 12", "Rows: 50", "Reported: 2026-11-11 09:00 UTC"],
    });
  });

  test("review and override", () => {
    const review: Doc = { id: "relay-bot-r1", kind: "review", project: "alpha", tip: "abc1234def", tier: "full", verdict: "ok", findings: 2, t: "2026-11-11T09:00:00Z" };
    expect(row(reviewRow(review, NOW))).toEqual({
      projects: ["alpha"],
      title: "Review of abc1234",
      text: "tier full, 2 findings",
      note: ["review", "ok", "1 h"],
      open: false,
      detail: ["Tip: abc1234def", "Tier: full", "Verdict: ok", "Findings: 2", "When: 2026-11-11 09:00 UTC"],
    });
    const override: Doc = {
      id: "relay-bot-o1", kind: "disagreement", project: "beta", status: "objected", summary: "Override on beta",
      headlines: ["Missing test"], findings: 3, reason: "False alarm", reviewer: "alpha", tip: "abc1234", t: "2026-11-11T09:00:00Z",
    };
    expect(row(reviewRow(override, NOW))).toEqual({
      projects: ["beta"],
      title: "Override on beta",
      text: "False alarm",
      note: ["override", "objected", "1 h"],
      open: true,
      detail: ["Missing test | and 2 more", "Reason: False alarm", "Asked: alpha", "Tip: abc1234", "When: 2026-11-11 09:00 UTC"],
    });
    expect(reviewRow({ ...override, resolution: { choice: "kept" } }, NOW)).toMatchObject({ open: false, note: ["override", "settled", "1 h"] });
    expect(reviewRow({ ...override, status: "agreed" }, NOW).open).toBe(false);
  });

  test("land queue entries", () => {
    const entries = queueEntries({
      holders: [{ project: "alpha", id: "alpha-0100", since: "2026-11-11T09:40:00Z", word: "LAND START", branches: ["fix-a"], ask: null }],
      waiting: [{ project: "beta", id: "beta-0200", t: "2026-11-11T09:50:00Z", branches: ["feat-b"], holding: false, granted: "relay-bot-0300" }],
      next: "beta", slots_free: 0, may_start: false, max_holders: 1, notes: [],
    });
    expect(entries.map((e) => row(queueRow(e, NOW)))).toEqual([
      {
        projects: ["alpha"], title: "alpha: fix-a", text: "LAND START, since 2026-11-11 09:40 UTC", note: ["landing", "20 min"], open: true,
        detail: ["Branches: fix-a", "Started by: alpha-0100"],
      },
      {
        projects: ["beta"], title: "Next: beta: feat-b", text: "", note: ["granted", "10 min"], open: true,
        detail: ["Branches: feat-b", "Asked in: beta-0200", "Granted in: relay-bot-0300"],
      },
    ]);
  });

  test("no mapper throws on an empty or odd doc", () => {
    const odd: Doc[] = [{}, { id: 5, summary: 7, body: null, refs: {}, runs: "x", commits: [1], headlines: "y", objections: [null], positions: 3 }];
    const mappers = [agreementRow, decisionRow, lessonRow, incidentRow, lockRow, scheduleRow, adoptionRow, progressRow, reviewRow];
    for (const map of mappers) {
      for (const doc of odd) {
        const view = map(doc, NOW);
        expect(typeof view.title).toBe("string");
        expect(Array.isArray(view.detail)).toBe(true);
      }
    }
    expect(reviewRow({ kind: "disagreement", objections: [null, 4] }, NOW).open).toBe(true);
    expect(queueEntries(undefined)).toEqual([]);
    expect(queueEntries({} as never)).toEqual([]);
    expect(() => queueRow({ role: "waiting", place: 1 } as never, NOW)).not.toThrow();
    expect(() => queueRow({ role: "holder" } as never, NOW)).not.toThrow();
  });
});
