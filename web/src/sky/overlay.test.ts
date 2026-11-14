import { describe, expect, it } from "vitest";
import { GAIA_EPOCH_MJD, sepArcsec } from "../app/astro";
import type { Epoch, FramesResponse, GaiaStar } from "../api/types";
import { CUBE_UM, cubeFrameForDet, cubeFrameUm } from "./layers";
import { blinkFieldArcmin, brightest, centerAt, fieldCorners, firstEpochMjd, gaiaBudget, gaiaDots, skyFovDeg } from "./overlay";

const barnard: GaiaStar = {
  id: "4472832130942575872",
  ra: 269.44850252543836,
  dec: 4.739420051112412,
  pmra: -801.551,
  pmdec: 10362.394,
  parallax: 546.976,
  g: 8.194,
};
const frame = { pixel_arcsec: 3.075, size: [293, 293] as [number, number] };

const epoch = (mjd: number, det: Epoch["det"], extra: Partial<Epoch> = {}): Epoch => ({
  id: `e${mjd}`,
  kind: "visit",
  mjd_mid: mjd,
  mjd_start: mjd - 10,
  mjd_end: mjd + 10,
  det,
  um_min: 1.1,
  um_max: 1.6,
  n_exposures: 50,
  flag_frac: 0,
  sat_frac: 0,
  data_url: "x.bin",
  scale: 1,
  zero: 0,
  ...extra,
});

describe("field size", () => {
  it("takes the side from the frames when they are loaded", () => {
    expect(blinkFieldArcmin({ fov: 5 }, frame)).toBeCloseTo((293 * 3.075) / 60, 6);
  });
  it("falls back to the view's fov", () => {
    expect(blinkFieldArcmin({ fov: 20 }, null)).toBe(20);
  });
  it("shows three blink fields across, in degrees, within limits", () => {
    expect(skyFovDeg(5)).toBeCloseTo(0.25, 9);
    expect(skyFovDeg(60)).toBeCloseTo(3, 9);
    expect(skyFovDeg(0.1)).toBe(0.05);
    expect(skyFovDeg(1e5)).toBe(30);
  });
  it("widens with the map so three fields fit across its short side", () => {
    expect(skyFovDeg(5, 3)).toBeCloseTo(0.75, 9);
    expect(skyFovDeg(5, 0.5)).toBeCloseTo(0.25, 9); // portrait: the width is already the short side
    expect(skyFovDeg(5, NaN)).toBeCloseTo(0.25, 9);
  });
});

describe("fieldCorners", () => {
  it("draws a square of the frame's size centred on the frame, north up, east left", () => {
    const c = { ra: 269.4462, dec: 4.76883 };
    const side = blinkFieldArcmin({ fov: 5 }, frame);
    const k = fieldCorners(c, side, frame);
    expect(k).toHaveLength(4);
    // Top edge (first two corners) is north of the centre; first corner is east (larger RA).
    expect(k[0][1]).toBeGreaterThan(c.dec);
    expect(k[1][1]).toBeGreaterThan(c.dec);
    expect(k[0][0]).toBeGreaterThan(k[1][0]);
    // Sides are the frame width (on the sky, arcsec), within a small gnomonic error.
    const w = sepArcsec({ ra: k[0][0], dec: k[0][1] }, { ra: k[1][0], dec: k[1][1] });
    const h = sepArcsec({ ra: k[1][0], dec: k[1][1] }, { ra: k[2][0], dec: k[2][1] });
    expect(w).toBeCloseTo(293 * 3.075, -1);
    expect(h).toBeCloseTo(293 * 3.075, -1);
    // Centre of the corners is the field centre.
    const mid = { ra: k.reduce((s, p) => s + p[0], 0) / 4, dec: k.reduce((s, p) => s + p[1], 0) / 4 };
    expect(sepArcsec(mid, c)).toBeLessThan(1);
  });

  it("uses the fov alone when there are no frames", () => {
    const k = fieldCorners({ ra: 0.01, dec: 0 }, 5, null);
    const w = sepArcsec({ ra: k[0][0], dec: k[0][1] }, { ra: k[1][0], dec: k[1][1] });
    expect(w).toBeCloseTo(300, 0);
    // RA wraps into [0, 360).
    k.forEach(([ra]) => expect(ra >= 0 && ra < 360).toBe(true));
  });
});

describe("gaiaDots", () => {
  it("leaves stars at J2016.0 where Gaia put them", () => {
    const [d] = gaiaDots([barnard], GAIA_EPOCH_MJD);
    expect(d.ra).toBeCloseTo(barnard.ra, 10);
    expect(d.dec).toBeCloseTo(barnard.dec, 10);
    expect(d.id).toBe(barnard.id);
  });

  it("moves Barnard's Star about 10.4″ a year north", () => {
    const tenYears = GAIA_EPOCH_MJD + 10 * 365.25;
    const [d] = gaiaDots([barnard], tenYears);
    expect(sepArcsec(d, barnard)).toBeCloseTo(103.9, 0);
    expect(d.dec).toBeGreaterThan(barnard.dec);
  });

  it("keeps stars without proper motion still, and drops faint ones on request", () => {
    const still: GaiaStar = { ...barnard, id: "2", pmra: null, pmdec: null, g: 19 };
    const out = gaiaDots([barnard, still], GAIA_EPOCH_MJD + 3000, 18);
    expect(out.map((s) => s.id)).toEqual([barnard.id]);
    const [s] = gaiaDots([still], GAIA_EPOCH_MJD + 3000);
    expect(s.ra).toBeCloseTo(still.ra, 12);
  });
});

describe("ring budget", () => {
  it("shows more rings when the field is bigger on screen, within limits", () => {
    expect(gaiaBudget(150)).toBeLessThan(gaiaBudget(450));
    expect(gaiaBudget(1)).toBe(30);
    expect(gaiaBudget(1e6)).toBe(3000);
    expect(gaiaBudget(NaN)).toBe(100);
  });
  it("keeps the brightest stars, stars without G last", () => {
    const d = [
      { id: "a", g: 15 },
      { id: "b", g: null },
      { id: "c", g: 9 },
      { id: "d", g: 12 },
    ];
    expect(brightest(d, 2).map((s) => s.id)).toEqual(["c", "d"]);
    expect(brightest(d, 3).map((s) => s.id)).toEqual(["c", "d", "a"]);
    expect(brightest(d, 10)).toBe(d);
  });
});

describe("epochs", () => {
  const frames = {
    center: { ra: 10, dec: 20 },
    epochs: [epoch(60800, 1), epoch(60810, 2), epoch(61000, 2)],
  } as Pick<FramesResponse, "center" | "epochs">;

  it("starts at the first epoch of the view's detector", () => {
    expect(firstEpochMjd(frames, 2)).toBe(60810);
    expect(firstEpochMjd(frames, 6)).toBe(60800);
    expect(firstEpochMjd(null, 2)).toBeNull();
  });

  it("keeps the frames' centre for a fixed field", () => {
    expect(centerAt(frames, 61000, { ra: 0, dec: 0 })).toEqual({ ra: 10, dec: 20 });
    expect(centerAt(null, 61000, { ra: 1, dec: 2 })).toEqual({ ra: 1, dec: 2 });
  });

  it("follows a moving object with the nearest epoch's centre", () => {
    const moving = {
      center: { ra: 10, dec: 20 },
      epochs: [epoch(60800, 2, { center: { ra: 11, dec: 21 } }), epoch(60900, 2, { center: { ra: 12, dec: 22 } })],
    };
    expect(centerAt(moving, 60880, { ra: 0, dec: 0 })).toEqual({ ra: 12, dec: 22 });
    expect(centerAt(moving, 60801, { ra: 0, dec: 0 })).toEqual({ ra: 11, dec: 21 });
  });
});

describe("SPHEREx cube frames", () => {
  it("has 102 wavelengths, ascending, 0.75 to 5.0 µm", () => {
    expect(CUBE_UM).toHaveLength(102);
    for (let i = 1; i < CUBE_UM.length; i++) expect(CUBE_UM[i]).toBeGreaterThan(CUBE_UM[i - 1]);
  });

  it("picks each detector's middle channel, inside that detector's range", () => {
    const ranges = { 1: [0.75, 1.1], 2: [1.1, 1.6], 3: [1.6, 2.4], 4: [2.4, 3.8], 5: [3.8, 4.4], 6: [4.4, 5.0] } as const;
    for (const d of [1, 2, 3, 4, 5, 6] as const) {
      const f = cubeFrameForDet(d);
      expect(f).not.toBe(50); // blank dichroic frames
      expect(f).not.toBe(51);
      const um = cubeFrameUm(f);
      expect(um).toBeGreaterThan(ranges[d][0]);
      expect(um).toBeLessThan(ranges[d][1]);
    }
  });
});
