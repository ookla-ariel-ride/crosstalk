// The model family of a post, from a table of name patterns (spec, "Who spoke").

const TABLE: [string, string][] = [
  ["claude", "Anthropic"],
  ["gpt", "OpenAI"],
  ["glm", "Zhipu"],
  ["kimi", "Moonshot"],
  ["gemini", "Google"],
];

// These match only at the start of the name or of a part after "/" or "-",
// so a name such as "llama-pro3" is not OpenAI.
const OPENAI_PARTS = ["o1", "o3", "o4"];

function startsAPart(name: string, pattern: string): boolean {
  let at = name.indexOf(pattern);
  while (at !== -1) {
    const before = name[at - 1];
    if (at === 0 || before === "/" || before === "-") return true;
    at = name.indexOf(pattern, at + 1);
  }
  return false;
}

/**
 * The family of `model`: the first `extra` pair whose pattern is in the name
 * (a plain substring, case blind), then the built-in table. "Other" when
 * nothing matches, "Not stated" when there is no model.
 */
export function family(model: string | undefined, extra?: [string, string][]): string {
  if (typeof model !== "string" || model.trim() === "") return "Not stated";
  const name = model.toLowerCase();
  if (Array.isArray(extra)) {
    for (const pair of extra) {
      if (!Array.isArray(pair)) continue;
      const [pattern, label] = pair;
      if (typeof pattern !== "string" || pattern === "" || typeof label !== "string") continue;
      if (name.includes(pattern.toLowerCase())) return label;
    }
  }
  for (const [pattern, label] of TABLE) if (name.includes(pattern)) return label;
  for (const pattern of OPENAI_PARTS) if (startsAPart(name, pattern)) return "OpenAI";
  return "Other";
}
