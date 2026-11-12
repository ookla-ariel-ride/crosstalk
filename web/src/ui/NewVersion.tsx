// The bar that offers a reload after a deploy. The page keeps the build that
// /api/vitals named first (web/dist/.build-hash, which changes exactly when the
// page files change) and reads it again on the vitals poll each minute. It
// never reloads by itself: a draft may be open.
import { useRef } from "react";
import { useVitals } from "../api/queries";
import styles from "./NewVersion.module.css";

export function NewVersion({ reload = () => window.location.reload() }: { reload?: () => void }) {
  const build = useVitals().data?.page_build ?? null;
  const first = useRef<string | null>(null);
  if (first.current === null && build !== null) first.current = build;
  if (build === null || build === first.current) return null;
  return (
    <div className={styles.bar} role="status">
      <span>A new version of this page is ready.</span>{" "}
      <button type="button" className={styles.button} onClick={reload}>
        Reload
      </button>
    </div>
  );
}
