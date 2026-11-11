// When a message counts as read: it was in view for one second while the tab
// was visible. The watch comes in through a context, so a test can fire it.
import { createContext } from "react";

/** Watch `el`; call `onSeen` once it was in view for one second. Returns the stop function. */
export type Watch = (el: Element, onSeen: () => void) => () => void;

const SECOND = 1000;

export const defaultWatch: Watch = (el, onSeen) => {
  if (typeof IntersectionObserver === "undefined") return () => {};
  let inView = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Start the second again on each change; a hidden tab or a message out of view stops it.
  const arm = () => {
    clearTimeout(timer);
    timer = undefined;
    if (inView && document.visibilityState === "visible") timer = setTimeout(onSeen, SECOND);
  };
  const observer = new IntersectionObserver((entries) => {
    inView = entries[entries.length - 1]?.isIntersecting === true;
    arm();
  });
  observer.observe(el);
  document.addEventListener("visibilitychange", arm);
  return () => {
    observer.disconnect();
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", arm);
  };
};

export const WatchContext = createContext<Watch>(defaultWatch);
