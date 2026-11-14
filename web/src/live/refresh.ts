// Refresh lifecycle for the Live search page: load now, then every intervalMs after each load ends
// (never two at once). Paused while the page is hidden (document.hidden) or stopped (another route);
// resuming loads at once if the last good load is older than the interval. stop() aborts the fetch.

type Doc = Pick<Document, "hidden" | "addEventListener" | "removeEventListener">;

export interface RefresherOptions<T> {
  load(signal: AbortSignal): Promise<T>;
  onData(data: T): void;
  onError(error: unknown): void;
  /** true when a load starts, false when it ends (either way). */
  onLoading?(loading: boolean): void;
  intervalMs?: number;
  now?: () => number;
  doc?: Doc;
}

export interface Refresher {
  /** Begin (or resume) refreshing. */
  start(): void;
  /** Pause: no timers, the current load is aborted. */
  stop(): void;
  /** Load now (a retry button). Starts the refresher if it was stopped. */
  refreshNow(): void;
  destroy(): void;
  readonly active: boolean;
  readonly loading: boolean;
}

export function createRefresher<T>(opts: RefresherOptions<T>): Refresher {
  const interval = opts.intervalMs ?? 60e3;
  const now = opts.now ?? Date.now;
  const doc: Doc | undefined = opts.doc ?? (typeof document !== "undefined" ? document : undefined);
  let active = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let ctl: AbortController | null = null;
  let lastOk: number | null = null;
  let listening = false;

  const hidden = () => !!doc?.hidden;

  function clearTimer() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  function abort() {
    if (ctl) {
      ctl.abort();
      ctl = null;
      opts.onLoading?.(false);
    }
  }

  function schedule(ms: number) {
    clearTimer();
    if (!active || hidden()) return;
    timer = setTimeout(() => {
      timer = null;
      fetchNow();
    }, Math.max(0, ms));
  }

  function fetchNow() {
    clearTimer();
    abort();
    if (!active || hidden()) return;
    const mine = new AbortController();
    ctl = mine;
    opts.onLoading?.(true);
    let p: Promise<T>;
    try {
      p = opts.load(mine.signal);
    } catch (e) {
      p = Promise.reject(e);
    }
    p.then(
      (data) => {
        if (mine.signal.aborted || ctl !== mine) return;
        lastOk = now();
        ctl = null;
        opts.onLoading?.(false);
        opts.onData(data);
        schedule(interval);
      },
      (err) => {
        if (mine.signal.aborted || ctl !== mine) return;
        ctl = null;
        opts.onLoading?.(false);
        opts.onError(err);
        schedule(interval);
      },
    );
  }

  /** Load if the last good load is older than the interval, else wait out the rest of it. */
  function kick() {
    if (!active || hidden()) return;
    if (ctl) return;
    const since = lastOk === null ? Infinity : now() - lastOk;
    if (since >= interval) fetchNow();
    else schedule(interval - since);
  }

  function onVisibility() {
    if (hidden()) {
      clearTimer();
      abort();
    } else kick();
  }

  return {
    start() {
      if (!listening && doc) {
        doc.addEventListener("visibilitychange", onVisibility);
        listening = true;
      }
      if (active) return;
      active = true;
      kick();
    },
    stop() {
      active = false;
      clearTimer();
      abort();
    },
    refreshNow() {
      if (!listening && doc) {
        doc.addEventListener("visibilitychange", onVisibility);
        listening = true;
      }
      active = true;
      fetchNow();
    },
    destroy() {
      this.stop();
      if (listening && doc) doc.removeEventListener("visibilitychange", onVisibility);
      listening = false;
    },
    get active() {
      return active;
    },
    get loading() {
      return ctl !== null;
    },
  };
}

/**
 * Calls fn at each minute boundary (by now()) while started; the page's "time left" and "updated ago"
 * tick per minute, not per second. Paused with the page.
 */
export function createMinuteTicker(fn: () => void, now: () => number = Date.now): { start(): void; stop(): void } {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const next = () => {
    const t = now();
    const wait = 60e3 - (t % 60e3) + 50;
    timer = setTimeout(() => {
      fn();
      next();
    }, wait);
  };
  return {
    start() {
      if (timer !== null) return;
      next();
    },
    stop() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}
