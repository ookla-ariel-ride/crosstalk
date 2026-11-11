// The api types must pass to the pure functions of logic/ with no cast.
// `npm run typecheck` fails on this file when one does not fit.
import { expect, test } from "vitest";
import type * as logic from "../logic/types";
import type * as api from "./types";

const thread = (t: api.Thread): logic.Thread => t;
const message = (m: api.Message): logic.Message => m;
const mark = (m: api.ReadMark): logic.Mark => m;
const roster = (r: Record<string, api.RosterEntry>): logic.Roster => r;

test("the api types fit the input types of logic/", () => {
  expect([thread, message, mark, roster].every((f) => typeof f === "function")).toBe(true);
});
