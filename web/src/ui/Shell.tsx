// The frame of the page (spec, "Layout"): the sidebar, the main part and the
// right column. Under 900 px the sidebar sits behind a menu button and the
// right column moves above the main part.
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import type { Mode } from "../api/events";
import { type Route, route as routeOf } from "../logic/route";
import markUrl from "./mark.svg";
import { RightColumn } from "./RightColumn";
import styles from "./Shell.module.css";
import { Sidebar } from "./Sidebar";

/** The route of `location.hash`, renewed on each `hashchange`. */
export function useHashRoute(): Route {
  const [current, setCurrent] = useState(() => routeOf(window.location.hash));
  useEffect(() => {
    const onChange = () => setCurrent(routeOf(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return current;
}

export interface ShellProps {
  route: Route;
  /** The mode of the live updates, for the health line; null until the stream answers. */
  mode: Mode | null;
  /** The main part: the page of the route. */
  children: ReactNode;
}

export function Shell({ route, mode, children }: ShellProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const sidebarId = useId();

  // While the menu is open, Escape closes it from anywhere and gives the focus to the button.
  useEffect(() => {
    if (!menuOpen) return;
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setMenuOpen(false);
      menuButton.current?.focus();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [menuOpen]);

  return (
    <div className={styles.shell}>
      <header className={styles.top}>
        <img className={styles.mark} src={markUrl} alt="" />
        <span className={styles.name}>Crosstalk</span>
        <button
          ref={menuButton}
          type="button"
          className={styles.menuButton}
          aria-expanded={menuOpen}
          aria-controls={sidebarId}
          onClick={() => setMenuOpen(!menuOpen)}
        >
          Menu
        </button>
      </header>
      <div id={sidebarId} className={styles.sidebar} data-open={menuOpen || undefined}>
        <Sidebar route={route} mode={mode} onNavigate={() => setMenuOpen(false)} />
      </div>
      <main className={styles.main}>{children}</main>
      <aside className={styles.right} aria-label="Needs you and the land queue">
        <RightColumn />
      </aside>
    </div>
  );
}
