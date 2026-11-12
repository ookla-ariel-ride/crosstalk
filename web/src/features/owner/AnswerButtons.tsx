// The answer of an open question: one button for each choice, the
// recommended one marked in words, and always "Write an answer". A choice
// sends what the present page's answer form sends, with the choice as text.
import { useRef, useState } from "react";
import type { Message } from "../../api/types";
import { choices } from "../../logic/choices";
import { answer, newOwnId } from "./actions";
import { Composer } from "./Composer";
import { drafts } from "./drafts";
import styles from "./owner.module.css";
import { Failure, useOwnerData, useSend } from "./shared";

export function AnswerButtons({ question }: { question: Message }) {
  const { options, recommend } = choices(question);
  const { roster } = useOwnerData();
  // The answer names the asker only when the roster holds it, as the present page does.
  const asker = question.from && roster && Object.hasOwn(roster, question.from) ? question.from : undefined;
  // One question gets one answer: a choice and the composer share one lock and
  // one message id, so a second answer cannot start while one is on its way, and
  // a try after a failure rewrites the same row. A sent answer shuts every
  // control here until the refetched thread shows it answered.
  const sending = useSend(true);
  const lock = useRef(false);
  const [writingBusy, setWritingBusy] = useState(false);
  const id = useRef<string | null>(null);
  const draft = "answer:" + question.id;
  // A draft kept from an unmounted row opens the box again, with its id.
  const answerId = () => (id.current ??= drafts.get(draft)?.id ?? newOwnId());
  const [writing, setWriting] = useState(() => drafts.has(draft));
  const [sent, setSent] = useState(false);
  const shut = sending.busy || sending.done || sent;

  async function pick(text: string) {
    if (lock.current) return;
    lock.current = true;
    // An answer sent by a choice leaves no half-written answer behind; only a
    // failure opens the lock again.
    if (await sending.run(() => answer(question.id, text, asker, answerId()))) drafts.delete(draft);
    else lock.current = false;
  }

  async function write(text: string, at: string) {
    if (lock.current) throw new Error("Another answer is on its way");
    lock.current = true;
    setWritingBusy(true);
    try {
      await answer(question.id, text, asker, at);
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
      {writing && !shut ? (
        <Composer
          key={question.id}
          label="Your answer"
          draft={draft}
          newId={answerId}
          send={(_, text, at) => write(text, at)}
          onClose={() => setWriting(false)}
        />
      ) : null}
    </div>
  );
}
