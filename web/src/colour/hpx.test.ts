import { describe, expect, it } from "vitest";
import { ang2pixNest, hpxProject, skyToTilePixel, type TileWcs } from "./hpx";
import ref from "./hpx_ref.json";

// hpx_ref.json: astropy_healpix tile indices and astropy WCS pixel positions from real SPHEREx
// cube tile headers (make_hpx_ref.py). 35 points: the featured targets, both poles, the RA 0/360
// and 180 seams, the 41.8° polar-cap boundary, tile corners, and 20 random positions.
type Hdr = Record<"CRPIX1" | "CRPIX2" | "PC1_1" | "PC1_2" | "PC2_1" | "PC2_2" | "CDELT1" | "CDELT2", number>;
const wcsOf = (h: Hdr): TileWcs => ({
  crpix1: h.CRPIX1,
  crpix2: h.CRPIX2,
  pc11: h.PC1_1,
  pc12: h.PC1_2,
  pc21: h.PC2_1,
  pc22: h.PC2_2,
  cdelt1: h.CDELT1,
  cdelt2: h.CDELT2,
});

describe("HEALPix order 5 nested tile", () => {
  // A point exactly on a tile edge (x or y = −0.5 or 511.5) belongs to either tile: skip those.
  const onEdge = (v: number) => Math.abs(v + 0.5) < 1e-6 || Math.abs(v - 511.5) < 1e-6;
  for (const p of ref.points.filter((q) => !onEdge(q.x) && !onEdge(q.y))) {
    it(`(${p.ra.toFixed(2)}, ${p.dec.toFixed(2)}) → tile ${p.tile}`, () => {
      expect(ang2pixNest(5, p.ra, p.dec)).toBe(p.tile);
    });
  }
  it("covers order 0 base pixels", () => {
    expect(ang2pixNest(0, 45, 80)).toBe(0);
    expect(ang2pixNest(0, 0, 0)).toBe(4);
    expect(ang2pixNest(0, 45, -80)).toBe(8);
  });
});

describe("pixel inside the tile (HPX)", () => {
  const headers = ref.headers as Record<string, Hdr>;
  for (const p of ref.points) {
    it(`(${p.ra.toFixed(2)}, ${p.dec.toFixed(2)}) → (${p.x.toFixed(1)}, ${p.y.toFixed(1)})`, () => {
      const xy = skyToTilePixel(wcsOf(headers[String(p.tile)]), p.ra, p.dec);
      expect(xy).not.toBeNull();
      expect(xy!.x).toBeCloseTo(p.x, 4);
      expect(xy!.y).toBeCloseTo(p.y, 4);
    });
  }
  it("returns null for a position in another tile", () => {
    const h = headers[String(ref.points[0].tile)];
    expect(skyToTilePixel(wcsOf(h), ref.points[0].ra + 30, ref.points[0].dec)).toBeNull();
  });
  it("equatorial zone is linear in sin(dec)", () => {
    expect(hpxProject(10, 0)).toEqual({ x: 10, y: 0 });
    expect(hpxProject(370, 30).x).toBeCloseTo(10, 9);
  });
});
