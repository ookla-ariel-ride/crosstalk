// The bar that offers a reload after a deploy. The page knows its own build
// from the crosstalk-build meta that `npm run build` writes into index.html
// (web/dist/.build-hash, which changes exactly when the page files change), or,
// with no meta, from the first build /api/vitals names. It reads the vitals
// again each minute. It never reloads by itself: a draft may be open.
import { useRef, useState } from "react";
import { useVitals } from "../api/queries";
import styles from "./NewVersion.module.css";

function builtAs(): string | null {
  return document.querySelector<HTMLMetaElement>('meta[name="crosstalk-build"]')?.content || null;
}

export function NewVersion({ reload = () => window.location.reload() }: { reload?: () => void }) {
  const build = useVitals().data?.page_build ?? null;
  const [own] = useState(builtAs);
  const first = useRef<string | null>(own);
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
