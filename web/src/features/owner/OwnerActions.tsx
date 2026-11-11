// The owner's controls for one message of a thread, or for the thread when
// `message` is null. ThreadOpen takes them through its `renderActions` slot,
// inside an OwnerScope that reads the roster once for all of them. A viewer
// gets a "Sign in" link where an action waits (the thread, an open question)
// and nothing else; while the role loads, nothing shows. The server still guards each write.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { keys } from "../../api/queries";
import type { Message, RosterEntry, Thread } from "../../api/types";
import { nameOf } from "../../ui/Bars";
import { approveParticipant, holdAgreement, markDone, recordDecision, reply, splitPost } from "./actions";
import { AnswerButtons } from "./AnswerButtons";
import { Composer } from "./Composer";
import { drafts } from "./drafts";
import styles from "./owner.module.css";
import { ActionButton, SIGN_IN, useIsOwner, useOwnerData } from "./shared";

type Roster = Record<string, RosterEntry> | undefined;

const has = (roster: Roster, id: string | undefined): id is string => !!id && !!roster && Object.hasOwn(roster, id);

/** "open", "answered" or "withdrawn" for a question to the owner; null for any other message. */
export function questionState(q: Message, messages: Message[], roster: Record<string, RosterEntry>): string | null {
  const asker = q.from;
  if (q.kind !== "question" || !asker || asker === "owner") return null;
  // A question from a participant the owner has not approved is not listed for an answer (isQuarantined).
  if (roster[asker]?.trust !== "approved") return null;
  const closer = messages
    .filter((m) => m.replyTo === q.id && (m.from === "owner" || (m.from === asker && m.kind === "withdrawn")))
    .sort((a, b) => String(a.t ?? "").localeCompare(String(b.t ?? "")) || a.id.localeCompare(b.id))[0];
  return closer ? (closer.from === "owner" ? "answered" : "withdrawn") : "open";
}

/** A reply to `id`; `draft` keys its draft: the thread's end and its root message each keep their own. */
function ReplyControl({ id, draft, from, roster }: { id: string; draft: string; from?: string; roster: Roster }) {
  // A draft kept from an unmounted row opens the box again.
  const [open, setOpen] = useState(() => drafts.has(draft));
  const to = from !== "owner" && has(roster, from) ? [from] : [];
  return (
    <>
      <button type="button" className={styles.button} aria-expanded={open} onClick={() => setOpen(true)}>
        Reply
      </button>
      {open ? (
        <Composer
          key={id}
          label="Message"
          draft={draft}
          summary
          send={(subject, text, at) => {
            const { summary, body } = splitPost(subject, text);
            return reply(id, summary, body, to, at);
          }}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

export function OwnerActions({ thread, message }: { thread: Thread; message: Message | null }) {
  const owner = useIsOwner();
  const { roster, records } = useOwnerData();
  // The open thread is in the cache already; the thread level needs no fetch.
  const detail = useQuery({ queryKey: keys.thread(thread.root), queryFn: () => api.getThread(thread.root), enabled: message !== null });
  // Until the thread and the roster are both in, no answer control shows: an
  // answered question, or one whose asker is not approved, must not look open.
  const state = message && detail.data && roster ? questionState(message, detail.data.messages, roster) : null;

  if (owner === null) return null;
  if (!owner)
    return message === null || state === "open" ? (
      <a className={styles.link} href={SIGN_IN}>
        Sign in
      </a>
    ) : null;

  if (!message)
    return (
      <div className={styles.actions}>
        <ReplyControl id={thread.root} draft={"thread:" + thread.root} from={thread.projects[0]} roster={roster} />
      </div>
    );

  const from = message.from ?? "";
  const recorded = !!records && (records.decisions.has(message.id) || records.decisions.has("d-from-" + message.id));
  return (
    <div className={styles.actions}>
      {state === "open" ? <AnswerButtons question={message} /> : null}
      {state === "answered" || state === "withdrawn" ? <span className={styles.state}>{state === "answered" ? "Answered" : "Withdrawn"}</span> : null}
      <ReplyControl id={message.id} draft={"reply:" + message.id} from={message.from} roster={roster} />
      {from !== "owner" && has(roster, from) && roster?.[from]?.trust !== "approved" ? (
        <ActionButton
          label={"Approve " + nameOf(from, roster)}
          done="Approved"
          failed="Not approved"
          act={() => approveParticipant(from)}
        />
      ) : null}
      {message.kind !== "decision" ? (
        recorded ? (
          <button type="button" className={styles.button} disabled>
            Recorded
          </button>
        ) : (
          <ActionButton label="Record as decision" done="Recorded" act={() => recordDecision(message)} />
        )
      ) : null}
      {records?.waiting.has(message.id) ? <MarkDoneButton id={message.id} /> : null}
    </div>
  );
}

/** Mark a waiting row done (the present page's Waiting tab). The owner alone sees it. */
export function MarkDoneButton({ id }: { id: string }) {
  if (!useIsOwner()) return null;
  return <ActionButton label="Mark done" done="Done" act={() => markDone(id)} />;
}

/** Hold or release an agreement (the present page's Agreements tab). The owner alone sees it. */
export function HoldButton({ agreement, held }: { agreement: { id: string; status?: unknown }; held: boolean }) {
  if (!useIsOwner()) return null;
  // The key opens a fresh button once the refetched agreement shows the new hold.
  return <ActionButton key={String(held)} label={held ? "Release hold" : "Hold"} done="Sent" act={() => holdAgreement(agreement, !held)} />;
}

/** The owner's controls for the `renderActions` slot of Task 12's ThreadOpen. */
export function ownerActions(thread: Thread, message: Message | null) {
  return <OwnerActions thread={thread} message={message} />;
}
