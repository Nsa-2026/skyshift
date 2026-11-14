// How the plate's circles and rings are laid out (web/DESIGN.md, "Pencil marks"). Each measured
// circle is as big as its source looks on the plate; each Gaia ring a little bigger, so a match
// reads as a circle inside a ring. One bright star is one mark, never its fragments. Two Gaia stars
// that SPHEREx sees as one dot get one circle and one ring. Pure: screen positions in, a plan out.
import type { MeasuredClass } from "../api/types";

/** A Gaia star in view, at this epoch. */
export interface PlanStar {
  id: string;
  X: number;
  Y: number;
  g: number | null;
  /** How far it visibly reaches on the plate, screen pixels (0: nothing shows). */
  extent: number;
  /** G < 9, or a burnt (saturated) core at its position. */
  saturated: boolean;
  /** Gets a ring of its own (bright enough, or a dot shows; not the target; not over the hatch). */
  ringed: boolean;
}

/** A measured detection in view, at this epoch. `i` is the caller's index. */
export interface PlanDet {
  i: number;
  X: number;
  Y: number;
  cls: MeasuredClass;
  extent: number;
  gaiaId: string | null;
  reasons: string[];
  /** Proposed fields (not in contracts/api.md yet): the star a `bright_fragment` belongs to, a blend's stars. */
  parentGaiaId?: string | null;
  blendGaiaIds?: string[] | null;
}

export interface PlanOptions {
  /** Screen pixels per arcsec. */
  pxPerArcsec: number;
  /** The smallest circle, screen pixels: half the PSF's FWHM at this zoom, at least 3.5. */
  floor: number;
  /** A star reaching this far (screen pixels) is "very bright": dots inside its glow are pieces of it. */
  brightExtent: number;
  /** Draw the measured layer (circles, saturated marks); off: Gaia rings only. */
  measured: boolean;
}

export interface PlanCircle {
  i: number;
  X: number;
  Y: number;
  r: number;
  /** The Gaia star it matches and how far from Gaia's position, arcsec. */
  star?: string;
  offsetArcsec?: number;
  /** Two or more Gaia stars it blends. */
  blend?: string[];
  /** It is the measurement of a bright or saturated star: sized to its whole blob. */
  bright?: string;
  /** Fragments of that star hidden on the plate. */
  fragments?: number;
}

export interface PlanRing {
  /** The Gaia star, or the first of a blend. */
  id: string;
  X: number;
  Y: number;
  r: number;
  /** A blend: every star's position, drawn as a small dot inside the one ring. */
  blend?: { id: string; X: number; Y: number }[];
}

/** A bright star with no detection of its own: one dashed ring around its blob. */
export interface PlanBig {
  id: string;
  X: number;
  Y: number;
  r: number;
  saturated: boolean;
  /** Its fragments hidden on the plate. */
  fragments: number;
}

export interface Plan {
  circles: PlanCircle[];
  /** Detections not drawn, by their index: pieces of a bright star (the parent's Gaia id). */
  hidden: Map<number, string>;
  rings: PlanRing[];
  big: PlanBig[];
}

/** Within this of a bright star, a detection is that star's own measurement. */
export const OWN_ARCSEC = 6;
/** Two Gaia stars closer than this (about 1.5 SPHEREx pixels) can blend into one dot. */
export const BLEND_ARCSEC = 9;
/** A blend partner at most this much fainter (G); fainter adds nothing SPHEREx can see. */
export const BLEND_DMAG = 3;

/** A measured circle: just outside how far the source reaches, never under the floor. */
export const circleRadius = (extent: number, floor: number): number => Math.max(floor, extent + 2);
/** How much bigger a Gaia ring is than the circle inside it. */
export const ringGap = (r: number): number => Math.max(3, 0.15 * r);

const dist = (a: { X: number; Y: number }, b: { X: number; Y: number }) => Math.hypot(a.X - b.X, a.Y - b.Y);

export function planMarks(allStars: PlanStar[], dets: PlanDet[], o: PlanOptions): Plan {
  const own = OWN_ARCSEC * o.pxPerArcsec;
  const blendPx = BLEND_ARCSEC * o.pxPerArcsec;
  // 0. A fainter star inside a saturated star's blob is lost in its glow: it reads the burnt core
  // as its own (not saturated itself), gets no ring and owns no detection. Brightest first.
  const g = (s: PlanStar) => s.g ?? 99;
  const glow: PlanStar[] = [];
  const lostIn = new Map<string, string>();
  for (const s of [...allStars].sort((a, b) => g(a) - g(b))) {
    const host = glow.find((t) => g(t) < g(s) && dist(s, t) <= circleRadius(t.extent, o.floor));
    if (host) lostIn.set(s.id, host.id);
    else if (s.saturated) glow.push(s);
  }
  const stars = allStars.map((s) => (lostIn.has(s.id) ? { ...s, saturated: false, extent: 0, ringed: false } : s));
  const byId = new Map(stars.map((s) => [s.id, s]));
  const D = o.measured ? dets : [];
  const fragmentReason = (d: PlanDet) => d.reasons.includes("bright_fragment");

  // 1. Bright stars: their own detection (the nearest within 6″), and the pieces of their glow.
  const bright = stars.filter((s) => s.saturated || s.extent >= o.brightExtent);
  const ownerOf = new Map<string, number>(); // star id → position in D
  const ownedBy = new Map<number, string>();
  for (const b of bright) {
    let best = -1;
    let bd = own;
    D.forEach((d, j) => {
      if (fragmentReason(d) || ownedBy.has(j)) return;
      // Another star's measurement (unless that star is lost in this one's glow).
      if (d.gaiaId && d.gaiaId !== b.id && byId.has(d.gaiaId) && lostIn.get(d.gaiaId) !== b.id) return;
      const r = dist(d, b);
      if (r <= bd) {
        bd = r;
        best = j;
      }
    });
    if (best >= 0) {
      ownerOf.set(b.id, best);
      ownedBy.set(best, b.id);
    }
  }
  const hidden = new Map<number, string>(); // position in D → parent id
  D.forEach((d, j) => {
    if (ownedBy.has(j)) return;
    if (fragmentReason(d)) {
      const parent = d.parentGaiaId && byId.has(d.parentGaiaId) ? d.parentGaiaId : nearest(bright, d)?.id;
      hidden.set(j, parent ?? d.parentGaiaId ?? "");
      return;
    }
    // Until the server says so: an artefact or unmatched dot inside a bright star's glow is a piece
    // of it; inside a saturated star's blob, so is any dot (a "star" there matched a Gaia star lost
    // in the glow).
    const anyClass = d.cls !== "artifact" && d.cls !== "other";
    for (const b of bright)
      if ((!anyClass || b.saturated) && dist(d, b) <= circleRadius(b.extent, o.floor)) {
        hidden.set(j, b.id);
        return;
      }
  });
  const pieces = (id: string) => [...hidden.values()].filter((p) => p === id).length;
  const visible = D.map((_, j) => j).filter((j) => !hidden.has(j));

  // 2. Blends: one detection, two or more Gaia stars under it, none with a detection of its own.
  const nearestVisible = (s: PlanStar): number => {
    let best = -1;
    let bd = blendPx;
    for (const j of visible) {
      const r = dist(D[j], s);
      if (r <= bd) {
        bd = r;
        best = j;
      }
    }
    return best;
  };
  const blendOf = new Map<number, string[]>();
  const inBlend = new Set<string>();
  for (const j of visible) {
    const d = D[j];
    if (ownedBy.has(j)) continue;
    let ids = (d.blendGaiaIds ?? []).filter((id) => byId.has(id));
    if (ids.length < 2 && !(d.blendGaiaIds?.length ?? 0)) {
      const cand = stars
        .filter((s) => s.ringed && !inBlend.has(s.id) && dist(s, d) <= blendPx && nearestVisible(s) === j)
        .sort((a, b) => dist(a, d) - dist(b, d));
      const gMin = Math.min(...cand.map((s) => s.g ?? 99));
      const ok = cand.filter((s) => s.g == null || gMin === 99 || s.g - gMin <= BLEND_DMAG);
      // Connected: each one within 9″ of another already in.
      const group = ok.slice(0, 1);
      for (const s of ok.slice(1)) if (group.some((t) => dist(t, s) <= blendPx)) group.push(s);
      ids = group.length >= 2 ? group.map((s) => s.id) : [];
    }
    if (ids.length >= 2) {
      blendOf.set(j, ids);
      ids.forEach((id) => inBlend.add(id));
    }
  }

  // 3. Circles, each as big as its source looks, never swallowing a neighbour.
  const circles: PlanCircle[] = [];
  for (const j of visible) {
    const d = D[j];
    let r = circleRadius(d.extent, o.floor);
    const c: PlanCircle = { i: d.i, X: d.X, Y: d.Y, r };
    const b = ownedBy.get(j);
    const blend = blendOf.get(j);
    if (b) {
      c.r = Math.max(r, circleRadius(byId.get(b)!.extent, o.floor));
      c.bright = b;
      c.fragments = pieces(b);
    } else if (blend) {
      c.blend = blend;
      c.r = Math.max(r, ...blend.map((id) => dist(d, byId.get(id)!) + o.floor * 0.75));
    } else {
      let nn = Infinity;
      for (const k of visible) if (k !== j) nn = Math.min(nn, dist(D[k], d));
      r = Math.min(r, Math.max(o.floor, 0.75 * nn));
      c.r = r;
    }
    const match = b ?? (d.gaiaId && byId.has(d.gaiaId) ? d.gaiaId : undefined);
    if (match && !blend) {
      const s = byId.get(match)!;
      const off = dist(d, s);
      if (off <= own) {
        c.star = match;
        c.offsetArcsec = off / o.pxPerArcsec;
      }
    }
    circles.push(c);
  }

  // 4. A bright star with no detection of its own: one dashed ring around its whole blob.
  const big: PlanBig[] = o.measured
    ? bright
        .filter((b) => !ownerOf.has(b.id) && (b.saturated || pieces(b.id) > 0))
        .map((b) => ({ id: b.id, X: b.X, Y: b.Y, r: circleRadius(b.extent, o.floor), saturated: b.saturated, fragments: pieces(b.id) }))
    : [];
  const isBig = new Set(big.map((b) => b.id));

  // 5. Gaia rings, a little bigger than the circle they hold, the circle's offset included.
  const matched = new Map(circles.filter((c) => c.star).map((c) => [c.star!, c]));
  const rings: PlanRing[] = [];
  for (const s of stars) {
    if (!s.ringed || inBlend.has(s.id) || isBig.has(s.id)) continue;
    let r = circleRadius(s.extent, o.floor);
    r += ringGap(r);
    const c = matched.get(s.id);
    if (c) r = Math.max(r, c.r + dist(c, s) + ringGap(c.r));
    rings.push({ id: s.id, X: s.X, Y: s.Y, r });
  }
  for (const c of circles)
    if (c.blend)
      rings.push({
        id: c.blend[0],
        X: c.X,
        Y: c.Y,
        r: c.r + ringGap(c.r),
        blend: c.blend.map((id) => ({ id, X: byId.get(id)!.X, Y: byId.get(id)!.Y })),
      });

  // Map positions in D back to the caller's indices.
  const hiddenByIndex = new Map([...hidden].map(([j, p]) => [D[j].i, p]));
  return { circles, hidden: hiddenByIndex, rings, big };
}

function nearest<T extends { X: number; Y: number }>(list: T[], p: { X: number; Y: number }): T | undefined {
  let best: T | undefined;
  let bd = Infinity;
  for (const s of list) {
    const r = dist(s, p);
    if (r < bd) {
      bd = r;
      best = s;
    }
  }
  return best;
}
