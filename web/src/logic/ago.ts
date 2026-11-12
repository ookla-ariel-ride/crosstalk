const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * How long ago `iso` was, as of `now`: "now" under a minute or in the future,
 * then "6 min", "2 h", "3 d". A bad date gives "".
 */
export function ago(iso: string, now: Date): string {
  if (typeof iso !== "string" || !(now instanceof Date)) return "";
  const then = Date.parse(iso);
  const diff = now.getTime() - then;
  if (Number.isNaN(diff)) return "";
  if (diff < MINUTE) return "now";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} min`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)} h`;
  return `${Math.floor(diff / DAY)} d`;
}
