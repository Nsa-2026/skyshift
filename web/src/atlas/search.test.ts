import { describe, expect, it, vi } from "vitest";
import { parseSkyrun } from "../live";
import real from "../live/fixtures/skyrun-real-1505Z.json";
import sample from "../live/fixtures/skyrun-sample.json";
import { shownSentence } from "./kinds";
import { liveLine, provisionalZone, suspectAlpha, toSearchLayer } from "./search";
import { paintSearch, zoneLook } from "./searchdraw";

const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;
type RawZone = { name: string; state: string; deferred?: boolean; checks?: string };

describe("tonight's search layer", () => {
  const l = toSearchLayer(parseSkyrun(sample));

  it("counts the run's zones by state", () => {
    expect(l.total).toBe(333);
    expect(l.zones).toHaveLength(333);
    expect(l.searched).toBe(sample.zones.filter((z) => z.state === "combined").length);
    expect(l.atWork).toBe(sample.zones.filter((z) => z.state === "extracting" || z.state === "combining").length);
    expect(l.failed).toBe(sample.zones.filter((z) => z.state === "failed").length);
    expect(l.searched).toBeGreaterThan(0);
    expect(l.failed).toBeGreaterThan(0);
    // Every zone has a closed outline in degrees.
    for (const z of l.zones) {
      expect(z.rings.length).toBeGreaterThan(0);
      for (const r of z.rings) for (const [ra, dec] of r) expect(Number.isFinite(ra) && Number.isFinite(dec)).toBe(true);
    }
    expect(l.maxSuspects).toBe(Math.max(...l.zones.filter((z) => z.state === "combined").map((z) => z.suspects ?? 0)));
  });

  it("draws queued bulge zones as deferred and keeps the file's note on provisional results", () => {
    const queuedBulge = (sample.zones as RawZone[]).filter((z) => z.deferred && z.state === "queued").map((z) => z.name);
    expect(queuedBulge.length).toBeGreaterThan(0);
    expect(l.deferred).toBe(queuedBulge.length);
    expect(l.zones.filter((z) => z.state === "deferred").map((z) => z.name)).toEqual(queuedBulge);
    expect(l.provisionalNote).toMatch(/Gaia/);
  });

  it("a bulge zone a machine has taken shows its state, not deferred", () => {
    const raw = clone(sample) as { zones: RawZone[] };
    const z = raw.zones.find((x) => x.deferred)!;
    z.state = "extracting";
    const next = toSearchLayer(parseSkyrun(raw));
    expect(next.zones.find((x) => x.name === z.name)!.state).toBe("extracting");
    expect(next.deferred).toBe(l.deferred - 1);
  });

  it("knows which zones' suspects are still provisional", () => {
    const run = toSearchLayer(parseSkyrun(real));
    expect(run.searched).toBe(1);
    const hunt = run.zones.find((z) => z.name === "hunt")!;
    expect(hunt.state).toBe("combined");
    expect(hunt.provisional).toBe(false); // gaia+catwise: the WISE check has run
    expect(l.zones.find((z) => z.name === "ecl06")!.provisional).toBe(true); // gaia only
  });

  it("writes the intro's live line", () => {
    expect(liveLine({ searched: 12, total: 333, atWork: 0 })).toBe("Live: 12 of 333 zones searched tonight.");
    expect(liveLine({ searched: 1, total: 333, atWork: 9 })).toBe("Live: 1 of 333 zones searched tonight, 9 at work.");
    expect(liveLine({ searched: 1200, total: 1333, atWork: 1 })).toBe("Live: 1,200 of 1,333 zones searched tonight, 1 at work.");
  });

  it("keeps the red wash faint, redder with more suspects", () => {
    expect(suspectAlpha(null, 100)).toBeCloseTo(0.06);
    expect(suspectAlpha(0, 100)).toBeCloseTo(0.06);
    expect(suspectAlpha(5, 0)).toBeCloseTo(0.06);
    expect(suspectAlpha(100, 100)).toBeCloseTo(0.28);
    expect(suspectAlpha(500, 100)).toBeCloseTo(0.28);
    expect(suspectAlpha(10, 100)).toBeGreaterThan(suspectAlpha(5, 100));
    expect(suspectAlpha(10, 100)).toBeLessThan(0.28);
  });
});

describe("provisional suspects", () => {
  const l = toSearchLayer(parseSkyrun(sample));

  it("finds the combined zone under the view's centre", () => {
    // ecl06: RA 60.5–70.5, Dec 16.5–26.5, combined, with a suspects file.
    const z = provisionalZone(l, { ra: 65, dec: 20 }, ["hunt", "pilot"]);
    expect(z?.name).toBe("ecl06");
    expect(z?.suspectsUrl).toMatch(/ecl06\.json$/);
  });

  it("never shows the hunt zone's: they are candidates already", () => {
    const run = toSearchLayer(parseSkyrun(real));
    expect(provisionalZone(run, { ra: 125.5, dec: 19.4 }, ["hunt", "pilot"])).toBeNull();
    expect(provisionalZone(run, { ra: 125.5, dec: 19.4 }, [])?.name).toBe("hunt");
  });

  it("is null off the run, on a zone not yet combined, or with no layer", () => {
    expect(provisionalZone(null, { ra: 65, dec: 20 }, [])).toBeNull();
    expect(provisionalZone(l, { ra: 65, dec: -85 }, [])).toBeNull();
    const busy = l.zones.find((z) => z.state === "extracting")!;
    expect(provisionalZone(l, busy.center, [])).toBeNull();
  });
});

describe("how each state is drawn", () => {
  it("gives every state its own look", () => {
    expect(zoneLook("queued")).toMatchObject({ fill: "none", lineWidth: 0.5, cross: false });
    expect(zoneLook("extracting")).toMatchObject({ stroke: "blue", fill: "hatch" });
    expect(zoneLook("combining")).toMatchObject({ stroke: "blue", fill: "hatch-dense" });
    expect(zoneLook("combined")).toMatchObject({ stroke: "red", fill: "wash" });
    expect(zoneLook("failed")).toMatchObject({ cross: true });
    expect(zoneLook("deferred")).toMatchObject({ stroke: "grey", fill: "stipple" });
  });

  it("skips rings it can't project and draws the rest", () => {
    const l = toSearchLayer(parseSkyrun(sample));
    const calls: string[] = [];
    const ctx = new Proxy(
      {},
      {
        get: (_, k) => (typeof k === "string" && ["beginPath", "fill", "stroke"].includes(k) ? () => calls.push(k) : () => undefined),
        set: () => true,
      },
    ) as unknown as CanvasRenderingContext2D;
    // jsdom has no canvas: the hatch and stipple patterns fall back to no fill.
    const spy = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const ink = { ink: "#18202e", red: "#cf2338", blue: "#2556c8", grey: "#78818f", warn: "#b4232f" };
    // Only the northern sky projects: zones with Dec below 0 are dropped.
    paintSearch(ctx, l, (ra, dec) => (dec < 0 ? null : { x: ra * 2, y: 200 - dec * 2 }), ink, { w: 800, h: 400 });
    const north = l.zones.filter((z) => z.rings.every((r) => r.every(([, d]) => d >= 0)));
    expect(calls.filter((c) => c === "stroke").length).toBeGreaterThanOrEqual(north.length);
    expect(calls.filter((c) => c === "stroke").length).toBeLessThan(l.zones.length + l.failed);
    spy.mockRestore();
  });
});

describe("the whole-sky caption", () => {
  it("says why it shows fewer than the total", () => {
    expect(shownSentence(1250, 83, 2)).toBe("1,250 shown; 83 likely mismatches hidden.");
    expect(shownSentence(1250, 1, 2)).toBe("1,250 shown; 1 likely mismatch hidden.");
    expect(shownSentence(1333, 0, 2)).toBe("1,333 candidates in 2 zones.");
  });
});
