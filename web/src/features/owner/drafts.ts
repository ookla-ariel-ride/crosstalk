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
  /**
   * When an answer to this question was sent (ms since 1970). The controls
   * stay shut until the loaded thread shows the question closed, or until a
   * thread loaded ANSWER_WAIT_MS later still shows it open.
   */
  answeredAt?: number;
  /** The last answer did not reach the thread: the controls say so until the next send. */
  lost?: boolean;
}

export const drafts = new Map<string, Draft>();

/** How long a sent answer may take to show in the thread. */
export const ANSWER_WAIT_MS = 60_000;

/** Merge `patch` into the draft under `key`; a draft left with nothing in it is dropped. */
export function keepDraft(key: string, patch: Partial<Draft>): void {
  const draft: Draft = { subject: "", text: "", id: null, ...drafts.get(key), ...patch };
  if (draft.subject || draft.text || draft.id || draft.kind || draft.to?.length || draft.answeredAt !== undefined || draft.lost) drafts.set(key, draft);
  else drafts.delete(key);
}
