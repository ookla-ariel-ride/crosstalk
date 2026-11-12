// An open thread: the read messages folded behind one line, the line "New
// since HH:MM", then the new ones. The thread is marked read when its last new
// message was in view for a second, or on "Mark read".
import { type ReactNode, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { useRoster, useThread } from "../../api/queries";
import type { Message, ReadMark, Thread } from "../../api/types";
import { afterSummary } from "../../logic/afterSummary";
import { ago } from "../../logic/ago";
import { speaker } from "../../logic/speaker";
import { splitRead } from "../../logic/splitRead";
import { Body } from "../../ui/Body";
import { Guard } from "../../ui/Guard";
import { isLater, markOf, useReading } from "./reading";
import { WatchContext } from "./seen";
import styles from "./Stream.module.css";

/** The owner's actions under a message, or at the end of the thread (message null). */
export type RenderActions = (thread: Thread, message: Message | null) => ReactNode;

const pad = (n: number) => String(n).padStart(2, "0");

/** "09:20" in the reader's local time. */
function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : pad(d.getHours()) + ":" + pad(d.getMinutes());
}

function fullTime(iso: string | undefined): string {
  const d = new Date(iso ?? "");
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

function newest(messages: Message[]): ReadMark | undefined {
  let top: ReadMark | undefined;
  for (const m of messages) {
    if (typeof m.t !== "string") continue;
    const mark = { t: m.t, id: m.id };
    if (isLater(mark, top)) top = mark;
  }
  return top;
}

function MessageView({ message, roster, now, children }: { message: Message; roster: Parameters<typeof speaker>[1]; now: Date; children?: ReactNode }) {
  const t = message.t ?? "";
  // The summary, then the body without a first line that repeats it: an answer
  // keeps its first line (the summary) above its explanation (the body).
  const summary = (message.summary ?? "").trim();
  const rest = afterSummary(message.body ?? "", summary);
  const quote = message.depth === 2 && message.quote ? message.quote.split("\n")[0] : "";
  return (
    <article className={styles.message} data-depth={message.depth}>
      {quote ? <blockquote className={styles.quote}>{quote}</blockquote> : null}
      <div className={styles.meta}>
        <span className={styles.who}>{speaker(message, roster)}</span>
        <time className={styles.when} dateTime={t} title={fullTime(t)}>
          {ago(t, now)}
        </time>
      </div>
      {summary ? (
        <div className={styles.summary}>
          <Body text={summary} />
        </div>
      ) : null}
      {rest ? <Body text={rest} /> : null}
      {children}
    </article>
  );
}

/**
 * The messages split at the mark, with the reader's own posts never new: a
 * run of own posts at the start of the new part joins the earlier part, so
 * the line "New since" sits above the first new post by someone else.
 */
export function splitOwn(messages: Message[], mark: ReadMark | undefined, self: string | null) {
  const freshSet = new Set(splitRead(messages, mark).fresh);
  const fresh = messages.filter((m) => freshSet.has(m));
  const first = fresh.findIndex((m) => self === null || m.from !== self);
  const cut = first === -1 ? fresh.length : first;
  const moved = new Set(fresh.slice(0, cut));
  return {
    earlier: messages.filter((m) => !freshSet.has(m) || moved.has(m)),
    fresh: fresh.slice(cut),
  };
}

export interface ThreadOpenProps {
  thread: Thread;
  now: Date;
  renderActions?: RenderActions;
}

export function ThreadOpen({ thread, now, renderActions }: ThreadOpenProps) {
  const query = useThread(thread.root);
  const roster = useRoster().data?.roster ?? {};
  const reading = useReading();
  const watch = useContext(WatchContext);
  const [showEarlier, setShowEarlier] = useState(false);
  const [muteFailed, setMuteFailed] = useState<boolean | null>(null);
  // The split stays as it was when the thread opened, so a mark does not
  // fold the messages being read.
  const [split, setSplit] = useState<ReadMark | undefined | null>(null);
  const lastFresh = useRef<HTMLDivElement>(null);

  const messages = query.data?.messages;
  const ready = messages !== undefined && reading.ready;
  if (ready && split === null) setSplit(markOf(reading.state, thread.root));

  // splitRead keeps each message object; its fresh part names which of ours are new.
  const parts = useMemo(() => (messages && split !== null ? splitOwn(messages, split, reading.self) : null), [messages, split, reading.self]);

  // A mark names the newest message drawn when it is made, never one of a later fetch.
  const top = messages ? newest(messages) : undefined;
  const markRead = () => {
    if (top) reading.markThread(thread.root, top);
  };
  const markThreadRef = useRef(reading.markThread);
  markThreadRef.current = reading.markThread;
  const lastId = parts?.fresh[parts.fresh.length - 1]?.id;
  const topT = top?.t;
  const topId = top?.id;
  useEffect(() => {
    if (!lastFresh.current || lastId === undefined || topT === undefined || topId === undefined) return;
    const drawn = { t: topT, id: topId };
    return watch(lastFresh.current, () => markThreadRef.current(thread.root, drawn));
  }, [watch, lastId, topT, topId, thread.root]);

  if (query.isError) {
    const denied = query.error instanceof ApiError && query.error.status === 403;
    return (
      <p className={styles.state}>
        {denied ? "This thread needs you to sign in. Sign in on the Roster page." : "The thread did not load. Fold it and open it again."}
      </p>
    );
  }
  if (!parts) return <p className={styles.state}>Loading.</p>;

  const { earlier, fresh } = parts;
  const view = (m: Message) => (
    <Guard key={m.id} what={"Message " + m.id}>
      <MessageView message={m} roster={roster} now={now}>
        {renderActions?.(thread, m)}
      </MessageView>
    </Guard>
  );
  // The reader's own posts are never new to the reader.
  const current = markOf(reading.state, thread.root);
  const unread = (messages ?? []).some(
    (m) => m.from !== reading.self && typeof m.t === "string" && isLater({ t: m.t, id: m.id }, current),
  );
  const muted = reading.state.muted.includes(thread.root);

  return (
    <div className={styles.thread}>
      {earlier.length > 0 && !showEarlier ? (
        <button type="button" className={styles.fold} onClick={() => setShowEarlier(true)}>
          {earlier.length === 1 ? "Show 1 earlier message" : `Show ${earlier.length} earlier messages`}
        </button>
      ) : null}
      {showEarlier ? earlier.map(view) : null}
      {earlier.length > 0 && fresh.length > 0 && split ? (
        <p className={styles.newSince}>
          <span>New since {clock(split.t)}</span>
        </p>
      ) : null}
      {fresh.map((m, i) =>
        i === fresh.length - 1 ? (
          <div key={m.id} ref={lastFresh}>
            {view(m)}
          </div>
        ) : (
          view(m)
        ),
      )}
      <div className={styles.end}>
        {unread ? (
          <button type="button" className={styles.action} onClick={markRead}>
            Mark read
          </button>
        ) : null}
        <button
          type="button"
          className={styles.action}
          onClick={() => {
            const next = !muted;
            setMuteFailed(null);
            void reading.mute(thread.root, next).then((ok) => setMuteFailed(ok ? null : next));
          }}
        >
          {muted ? "Unmute thread" : "Mute thread"}
        </button>
        {muteFailed !== null ? (
          <span role="alert" className={styles.error}>
            {muteFailed ? "The thread was not muted" : "The thread was not unmuted"}: the server did not save it. Check that the relay
            server runs, then try again.
          </span>
        ) : null}
        {renderActions?.(thread, null)}
      </div>
    </div>
  );
}
