// Test doubles for events.ts: an EventSource and a clock driven by hand.
// Only the tests import this file, so it never reaches the build.
import type { EventsDeps } from "./events";

type Listener = (event: MessageEvent) => void;

export class FakeEventSource {
  static made: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
  private listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.made.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
  }

  /** Send one event to the listeners, as the browser would. */
  emit(type: string, data = ""): void {
    for (const listener of this.listeners.get(type) ?? []) listener(new MessageEvent(type, { data }));
  }

  static latest(): FakeEventSource {
    const last = FakeEventSource.made[FakeEventSource.made.length - 1];
    if (!last) throw new Error("no EventSource was made");
    return last;
  }
}

interface Timer {
  id: number;
  due: number;
  every: number | null;
  fn: () => void;
}

/** setTimeout and setInterval that run only when advance() moves the time. */
export class FakeClock {
  now = 0;
  private timers: Timer[] = [];
  private next = 1;

  get pending(): number {
    return this.timers.length;
  }

  setTimeout = (fn: () => void, ms: number): number => this.add(fn, ms, null);
  setInterval = (fn: () => void, ms: number): number => this.add(fn, ms, ms);
  clearTimeout = (id: number | undefined): void => this.remove(id);
  clearInterval = (id: number | undefined): void => this.remove(id);

  advance(ms: number): void {
    const end = this.now + ms;
    for (;;) {
      const due = this.timers.filter((t) => t.due <= end).sort((a, b) => a.due - b.due)[0];
      if (!due) break;
      this.now = due.due;
      if (due.every === null) this.remove(due.id);
      else due.due += due.every;
      due.fn();
    }
    this.now = end;
  }

  private add(fn: () => void, ms: number, every: number | null): number {
    const id = this.next++;
    this.timers.push({ id, due: this.now + ms, every, fn });
    return id;
  }

  private remove(id: number | undefined): void {
    this.timers = this.timers.filter((t) => t.id !== id);
  }
}

/** The deps of startEvents, all fake: a fresh EventSource list, the clock, a fetch that answers `status`. */
export function fakeDeps(clock: FakeClock, status = 200): EventsDeps & { fetchCalls: string[] } {
  FakeEventSource.made = [];
  const fetchCalls: string[] = [];
  return {
    fetchCalls,
    EventSource: FakeEventSource as unknown as NonNullable<EventsDeps["EventSource"]>,
    fetch: async (input) => {
      fetchCalls.push(String(input));
      return new Response("{}", { status, headers: { "Content-Type": "application/json" } });
    },
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    setInterval: clock.setInterval,
    clearInterval: clock.clearInterval,
  };
}

/** Let the promises that are already settled run their callbacks. */
export function flush(): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, 0));
}
