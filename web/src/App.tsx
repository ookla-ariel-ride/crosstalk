import styles from "./App.module.css";
import { useLive } from "./api/queries";
import { Conversations, NeedsYou } from "./features/conversations/Stream";
import type { Route } from "./logic/route";
import { Shell, useHashRoute } from "./ui/Shell";
import { labelOf } from "./ui/Sidebar";

/** The page of a route. Each one comes in a later step of release 1 (Tasks 12 to 14). */
function page(route: Route) {
  if (route.name === "conversations") return <Conversations route={route} />;
  if (route.name === "needs") return <NeedsYou />;
  return (
    <div className={styles.placeholder}>
      <h1 className={styles.heading}>{labelOf(route)}</h1>
      <p className={styles.note}>This section comes in a later step.</p>
    </div>
  );
}

export function App() {
  const route = useHashRoute();
  // The one place that starts the live updates.
  const mode = useLive();
  return (
    <Shell route={route} mode={mode}>
      {page(route)}
    </Shell>
  );
}
