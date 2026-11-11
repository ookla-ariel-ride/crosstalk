// The narrow shapes the pure functions of this folder read. Field names are
// the server's (scripts/crosstalk/threads.py, readstate.py, the message rows).
// Every field is optional: the functions take what the server sends and must
// not throw on a row that lacks one.

/** A message row, as /api/thread/<root> and the snapshot send it. */
export interface Message {
  id?: string;
  t?: string;
  from?: string;
  kind?: string;
  summary?: string;
  body?: string;
  harness?: string;
  model?: string;
  options?: unknown;
  recommend?: unknown;
}

/** A thread, as /api/threads sends it. */
export interface Thread {
  root?: string;
  title?: string;
  kind?: string;
  projects?: string[];
  last_t?: string;
  last_id?: string;
  routine?: boolean;
  hot?: number;
}

/** The owner's read position in one thread: the newest message read. */
export interface Mark {
  t?: string;
  id?: string;
}

/** The roster, keyed by participant id. */
export type Roster = Record<string, { harness?: string | null } | undefined>;
