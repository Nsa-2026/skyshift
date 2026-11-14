import { describe, expect, it } from "vitest";
import sample from "./fixtures/skyrun-sample.json";
import { parseSkyrun } from "./model";
import {
  DEFAULT_FRAME,
  MOLL_HALF_H,
  MOLL_HALF_W,
  boxRing,
  eclipticPath,
  eclipticToEq,
  lambdaToXY,
  mollweide,
  mollweideInverse,
  raPieces,
  skyToXY,
  xyToSky,
  zonePath,
  zonesToPolygons,
} from "./project";
import { buildGeometry, neighbour } from "./map";

const D = Math.PI / 180;

describe("Mollweide", () => {
  it("maps the frame: centre, poles, edges", () => {
    expect(mollweide(0, 0)).toEqual({ x: 0, y: 0 });
    expect(mollweide(Math.PI, 0).x).toBeCloseTo(MOLL_HALF_W);
    expect(mollweide(0, Math.PI / 2).y).toBeCloseTo(MOLL_HALF_H);
    expect(mollweide(1, Math.PI / 2).x).toBeCloseTo(0);
    expect(mollweide(0, -Math.PI / 2).y).toBeCloseTo(-MOLL_HALF_H);
  });

  it("is equal-area: a band's share of the map equals its share of the sphere", () => {
    // Area between the equator and latitude φ on the map: (y/√2) relation via θ; check numerically.
    const y30 = mollweide(0, 30 * D).y;
    const t = Math.asin(y30 / Math.SQRT2);
    const mapShare = (2 * t + Math.sin(2 * t)) / Math.PI; // fraction of the half-ellipse below y
    expect(mapShare).toBeCloseTo(Math.sin(30 * D), 10);
  });

  it("inverts its own forward projection", () => {
    for (const lon of [-179, -120, -45, 0, 33, 90, 179.9]) {
      for (const lat of [-89, -60, -23.4, 0, 14.4, 45, 88]) {
        const p = mollweide(lon * D, lat * D);
        const q = mollweideInverse(p.x, p.y)!;
        expect(q.lon / D).toBeCloseTo(lon, 6);
        expect(q.lat / D).toBeCloseTo(lat, 6);
      }
    }
    expect(mollweideInverse(MOLL_HALF_W * 0.99, MOLL_HALF_H * 0.99)).toBeNull();
  });
});

describe("the map frame", () => {
  const f = DEFAULT_FRAME;
  it("puts RA increasing to the LEFT, 12h in the middle and 0h on the edges", () => {
    const [x12] = skyToXY(180, 0);
    const [x6] = skyToXY(90, 0);
    const [x18] = skyToXY(270, 0);
    expect(x12).toBeCloseTo(f.pad + f.width / 2);
    expect(x18).toBeLessThan(x12);
    expect(x6).toBeGreaterThan(x12);
    expect(skyToXY(0, 0)[0]).toBeCloseTo(f.pad + f.width);
    expect(lambdaToXY(-180, 0)[0]).toBeCloseTo(f.pad);
    // north is up
    expect(skyToXY(180, 30)[1]).toBeLessThan(skyToXY(180, 0)[1]);
  });

  it("reads a point back to RA/Dec", () => {
    for (const [ra, dec] of [
      [125.5, 19.4],
      [3, -40],
      [350, 60],
      [270, -66],
    ]) {
      const [x, y] = skyToXY(ra, dec);
      const s = xyToSky(x, y)!;
      expect(s.ra).toBeCloseTo(ra, 6);
      expect(s.dec).toBeCloseTo(dec, 6);
    }
    expect(xyToSky(0, 0)).toBeNull();
  });
});

describe("zone edges", () => {
  it("samples every edge at the step, as a closed ring", () => {
    const ring = boxRing(120.5, 130.5, 14.4, 24.4, 1);
    // 11 points along each RA edge, 11 along each Dec edge, corners shared: 4 × 10
    expect(ring).toHaveLength(40);
    expect(ring[0]).toEqual([120.5, 14.4]);
    expect(ring).toContainEqual([130.5, 24.4]);
    for (let i = 1; i < ring.length; i++) {
      const [a, b] = [ring[i - 1], ring[i]];
      expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeLessThanOrEqual(1.0001);
    }
  });

  it("curves: the middle of a box's RA edge is off the straight line between its corners", () => {
    // A meridian at RA 60° from Dec 20 to 60 is an arc in Mollweide; sampling keeps its bulge.
    const ring = boxRing(50, 60, 20, 60, 1).map(([r, d]) => skyToXY(r, d));
    const a = skyToXY(60, 20);
    const b = skyToXY(60, 60);
    const mid = skyToXY(60, 40);
    const t = (mid[1] - a[1]) / (b[1] - a[1]);
    const straightX = a[0] + t * (b[0] - a[0]);
    expect(Math.abs(mid[0] - straightX)).toBeGreaterThan(1);
    expect(ring.some(([x, y]) => Math.abs(x - mid[0]) < 1e-9 && Math.abs(y - mid[1]) < 1e-9)).toBe(true);
  });

  it("splits boxes through RA 0, and at the map edge when asked", () => {
    expect(raPieces([120.5, 130.5])).toEqual([[120.5, 130.5]]);
    expect(raPieces([355, 5])).toEqual([
      [355, 360],
      [0, 5],
    ]);
    expect(raPieces([350, 0])).toEqual([[350, 360]]);
    // centred on 0h the edge is at 12h: a box over 12h is cut, a box through 0h is kept whole
    expect(raPieces([170.5, 180.5], 180)).toEqual([
      [170.5, 180],
      [180, 180.5],
    ]);
    expect(raPieces([355, 5], 180)).toEqual([[355, 365]]);
  });

  it("draws the zones next to the seam on their own sides", () => {
    const f = DEFAULT_FRAME;
    const xs = (d: string) => [...d.matchAll(/[ML](-?[\d.]+) /g)].map((m) => Number(m[1]));
    const left = xs(zonePath({ ra: [350.5, 360], dec: [-5, 5] }));
    const right = xs(zonePath({ ra: [0, 0.5], dec: [-5, 5] }));
    expect(Math.max(...left)).toBeLessThan(f.pad + f.width * 0.05);
    expect(Math.min(...right)).toBeGreaterThan(f.pad + f.width * 0.95);
    // a box through RA 0 is drawn as two pieces, one at each edge
    const wrap = zonePath({ ra: [355, 5], dec: [-5, 5] });
    expect(wrap.match(/M/g)).toHaveLength(2);
  });

  it("gives every zone sky polygons for other maps (RA in 0..360)", () => {
    const run = parseSkyrun(sample);
    const polys = zonesToPolygons(run.zones);
    expect(polys).toHaveLength(333);
    const hunt = polys.find((p) => p.name === "hunt")!;
    expect(hunt.state).toBe("combined");
    expect(hunt.rings).toHaveLength(1);
    for (const p of polys) for (const r of p.rings) for (const [ra, dec] of r) {
      expect(ra).toBeGreaterThanOrEqual(0);
      expect(ra).toBeLessThanOrEqual(360);
      expect(Math.abs(dec)).toBeLessThanOrEqual(90);
    }
    const wrapped = zonesToPolygons([{ ...run.zones[0], ra: [355, 5] }]);
    expect(wrapped[0].rings).toHaveLength(2);
  });
});

describe("the ecliptic", () => {
  it("passes through the equinoxes and solstices", () => {
    expect(eclipticToEq(0)).toEqual({ ra: 0, dec: 0 });
    const s = eclipticToEq(90);
    expect(s.ra).toBeCloseTo(90);
    expect(s.dec).toBeCloseTo(23.4393);
    expect(eclipticToEq(270).dec).toBeCloseTo(-23.4393);
  });
  it("is one unbroken line across the map centred on 12h", () => {
    expect(eclipticPath().match(/M/g)).toHaveLength(1);
  });
});

describe("keyboard order", () => {
  const run = parseSkyrun(sample);
  const geom = buildGeometry(run.zones);
  it("has 37 columns left to right, each top to bottom", () => {
    expect(geom.columns).toHaveLength(37);
    expect(geom.columns.every((c) => c.length === 9)).toBe(true);
    // leftmost column is RA 350.5–360 (RA increases to the left), rightmost RA 0–0.5
    expect(geom.columns[0][0]).toMatch(/^ecl35/);
    expect(geom.columns[36][0]).toMatch(/^ecl36/);
    expect(geom.columns[0][0]).toBe("ecl35n4");
  });
  it("moves with the arrows and wraps round the sky", () => {
    expect(neighbour(geom, "hunt", "ArrowUp")).toBe("ecl12n1");
    expect(neighbour(geom, "hunt", "ArrowDown")).toBe("ecl12s1");
    expect(neighbour(geom, "hunt", "ArrowLeft")).toBe("ecl13"); // higher RA is to the left
    expect(neighbour(geom, "hunt", "ArrowRight")).toBe("ecl11");
    expect(neighbour(geom, "ecl12n4", "ArrowUp")).toBe("ecl12n4");
    expect(neighbour(geom, "ecl35", "ArrowLeft")).toBe("ecl36");
    expect(neighbour(geom, "ecl36", "ArrowRight")).toBe("ecl35");
    // Home/End: the first/last column, the zone nearest in Dec (hunt is at +19°; there the ecliptic is near 0°)
    expect(neighbour(geom, "hunt", "Home")).toBe("ecl35n2");
    expect(neighbour(geom, "hunt", "End")).toBe("ecl36n2");
  });
});
