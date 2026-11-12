// The People page: the roster on the shared list, the owner sign-in, and for
// the owner the approval of a participant and the Add form, whose kit and
// token show once, in this page's state only.
import { type FormEvent, useState } from "react";
import { useMe, useRoster, useVitals } from "../../api/queries";
import type { OnboardResult, RosterEntry } from "../../api/types";
import { ago } from "../../logic/ago";
import { family } from "../../logic/family";
import { Row } from "../../ui/Row";
import { RowList } from "../../ui/RowList";
import { useNow } from "../../ui/useNow";
import { type AddForm, addParticipant, approveParticipant } from "./actions";
import styles from "./owner.module.css";
import { ActionButton, Failure, useRecords, useSend } from "./shared";
import { SignIn } from "./SignIn";

/** The harnesses of the present page's Add form, by id. */
export const HARNESSES: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  "gemini-cli": "Gemini CLI",
  copilot: "GitHub Copilot",
  cursor: "Cursor",
  hermes: "Hermes",
  opencode: "OpenCode",
  pi: "Pi",
};
const KINDS = ["project", "agent", "reviewer", "researcher", "tool", "person"];
const EMPTY: AddForm = { id: "", name: "", kind: "project", repo: "", parent: "", harness: "", checkInMin: "" };
const FIELDS: [keyof AddForm, string][] = [
  ["id", "Id"],
  ["name", "Name"],
  ["repo", "Repo (optional)"],
  ["parent", "Parent (agents only)"],
  ["checkInMin", "Check-in in minutes (optional)"],
];

function Add() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [result, setResult] = useState<OnboardResult | null>(null);
  const sending = useSend();
  const set = (k: keyof AddForm) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!form.id.trim() || !form.name.trim()) return sending.fail("Give an id and a name.");
    let got: OnboardResult | null = null;
    if (await sending.run(async () => (got = await addParticipant(form)), "Not added")) {
      setResult(got);
      setForm(EMPTY);
    }
  }

  if (!open)
    return (
      <button type="button" className={styles.button} onClick={() => setOpen(true)}>
        Add participant
      </button>
    );
  return (
    <>
      <form className={styles.form} onSubmit={submit} aria-label="Add participant">
        {FIELDS.filter(([k]) => k !== "parent" || form.kind === "agent").map(([k, label]) => (
          <label key={k}>
            {label}
            <input value={form[k]} onChange={set(k)} disabled={sending.busy} />
          </label>
        ))}
        <label>
          Kind
          <select value={form.kind} onChange={set("kind")} disabled={sending.busy}>
            {KINDS.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>
        <label>
          Harness (optional)
          <select value={form.harness} onChange={set("harness")} disabled={sending.busy}>
            <option value="">none</option>
            {Object.entries(HARNESSES).map(([k, name]) => (
              <option key={k} value={k}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <span className={styles.inline}>
          <button type="submit" className={styles.primary} disabled={sending.busy}>
            Add
          </button>
          <button
            type="button"
            className={styles.button}
            onClick={() => {
              // The kit and the token show once: Close drops them.
              setOpen(false);
              setResult(null);
            }}
          >
            Close
          </button>
          <Failure error={sending.error} />
        </span>
      </form>
      {result ? (
        <div className={styles.form}>
          <label>
            Kit
            <textarea readOnly rows={6} value={result.kit} />
          </label>
          <button type="button" className={styles.button} onClick={() => void navigator.clipboard?.writeText(result.kit)}>
            Copy kit
          </button>
          <label>
            Token (shown once)
            <input readOnly value={result.token} />
          </label>
        </div>
      ) : null}
    </>
  );
}

/** A participant's harness: its own, else its parent's (harnessOf of the present page). */
function harnessOf(entry: RosterEntry, roster: Record<string, RosterEntry>): string | null {
  const h = entry.harness || (entry.parent && Object.hasOwn(roster, entry.parent) ? roster[entry.parent]?.harness : null);
  return h ? (HARNESSES[h] ?? h) : null;
}

export function People() {
  const now = useNow();
  const me = useMe().data;
  const roster = useRoster().data?.roster ?? {};
  const seen = useRecords().data?.seen ?? {};
  const extra = useVitals().data?.model_families;
  const [open, setOpen] = useState<string | null>(null);
  const people = Object.values(roster)
    .filter((p) => p && p.id && p.id !== "owner")
    .sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));

  return (
    <div className={styles.page}>
      <div className={styles.top}>
        <h1 className={styles.heading}>People</h1>
        {me?.isOwner ? <p className={styles.state}>You are signed in as the owner.</p> : null}
        {me && !me.isOwner ? <SignIn /> : null}
        {me?.isOwner ? <Add /> : null}
      </div>
      <RowList
        label="People"
        items={people}
        itemKey={(p) => p.id}
        estimateSize={64}
        renderRow={(p) => {
          const approved = p.trust === "approved";
          const last = seen[p.id];
          const text = [p.kind, harnessOf(p, roster), family(last?.model, extra), approved ? "" : "not approved"].filter(Boolean).join(", ");
          return (
            <Row
              projects={[p.id]}
              title={p.name || p.id}
              text={text}
              note={last ? ago(last.t, now) : "not seen"}
              open={open === p.id}
              onOpen={() => setOpen(open === p.id ? null : p.id)}
            >
              <p className={styles.state}>
                {[p.id, p.parent ? "parent " + p.parent : "", p.checkInMin ? "checks in every " + p.checkInMin + " min" : "", approved ? "approved" : "not approved"]
                  .filter(Boolean)
                  .join(", ")}
              </p>
              {me?.isOwner && !approved ? <ActionButton label="Approve" done="Approved" failed="Not approved" act={() => approveParticipant(p.id)} /> : null}
            </Row>
          );
        }}
      />
    </div>
  );
}
