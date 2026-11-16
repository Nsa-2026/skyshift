// How the plate's circles and rings are laid out (web/DESIGN.md, "Pencil marks"). Each measured
// circle is as big as its source looks on the plate; each Gaia ring a little bigger, so a match
// reads as a circle inside a ring. One object, one set of marks: one circle and at most one Gaia
// ring hugging it, never a circle inside another circle. One bright star is one mark, never its
// fragments. Two Gaia stars that SPHEREx sees as one dot get one circle and one ring. Two dots a PSF
// width apart keep a mark each. Pure: screen positions in, a plan out.
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
  /** A detection on this plate (any of its photos) is matched to it: SPHEREx sees it on its own.
   *  Known even when the measured layer is off, so close pairs plan the same either way. */
  detected?: boolean;
  /** The plate shows a dot of its own at its place (rings.ts `peakNear`): a separate object, never
   *  folded into a brighter star's ring or lost in its light. */
  peak?: boolean;
  /** A bright star: how far its glow reaches on the plate (rings.ts `glowRadius`), screen pixels. */
  glow?: number;
  /** SkyShift measured it in this photo (a detection matched to it, or a source without a match on
   *  it: `sourceGaia`), drawn or not. Off a saturated star's burnt core, such a star is never lost
   *  in its glow, and gets a ring of its own. */
  found?: boolean;
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
  /** Signal-to-noise: of two measurements of one object, the plate draws the stronger. */
  snr?: number;
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
  /** Draw the measured layer (circles, saturated marks); off: Gaia rings only. The rings are the
   *  same either way: the plan is made with every detection, then the circles are left out. */
  measured: boolean;
  /** The plain Viewer: sources that may be the glow's own (see `glowHost`) are not drawn. Else they
   *  are drawn greyed (`PlanCircle.glow`). */
  hideGlow?: boolean;
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
  /** A source without a Gaia match (`other`, or a stack-only one in a glow) that sits on Gaia stars:
   *  drawn and named as them (the server's match missed a blend). Display only. */
  asGaia?: boolean;
  /** It is the measurement of a bright or saturated star: sized to its whole blob. */
  bright?: string;
  /** Fragments of that star hidden on the plate. */
  fragments?: number;
  /** In this bright star's glow, with nothing in Gaia to back it: drawn greyed, "may not be real". */
  glow?: string;
  /** Other detections of the same object (closer than half a PSF FWHM), not drawn: caller indices. */
  also?: number[];
}

export interface PlanRing {
  /** The Gaia star, or the first of a blend. */
  id: string;
  X: number;
  Y: number;
  r: number;
  /** A blend: every star's position, drawn as a small dot inside the one ring (Advanced only). */
  blend?: { id: string; X: number; Y: number }[];
}

/** A saturated star (always), or a bright one with no detection of its own: one dashed ring around its blob. */
export interface PlanBig {
  id: string;
  X: number;
  Y: number;
  r: number;
  saturated: boolean;
  /** Its fragments hidden on the plate. */
  fragments: number;
  /** Its own detection (caller index), drawn as this ring rather than as a circle of its own. */
  det?: number;
}

/** A bright star's glow on the plate: where sources may be the glow's own. */
export interface PlanGlow {
  id: string;
  X: number;
  Y: number;
  r: number;
}

export interface Plan {
  circles: PlanCircle[];
  /** Detections not drawn, by their index: pieces of a bright star or sources in its glow (the star's Gaia id). */
  hidden: Map<number, string>;
  rings: PlanRing[];
  big: PlanBig[];
  /** The glows that hold a greyed or hidden source. */
  glows: PlanGlow[];
}

/** Within this of a bright star, a detection is that star's own measurement. */
export const OWN_ARCSEC = 6;
/** Two Gaia stars closer than this (about 1.5 SPHEREx pixels) can blend into one dot. */
export const BLEND_ARCSEC = 9;
/** A blend partner at most this much fainter (G); fainter adds nothing SPHEREx can see. */
export const BLEND_DMAG = 3;
/**
 * One object: two marks closer than this (half the PSF's 7.53″ FWHM) are the same thing. Two
 * measurements this close keep one circle; a Gaia star this close to a source without a Gaia match
 * is that source. Two dots a whole FWHM apart are two objects, and keep a mark each.
 */
export const SAME_OBJECT_ARCSEC = 3.75;
/**
 * A Gaia star this bright or brighter, within SAME_OBJECT_ARCSEC of a source the server didn't match
 * to Gaia, is that source (or part of it): the pipeline's own `blend` cut (contracts/api.md: two or
 * more Gaia DR3 stars, G < 18). Fainter ones add little a SPHEREx photo can show.
 */
export const GAIA_SEEN_G = 18;

/** A measured circle: just outside how far the source reaches, never under the floor. */
export const circleRadius = (extent: number, floor: number): number => Math.max(floor, extent + 2);
/** How much bigger a Gaia ring is than the circle inside it. */
export const ringGap = (r: number): number => Math.max(3, 0.15 * r);

const dist = (a: { X: number; Y: number }, b: { X: number; Y: number }) => Math.hypot(a.X - b.X, a.Y - b.Y);
const gOf = (s: { g: number | null }) => s.g ?? 99;
/** Movers, suspects and known asteroids: never hidden, never folded into another mark. */
export const PROTECTED: ReadonlySet<MeasuredClass> = new Set<MeasuredClass>(["suspect", "known", "mover"]);
/** Classes a bright star's glow can fake (contracts/api.md: no Gaia match, or found only on the stacks). */
const GLOW_PRONE: ReadonlySet<MeasuredClass> = new Set<MeasuredClass>(["other", "stack"]);

/**
 * The bright star whose glow holds a point, if any: within its measured glow (`PlanStar.glow`) or
 * its blob; of several, the brightest (a star sitting in Barnard's glow reads that glow as its own
 * blob, but the glow is Barnard's). The star's own measurement (within OWN_ARCSEC) is not "in" it.
 * `stars`: the bright ones.
 */
export function glowHost(p: { X: number; Y: number }, stars: PlanStar[], o: Pick<PlanOptions, "pxPerArcsec" | "floor">): PlanStar | undefined {
  let best: PlanStar | undefined;
  for (const s of stars) {
    const r = dist(p, s);
    if (r <= OWN_ARCSEC * o.pxPerArcsec || r > Math.max(s.glow ?? 0, circleRadius(s.extent, o.floor))) continue;
    if (!best || gOf(s) < gOf(best) || (gOf(s) === gOf(best) && r < dist(p, best))) best = s;
  }
  return best;
}

/**
 * The Gaia stars a source without a Gaia match is, by position (display only; the data keep its
 * class). For an `other` source, or a stack-only one inside a bright star's glow (`inGlow`): the
 * stars the server says it blends (`blend_gaia_ids`), and any star of G ≤ GAIA_SEEN_G within
 * SAME_OBJECT_ARCSEC of it; a stack-only source's own Gaia match too. Not a star another
 * detection already measures on its own (`claimed`), nor one more than BLEND_DMAG fainter than the
 * brightest. Nearest first. Empty: the source really is one Gaia doesn't list.
 *
 * The server's match is 4″ (contracts/api.md `measured` class rules), so a source whose centroid
 * falls between two stars of a blend matches neither and reads `other`, "Source Gaia doesn't list",
 * right on top of the Gaia ring of the two. P1/P2: docs/TODO.md asks for the class rule to do the same.
 */
export function sourceGaia(
  d: Pick<PlanDet, "X" | "Y" | "cls" | "gaiaId" | "blendGaiaIds">,
  stars: { id: string; X: number; Y: number; g: number | null }[],
  claimed: ReadonlySet<string>,
  pxPerArcsec: number,
  inGlow = false,
): string[] {
  if (d.cls !== "other" && !(d.cls === "stack" && inGlow)) return [];
  const near = SAME_OBJECT_ARCSEC * pxPerArcsec;
  const named = new Set([...(d.blendGaiaIds ?? []), ...(d.cls === "stack" && d.gaiaId ? [d.gaiaId] : [])]);
  const list = stars
    .filter((s) => !claimed.has(s.id) && (named.has(s.id) || (dist(s, d) <= near && gOf(s) <= GAIA_SEEN_G)))
    .sort((a, b) => dist(a, d) - dist(b, d));
  const gMin = Math.min(...list.map(gOf));
  return list.filter((s) => gOf(s) - gMin <= BLEND_DMAG).map((s) => s.id);
}

export function planMarks(allStars: PlanStar[], dets: PlanDet[], o: PlanOptions): Plan {
  const own = OWN_ARCSEC * o.pxPerArcsec;
  const blendPx = BLEND_ARCSEC * o.pxPerArcsec;
  const sameObj = SAME_OBJECT_ARCSEC * o.pxPerArcsec;
  // 0. A fainter star inside a saturated star's blob is lost in its glow: it reads the burnt core
  // as its own (not saturated itself), gets no ring and owns no detection. Brightest first. Not one
  // whose own dot the plate shows, nor one SkyShift measured away from the burnt core: that is a
  // separate star next to it (Barnard's months plate: a G 16.9 star 51″ out, S/N 460 on the stacks).
  const glowStars: PlanStar[] = [];
  const lostIn = new Map<string, string>();
  for (const s of [...allStars].sort((a, b) => gOf(a) - gOf(b))) {
    // (A star that sees burnt pixels near it can't tell its own dot from the burnt core.)
    const apart = !s.saturated && (s.peak || s.found);
    const host = apart ? undefined : glowStars.find((t) => gOf(t) < gOf(s) && dist(s, t) <= circleRadius(t.extent, o.floor));
    if (host) lostIn.set(s.id, host.id);
    else if (s.saturated) glowStars.push(s);
  }
  const stars = allStars.map((s) => (lostIn.has(s.id) ? { ...s, saturated: false, extent: 0, ringed: false, glow: undefined } : s));
  const byId = new Map(stars.map((s) => [s.id, s]));
  // Every detection, even with the measured layer off: the rings are planned around the circles
  // whether or not they are drawn, so the same Gaia stars get the same rings either way.
  const D = dets;
  const fragmentReason = (d: PlanDet) => d.reasons.includes("bright_fragment");
  const single = (d: PlanDet) => d.cls !== "stack" && d.cls !== "artifact";

  // 1. Bright stars: their own detection (the nearest within 6″), and the pieces of their glow.
  const bright = stars.filter((s) => s.saturated || s.extent >= o.brightExtent);
  const ownerOf = new Map<string, number>(); // star id → position in D
  const ownedBy = new Map<number, string>();
  for (const b of bright) {
    let best = -1;
    let bd = own;
    D.forEach((d, j) => {
      if (fragmentReason(d) || ownedBy.has(j) || PROTECTED.has(d.cls)) return;
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
  // Which stars single-photo detections measure on their own (by position in D).
  const claimedBy = new Map<string, number>();
  D.forEach((d, j) => {
    if (single(d) && d.gaiaId && !claimedBy.has(d.gaiaId)) claimedBy.set(d.gaiaId, j);
  });
  for (const [id, j] of ownerOf) claimedBy.set(id, j);
  const claimedExcept = (j: number) => new Set([...claimedBy].filter(([, k]) => k !== j).map(([id]) => id));
  // The Gaia stars a source without a match is (sourceGaia), before anything is hidden: a source
  // Gaia backs is never taken for a piece of a glow.
  const hostOf = new Map<number, PlanStar>();
  const gaiaOfSource = new Map<number, string[]>();
  D.forEach((d, j) => {
    if (ownedBy.has(j)) return;
    const h = GLOW_PRONE.has(d.cls) ? glowHost(d, bright, o) : undefined;
    if (h) hostOf.set(j, h);
    const ids = sourceGaia(d, stars, claimedExcept(j), o.pxPerArcsec, !!h).filter((id) => byId.has(id));
    if (ids.length) gaiaOfSource.set(j, ids);
  });

  const hidden = new Map<number, string>(); // position in D → parent id
  D.forEach((d, j) => {
    if (ownedBy.has(j) || PROTECTED.has(d.cls)) return;
    // Inside a saturated star's burnt core (its dashed ring): a source with no Gaia match, or found
    // only on the stacks, is a piece of that core, never drawn, not even greyed (Barnard's Star held
    // a cluster of dashed circles). Only a Gaia star that stands apart from the core backs it.
    if (GLOW_PRONE.has(d.cls)) {
      const core = bright.find((b) => b.saturated && dist(d, b) <= circleRadius(b.extent, o.floor));
      if (core && !(gaiaOfSource.get(j) ?? []).some((id) => !lostIn.has(id) && id !== core.id)) {
        hidden.set(j, core.id);
        return;
      }
    }
    if (gaiaOfSource.has(j)) return;
    if (fragmentReason(d)) {
      const parent = d.parentGaiaId && byId.has(d.parentGaiaId) ? d.parentGaiaId : nearest(bright, d)?.id;
      hidden.set(j, parent ?? d.parentGaiaId ?? "");
      return;
    }
    // A source with no Gaia match, or found only on the stacks, inside a bright star's glow (or its
    // blob): the glow makes such sources (Barnard's months plate: stack-only ones 10 times denser
    // within 30″ than 2′ out, no-Gaia ones 4 times). The plain Viewer leaves them out; Advanced draws
    // them greyed (step 3), with the hover "may not be real".
    const h = hostOf.get(j);
    if (h) {
      if (o.hideGlow) hidden.set(j, h.id);
      return;
    }
    // Until the server says so: an artefact inside a bright star's blob is a piece of it; inside a
    // saturated star's blob, so is any dot (a "star" there matched a Gaia star lost in the glow).
    for (const b of bright)
      if ((d.cls === "artifact" || b.saturated) && dist(d, b) <= circleRadius(b.extent, o.floor)) {
        hidden.set(j, b.id);
        return;
      }
  });
  const pieces = (id: string) => [...hidden.values()].filter((p) => p === id).length;

  // 1b. One object, one circle. Two measurements closer than half a FWHM are one object: the
  // stronger is drawn and names the other. A stack-only source on a drawn source's core (within half
  // a FWHM) or on a Gaia star that source is (its match, or a star of its blend) is that source
  // again. A mover, suspect or known asteroid is never folded away; two dots a FWHM apart keep a
  // circle each.
  const also = new Map<number, number[]>(); // kept position in D → folded positions
  const fold = (keep: number, away: number) => {
    hidden.set(away, "");
    also.set(keep, [...(also.get(keep) ?? []), away, ...(also.get(away) ?? [])]);
    also.delete(away);
  };
  const live = () => D.map((_, j) => j).filter((j) => !hidden.has(j));
  const strength = (j: number) => (ownedBy.has(j) ? 2 : 0) + (D[j].snr ?? 0) / 1e9;
  {
    const singles = live().filter((j) => single(D[j]));
    for (let a = 0; a < singles.length; a++)
      for (let b = a + 1; b < singles.length; b++) {
        const ja = singles[a];
        const jb = singles[b];
        if (hidden.has(ja) || hidden.has(jb) || dist(D[ja], D[jb]) >= sameObj) continue;
        const pa = PROTECTED.has(D[ja].cls);
        const pb = PROTECTED.has(D[jb].cls);
        if (pa && pb) continue;
        const keepA = pa || (!pb && (strength(ja) > strength(jb) || (strength(ja) === strength(jb) && (D[ja].snr ?? 0) >= (D[jb].snr ?? 0))));
        if (keepA) fold(ja, jb);
        else fold(jb, ja);
      }
    const kept = live().filter((j) => single(D[j]));
    for (const j of live()) {
      const d = D[j];
      if (d.cls !== "stack") continue;
      const host = kept.find((k) => {
        const s = D[k];
        // Not "inside the dot it reaches": in a bright star's glow a source's reach is the glow's.
        if (dist(s, d) < sameObj) return true;
        const mine = d.gaiaId ?? gaiaOfSource.get(j)?.[0];
        return !!mine && (s.gaiaId === mine || (s.blendGaiaIds ?? []).includes(mine) || (gaiaOfSource.get(k) ?? []).includes(mine));
      });
      if (host != null) fold(host, j);
    }
  }
  const visible = live();

  // 2. Blends: one detection, two or more Gaia stars under it, none measured on its own by another
  // detection. The server's list (blend_gaia_ids) with the detection's own match added; else the
  // stars nearer to it than to any other detection.
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
  const asGaia = new Map<number, string>();
  const inBlend = new Set<string>();
  const dimmest = (ids: string[]) => {
    const gMin = Math.min(...ids.map((id) => gOf(byId.get(id)!)));
    return ids.filter((id) => gOf(byId.get(id)!) - gMin <= BLEND_DMAG);
  };
  for (const j of visible) {
    const d = D[j];
    if (ownedBy.has(j)) continue;
    const claimed = claimedExcept(j);
    let ids: string[];
    const src = gaiaOfSource.get(j);
    if (src) ids = src.filter((id) => !inBlend.has(id));
    else if (d.blendGaiaIds?.length) {
      ids = d.blendGaiaIds.filter((id) => byId.has(id) && !claimed.has(id) && !inBlend.has(id));
      if (ids.length && d.gaiaId && byId.has(d.gaiaId) && !ids.includes(d.gaiaId)) ids.push(d.gaiaId);
      ids = ids.length ? dimmest(ids) : [];
    } else {
      const cand = stars
        .filter((s) => s.ringed && !inBlend.has(s.id) && !claimed.has(s.id) && dist(s, d) <= blendPx && nearestVisible(s) === j)
        .sort((a, b) => dist(a, d) - dist(b, d));
      const gMin = Math.min(...cand.map(gOf));
      const ok = cand.filter((s) => s.g == null || gMin === 99 || s.g - gMin <= BLEND_DMAG);
      // Connected: each one within 9″ of another already in.
      const group = ok.slice(0, 1);
      for (const s of ok.slice(1)) if (group.some((t) => dist(t, s) <= blendPx)) group.push(s);
      ids = group.length >= 2 ? group.map((s) => s.id) : [];
    }
    if (ids.length >= 2) {
      blendOf.set(j, ids);
      ids.forEach((id) => inBlend.add(id));
    } else if (src && ids.length === 1) asGaia.set(j, ids[0]);
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
      if (gaiaOfSource.has(j)) c.asGaia = true;
      c.r = Math.max(r, ...blend.map((id) => dist(d, byId.get(id)!) + o.floor * 0.75));
    } else {
      // Half the way to the next detection: neighbours' circles meet, never overlap (they used to
      // reach three quarters of the way, and close pairs read as double rings).
      let nn = Infinity;
      for (const k of visible) if (k !== j) nn = Math.min(nn, dist(D[k], d));
      r = Math.min(r, Math.max(o.floor, 0.5 * nn));
      c.r = r;
    }
    const match = b ?? asGaia.get(j) ?? (d.gaiaId && byId.has(d.gaiaId) ? d.gaiaId : undefined);
    if (match && !blend) {
      const s = byId.get(match)!;
      const off = dist(d, s);
      if (off <= own || asGaia.has(j)) {
        c.star = match;
        c.offsetArcsec = off / o.pxPerArcsec;
        if (asGaia.has(j)) {
          c.asGaia = true;
          // Sized to hold its star: the ring that hugs it then holds the dot Gaia puts there.
          c.r = Math.max(c.r, Math.min(off + o.floor * 0.75, SAME_OBJECT_ARCSEC * 2 * o.pxPerArcsec));
        }
      }
    }
    const h = hostOf.get(j);
    if (h && !c.star && !c.blend) c.glow = h.id;
    const folded = also.get(j);
    if (folded?.length) c.also = folded.map((k) => D[k].i);
    circles.push(c);
  }
  // No circle inside another: where one's centre falls inside a bigger one (a bright star's blob,
  // a blend), the bigger shrinks until they meet. Two objects, two circles side by side.
  for (const a of circles)
    for (const c of circles) {
      if (a === c || a.r < c.r) continue;
      const r = dist(a, c);
      if (r < a.r) a.r = Math.max(o.floor, r - c.r, r / 2);
    }

  // 4. A saturated star: one dashed ring around its whole blob, always, with its own detection (if
  // any) drawn as that ring, not as a circle of its own. A bright star with no detection of its own
  // whose glow broke into pieces: the same ring.
  const big: PlanBig[] = bright
    .filter((b) => b.saturated || (!ownerOf.has(b.id) && pieces(b.id) > 0))
    .map((b) => {
      const k = ownerOf.get(b.id);
      const c = k != null ? circles.find((x) => x.i === D[k].i) : undefined;
      const r = Math.max(circleRadius(b.extent, o.floor), c && b.saturated ? c.r : 0);
      const one: PlanBig = { id: b.id, X: b.X, Y: b.Y, r, saturated: b.saturated, fragments: pieces(b.id) };
      if (c && b.saturated) one.det = c.i;
      return one;
    });
  const drawnAsBig = new Set(big.flatMap((b) => (b.det != null ? [b.det] : [])));
  const isBig = new Map(big.map((b) => [b.id, b]));

  // 5. Gaia rings, a little bigger than the circle they hold, the circle's offset included. One
  // ring per blob: brightest first, a ringed star that SPHEREx doesn't see on its own (no detection
  // matched to it on this plate, and no dot of its own on the plate) and that sits within 9″ of a
  // ring already planned, or inside that ring's blob, is no ring of its own. More than 3 mag fainter
  // it is lost in that light (no mark); else it joins that ring as a blend (one ring, a dot per
  // star). Under a saturated star's dashed ring it is always lost. Without this, close pairs got two
  // or three overlapping rings.
  const matched = new Map<string, PlanCircle>();
  for (const c of circles) if (c.star && !matched.has(c.star)) matched.set(c.star, c);
  // Seen: measured here, whether or not its circle is drawn (the plain Viewer leaves stack-only
  // sources out, but the star under one still gets its own ring, as in Advanced).
  const seen = new Set<string>(stars.filter((s) => s.detected || s.found).map((s) => s.id));
  for (const d of D) if (d.gaiaId) seen.add(d.gaiaId);
  for (const c of circles) if (c.star) seen.add(c.star);
  const rings: PlanRing[] = [];
  /** Rings (and dashed rings) planned so far: where they sit, how far their blob reaches, their brightest G. */
  const heads: { X: number; Y: number; inner: number; g: number; ring: PlanRing | null }[] = [];
  for (const b of big) if (!byId.get(b.id)!.ringed) heads.push({ X: b.X, Y: b.Y, inner: b.r, g: gOf(byId.get(b.id)!), ring: null });
  for (const c of circles)
    if (c.blend) {
      const ring: PlanRing = {
        id: c.blend[0],
        X: c.X,
        Y: c.Y,
        r: c.r + ringGap(c.r),
        blend: c.blend.map((id) => ({ id, X: byId.get(id)!.X, Y: byId.get(id)!.Y })),
      };
      rings.push(ring);
      heads.push({ X: c.X, Y: c.Y, inner: c.r, g: Math.min(...c.blend.map((id) => gOf(byId.get(id)!))), ring });
    }
  const lone = stars.filter((s) => s.ringed && !inBlend.has(s.id)).sort((a, b) => gOf(a) - gOf(b));
  for (const s of lone) {
    let r = circleRadius(s.extent, o.floor);
    r += ringGap(r);
    const c = matched.get(s.id);
    const bg = isBig.get(s.id);
    // A matched star's ring hugs the circle it holds (a bright star's circle is its whole blob). Sized
    // on the star's own reach instead, a faint star in a bright star's halo got a ring far wider than
    // its circle. A saturated star's ring hugs its dashed ring.
    if (bg) r = bg.r + dist(bg, s) + ringGap(bg.r);
    else if (c) r = c.r + dist(c, s) + ringGap(c.r);
    if (!seen.has(s.id) && !s.peak && !bg) {
      const h = nearest(
        heads.filter((t) => dist(s, t) <= Math.max(blendPx, t.inner)),
        s,
      );
      if (h) {
        if (!h.ring || gOf(s) - h.g > BLEND_DMAG) continue; // lost in a brighter star's light
        h.ring.blend ??= [{ id: h.ring.id, X: h.ring.X, Y: h.ring.Y }];
        h.ring.blend.push({ id: s.id, X: s.X, Y: s.Y });
        h.ring.r = Math.max(h.ring.r, dist(h.ring, s) + o.floor * 0.75 + ringGap(o.floor));
        continue;
      }
    }
    const ring: PlanRing = { id: s.id, X: s.X, Y: s.Y, r };
    rings.push(ring);
    const inner = bg ? bg.r : c ? Math.max(c.r, circleRadius(s.extent, o.floor)) : circleRadius(s.extent, o.floor);
    // Under a saturated star's dashed ring a fainter star is lost in its light, never a dot in its ring.
    heads.push({ X: s.X, Y: s.Y, inner, g: gOf(s), ring: bg ? null : ring });
  }

  // 6. Two stars SPHEREx sees apart, close together: their rings meet rather than overlap. A ring
  // that isn't a bright star's or a blend's is no wider than half the way to the next mark (plus the
  // gap), never tighter than the circle it holds. A faint star in a bright one's halo reads that halo
  // as its own reach; without this its ring swallowed its neighbours.
  const brightIds = new Set(bright.map((b) => b.id));
  for (const rg of rings) {
    if (rg.blend || brightIds.has(rg.id)) continue;
    const c = matched.get(rg.id);
    let nn = Infinity;
    for (const t of rings) if (t !== rg) nn = Math.min(nn, dist(t, rg));
    for (const k of circles) if (k !== c) nn = Math.min(nn, dist(k, rg));
    if (!Number.isFinite(nn)) continue;
    const hold = c ? c.r + dist(c, rg) + ringGap(c.r) : o.floor + ringGap(o.floor);
    rg.r = Math.max(hold, Math.min(rg.r, nn / 2 + ringGap(o.floor)));
  }

  // The glows that hold a greyed or hidden source (the Advanced plate draws their edge).
  const glowIds = new Set<string>();
  for (const [j, h] of hostOf) if (hidden.get(j) === h.id || circles.some((c) => c.i === D[j].i && c.glow)) glowIds.add(h.id);
  const glows: PlanGlow[] = bright
    .filter((b) => glowIds.has(b.id))
    .map((b) => ({ id: b.id, X: b.X, Y: b.Y, r: Math.max(b.glow ?? 0, circleRadius(b.extent, o.floor)) }));

  // Map positions in D back to the caller's indices. With the measured layer off, the circles (and
  // the saturated stars' dashed rings) stay off the plate; the rings are the ones planned around them.
  const hiddenByIndex = new Map([...hidden].filter(([, p]) => p !== "").map(([j, p]) => [D[j].i, p]));
  if (!o.measured) return { circles: [], hidden: new Map(), rings, big: [], glows: [] };
  return { circles: circles.filter((c) => !drawnAsBig.has(c.i)), hidden: hiddenByIndex, rings, big, glows };
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
