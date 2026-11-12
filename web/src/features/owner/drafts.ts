// The composer drafts, in memory for this tab and keyed by their target, so
// a virtual row that unmounts keeps its draft. No draft reaches storage.

export interface Draft {
  subject: string;
  text: string;
  /** The message id of a failed send, so a retry rewrites the same row. */
  id: string | null;
  /** A new thread's kind other than a note, and its recipients: the draft goes where it was meant to. */
  kind?: string;
  to?: string[];
  /** An answer to this question was sent; the controls stay shut until the loaded thread shows it. */
  answered?: boolean;
}

export const drafts = new Map<string, Draft>();

/** Merge `patch` into the draft under `key`; a draft left with nothing in it is dropped. */
export function keepDraft(key: string, patch: Partial<Draft>): void {
  const draft: Draft = { subject: "", text: "", id: null, ...drafts.get(key), ...patch };
  if (draft.subject || draft.text || draft.id || draft.kind || draft.to?.length || draft.answered) drafts.set(key, draft);
  else drafts.delete(key);
}
