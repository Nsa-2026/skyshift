// Where a sky position sits in the SPHEREx all-sky cube: which HiPS tile (HEALPix order 5,
// NESTED) and which pixel inside that tile's FITS cube (HPX projection, from the tile header).
// Checked against astropy + astropy_healpix on real tile headers (hpx.test.ts).
// References: Górski et al. 2005 (HEALPix); Calabretta & Roukema 2007 (HPX, H = 4, K = 3).

const D2R = Math.PI / 180;

/** HEALPix NESTED pixel index of (ra, dec) at this order (nside = 2^order). */
export function ang2pixNest(order: number, raDeg: number, decDeg: number): number {
  const nside = 1 << order;
  const z = Math.sin(decDeg * D2R);
  const za = Math.abs(z);
  let tt = ((raDeg % 360) + 360) % 360 / 90; // [0, 4)
  if (tt >= 4) tt = 0;
  let face: number;
  let ix: number;
  let iy: number;
  if (za <= 2 / 3) {
    const t1 = nside * (0.5 + tt);
    const t2 = nside * (z * 0.75);
    const jp = Math.floor(t1 - t2);
    const jm = Math.floor(t1 + t2);
    const ifp = Math.floor(jp / nside);
    const ifm = Math.floor(jm / nside);
    face = ifp === ifm ? (ifp | 4) : ifp < ifm ? ifp : ifm + 8;
    ix = jm & (nside - 1);
    iy = nside - (jp & (nside - 1)) - 1;
  } else {
    const ntt = Math.min(3, Math.floor(tt));
    const tp = tt - ntt;
    const tmp = nside * Math.sqrt(3 * (1 - za));
    const jp = Math.min(nside - 1, Math.floor(tp * tmp));
    const jm = Math.min(nside - 1, Math.floor((1 - tp) * tmp));
    if (z >= 0) {
      face = ntt;
      ix = nside - jm - 1;
      iy = nside - jp - 1;
    } else {
      face = ntt + 8;
      ix = jp;
      iy = jm;
    }
  }
  return face * nside * nside + interleave(ix, iy);
}

/** ix bits to the even bit positions, iy bits to the odd ones. */
function interleave(ix: number, iy: number): number {
  let out = 0;
  for (let b = 0; b < 16; b++) {
    out += ((ix >> b) & 1) * 2 ** (2 * b) + ((iy >> b) & 1) * 2 ** (2 * b + 1);
  }
  return out;
}

/** HPX projection (H = 4, K = 3): sky → intermediate coordinates (x, y) in degrees. */
export function hpxProject(raDeg: number, decDeg: number): { x: number; y: number } {
  const phi = ((((raDeg + 180) % 360) + 360) % 360) - 180; // [-180, 180)
  const s = Math.sin(decDeg * D2R);
  if (Math.abs(s) <= 2 / 3) return { x: phi, y: 67.5 * s };
  const sigma = Math.sqrt(3 * (1 - Math.abs(s)));
  const phiC = -180 + 90 * Math.floor((phi + 180) / 90) + 45; // centre of the polar facet
  return { x: phiC + (phi - phiC) * sigma, y: Math.sign(decDeg) * 45 * (2 - sigma) };
}

/** The celestial part of a tile's IMAGE header (CTYPE RA---HPX / DEC--HPX, CRVAL 0 0). */
export interface TileWcs {
  crpix1: number;
  crpix2: number;
  pc11: number;
  pc12: number;
  pc21: number;
  pc22: number;
  cdelt1: number;
  cdelt2: number;
}

/**
 * 0-based pixel position (x along NAXIS1, y along NAXIS2) of a sky position in a tile, or null
 * when it falls outside the tile's 512 × 512 square. HPX x wraps at ±180°, so try the wraps.
 */
export function skyToTilePixel(
  w: TileWcs,
  raDeg: number,
  decDeg: number,
  size = 512,
): { x: number; y: number } | null {
  const p = hpxProject(raDeg, decDeg);
  const det = w.pc11 * w.pc22 - w.pc12 * w.pc21;
  for (const wrap of [0, -360, 360]) {
    const u = (p.x + wrap) / w.cdelt1;
    const v = p.y / w.cdelt2;
    const x = w.crpix1 + (w.pc22 * u - w.pc12 * v) / det - 1;
    const y = w.crpix2 + (-w.pc21 * u + w.pc11 * v) / det - 1;
    if (x >= -0.5 && x <= size - 0.5 && y >= -0.5 && y <= size - 0.5) return { x, y };
  }
  return null;
}
