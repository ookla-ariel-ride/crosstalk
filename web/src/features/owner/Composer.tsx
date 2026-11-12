// The owner's writing box: a summary field where the present page has one,
// and the text. Ctrl+Enter or Cmd+Enter sends; Escape closes only an empty
// box. The draft lives in the memory store of drafts.ts under `draft`, its
// target, so a row that unmounts keeps it and none reaches storage. A failed
// send keeps the draft and its message id, so sending again rewrites the same
// row; a sent draft, or one cancelled, is gone.
import { type FormEvent, type KeyboardEvent, useId, useRef, useState } from "react";
import { newOwnId } from "./actions";
import { drafts, keepDraft } from "./drafts";
import styles from "./owner.module.css";
import { Failure, useSend } from "./shared";

export interface ComposerProps {
  /** The label of the text box. */
  label: string;
  /** The key of the draft: what it answers, such as "reply:" and a message id. */
  draft: string;
  /** Show the summary field. */
  summary?: boolean;
  send: (summary: string, text: string, id: string) => Promise<unknown>;
  onClose: () => void;
  /** The message id of the first send when the draft has none; a new one by default. */
  newId?: () => string;
}

export function Composer({ label, draft, summary = false, send, onClose, newId = newOwnId }: ComposerProps) {
  const [kept] = useState(() => drafts.get(draft));
  const [subject, setSubject] = useState(kept?.subject ?? "");
  const [text, setText] = useState(kept?.text ?? "");
  const id = useRef<string | null>(kept?.id ?? null);
  const sending = useSend();
  const ids = useId();
  const empty = !subject.trim() && !text.trim();

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (empty) return sending.fail("Write something first.");
    id.current ??= newId();
    keepDraft(draft, { subject, text, id: id.current });
    if (await sending.run(() => send(subject, text, id.current as string))) close();
  }

  function close() {
    drafts.delete(draft);
    onClose();
  }

  function edit(next: { subject?: string; text?: string }) {
    const d = { subject, text, id: id.current, ...next };
    keepDraft(draft, d);
    setSubject(d.subject);
    setText(d.text);
  }

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void submit();
    } else if (e.key === "Escape" && empty) {
      e.preventDefault();
      close();
    }
  }

  return (
    <form className={styles.composer} onSubmit={submit} onKeyDown={onKeyDown}>
      {summary ? (
        <>
          <label htmlFor={ids + "s"}>Summary</label>
          <input id={ids + "s"} value={subject} onChange={(e) => edit({ subject: e.target.value })} disabled={sending.busy} />
        </>
      ) : null}
      <label htmlFor={ids + "t"}>{label}</label>
      <textarea id={ids + "t"} rows={4} autoFocus value={text} onChange={(e) => edit({ text: e.target.value })} disabled={sending.busy} />
      <span className={styles.inline}>
        <button type="submit" className={styles.primary} disabled={sending.busy}>
          Send
        </button>
        <button type="button" className={styles.button} onClick={close} disabled={sending.busy}>
          Cancel
        </button>
        <Failure error={sending.error} />
      </span>
    </form>
  );
}
