// What every owner control shares: who may see it, one send at a time, the
// error next to the control, and the owner rows the snapshot holds.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import { keys, useMe } from "../../api/queries";
import type { Snapshot } from "../../api/types";
import { href } from "../../logic/route";
import styles from "./owner.module.css";

export const SIGN_IN = href({ name: "people" });

/** true for the owner, false for a viewer, null while the role loads. */
export function useIsOwner(): boolean | null {
  const me = useMe().data;
  return me ? me.isOwner === true : null;
}

export interface Failed {
  text: string;
  /** The server said 401 or 403: the session is gone. */
  auth: boolean;
}

/** Run one request at a time: a second call while one runs does nothing. On success the owner's data is fetched again. */
export function useSend() {
  const qc = useQueryClient();
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Failed | null>(null);
  async function run(fn: () => Promise<unknown>, failed = "Not sent"): Promise<boolean> {
    if (running.current) return false;
    running.current = true;
    setBusy(true);
    setError(null);
    try {
      await fn();
      for (const key of [["threads"], ["thread"], ["section"], keys.roster]) void qc.invalidateQueries({ queryKey: key });
      return true;
    } catch (e) {
      const auth = e instanceof ApiError && (e.status === 401 || e.status === 403);
      setError({ text: `${failed}: ${e instanceof Error ? e.message : String(e)}.`, auth });
      return false;
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  return { busy, error, run, fail: (text: string) => setError({ text, auth: false }) };
}

/** The error of a control, with what to do next. */
export function Failure({ error }: { error: Failed | null }) {
  if (!error) return null;
  return (
    <span role="alert" className={styles.error}>
      {error.text}{" "}
      {error.auth ? <a href={SIGN_IN}>Sign in again</a> : "Check it and try again."}
    </span>
  );
}

/** One button that sends one action; `done` is its label after a success. */
export function ActionButton({ label, done, act, failed }: { label: ReactNode; done?: string; act: () => Promise<unknown>; failed?: string }) {
  const send = useSend();
  const [ok, setOk] = useState(false);
  return (
    <span className={styles.inline}>
      <button
        type="button"
        className={styles.button}
        disabled={send.busy || (ok && !!done)}
        onClick={() => void send.run(act, failed).then(setOk)}
      >
        {ok && done ? done : label}
      </button>
      <Failure error={send.error} />
    </span>
  );
}

export interface Seen {
  t: string;
  model?: string;
}

export interface Records {
  /** Ids of waiting rows not done yet. */
  waiting: Set<string>;
  /** Ids of decision rows. */
  decisions: Set<string>;
  /** Each sender's newest message, and the model of its newest message that names one. */
  seen: Record<string, Seen>;
}

function recordsOf(snap: Snapshot): Records {
  const c = snap.collections ?? {};
  const waiting = new Set(Object.entries(c.waiting ?? {}).filter(([, w]) => w && !(w as { done?: unknown }).done).map(([id]) => id));
  const seen: Record<string, Seen> = {};
  const modelAt: Record<string, number> = {};
  for (const doc of Object.values(c.messages ?? {})) {
    const { from, t, model } = (doc ?? {}) as { from?: unknown; t?: unknown; model?: unknown };
    const at = typeof t === "string" ? Date.parse(t) : NaN;
    if (typeof from !== "string" || Number.isNaN(at)) continue;
    const s = (seen[from] ??= { t: t as string });
    if (at > Date.parse(s.t)) s.t = t as string;
    if (typeof model === "string" && model && !(at < (modelAt[from] ?? -Infinity))) {
      s.model = model;
      modelAt[from] = at;
    }
  }
  return { waiting, decisions: new Set(Object.keys(c.decisions ?? {})), seen };
}

/** The owner's rows of the snapshot that the roster query reads already. */
export function useRecords() {
  return useQuery({ queryKey: keys.roster, queryFn: api.getSnapshot, select: recordsOf, refetchInterval: 60_000 });
}
