// The answer of an open question: one button for each choice, the
// recommended one marked in words, and always "Write an answer". A choice
// sends what the present page's answer form sends, with the choice as text.
import { useRef, useState } from "react";
import type { Message } from "../../api/types";
import { choices } from "../../logic/choices";
import { answer, newOwnId } from "./actions";
import { Composer } from "./Composer";
import { drafts, keepDraft } from "./drafts";
import styles from "./owner.module.css";
import { Failure, useOwnerData, useSend } from "./shared";

export function AnswerButtons({ question }: { question: Message }) {
  const { options, recommend } = choices(question);
  const { roster } = useOwnerData();
  // The answer names the asker only when the roster holds it, as the present page does.
  const asker = question.from && roster && Object.hasOwn(roster, question.from) ? question.from : undefined;
  // One page sends one answer for a question: a choice and the composer share
  // one lock and one message id, so a second answer cannot start while one is
  // on its way, and a try after a failure rewrites the same row. A sent answer shuts every
  // control here until the refetched thread shows it answered.
  const sending = useSend(true);
  const lock = useRef(false);
  const [writingBusy, setWritingBusy] = useState(false);
  const id = useRef<string | null>(null);
  // A sent answer: the composer's close then keeps no id.
  const landed = useRef(false);
  const draft = "answer:" + question.id;
  // The id lives in the draft store too, so a row that mounts again after a
  // failed send uses it again: a first try that did land is then rewritten.
  function answerId(): string {
    id.current ??= drafts.get(draft)?.id ?? newOwnId();
    keepDraft(draft, { id: id.current });
    return id.current;
  }
  // A draft with text kept from an unmounted row opens the box again.
  const [writing, setWriting] = useState(() => !!(drafts.get(draft)?.text || drafts.get(draft)?.subject));
  const [sent, setSent] = useState(false);
  // A row that mounts again after a sent answer, before the thread shows it,
  // finds the mark in the draft store and stays shut (OwnerActions drops it).
  const shut = sending.busy || sending.done || sent || drafts.get(draft)?.answered === true;

  async function pick(text: string) {
    if (lock.current) return;
    lock.current = true;
    // An answer sent by a choice leaves no half-written answer behind, only the
    // mark that it was sent; a failure opens the lock again.
    if (await sending.run(() => answer(question.id, text, asker, answerId()))) keepDraft(draft, { subject: "", text: "", answered: true });
    else lock.current = false;
  }

  async function write(text: string, at: string) {
    // A choice clicked in the same frame is on its way: send nothing, keep the
    // text, show no error (false tells the composer so).
    if (lock.current) return false;
    lock.current = true;
    setWritingBusy(true);
    try {
      await answer(question.id, text, asker, at);
      landed.current = true;
      setSent(true);
    } catch (e) {
      lock.current = false;
      throw e;
    } finally {
      setWritingBusy(false);
    }
  }

  return (
    <div className={styles.actions} role="group" aria-label="Answer">
      {options.map((option, i) => (
        <button key={i} type="button" className={styles.button} disabled={shut || writingBusy} onClick={() => void pick(option)}>
          {option}
          {i === recommend ? <> <span className={styles.tag}>Recommended</span></> : null}
        </button>
      ))}
      <button type="button" className={styles.button} disabled={shut || writingBusy} aria-expanded={writing} onClick={() => setWriting(true)}>
        Write an answer
      </button>
      {shut && !sending.busy ? <span className={styles.state}>Answer sent</span> : null}
      <Failure error={sending.error} />
      {/* A choice on its way keeps the box shown but shut, with its text. */}
      {writing && !sending.done && !sent ? (
        <Composer
          key={question.id}
          label="Your answer"
          draft={draft}
          newId={answerId}
          disabled={sending.busy}
          send={(_, text, at) => write(text, at)}
          onClose={() => {
            // A sent answer leaves its mark; Cancel drops the text, not the id
            // of a try that may have landed.
            if (landed.current) keepDraft(draft, { answered: true });
            else if (id.current) keepDraft(draft, { id: id.current });
            setWriting(false);
          }}
        />
      ) : null}
    </div>
  );
}
