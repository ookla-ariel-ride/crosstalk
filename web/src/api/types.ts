// The shapes the relay server sends, field for field (scripts/crosstalk/serve.py
// and the modules behind each route). An open set of strings is typed `string`.

/** The sort orders of GET /api/threads (threads.ORDERS). */
export type Sort = "needs" | "new" | "hot" | "unanswered";

/** One thread of GET /api/threads (threads.ThreadIndex). */
export interface Thread {
  root: string;
  /** The root message's summary. */
  title: string;
  /** The newest message's summary. */
  preview: string;
  /** Who wrote in the thread, first writer first. */
  projects: string[];
  count: number;
  last_t: string;
  last_id: string;
  needs_owner: boolean;
  unanswered: boolean;
  routine: boolean;
  /** Messages of the last hour when two or more participants wrote; 0 for a routine thread. */
  hot: number;
  /** The root message's kind, or "". */
  kind: string;
  /** New messages for the owner; only an owner's read has it. */
  unread?: number;
}

export interface ThreadsPage {
  threads: Thread[];
  /** The cursor of the next page, or null at the end. */
  next: string | null;
  server_time: string;
}

/** A disagreement's ruling, as the present page writes it. */
export interface Resolution {
  choice: string;
  t?: string;
  by?: string;
}

/** A replayed doc of the `messages` collection (PROTOCOL.md, the table of collections). */
export interface MessageDoc {
  t?: string;
  from?: string;
  kind?: string;
  summary?: string;
  body?: string;
  refs?: string | string[];
  replyTo?: string;
  to?: string[];
  important?: boolean;
  positions?: Record<string, string>;
  resolution?: Resolution;
  harness?: string;
  model?: string;
  options?: string[];
  /** Index into `options`, from 0. */
  recommend?: number;
  /** On review rows: `codex-review`, the reviewed project and the status. */
  source?: string;
  project?: string;
  status?: string;
}

/** One message of GET /api/thread/<root>. */
export interface Message extends MessageDoc {
  id: string;
  /** 0 the root, 1 a reply to the root, 2 a deeper reply. */
  depth: 0 | 1 | 2;
  /** The parent's summary at depth 2, else null. */
  quote: string | null;
}

export interface ThreadDetail {
  root: string;
  messages: Message[];
}

/** The sections of GET /api/section/<name> (sections.SECTIONS). */
export type SectionName =
  | "agreements"
  | "decisions"
  | "lessons"
  | "incidents"
  | "locks"
  | "progress"
  | "reviews"
  | "schedules"
  | "adoption";

/** A section row: a copy of its doc with `id` set. The fields depend on the collection. */
export interface SectionRow {
  id: string;
  [field: string]: unknown;
}

export interface SectionPage {
  rows: SectionRow[];
  next: string | null;
  /** How many rows of the whole section (after `q`) are open. */
  open: number;
  total: number;
}

export interface BuildHost {
  reachable: boolean;
  load: number | null;
  t: string | null;
}

/** GET /api/vitals (vitals.read, plus model_families from config.json). */
export interface Vitals {
  disk_free_gb: number;
  usage: number | null;
  usage_limit: number | null;
  build_host: BuildHost | null;
  /** [pattern, family] pairs. */
  model_families: [string, string][];
}

/** GET /api/me: the role of the caller. */
export interface Me {
  name: string | null;
  isOwner: boolean;
  canEdit: boolean;
  /** owner, editor or viewer. */
  viewer: string;
}

export interface ReadMark {
  t: string;
  id: string;
}

/** GET and PUT /api/read-state (readstate.py). */
export interface ReadState {
  threads: Record<string, ReadMark>;
  muted: string[];
  all_before: string | null;
}

/** The three bodies PUT /api/read-state takes. */
export type ReadStatePut =
  | { thread: string; t: string; id: string }
  | { all_before: string }
  | { thread: string; muted: boolean };

/** The code an install runs (service.version). */
export interface Version {
  sha: string | null;
  /** deployment, checkout, or null. */
  source: string | null;
  ref?: string | null;
  time?: string | null;
}

/** GET /api/health. */
export interface Health {
  ok: boolean;
  server_time: string;
  sha: string | null;
  version: Version;
  fatal: string[];
}

/** POST /api/login takes a PIN or the owner token. */
export type LoginBody = { pin: string } | { token: string };

export interface LoginResult {
  ok: boolean;
  viewer: string;
}

/** One land run that holds the queue (landqueue.land_queue, `holders`). */
export interface LandHolder {
  project: string;
  /** The id of the LAND START or LANE START message. */
  id: string;
  since: string;
  /** LAND START or LANE START. */
  word: string;
  branches: string[];
  /** The TURN ASK this run answers, or null. */
  ask: string | null;
}

/** One TURN ASK that waits (landqueue.land_queue, `waiting`). */
export interface LandWaiting {
  project: string;
  id: string;
  t: string;
  branches: string[];
  /** The project holds a run already. */
  holding: boolean;
  /** The id of a later TURN ASK that added branches. */
  updated?: string;
  /** The GRANTED message id, its time and the branches it names. */
  granted?: string;
  granted_t?: string;
  granted_branches?: string[];
  grant_lapsed?: boolean;
}

/** GET /api/queue: the land queue of the build host (landqueue.py). */
export interface LandQueue {
  holders: LandHolder[];
  waiting: LandWaiting[];
  /** The project whose turn comes next, or null. */
  next: string | null;
  slots_free: number;
  may_start: boolean;
  max_holders: number;
  /** Traffic that breaks the queue rules. */
  notes: string[];
}

/** One entry of participants.json, as GET /api/snapshot serves it (PROTOCOL.md, "Participants and trust"). */
export interface RosterEntry {
  id: string;
  name?: string;
  /** project, agent, reviewer, researcher, tool or person. */
  kind?: string;
  /** `#rrggbb` for the light theme and for the dark theme. */
  color?: string;
  colorDark?: string;
  harness?: string | null;
  checkInMin?: number;
  parent?: string;
  trust?: string;
}

/** GET /api/snapshot: every collection replayed and the roster. Only the fields the page reads are typed. */
export interface Snapshot {
  server_time: string;
  roster: Record<string, RosterEntry>;
  collections: Record<string, Record<string, unknown>>;
}

/** One new log line of an `event: rows` on GET /api/events (events._row). */
export interface RowRef {
  from: string;
  seq: number | null;
  collection: string | null;
  id: string | null;
}
