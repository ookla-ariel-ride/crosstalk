import type { Message, Roster } from "./types";

const text = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

/**
 * Who spoke, as the page shows it: "alpha, Codex CLI, gpt-5". The harness
 * comes from the message, else from the roster; a post with no model says
 * "model not stated". The owner is a person: only the name.
 */
export function speaker(message: Message, roster: Roster): string {
  const from = text(message?.from);
  if (from === "owner") return from;
  const listed = from && roster && Object.hasOwn(roster, from) ? roster[from] : undefined;
  const harness = text(message?.harness) || text(listed?.harness);
  const model = text(message?.model) || "model not stated";
  return [from, harness, model].filter(Boolean).join(", ");
}
