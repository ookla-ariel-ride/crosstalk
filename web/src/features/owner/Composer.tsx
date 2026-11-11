// The owner's writing box: a summary field where the present page has one,
// and the text. Ctrl+Enter or Cmd+Enter sends; Escape closes only an empty
// box. The draft lives in this component's state: give it `key={target id}`
// so each target keeps its own draft and none reaches storage. A failed send
// keeps the draft and its message id, so sending again rewrites the same row.
import { type FormEvent, type KeyboardEvent, useId, useRef, useState } from "react";
import { newOwnId } from "./actions";
import styles from "./owner.module.css";
import { Failure, useSend } from "./shared";

export interface ComposerProps {
  /** The label of the text box. */
  label: string;
  /** Show the summary field. */
  summary?: boolean;
  send: (summary: string, text: string, id: string) => Promise<unknown>;
  onClose: () => void;
}

export function Composer({ label, summary = false, send, onClose }: ComposerProps) {
  const [subject, setSubject] = useState("");
  const [text, setText] = useState("");
  const id = useRef<string | null>(null);
  const sending = useSend();
  const ids = useId();
  const empty = !subject.trim() && !text.trim();

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (empty) return sending.fail("Write something first.");
    id.current ??= newOwnId();
    if (await sending.run(() => send(subject, text, id.current as string))) {
      id.current = null;
      onClose();
    }
  }

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void submit();
    } else if (e.key === "Escape" && empty) {
      e.preventDefault();
      onClose();
    }
  }

  return (
    <form className={styles.composer} onSubmit={submit} onKeyDown={onKeyDown}>
      {summary ? (
        <>
          <label htmlFor={ids + "s"}>Summary</label>
          <input id={ids + "s"} value={subject} onChange={(e) => setSubject(e.target.value)} disabled={sending.busy} />
        </>
      ) : null}
      <label htmlFor={ids + "t"}>{label}</label>
      <textarea id={ids + "t"} rows={4} autoFocus value={text} onChange={(e) => setText(e.target.value)} disabled={sending.busy} />
      <span className={styles.inline}>
        <button type="submit" className={styles.primary} disabled={sending.busy}>
          Send
        </button>
        <button type="button" className={styles.button} onClick={onClose} disabled={sending.busy}>
          Cancel
        </button>
        <Failure error={sending.error} />
      </span>
    </form>
  );
}
