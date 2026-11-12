// The body of a message as it shows under its summary. Many posts repeat the
// summary as the first line of the body; that line shows once, as the present
// page's bodyEl does.

/** `body` without a first line that only repeats `summary`; "" when the body is the summary. */
export function afterSummary(body: string, summary: string): string {
  const sum = summary.trim();
  if (!sum) return body;
  if (body.trim() === sum) return "";
  if (body.startsWith(sum) && /^\s*\n/.test(body.slice(sum.length))) return body.slice(sum.length).replace(/^\s+/, "");
  return body;
}
