import { describe, expect, it } from "vitest";
import {
  aitoff,
  aitoffInverse,
  fromGalactic,
  gnomonic,
  gnomonicInverse,
  lonLat,
  meanPosition,
  offsetPa,
  paDeg,
  sepDeg,
  toGalactic,
  wrap180,
  wrap360,
} from "./sphere";

describe("angles", () => {
  it("wraps", () => {
    expect(wrap360(-30)).toBe(330);
    expect(wrap360(725)).toBe(5);
    expect(wrap180(190)).toBe(-170);
    expect(wrap180(-180)).toBe(180);
  });
});

describe("galactic frame", () => {
  it("puts the galactic centre and north pole where IAU puts them", () => {
    // Sgr A* region: l = 0, b = 0 at RA 266.405, Dec −28.936.
    const gc = toGalactic({ ra: 266.405, dec: -28.936 });
    expect(wrap180(gc.l)).toBeCloseTo(0, 1);
    expect(gc.b).toBeCloseTo(0, 1);
    // North galactic pole: RA 192.859, Dec +27.128.
    expect(toGalactic({ ra: 192.859, dec: 27.128 }).b).toBeCloseTo(90, 2);
  });

  it("round-trips", () => {
    for (const p of [
      { ra: 122.55, dec: 17.0 },
      { ra: 0.1, dec: -89 },
      { ra: 300, dec: 45 },
    ]) {
      const g = toGalactic(p);
      const back = fromGalactic(g.l, g.b);
      expect(sepDeg(back, p)).toBeLessThan(1e-8);
    }
  });

  it("gives frame coordinates", () => {
    expect(lonLat({ ra: 10, dec: 20 }, false)).toEqual([10, 20]);
    const [l, b] = lonLat({ ra: 266.405, dec: -28.936 }, true);
    expect(wrap180(l)).toBeCloseTo(0, 1);
    expect(b).toBeCloseTo(0, 1);
  });
});

describe("separation and position angle", () => {
  it("measures across RA 0 and at the poles", () => {
    expect(sepDeg({ ra: 359.5, dec: 0 }, { ra: 0.5, dec: 0 })).toBeCloseTo(1, 9);
    expect(sepDeg({ ra: 0, dec: 89 }, { ra: 180, dec: 89 })).toBeCloseTo(2, 9);
  });

  it("measures position angle east of north", () => {
    expect(paDeg({ ra: 10, dec: 0 }, { ra: 10, dec: 1 })).toBeCloseTo(0, 6);
    expect(paDeg({ ra: 10, dec: 0 }, { ra: 11, dec: 0 })).toBeCloseTo(90, 6);
    expect(paDeg({ ra: 10, dec: 0 }, { ra: 10, dec: -1 })).toBeCloseTo(180, 6);
    expect(paDeg({ ra: 10, dec: 0 }, { ra: 9, dec: 0 })).toBeCloseTo(270, 6);
  });

  it("steps along a position angle, the inverse of paDeg and sepDeg", () => {
    const p = { ra: 122.55, dec: 17 };
    for (const pa of [0, 37, 151, 270]) {
      const q = offsetPa(p, pa, 0.5);
      expect(sepDeg(p, q)).toBeCloseTo(0.5, 9);
      expect(paDeg(p, q)).toBeCloseTo(pa, 6);
    }
  });

  it("averages positions across RA 0", () => {
    const m = meanPosition([
      { ra: 359, dec: 0 },
      { ra: 1, dec: 0 },
    ])!;
    expect(wrap180(m.ra)).toBeCloseTo(0, 9);
    expect(meanPosition([])).toBeNull();
  });
});

describe("Aitoff", () => {
  it("fits the whole sky in a 2:1 ellipse, east to the left", () => {
    expect(aitoff(0, 0).x).toBeCloseTo(0, 12);
    expect(aitoff(0, 0).y).toBeCloseTo(0, 12);
    expect(aitoff(0, 90).y).toBeCloseTo(1, 9);
    expect(aitoff(179.999, 0).x).toBeCloseTo(-2, 3);
    expect(aitoff(-179.999, 0).x).toBeCloseTo(2, 3);
    expect(aitoff(30, 0).x).toBeLessThan(0);
    expect(aitoff(130, 10, 120).x).toBeLessThan(0);
  });

  it("inverts", () => {
    for (const [lon, lat] of [
      [10, 20],
      [200, -60],
      [122.5, 17],
      [359, 85],
    ]) {
      for (const lon0 of [0, 120]) {
        const p = aitoff(lon, lat, lon0);
        const r = aitoffInverse(p.x, p.y, lon0)!;
        expect(sepDeg({ ra: r.lon, dec: r.lat }, { ra: lon, dec: lat })).toBeLessThan(1e-5);
      }
    }
  });

  it("finds nothing outside the ellipse", () => {
    expect(aitoffInverse(1.9, 0.9)).toBeNull();
  });
});

describe("gnomonic", () => {
  const c = { ra: 122.3, dec: 16.2 };

  it("puts north up and east left", () => {
    expect(gnomonic(c, c)).toEqual({ x: -0, y: 0 });
    expect(gnomonic({ ra: 122.3, dec: 16.7 }, c)!.y).toBeGreaterThan(0);
    expect(gnomonic({ ra: 122.8, dec: 16.2 }, c)!.x).toBeLessThan(0);
    expect(gnomonic({ ra: 302.3, dec: -16.2 }, c)).toBeNull();
  });

  it("inverts", () => {
    for (const p of [
      { ra: 121.0, dec: 15.0 },
      { ra: 124.1, dec: 17.9 },
      { ra: 122.31, dec: 16.2 },
    ]) {
      const g = gnomonic(p, c)!;
      expect(sepDeg(gnomonicInverse(g.x, g.y, c), p)).toBeLessThan(1e-9);
    }
    expect(gnomonicInverse(0, 0, c)).toEqual(c);
  });
});
