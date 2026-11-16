// Which Gaia stars get a blue ring (P4). Gaia lists stars to G 20.5, far fainter than a SPHEREx
// plate shows, and how deep a plate goes depends on the detector, the clock (one photo or a visit
// stack), the sky glow, the crowding, and on "All" (six detectors summed, each in its own noise, so
// a star seen in one detector only is diluted). A fixed cut (G 15.5 for photos, 16.5 for stacks) put
// dozens of rings on what looks like blank sky around Barnard's Star and R Draconis. So each plate
// measures its own depth from its own pixels, and a Gaia star is ringed only if it is brighter than
// that, or the plate shows a dot at its place. Pure: no DOM.
//
// Why pixels and not detections: on Barnard's months plate (All) the pipeline matched 49 ringed Gaia
// stars to G 17.6 in one visit, each in some detector's stack, but only 16 of them stand out on the
// summed plate the visitor looks at. A detection-based limit (G 18.0 there) left the blank rings.

/** A Gaia star on this plate: its G, the photos that cover it, and in how many the plate shows a dot there. */
export interface SeenStar {
  g: number;
  /** Photos of this plate with data at the star's place. */
  frames: number;
  /** Of those, the photos that show a dot there (`standsOutAt`). */
  hits: number;
}

/** Fewer stars seen than this: too few to measure the plate's depth, so the old cut is used. */
export const MIN_SEEN = 8;
/** A dot "shows" when its peak stands this many σ over the sky around it. */
export const SHOWS_SIGMA = 6;

/**
 * The faintest G this plate shows: the G at which about half of the Gaia stars show a dot (a 50%
 * completeness limit, measured on the plate itself).
 *
 * Walk the stars from bright to faint, adding 2·hits/frames − 1 for each: +1 for a star seen in
 * every photo that covers it, −1 for one never seen. The running sum climbs while most stars show
 * and falls once most don't, so it peaks where the share seen crosses one half; the limit is
 * halfway between the star at the peak and the next fainter one. No per-detector constant: a D6
 * plate or a plate deep in a star cloud finds its own (brighter) limit.
 *
 * null (use the old cut) when fewer than MIN_SEEN stars show at all.
 */
export function ringLimitG(stars: SeenStar[]): number | null {
  const list = stars.filter((s) => Number.isFinite(s.g) && s.frames > 0).sort((a, b) => a.g - b.g);
  if (list.filter((s) => s.hits > 0).length < MIN_SEEN) return null;
  let sum = 0;
  let best = -Infinity;
  let at = -1;
  list.forEach((s, i) => {
    sum += (2 * Math.min(s.hits, s.frames)) / s.frames - 1;
    // ≥: the faintest of equal peaks, so a flat stretch of half-seen stars stays in.
    if (sum >= best) {
      best = sum;
      at = i;
    }
  });
  const next = list[at + 1];
  return next ? (list[at].g + next.g) / 2 : list[at].g;
}

/**
 * Does the plate show a dot at image position (x, y)? Its peak (3 × 3 pixels) stands more than
 * `k`σ over the median of a ring of 16 pixels `annulus` pixels out. A burnt (saturated) pixel in the
 * peak counts as a dot; too little sky around it (an edge) counts as none.
 */
export function standsOutAt(
  vals: ArrayLike<number>,
  burnt: ArrayLike<number> | null,
  nx: number,
  ny: number,
  x: number,
  y: number,
  annulus: number,
  sigma: number,
  k = SHOWS_SIGMA,
): boolean {
  const ix = Math.round(x);
  const iy = Math.round(y);
  let peak = -Infinity;
  for (let dy = -1; dy <= 1; dy++)
    for (let dx = -1; dx <= 1; dx++) {
      const xx = ix + dx;
      const yy = iy + dy;
      if (xx < 0 || yy < 0 || xx >= nx || yy >= ny) continue;
      const i = yy * nx + xx;
      const v = vals[i];
      if (Number.isNaN(v)) {
        if (burnt?.[i]) return true;
      } else if (v > peak) peak = v;
    }
  const bg = localSky(vals, nx, ny, ix, iy, annulus);
  return !Number.isNaN(bg) && peak - bg > k * sigma;
}

/**
 * The sky around image position (x, y), whatever lies under it there (a bright star's glow too):
 * the median of a ring of 16 pixels `annulus` pixels out. NaN with too little sky around it (an edge).
 */
export function localSky(vals: ArrayLike<number>, nx: number, ny: number, x: number, y: number, annulus: number): number {
  const ix = Math.round(x);
  const iy = Math.round(y);
  const bg: number[] = [];
  for (let a = 0; a < 16; a++) {
    const xx = Math.round(ix + annulus * Math.cos((a * Math.PI) / 8));
    const yy = Math.round(iy + annulus * Math.sin((a * Math.PI) / 8));
    if (xx < 0 || yy < 0 || xx >= nx || yy >= ny) continue;
    const v = vals[yy * nx + xx];
    if (!Number.isNaN(v)) bg.push(v);
  }
  if (bg.length < 6) return NaN;
  bg.sort((p, q) => p - q);
  return bg[bg.length >> 1];
}

/**
 * Does the plate show a dot of its own near image position (x, y): a pixel within `radius` pixels
 * that is a local maximum (no neighbour higher) and stands out (`standsOutAt`)? Stricter than
 * `standsOutAt` alone: on the steep wing of a bright star the 3 × 3 peak, which reaches pixels
 * nearer the star, stands out over the ring around it with no star there. A burnt pixel counts.
 */
export function peakNear(
  vals: ArrayLike<number>,
  burnt: ArrayLike<number> | null,
  nx: number,
  ny: number,
  x: number,
  y: number,
  radius: number,
  annulus: number,
  sigma: number,
  k = SHOWS_SIGMA,
): boolean {
  const R = Math.max(1, radius);
  for (let yy = Math.max(0, Math.floor(y - R)); yy <= Math.min(ny - 1, Math.ceil(y + R)); yy++)
    for (let xx = Math.max(0, Math.floor(x - R)); xx <= Math.min(nx - 1, Math.ceil(x + R)); xx++) {
      if (Math.hypot(xx - x, yy - y) > R) continue;
      const i = yy * nx + xx;
      const v = vals[i];
      if (Number.isNaN(v)) {
        if (burnt?.[i]) return true;
        continue;
      }
      let top = true;
      for (let dy = -1; dy <= 1 && top; dy++)
        for (let dx = -1; dx <= 1 && top; dx++) {
          if (!dx && !dy) continue;
          const xn = xx + dx;
          const yn = yy + dy;
          if (xn < 0 || yn < 0 || xn >= nx || yn >= ny) continue;
          const w = vals[yn * nx + xn];
          if (w > v || (Number.isNaN(w) && burnt?.[yn * nx + xn])) top = false;
        }
      if (top && standsOutAt(vals, burnt, nx, ny, xx, yy, annulus, sigma, k)) return true;
    }
  return false;
}

/** A bright star's glow ends where its light falls under this many times the plate's sky noise. */
export const GLOW_SIGMA = 3;

/**
 * How far a bright star's glow reaches on this plate, in image pixels: walking out from its centre
 * in rings one pixel wide, the last one before the first whose median is less than `k`·σ over the
 * sky (burnt pixels count as bright, empty ones are skipped). Measured, not modelled: on
 * Barnard's months plate (All) the glow reaches about 61″, on R Draconis' about 80″, while a G 13
 * star's ends with its dot. 0 when even the first ring is below; `maxR` when it never falls.
 *
 * Inside it the plate makes sources of its own (the pipeline's stack-only and no-Gaia detections
 * are 4–15 times denser within Barnard's glow than 2′ out, where Gaia's stars are not).
 */
export function glowRadius(
  vals: ArrayLike<number>,
  burnt: ArrayLike<number> | null,
  nx: number,
  ny: number,
  x: number,
  y: number,
  sky: number,
  sigma: number,
  maxR: number,
  k = GLOW_SIGMA,
): number {
  for (let r = 1; r <= maxR; r++) {
    const ring: number[] = [];
    for (let yy = Math.max(0, Math.floor(y - r - 1)); yy <= Math.min(ny - 1, Math.ceil(y + r + 1)); yy++)
      for (let xx = Math.max(0, Math.floor(x - r - 1)); xx <= Math.min(nx - 1, Math.ceil(x + r + 1)); xx++) {
        const d = Math.hypot(xx - x, yy - y);
        if (d < r - 0.5 || d >= r + 0.5) continue;
        const i = yy * nx + xx;
        const v = vals[i];
        if (Number.isNaN(v)) {
          if (burnt?.[i]) ring.push(Infinity);
        } else ring.push(v);
      }
    if (ring.length < 6) continue; // past the plate's edge, or no data there
    ring.sort((p, q) => p - q);
    if (ring[ring.length >> 1] - sky < k * sigma) return r - 1;
  }
  return maxR;
}

/**
 * Does a Gaia star get a ring? A star SkyShift found in this photo always does (a circle inside a
 * ring is the match), whether or not the measured layer is drawn: the same stars get rings with
 * the layer on or off. Any other: brighter than the plate's limit (with no limit, the old cut
 * `fallbackG`), or where the plate shows a dot (`shows`), so no visible star goes unmarked and no
 * ring sits on blank sky. Inside a bright star's glow (`inGlow`) the plate's limit says nothing (the
 * glow hides stars far brighter), so only a star that stands out there (`shows`, measured against
 * its local background, glow included) or was found keeps its ring.
 */
export function getsRing(
  g: number,
  foundHere: boolean,
  limit: number | null,
  fallbackG: number,
  shows: () => boolean,
  inGlow = false,
): boolean {
  return foundHere || (!inGlow && g <= (limit ?? fallbackG)) || shows();
}
