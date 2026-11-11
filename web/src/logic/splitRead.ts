import type { Mark, Message } from "./types";

const str = (value: unknown): string => (typeof value === "string" ? value : "");

/**
 * The messages of an open thread split at the owner's mark, each part in the
 * order given. A message is fresh when its (`t`, `id`) pair is later than the
 * mark's, compared as strings like the server (readstate.unread). With no
 * mark, every message is fresh.
 */
export function splitRead(messages: Message[], mark: Mark | undefined): { earlier: Message[]; fresh: Message[] } {
  const earlier: Message[] = [];
  const fresh: Message[] = [];
  if (!Array.isArray(messages)) return { earlier, fresh };
  const markT = typeof mark?.t === "string" && mark.t ? mark.t : null;
  const markId = str(mark?.id);
  for (const m of messages) {
    if (!m || typeof m !== "object") continue;
    const t = str(m.t);
    const later = markT === null || t > markT || (t === markT && str(m.id) > markId);
    (later ? fresh : earlier).push(m);
  }
  return { earlier, fresh };
}
