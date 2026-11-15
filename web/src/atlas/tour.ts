/**
 * The whole-sky tour: the map drifts from one featured target to the next on its own, pauses at
 * each one with a card naming what it is and what to look for, then moves on and loops.
 *
 * It is a visitor, not an owner. It only calls flyTo on the map and writes into its own card
 * element, so the map, the marks and every control behave exactly as they do with the tour off.
 * The moment somebody touches the map the tour gets out of the way, and it comes back only after
 * the map has been still for a while, so it never fights the person for the view.
 *
 * Every word on the card comes from targets.json (the target's own `look_for`): the tour states
 * nothing the data does not already say.
 */

/** One stop. The fields are a featured target's own, so a Target can be passed straight in. */
export interface TourStop {
  slug: string;
  name: string;
  ra: number;
  dec: number;
  /** Field of view to settle at, in degrees. */
  fov?: number;
  kind?: string;
  /** What to look for here, in the target's own words. */
  look_for?: string;
}

export interface TourTimings {
  /** The drift to the next stop. */
  travelMs: number;
  /** How long the card stays up before the next stop. */
  dwellMs: number;
  /** How long the map must be still after a touch before the tour resumes. */
  resumeMs: number;
}

export const DEFAULT_TIMINGS: TourTimings = { travelMs: 4200, dwellMs: 5200, resumeMs: 9000 };

/**
 * The one field the tour ever uses, in degrees: about where the whole-sky ellipse fills a wide
 * stage, so the sky is the picture and the drift between targets is a pan across it.
 *
 * It never changes during a tour, and that is the point rather than a style choice. Changing the
 * field changes which HiPS order the sky is drawn from, so every leg would throw away the tiles
 * on screen and fetch a new set; the view lands long before they arrive and the picture assembles
 * itself in patches after the fact. Panning at one field reuses everything already loaded and
 * only fetches the sky newly brought into frame. A target's own `fov` is for opening it to work
 * on, which is a different job.
 */
export const TOUR_FOV = 200;

/**
 * How far the view keeps drifting while a card is up, in degrees on the sky.
 *
 * The tour never comes to a stop. A still view invites the eye to settle on tile seams and on
 * the stepped edge of the sky, and a hard stop followed by a hard start reads as two cuts; a
 * view that is always moving, fast between targets and barely at all beside one, reads as one
 * continuous flight. The target crosses the centre halfway through its own card: the approach
 * aims half of this short of it and the drift carries on half past.
 */
export const DRIFT_DEG = 9;

/** The drift in right ascension that covers DRIFT_DEG on the sky at this declination. */
function driftRa(dec: number): number {
  const c = Math.cos((dec * Math.PI) / 180);
  // Near the poles a degree of RA is almost no sky at all, so the offset would run away.
  return DRIFT_DEG / Math.max(0.25, c);
}

export interface TourDeps {
  /** The map's own flyTo: the tour never reaches past this. */
  flyTo(to: { ra: number; dec: number; fov?: number }, animate: boolean, seconds?: number): void;
  /** Where the card is written. The tour owns this element's contents and nothing else. */
  card: HTMLElement;
  /** True when the person asked for less motion: the tour then does not run at all. */
  reducedMotion?: boolean;
  timings?: Partial<TourTimings>;
  /** Swappable for the tests. */
  now?: () => number;
}

export interface TourHandle {
  /** Begin, or begin again after a stop. Does nothing when there is nothing to show. */
  start(): void;
  /** Step to a given stop (or the next one) and keep going from there. */
  goTo(index: number): void;
  next(): void;
  /** Somebody is driving: hide the card and stand down until the map is still again. */
  interrupted(): void;
  /** Stop for good and clear the card. */
  stop(): void;
  destroy(): void;
  readonly running: boolean;
  /** The stop the tour is on, or -1 before it starts. For the tests and the dot strip. */
  readonly index: number;
}

/** A tour of no stops is not a tour: callers get a handle that does nothing. */
const IDLE: TourHandle = {
  start() {},
  goTo() {},
  next() {},
  interrupted() {},
  stop() {},
  destroy() {},
  running: false,
  index: -1,
};

export function createTour(stops: readonly TourStop[], deps: TourDeps): TourHandle {
  if (!stops.length || deps.reducedMotion) {
    // Reduced motion: no drift, no timers, and the card stays empty. The map is still fully
    // usable by hand, which is the whole of the page's function.
    deps.card.hidden = true;
    deps.card.innerHTML = "";
    return IDLE;
  }

  const t: TourTimings = { ...DEFAULT_TIMINGS, ...deps.timings };
  let i = -1;
  let running = false;
  let dead = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const clear = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };
  const after = (ms: number, fn: () => void) => {
    clear();
    timer = setTimeout(() => {
      timer = null;
      if (!dead) fn();
    }, ms);
  };

  function showCard(s: TourStop) {
    deps.card.hidden = false;
    deps.card.innerHTML = cardHtml(s);
  }

  function hideCard() {
    deps.card.hidden = true;
    deps.card.innerHTML = "";
  }

  function land() {
    const s = stops[i];
    if (!s) return;
    showCard(s);
    // Still moving, just barely: the target crosses the centre about halfway through its card.
    deps.flyTo({ ra: s.ra + driftRa(s.dec) / 2, dec: s.dec, fov: TOUR_FOV }, true, t.dwellMs / 1000);
    after(t.dwellMs, () => {
      if (running) travel(i + 1);
    });
  }

  function travel(to: number) {
    i = ((to % stops.length) + stops.length) % stops.length;
    const s = stops[i];
    hideCard();
    // Aim half the drift short of the target, so the slow pass that follows carries it through
    // the centre rather than away from it. The leg lasts all but a moment of its own time, so
    // the sky it brings into frame has arrived before the card goes up.
    deps.flyTo(
      { ra: s.ra - driftRa(s.dec) / 2, dec: s.dec, fov: TOUR_FOV },
      true,
      Math.max(0.6, (t.travelMs - 600) / 1000),
    );
    after(t.travelMs, land);
  }

  const handle: TourHandle = {
    start() {
      if (dead || running) return;
      running = true;
      travel(i < 0 ? 0 : i);
    },
    goTo(index: number) {
      if (dead) return;
      running = true;
      travel(index);
    },
    next() {
      if (dead) return;
      travel(i + 1);
    },
    interrupted() {
      if (dead) return;
      // The person has the view. Drop the card at once so nothing covers what they are doing,
      // and come back only once the map has been still for a while.
      running = false;
      hideCard();
      after(t.resumeMs, () => {
        if (!dead) {
          running = true;
          // Resume at the next stop rather than re-flying to the one they interrupted, which
          // would yank the view straight back off whatever they went to look at.
          travel(i + 1);
        }
      });
    },
    stop() {
      running = false;
      clear();
      hideCard();
    },
    destroy() {
      dead = true;
      running = false;
      clear();
      hideCard();
    },
    get running() {
      return running;
    },
    get index() {
      return i;
    },
  };
  return handle;
}

/** The card's markup. Kept here so the tests can assert on it without a DOM harness. */
export function cardHtml(s: TourStop): string {
  const kind = s.kind ? `<p class="atl-tour-kind">${esc(s.kind)}</p>` : "";
  const look = s.look_for ? `<p class="atl-tour-look">${esc(s.look_for)}</p>` : "";
  return `${kind}<h2 class="atl-tour-name">${esc(s.name)}</h2>${look}`;
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}
