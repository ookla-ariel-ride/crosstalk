// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { defaultWatch } from "./seen";

let observed: ((entries: { isIntersecting: boolean }[]) => void) | null = null;
let hidden = false;

beforeEach(() => {
  vi.useFakeTimers();
  hidden = false;
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => (hidden ? "hidden" : "visible"));
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(fn: (entries: { isIntersecting: boolean }[]) => void) {
        observed = fn;
      }
      observe() {}
      disconnect() {
        observed = null;
      }
    },
  );
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("defaultWatch", () => {
  test("calls back after one second in view", () => {
    const seen = vi.fn();
    defaultWatch(document.body, seen);
    observed?.([{ isIntersecting: true }]);
    vi.advanceTimersByTime(999);
    expect(seen).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  test("a message that leaves the view before one second is not seen", () => {
    const seen = vi.fn();
    defaultWatch(document.body, seen);
    observed?.([{ isIntersecting: true }]);
    vi.advanceTimersByTime(500);
    observed?.([{ isIntersecting: false }]);
    vi.advanceTimersByTime(2000);
    expect(seen).not.toHaveBeenCalled();
  });

  test("nothing is marked while the tab is hidden", () => {
    const seen = vi.fn();
    defaultWatch(document.body, seen);
    hidden = true;
    observed?.([{ isIntersecting: true }]);
    vi.advanceTimersByTime(5000);
    expect(seen).not.toHaveBeenCalled();
    hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(1000);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  test("a tab hidden during the second stops the mark", () => {
    const seen = vi.fn();
    defaultWatch(document.body, seen);
    observed?.([{ isIntersecting: true }]);
    vi.advanceTimersByTime(500);
    hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(5000);
    expect(seen).not.toHaveBeenCalled();
  });

  test("the returned function stops the watch", () => {
    const seen = vi.fn();
    const stop = defaultWatch(document.body, seen);
    observed?.([{ isIntersecting: true }]);
    stop();
    vi.advanceTimersByTime(2000);
    expect(seen).not.toHaveBeenCalled();
    expect(observed).toBeNull();
  });
});
