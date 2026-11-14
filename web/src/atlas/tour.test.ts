import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createTour, cardHtml, DEFAULT_TIMINGS, TOUR_FOV, type TourStop } from "./tour";

const STOPS: TourStop[] = [
  { slug: "a", name: "Barnard's Star", ra: 269.4462, dec: 4.76883, fov: 5, kind: "star", look_for: "Steps north about 5″ each visit." },
  { slug: "b", name: "Eps Indi B", ra: 331.09668, dec: -56.80067, fov: 5, kind: "brown dwarf" },
  { slug: "c", name: "R Dra", ra: 266.1, dec: 66.8, fov: 4 },
];

function harness(opts: { reducedMotion?: boolean; stops?: TourStop[] } = {}) {
  const flown: { ra: number; dec: number; fov?: number; animate: boolean }[] = [];
  const card = document.createElement("div");
  const tour = createTour(opts.stops ?? STOPS, {
    flyTo: (to, animate) => flown.push({ ...to, animate }),
    card,
    reducedMotion: opts.reducedMotion,
  });
  return { flown, card, tour };
}

const T = DEFAULT_TIMINGS;

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the tour drifts from stop to stop", () => {
  it("starts at the first stop and animates there", () => {
    const { flown, tour } = harness();
    tour.start();
    expect(flown).toHaveLength(1);
    expect(flown[0]).toMatchObject({ ra: STOPS[0].ra, dec: STOPS[0].dec, fov: TOUR_FOV, animate: true });
    expect(tour.index).toBe(0);
  });

  it("rests at the tour's own field, not the target's working field", () => {
    const { flown, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs + T.dwellMs);
    // Every stop comes in at the same width, so moving between them reads as travel and the
    // view never snaps to a different scale mid-tour.
    expect(flown.map((f) => f.fov)).toEqual([TOUR_FOV, TOUR_FOV]);
    expect(STOPS[0].fov).not.toBe(TOUR_FOV);
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
    expect(flown).toHaveLength(2);
    expect(flown[1]).toMatchObject({ ra: STOPS[1].ra, dec: STOPS[1].dec });
    expect(card.hidden).toBe(true);
  });

  it("loops back round to the first stop", () => {
    const { flown, tour } = harness();
    tour.start();
    for (let n = 0; n < STOPS.length; n++) vi.advanceTimersByTime(T.travelMs + T.dwellMs);
    expect(flown).toHaveLength(STOPS.length + 1);
    expect(flown[STOPS.length]).toMatchObject({ ra: STOPS[0].ra });
    expect(tour.index).toBe(0);
  });
});

describe("the person always wins the view", () => {
  it("drops the card and stops moving the moment the map is touched", () => {
    const { flown, card, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs);
    expect(card.hidden).toBe(false);

    tour.interrupted();
    expect(card.hidden).toBe(true);
    expect(tour.running).toBe(false);

    // Well past the dwell, but still inside the quiet period: the view must not have moved.
    expect(T.dwellMs).toBeLessThan(T.resumeMs);
    vi.advanceTimersByTime(T.resumeMs - 1);
    expect(flown).toHaveLength(1);
  });

  it("resumes only after the map has been still, and at the next stop, not the one left behind", () => {
    const { flown, tour } = harness();
    tour.start();
    vi.advanceTimersByTime(T.travelMs);
    tour.interrupted();

    vi.advanceTimersByTime(T.resumeMs - 1);
    expect(flown).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(flown).toHaveLength(2);
    expect(flown[1]).toMatchObject({ ra: STOPS[1].ra });
  });

  it("keeps standing down while the person is still working", () => {
    const { flown, tour } = harness();
    tour.start();
    for (let n = 0; n < 5; n++) {
      tour.interrupted();
      vi.advanceTimersByTime(T.resumeMs - 500);
    }
    expect(flown).toHaveLength(1);
  });

  it("stop() leaves the view alone for good", () => {
    const { flown, card, tour } = harness();
    tour.start();
    tour.stop();
    vi.advanceTimersByTime(T.resumeMs * 4);
    expect(flown).toHaveLength(1);
    expect(card.hidden).toBe(true);
    expect(tour.running).toBe(false);
  });

  it("fires nothing after destroy", () => {
    const { flown, tour } = harness();
    tour.start();
    tour.destroy();
    vi.advanceTimersByTime(T.travelMs * 10);
    expect(flown).toHaveLength(1);
  });
});

describe("goTo", () => {
  it("flies to the stop asked for and carries on from there", () => {
    const { flown, tour } = harness();
    tour.goTo(2);
    expect(tour.index).toBe(2);
    vi.advanceTimersByTime(T.travelMs + T.dwellMs);
    expect(flown[1]).toMatchObject({ ra: STOPS[0].ra });
  });

  it("wraps an index past the end instead of going blank", () => {
    const { flown, tour } = harness();
    tour.goTo(STOPS.length + 1);
    expect(tour.index).toBe(1);
    expect(flown[0]).toMatchObject({ ra: STOPS[1].ra });
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
