// An open thread: the read messages folded behind one line, the line "New
// since HH:MM", then the new ones. The thread is marked read when its last new
// message was in view for a second, or on "Mark read".
import { type ReactNode, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { useRoster, useThread } from "../../api/queries";
import type { Message, ReadMark, Thread } from "../../api/types";
import { ago } from "../../logic/ago";
import { speaker } from "../../logic/speaker";
import { splitRead } from "../../logic/splitRead";
import { Body } from "../../ui/Body";
import { isLater, markOf, useReading } from "./reading";
import { WatchContext } from "./seen";
import styles from "./Stream.module.css";

/** What Task 13 adds: the owner's actions under a message, or at the end of the thread (message null). */
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
      <Body text={message.body || message.summary || ""} />
      {children}
    </article>
  );
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
  // The split stays as it was when the thread opened, so a mark does not
  // fold the messages being read.
  const [split, setSplit] = useState<ReadMark | undefined | null>(null);
  const lastFresh = useRef<HTMLDivElement>(null);

  const messages = query.data?.messages;
  const ready = messages !== undefined && reading.ready;
  if (ready && split === null) setSplit(markOf(reading.state, thread.root));

  const top = messages ? newest(messages) : undefined;
  const markRead = () => {
    if (top) reading.markThread(thread.root, top);
  };
  const markReadRef = useRef(markRead);
  markReadRef.current = markRead;

  // splitRead keeps each message object; its fresh part names which of ours are new.
  const parts = useMemo(() => {
    if (!messages || split === null) return null;
    const fresh = new Set(splitRead(messages, split).fresh);
    return { earlier: messages.filter((m) => !fresh.has(m)), fresh: messages.filter((m) => fresh.has(m)) };
  }, [messages, split]);
  const freshCount = parts?.fresh.length ?? 0;
  useEffect(() => {
    if (!lastFresh.current || freshCount === 0) return;
    return watch(lastFresh.current, () => markReadRef.current());
  }, [watch, freshCount]);

  if (query.isError) {
    const denied = query.error instanceof ApiError && query.error.status === 403;
    return (
      <p className={styles.state}>
        {denied ? "This thread needs you to sign in. Sign in on the People page." : "The thread did not load. Fold it and open it again."}
      </p>
    );
  }
  if (!parts) return <p className={styles.state}>Loading.</p>;

  const { earlier, fresh } = parts;
  const view = (m: Message) => (
    <MessageView key={m.id} message={m} roster={roster} now={now}>
      {renderActions?.(thread, m)}
    </MessageView>
  );
  const unread = top !== undefined && isLater(top, markOf(reading.state, thread.root));

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
        {renderActions?.(thread, null)}
      </div>
    </div>
  );
}
