import { describe, expect, test } from "vitest";
import type { Message, RosterEntry } from "../../api/types";
import { answerTag, questionState } from "./OwnerActions";

const roster = { alpha: { trust: "approved" } } as unknown as Record<string, RosterEntry>;
const q = { id: "alpha-q1", from: "alpha", kind: "question", summary: "Ship it?", t: "2026-10-21T18:00:00Z" } as Message;
const ans = (id: string, t: string, summary: string) => ({ id, from: "owner", kind: "owner", replyTo: q.id, summary, t }) as Message;

describe("an answer given twice", () => {
  test("one answer: answered, no tag", () => {
    const one = [q, ans("own-1", "2026-10-21T18:30:00Z", "Yes")];
    expect(questionState(q, one, roster)).toBe("answered");
    expect(answerTag(one[1]!, one, roster)).toBeNull();
  });

  test("two answers from two tabs: answer changed, the newest in force", () => {
    // The ids sort the other way to the times: the time decides.
    const two = [q, ans("own-b", "2026-10-21T18:30:00Z", "Yes"), ans("own-a", "2026-10-21T18:40:00Z", "No, wait")];
    expect(questionState(q, two, roster)).toBe("answer changed");
    expect(answerTag(two[1]!, two, roster)).toBe("Replaced");
    expect(answerTag(two[2]!, two, roster)).toBe("In force");
    expect(answerTag(q, two, roster)).toBeNull();
  });

  test("an owner reply after a withdrawal leaves it withdrawn", () => {
    const w = { id: "alpha-w1", from: "alpha", kind: "withdrawn", replyTo: q.id, t: "2026-10-21T18:10:00Z" } as Message;
    const msgs = [q, w, ans("own-1", "2026-10-21T18:30:00Z", "Yes"), ans("own-2", "2026-10-21T18:40:00Z", "No")];
    expect(questionState(q, msgs, roster)).toBe("withdrawn");
    expect(answerTag(msgs[3]!, msgs, roster)).toBeNull();
  });
});
