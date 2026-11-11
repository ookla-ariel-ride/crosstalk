// The answer of an open question: one button for each choice, the
// recommended one marked in words, and always "Write an answer". A choice
// sends what the present page's answer form sends, with the choice as text.
import { useRef, useState } from "react";
import { useRoster } from "../../api/queries";
import type { Message } from "../../api/types";
import { choices } from "../../logic/choices";
import { answer, newOwnId } from "./actions";
import { Composer } from "./Composer";
import styles from "./owner.module.css";
import { Failure, useSend } from "./shared";

export function AnswerButtons({ question }: { question: Message }) {
  const { options, recommend } = choices(question);
  const roster = useRoster().data?.roster;
  // The answer names the asker only when the roster holds it, as the present page does.
  const asker = question.from && roster && Object.hasOwn(roster, question.from) ? question.from : undefined;
  // One question gets one answer: a sent answer, by a choice or by the composer,
  // shuts every control here until the refetched thread shows it answered.
  const sending = useSend(true);
  const id = useRef<string | null>(null);
  const [writing, setWriting] = useState(false);
  const [sent, setSent] = useState(false);
  const shut = sending.busy || sending.done || sent;

  async function pick(text: string) {
    id.current ??= newOwnId();
    await sending.run(() => answer(question.id, text, asker, id.current as string));
  }

  return (
    <div className={styles.actions} role="group" aria-label="Answer">
      {options.map((option, i) => (
        <button key={i} type="button" className={styles.button} disabled={shut} onClick={() => void pick(option)}>
          {option}
          {i === recommend ? <> <span className={styles.tag}>Recommended</span></> : null}
        </button>
      ))}
      <button type="button" className={styles.button} disabled={shut} aria-expanded={writing} onClick={() => setWriting(true)}>
        Write an answer
      </button>
      {shut && !sending.busy ? <span className={styles.state}>Answer sent</span> : null}
      <Failure error={sending.error} />
      {writing && !shut ? (
        <Composer
          key={question.id}
          label="Your answer"
          send={async (_, text, at) => {
            await answer(question.id, text, asker, at);
            setSent(true);
          }}
          onClose={() => setWriting(false)}
        />
      ) : null}
    </div>
  );
}
