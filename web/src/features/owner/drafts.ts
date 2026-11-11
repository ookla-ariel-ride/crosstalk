// The composer drafts, in memory for this tab and keyed by their target, so
// a virtual row that unmounts keeps its draft. No draft reaches storage.

export interface Draft {
  subject: string;
  text: string;
  /** The message id of a failed send, so a retry rewrites the same row. */
  id: string | null;
}

export const drafts = new Map<string, Draft>();

/** Keep `draft` under `key`; an empty one is dropped. */
export function keepDraft(key: string, draft: Draft): void {
  if (draft.subject || draft.text || draft.id) drafts.set(key, draft);
  else drafts.delete(key);
}
