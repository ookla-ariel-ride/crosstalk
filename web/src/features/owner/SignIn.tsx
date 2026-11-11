// The owner sign-in: the PIN or the owner token, typed into a password field.
// The secret goes only into the body of POST /api/login; it lives in this
// component's state until the request ends and is cleared then, on success
// and on failure. No query key, storage or log line holds it.
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { keys } from "../../api/queries";
import { signIn } from "./actions";
import styles from "./owner.module.css";
import { Failure, useSend } from "./shared";

export function SignIn() {
  const qc = useQueryClient();
  const [token, setToken] = useState(false);
  const [value, setValue] = useState("");
  const sending = useSend();

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!value.trim()) return sending.fail("Enter the PIN or the owner token.");
    const secret = value;
    const ok = await sending.run(() => signIn(secret), "Not signed in");
    setValue("");
    // The role and everything that depends on it (read state, unread counts) come again.
    // useMe() refetches even where no component shows it now.
    if (ok) await Promise.all([qc.invalidateQueries({ queryKey: keys.me, refetchType: "all" }), qc.invalidateQueries()]);
  }

  const label = token ? "Owner token" : "PIN";
  return (
    <form className={styles.form} onSubmit={submit} aria-label="Owner sign-in">
      <label htmlFor="owner-secret">{label}</label>
      <input
        id="owner-secret"
        type="password"
        autoComplete="current-password"
        inputMode={token ? undefined : "numeric"}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        disabled={sending.busy}
      />
      <span className={styles.inline}>
        <button type="submit" className={styles.primary} disabled={sending.busy}>
          Sign in
        </button>
        <button type="button" className={styles.button} onClick={() => setToken(!token)}>
          {token ? "Use the PIN" : "Use the owner token"}
        </button>
        {/* A 401 here is a wrong secret, not a lost session. */}
        <Failure error={sending.error && { ...sending.error, auth: false }} />
      </span>
    </form>
  );
}
