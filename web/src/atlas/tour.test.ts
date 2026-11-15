import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createTour, cardHtml, DEFAULT_TIMINGS, DRIFT_DEG, TOUR_FOV, type TourStop } from "./tour";

const STOPS: TourStop[] = [
  { slug: "a", name: "Barnard's Star", ra: 269.4462, dec: 4.76883, fov: 5, kind: "star", look_for: "Steps north about 5″ each visit." },
  { slug: "b", name: "Eps Indi B", ra: 331.09668, dec: -56.80067, fov: 5, kind: "brown dwarf" },
  { slug: "c", name: "R Dra", ra: 266.1, dec: 66.8, fov: 4 },
];

interface Flight {
  ra: number;
  dec: number;
  fov?: number;
  animate: boolean;
  seconds?: number;
}

function harness(opts: { reducedMotion?: boolean; stops?: TourStop[] } = {}) {
  const flown: Flight[] = [];
  const card = document.createElement("div");
  const tour = createTour(opts.stops ?? STOPS, {
    flyTo: (to, animate, seconds) => flown.push({ ...to, animate, seconds }),
    card,
    reducedMotion: opts.reducedMotion,
  });
  return { flown, card, tour };
}

const T = DEFAULT_TIMINGS;

/** One stop is two flights: the approach, then the slow drift that carries on through it. */
const APPROACH = 0;
const DRIFT = 1;
const leg = (flown: Flight[], n: number) => ({ approach: flown[n * 2 + APPROACH], drift: flown[n * 2 + DRIFT] });

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the tour drifts from stop to stop", () => {
  it("starts by flying towards the first stop", () => {
    const { flown, tour } = harness();
    tour.start();
    expect(flown).toHaveLength(1);
    expect(flown[0]).toMatchObject({ dec: STOPS[0].dec, fov: TOUR_FOV, animate: true });
    expect(tour.index).toBe(0);
  });

  it("shows the card only once it has arrived, so nothing covers a moving view", () => {
    const { card, tour } = harness();
    tour.start();
    expect(card.hidden).toBe(true);
    vi.advanceTimersByTime(T.travelMs);
    expect(card.hidden).toBe(false);
    expect(card.textContent).toContain("Barnard's Star");
  });

  it("moves on after the dwell and hides the card while travelling", () => {
    const { flown, card, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs + T.dwellMs);
    expect(leg(flown, 1).approach).toMatchObject({ dec: STOPS[1].dec });
    expect(card.hidden).toBe(true);
  });

  it("loops back round to the first stop", () => {
    const { flown, tour } = harness();
    tour.start();
    for (let n = 0; n < STOPS.length; n++) vi.advanceTimersByTime(T.travelMs + T.dwellMs);
    expect(leg(flown, STOPS.length).approach).toMatchObject({ dec: STOPS[0].dec });
    expect(tour.index).toBe(0);
  });

  it("rests at the tour's own field, not the target's working field", () => {
    const { flown, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs + T.dwellMs);
    // Every flight comes in at the same width, so the view never snaps to a different scale.
    expect(flown.every((f) => f.fov === TOUR_FOV)).toBe(true);
    expect(STOPS[0].fov).not.toBe(TOUR_FOV);
  });

  it("drifts for most of the leg rather than arriving early and waiting", () => {
    const { flown, tour } = harness();
    tour.start();
    const secs = flown[0].seconds ?? 0;
    expect(secs).toBeGreaterThan(T.travelMs / 1000 - 1);
    expect(secs).toBeLessThan(T.travelMs / 1000);
  });
});

describe("it never comes to a stop", () => {
  it("keeps drifting while the card is up, over the whole dwell", () => {
    const { flown, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs);
    const { approach, drift } = leg(flown, 0);
    expect(drift).toBeDefined();
    expect(drift.seconds).toBeCloseTo(T.dwellMs / 1000, 5);
    expect(drift.ra).not.toBeCloseTo(approach.ra, 5);
  });

  it("approaches short of the target and drifts past it, so it crosses the centre", () => {
    const { flown, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs);
    const { approach, drift } = leg(flown, 0);
    const target = STOPS[0].ra;
    expect(approach.ra).toBeLessThan(target);
    expect(drift.ra).toBeGreaterThan(target);
    // Centred on the target, and the whole pass is the drift's width.
    expect((approach.ra + drift.ra) / 2).toBeCloseTo(target, 5);
    expect(drift.ra - approach.ra).toBeGreaterThanOrEqual(DRIFT_DEG);
  });

  it("drifts far more slowly than it travels", () => {
    const { flown, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs + T.dwellMs);
    // The first leg has no known starting point, so compare the drift at the first stop with
    // the leg the tour actually flew from there to the second.
    const first = leg(flown, 0);
    const second = leg(flown, 1);
    const driftSpeed = Math.abs(first.drift.ra - first.approach.ra) / (first.drift.seconds ?? 1);
    const travelSpeed = Math.abs(second.approach.ra - first.drift.ra) / (second.approach.seconds ?? 1);
    expect(driftSpeed).toBeLessThan(travelSpeed / 3);
  });

  it("does not let the drift run away near the pole, where a degree of RA is almost no sky", () => {
    const polar: TourStop[] = [{ slug: "p", name: "Near the pole", ra: 100, dec: 89.5 }];
    const { flown, tour } = harness({ stops: polar });
    tour.start();
    vi.advanceTimersByTime(T.travelMs);
    const { approach, drift } = leg(flown, 0);
    // Unclamped, 1/cos(89.5°) is over 100, which would swing the view right round the pole.
    expect(Math.abs(drift.ra - approach.ra)).toBeLessThanOrEqual(DRIFT_DEG * 4 + 0.001);
  });
});

describe("the person always wins the view", () => {
  it("drops the card and stops moving the moment the map is touched", () => {
    const { flown, card, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs);
    expect(card.hidden).toBe(false);
    const n = flown.length;

    tour.interrupted();
    expect(card.hidden).toBe(true);
    expect(tour.running).toBe(false);

    expect(T.dwellMs).toBeLessThan(T.resumeMs);
    vi.advanceTimersByTime(T.resumeMs - 1);
    expect(flown).toHaveLength(n);
  });

  it("resumes only after the map has been still, and at the next stop, not the one left behind", () => {
    const { flown, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs);
    const n = flown.length;
    tour.interrupted();

    vi.advanceTimersByTime(T.resumeMs - 1);
    expect(flown).toHaveLength(n);

    vi.advanceTimersByTime(1);
    expect(flown).toHaveLength(n + 1);
    expect(flown[n]).toMatchObject({ dec: STOPS[1].dec });
  });

  it("keeps standing down while the person is still working", () => {
    const { flown, tour } = harness();
    tour.start();
    const n = flown.length;
    for (let k = 0; k < 5; k++) {
      tour.interrupted();
      vi.advanceTimersByTime(T.resumeMs - 500);
    }
    expect(flown).toHaveLength(n);
  });

  it("stop() leaves the view alone for good", () => {
    const { flown, card, tour } = harness();
    tour.start();
    const n = flown.length;
    tour.stop();
    vi.advanceTimersByTime(T.resumeMs * 4);
    expect(flown).toHaveLength(n);
    expect(card.hidden).toBe(true);
    expect(tour.running).toBe(false);
  });

  it("fires nothing after destroy", () => {
    const { flown, tour } = harness();
    tour.start();
    const n = flown.length;
    tour.destroy();
    vi.advanceTimersByTime(T.travelMs * 10);
    expect(flown).toHaveLength(n);
  });
});

describe("goTo", () => {
  it("flies to the stop asked for and carries on from there", () => {
    const { flown, tour } = harness();
    tour.goTo(2);
    expect(tour.index).toBe(2);
    vi.advanceTimersByTime(T.travelMs + T.dwellMs);
    expect(leg(flown, 1).approach).toMatchObject({ dec: STOPS[0].dec });
  });

  it("wraps an index past the end instead of going blank", () => {
    const { flown, tour } = harness();
    tour.goTo(STOPS.length + 1);
    expect(tour.index).toBe(1);
    expect(flown[0]).toMatchObject({ dec: STOPS[1].dec });
  });
});

describe("it stays out of the way when it should", () => {
  it("does not run at all when the person asked for less motion", () => {
    const { flown, card, tour } = harness({ reducedMotion: true });
    tour.start();
    vi.advanceTimersByTime(T.travelMs * 5);
    expect(flown).toHaveLength(0);
    expect(card.hidden).toBe(true);
    expect(tour.running).toBe(false);
  });

  it("does nothing with no stops, rather than throwing", () => {
    const { flown, tour } = harness({ stops: [] });
    expect(() => {
      tour.start();
      tour.next();
      tour.interrupted();
    }).not.toThrow();
    vi.advanceTimersByTime(T.travelMs * 5);
    expect(flown).toHaveLength(0);
  });
});

describe("the card says only what the data says", () => {
  it("carries the target's own words", () => {
    const html = cardHtml(STOPS[0]);
    expect(html).toContain("Steps north about 5″ each visit.");
    expect(html).toContain("star");
  });

  it("leaves out what a target does not have, with no placeholder", () => {
    const html = cardHtml(STOPS[2]);
    expect(html).toContain("R Dra");
    expect(html).not.toContain("atl-tour-look");
    expect(html).not.toContain("atl-tour-kind");
  });

  it("escapes a name rather than writing it into the page as markup", () => {
    const html = cardHtml({ slug: "x", name: "<img src=x onerror=1>", ra: 0, dec: 0 });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});
