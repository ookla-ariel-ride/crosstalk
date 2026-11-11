// Start a thread: the kind of the present composer, who it goes to (none
// ticked is every session), then the summary and the text.
import { useState } from "react";
import { nameOf } from "../../ui/Bars";
import { type Kind, KINDS, splitPost, startThread } from "./actions";
import { Composer } from "./Composer";
import { drafts, keepDraft } from "./drafts";
import styles from "./owner.module.css";
import { useIsOwner, useOwnerData } from "./shared";

const DRAFT = "new";

export function NewThread() {
  const owner = useIsOwner();
  const roster = useOwnerData().roster ?? {};
  // A kept draft opens the form again with its kind and recipients.
  const [open, setOpen] = useState(() => drafts.has(DRAFT));
  const [kind, setKindState] = useState<Kind>(() => (drafts.get(DRAFT)?.kind as Kind | undefined) ?? "owner");
  const [to, setToState] = useState<string[]>(() => drafts.get(DRAFT)?.to ?? []);
  const setKind = (k: Kind) => {
    keepDraft(DRAFT, { kind: k === "owner" ? undefined : k });
    setKindState(k);
  };
  const setTo = (next: string[]) => {
    keepDraft(DRAFT, { to: next });
    setToState(next);
  };
  if (!owner) return null;
  if (!open)
    return (
      <button type="button" className={styles.primary} onClick={() => setOpen(true)}>
        Start a thread
      </button>
    );
  const ids = Object.keys(roster).filter((id) => id !== "owner").sort();
  return (
    <div className={styles.form}>
      <label>
        Kind
        <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          {Object.entries(KINDS).map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <fieldset className={styles.to}>
        <legend>To (none ticked: every session)</legend>
        {ids.map((id) => (
          <label key={id}>
            <input
              type="checkbox"
              checked={to.includes(id)}
              onChange={(e) => setTo(e.target.checked ? [...to, id] : to.filter((x) => x !== id))}
            />
            {nameOf(id, roster)}
          </label>
        ))}
      </fieldset>
      <Composer
        label="Message"
        draft={DRAFT}
        summary
        send={(subject, text, id) => {
          const { summary, body } = splitPost(subject, text);
          return startThread(to, kind, summary, body, id);
        }}
        onClose={() => {
          // The composer dropped the draft; the form starts empty next time.
          setOpen(false);
          setToState([]);
          setKindState("owner");
        }}
      />
    </div>
  );
}
