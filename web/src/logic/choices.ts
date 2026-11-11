import type { Message } from "./types";

const LETTERED = /^[a-f][.)] /;

const strip = (option: string): string => option.replace(LETTERED, "");

function fromField(options: unknown): string[] {
  if (!Array.isArray(options) || !options.every((o) => typeof o === "string")) return [];
  return options.length >= 2 ? options.map(strip) : [];
}

function fromBody(body: unknown): string[] {
  if (typeof body !== "string") return [];
  const lines = body.split("\n").map((line) => line.replace(/\r$/, ""));
  const found = lines.filter((line) => LETTERED.test(line)).map(strip);
  return found.length >= 2 ? found : [];
}

/**
 * The answer choices of a question: its `options` field, else two or more
 * lettered lines of the body ("a. ...", "b) ..."), else none. `recommend` is
 * the index the question's `recommend` field names, else the first option
 * whose text says "recommended" (case blind), else null.
 */
export function choices(question: Message): { options: string[]; recommend: number | null } {
  const fielded = fromField(question?.options);
  const options = fielded.length ? fielded : fromBody(question?.body);
  const named = question?.recommend;
  let recommend: number | null = null;
  if (typeof named === "number" && Number.isInteger(named) && named >= 0 && named < options.length) {
    recommend = named;
  } else {
    const at = options.findIndex((o) => o.toLowerCase().includes("recommended"));
    recommend = at === -1 ? null : at;
  }
  return { options, recommend };
}
