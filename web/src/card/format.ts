// Pure helpers for the object card: numbers, wording and axis maths. No DOM here, so tests are cheap.
import type { Det, FramesResponse, GaiaStar, Measure, MeasureReason, MeasureSource, ObjectResponse, SkyPos, SpectrumPoint } from "../api/types";
import { formatMjd, sepArcsec } from "../app/astro";

export const LY_PER_PC = 3.26156;
/** Largest parallax shift between two visits six months apart for our targets (Barnard's Star). */
export const PARALLAX_BETWEEN_VISITS_ARCSEC = 1.1;

/** Gaia distance: 1000 / parallax(mas) in parsecs, or null when Gaia has no usable parallax. */
export function distancePc(parallaxMas: number | null | undefined): number | null {
  if (parallaxMas == null || !Number.isFinite(parallaxMas) || parallaxMas <= 0) return null;
  return 1000 / parallaxMas;
}

export function pcToLy(pc: number): number {
  return pc * LY_PER_PC;
}

/** Few significant figures for a distance: 1.83, 18.3, 183, 1,830. */
export function formatDistance(x: number): string {
  if (x < 10) return x.toFixed(2);
  if (x < 100) return x.toFixed(1);
  return Math.round(x).toLocaleString("en-US");
}

/** (measured − predicted) / σ, signed. Null without a prediction or a usable σ. */
export function sigmaDiff(m: Measure): number | null {
  if (m.predicted_arcsec == null || !(m.sigma_arcsec > 0)) return null;
  return (m.shift_arcsec - m.predicted_arcsec) / m.sigma_arcsec;
}

/** 3σ: the slowest shift the measurement can see. */
export function detectionLimit(m: Measure): number {
  return 3 * m.sigma_arcsec;
}

export function isDetectedMotion(m: Measure): boolean {
  return m.shift_arcsec >= detectionLimit(m);
}

export type Agreement = "agrees" | "marginal" | "disagrees";
export function agreement(nSigma: number): Agreement {
  const a = Math.abs(nSigma);
  if (a < 2) return "agrees";
  if (a < 3) return "marginal";
  return "disagrees";
}

/** Arcsec with two decimals and a double prime: 10.83″. */
export function arcsec(x: number, digits = 2): string {
  return `${x.toFixed(digits)}″`;
}

/** Compass word for a position angle (east of north). */
export function paWord(pa: number): string {
  const names = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"];
  const i = Math.round((((pa % 360) + 360) % 360) / 45) % 8;
  return names[i];
}

/** Position angle of a proper motion (east of north), degrees, or null without one. */
export function pmPa(g: GaiaStar | null): number | null {
  if (!g || g.pmra == null || g.pmdec == null || (g.pmra === 0 && g.pmdec === 0)) return null;
  return ((Math.atan2(g.pmra, g.pmdec) * 180) / Math.PI + 360) % 360;
}

/** Screen direction for a position angle with north up and east left: (dx, dy) with y down. */
export function paVector(pa: number): { dx: number; dy: number } {
  const r = (pa * Math.PI) / 180;
  return { dx: -Math.sin(r), dy: -Math.cos(r) };
}

// ---------------------------------------------------------------------------------------------
// Spectrum

export interface Saturation {
  dets: Det[];
  n: number;
  total: number;
}

export function saturation(spectrum: SpectrumPoint[]): Saturation | null {
  const sat = spectrum.filter((p) => p.saturated);
  if (!sat.length) return null;
  const dets = [...new Set(sat.map((p) => p.det))].sort((a, b) => a - b) as Det[];
  return { dets, n: sat.length, total: spectrum.length };
}

/** "D1, D3 and D6" */
export function listDets(dets: number[]): string {
  const names = dets.map((d) => `D${d}`);
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** A point below 2σ is not a detection: it is drawn as an upper limit at flux + 2σ (floored at 2σ). */
export const DETECT_SIGMA = 2;
/** Below 2σ, drawn as an upper limit. A saturated point is a detection by definition: its
 * error carries the ±50% wing systematic, so the 2σ test would wrongly call it a limit. */
export function isUpperLimit(p: Pick<SpectrumPoint, "flux_ujy" | "err_ujy"> & { saturated?: boolean }): boolean {
  if (p.flux_ujy <= 0) return true;
  if (p.saturated) return false;
  return !(p.flux_ujy > DETECT_SIGMA * p.err_ujy);
}
export function upperLimitValue(p: Pick<SpectrumPoint, "flux_ujy" | "err_ujy">): number {
  return Math.max(p.flux_ujy, 0) + DETECT_SIGMA * p.err_ujy;
}

/** Distinct visit numbers in the spectrum, in order. */
export function visitsOf(spectrum: SpectrumPoint[]): number[] {
  return [...new Set(spectrum.map((p) => p.visit))].sort((a, b) => a - b);
}

export interface MissingVisit {
  visit: number;
  /** The visit's middle (median of its stacks' mjd_mid over the detectors given). */
  mjd: number;
}

/**
 * Visits the months blink shows that have no spectrum point: every exposure there was left out of
 * the photometry (a Gaia star too close to measure apart, a masked core). WISE 0855−0714 was the
 * case (4″ from a G = 16.3 star in visit 3) until the server fitted both there; live answers at
 * other coordinates can still lack a visit.
 * Both number visits by the same rule over the same search, so the numbers compare directly
 * (contracts/api.md: months epoch ids are `d<det>-v<visit>`). det: only that detector's stacks and
 * points (the light curve). [] without months frames near the view (hours clock, stale frames).
 */
export function visitsMissingFromSpectrum(
  frames: FramesResponse | null,
  near: { ra: number; dec: number },
  fovArcmin: number,
  spectrum: SpectrumPoint[],
  det?: Det,
): MissingVisit[] {
  if (!frames || frames.clock !== "months" || !spectrum.length) return [];
  if (sepArcsec(frames.center, near) > fovArcmin * 60) return [];
  const have = new Set(spectrum.filter((p) => det == null || p.det === det).map((p) => p.visit));
  const times = new Map<number, number[]>();
  for (const e of frames.epochs) {
    const v = e.kind === "visit" ? /-v(\d+)$/.exec(e.id) : null;
    if (!v || (det != null && e.det !== det) || have.has(Number(v[1]))) continue;
    times.set(Number(v[1]), [...(times.get(Number(v[1])) ?? []), e.mjd_mid]);
  }
  return [...times].map(([visit, t]) => ({ visit, mjd: median(t) })).sort((a, b) => a.visit - b.visit);
}

/** "Visit 3, May 2026, is in the blink but not here: none of its exposures could be measured cleanly." */
export function missingVisitsSentence(missing: MissingVisit[], det?: Det): string {
  if (!missing.length) return "";
  const month = (mjd: number) => formatMjd(mjd).split(" ").slice(1).join(" ");
  const when = (m: MissingVisit) => `${m.visit}, ${month(m.mjd)}`;
  const list =
    missing.length === 1
      ? `Visit ${when(missing[0])}, is`
      : `Visits ${missing.slice(0, -1).map(when).join("; ")} and ${when(missing[missing.length - 1])} are`;
  const one = missing.length === 1;
  const what = det != null ? `${one ? "its" : "their"} D${det} exposures` : `${one ? "its" : "their"} exposures`;
  return `${list} in the blink but not here: none of ${what} could be measured cleanly.`;
}

/** Median of a list (NaN for an empty list). */
export function median(xs: number[]): number {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Log-axis domain and ticks. Decades always; 2 and 5 inside when the range spans under ~2 decades. */
export function logTicks(lo: number, hi: number): { min: number; max: number; ticks: number[] } {
  if (!(lo > 0) || !(hi > 0)) return { min: 1, max: 10, ticks: [1, 10] };
  if (hi < lo) [lo, hi] = [hi, lo];
  if (hi / lo < 1.5) {
    lo /= 1.25;
    hi *= 1.25;
  }
  const d0 = Math.floor(Math.log10(lo));
  const d1 = Math.ceil(Math.log10(hi));
  const span = Math.log10(hi) - Math.log10(lo);
  const mults = span < 1.3 ? [1, 2, 5] : span < 2.3 ? [1, 3] : [1];
  const ticks: number[] = [];
  for (let d = d0; d <= d1; d++) {
    for (const m of mults) {
      const v = m * 10 ** d;
      if (v >= lo * 0.999 && v <= hi * 1.001) ticks.push(Number(v.toPrecision(3)));
    }
  }
  // Too few labels inside the data range: widen to the enclosing decades.
  if (ticks.length < 2) {
    const min = 10 ** Math.floor(Math.log10(lo));
    const max = 10 ** Math.ceil(Math.log10(hi));
    return { min, max, ticks: max / min > 10 ? decadesBetween(min, max) : [min, max] };
  }
  return { min: lo, max: hi, ticks };
}

function decadesBetween(min: number, max: number): number[] {
  const out: number[] = [];
  for (let v = min; v <= max * 1.001; v *= 10) out.push(Number(v.toPrecision(3)));
  return out;
}

/** Nice linear ticks (1, 2, 5 steps). */
export function linearTicks(lo: number, hi: number, target = 4): { min: number; max: number; ticks: number[] } {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { min: 0, max: 1, ticks: [0, 1] };
  if (hi === lo) {
    const pad = Math.abs(hi) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  }
  const raw = (hi - lo) / target;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? 10 * p;
  const min = Math.floor(lo / step) * step;
  const max = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = min; v <= max + step / 2; v += step) ticks.push(Number(v.toPrecision(6)));
  return { min, max, ticks };
}

/** Compact µJy for axes: 300, 1k, 30k, 1M. */
export function compactUjy(v: number): string {
  const a = Math.abs(v);
  const f = (x: number, s: string) => `${Number(x.toPrecision(3))}${s}`;
  if (a >= 1e6) return f(v / 1e6, "M");
  if (a >= 1e3) return f(v / 1e3, "k");
  return f(v, "");
}

/** Full µJy for readouts: 127,811. */
export function fullUjy(v: number): string {
  return Math.round(v).toLocaleString("en-US");
}

// ---------------------------------------------------------------------------------------------
// Visits and the reason a measure is missing

/** Number of distinct visits in frames, or null when the frames don't count visits (hours clock, other spot). */
export function visitCountFromFrames(frames: FramesResponse | null, near: { ra: number; dec: number }, fovArcmin: number): number | null {
  if (!frames || frames.clock !== "months") return null;
  if (sepArcsec(frames.center, near) > fovArcmin * 60) return null; // stale frames from the last view
  // Epochs repeat per detector (d2-v1, d5-v1, …): count visits within each detector, keep the most.
  const perDet = new Map<number, number>();
  for (const e of frames.epochs) if (e.kind === "visit") perDet.set(e.det, (perDet.get(e.det) ?? 0) + 1);
  return perDet.size ? Math.max(...perDet.values()) : 0;
}

/**
 * Whether the frames follow a moving object (each epoch carries its own centre, as for 3I/ATLAS),
 * or keep one fixed centre that the object crosses. Null when there are no frames yet.
 */
export function framesFollow(frames: FramesResponse | null): boolean | null {
  if (!frames || !frames.epochs.length) return null;
  return frames.epochs.some((e) => e.center != null);
}

const BLINK_HINT = "Blink the visits and flag anything that steps.";

/** One plain sentence per server reason (contracts/api.md v3.2 `measure_reason`); moved_away is worded apart. */
export const MEASURE_REASON_TEXT: Record<Exclude<MeasureReason, "moved_away">, string> = {
  few_visits: "SPHEREx hasn't seen this spot often enough yet. A shift needs two visits about six months apart, each with a few photos.",
  not_detected: `Nothing stands out clearly at this spot in the first or the last visit, so there is no shift to measure. ${BLINK_HINT}`,
  edge: `This spot is at the edge of SPHEREx's photos in the first or the last visit, so its position can't be measured cleanly. ${BLINK_HINT}`,
  different_sources: "Different sources stand out here in the first and the last visit, too far apart to be one moving star. Blink the visits to compare them.",
  no_time: "SkyShift ran out of time before measuring the shift. Try again in a minute: it is faster the second time.",
};

/** The server's reason, or null when the answer has none (static files, older answers, unknown codes). */
export function measureReasonOf(object: ObjectResponse | null): MeasureReason | null {
  const r = object?.measure_reason ?? null;
  return r === "moved_away" || (r != null && Object.prototype.hasOwnProperty.call(MEASURE_REASON_TEXT, r)) ? r : null;
}

/** A fast star catalogued at the position asked has moved away: say so, and how far it went. */
export function movedAwaySentence(object: ObjectResponse | null, at: SkyPos | null): string {
  const hint = object?.measure_hint ?? null;
  if (!hint) return "No source at this position in either visit. Fast stars move: try their current position or their name.";
  const who = hint.name ?? "a fast-moving star";
  const sep = at ? sepArcsec(at, hint) : null;
  const far = sep != null && Number.isFinite(sep) ? `${Math.round(sep)}″ away` : "somewhere else";
  return `This is a catalogue position of ${who}, but it moves fast: during SPHEREx's visits it was ${far}, so there is nothing here to measure.`;
}

export function noMeasureReason(opts: {
  object: ObjectResponse | null;
  kind: string | null;
  visits: number | null;
  /** framesFollow(frames): true, false, or null when unknown. */
  follows?: boolean | null;
  /** The position asked, to say how far a moved-away star went. */
  at?: SkyPos | null;
}): string {
  const { object, kind, visits, follows = null, at = null } = opts;
  if (kind && /asteroid|comet/i.test(kind)) {
    if (follows === true) {
      return "It moves in hours, not months, so there is no shift between visits to measure. The frames follow it across the sky.";
    }
    if (follows === false) {
      return "It moves in hours, not months, so there is no shift between visits to measure. It crosses the field between exposures; the violet diamond marks its predicted position.";
    }
    return "It moves in hours, not months, so there is no shift between visits to measure.";
  }
  const reason = measureReasonOf(object);
  if (reason === "moved_away") return movedAwaySentence(object, at);
  const spectrumVisits = object ? visitsOf(object.spectrum).length : 0;
  const n = visits ?? (spectrumVisits || null);
  if (n != null && n < 2) {
    return `SPHEREx has seen this spot in ${n === 0 ? "no visits" : "one visit"} so far. A shift needs two visits, about six months apart.`;
  }
  if (reason) return MEASURE_REASON_TEXT[reason];
  // The server measured nothing although there are two visits, and didn't say why (static files and
  // answers from before contracts v3.2): its detection guard, or the live answer ran out of time.
  if (object) {
    return "No shift measured here: SkyShift shows one only when a single source stands out clearly at this spot in both the first and last visits. Blink the visits and flag anything that steps.";
  }
  return "Nothing has been measured at this spot yet. Blink the visits and flag anything that steps.";
}

// ---------------------------------------------------------------------------------------------
// Plain sentences for the card

/** Light-years for prose: 6, 12, 510, 2,600 (two significant figures past 20). */
export function proseLy(ly: number): string {
  if (!Number.isFinite(ly) || ly <= 0) return "";
  if (ly < 2) return ly.toFixed(1);
  if (ly < 20) return String(Math.round(ly));
  return Number(ly.toPrecision(2)).toLocaleString("en-US");
}

/**
 * One factual sentence about the featured targets. `ly` is Gaia's distance in prose form, or null.
 * Facts beyond the data (spectral class, record holders) are catalogue knowledge, kept short.
 */
const ABOUT: Record<string, (ly: string | null) => string> = {
  barnard: (ly) => `A red dwarf${ly ? ` ${ly} light-years away` : ""}, the fastest-moving star in our sky.`,
  epsindib: (ly) => `A pair of brown dwarfs${ly ? ` ${ly} light-years away` : ""}, orbiting the bright star Epsilon Indi A.`,
  wise0855: () => "The coldest brown dwarf known, too faint for Gaia. SPHEREx sees it at 4–5 µm.",
  rwaur: (ly) => `A young variable star${ly ? ` ${ly} light-years away` : ""}, still surrounded by its disk.`,
  rdra: (ly) => `A pulsating red giant${ly ? ` ${ly} light-years away` : ""} that brightens and fades every 246 days.`,
  "3i-atlas": () => "The third interstellar object ever found: a comet passing through the Solar System.",
  elpis: () => "A main-belt asteroid, between Mars and Jupiter.",
  euphrosyne: () => "One of the largest asteroids in the main belt, between Mars and Jupiter.",
};

/** What the object is, in one plain sentence, from the featured slug or the kind and Gaia's distance. */
export function aboutSentence(opts: {
  slug: string | null;
  kind: string | null;
  object: ObjectResponse | null;
  /** Shown when nothing is known: "The sky at …". */
  where: string;
}): string {
  const { slug, kind, object, where } = opts;
  const pc = distancePc(object?.gaia?.parallax);
  const ly = pc != null ? proseLy(pcToLy(pc)) : null;
  const known = slug ? ABOUT[slug] : undefined;
  if (known) return known(ly);
  const away = ly ? ` ${ly} light-years away` : "";
  const ucd = object?.known_ucd;
  if (ucd) return `An ultracool dwarf of spectral type ${ucd.spt}${away ? `,${away}` : ""}.`;
  if (kind && !/gaia/i.test(kind)) {
    const article = /^[aeiou]/i.test(kind) ? "An" : "A";
    return `${article} ${kind}${away}.`;
  }
  if (object?.gaia) return ly ? `A star in Gaia DR3,${away}.` : "A star in Gaia DR3, with no distance from Gaia.";
  return `The sky at ${where}.`;
}

/** Visit mid-times per detector from months frames near the view, or null (hours clock, stale frames). */
export function visitTimesFromFrames(frames: FramesResponse | null, near: { ra: number; dec: number }, fovArcmin: number): Map<number, number[]> | null {
  if (!frames || frames.clock !== "months") return null;
  if (sepArcsec(frames.center, near) > fovArcmin * 60) return null;
  const out = new Map<number, number[]>();
  for (const e of frames.epochs) if (e.kind === "visit") out.set(e.det, [...(out.get(e.det) ?? []), e.mjd_mid]);
  return out.size ? out : null;
}

/** Visit times per detector from the spectrum: the median MJD of each (detector, visit). */
export function visitTimesFromSpectrum(spectrum: SpectrumPoint[]): Map<number, number[]> | null {
  const by = new Map<number, Map<number, number[]>>();
  for (const p of spectrum) {
    if (p.visit <= 0) continue;
    const d = by.get(p.det) ?? new Map<number, number[]>();
    d.set(p.visit, [...(d.get(p.visit) ?? []), p.mjd]);
    by.set(p.det, d);
  }
  const out = new Map<number, number[]>();
  for (const [det, visits] of by) out.set(det, [...visits.values()].map(median).sort((a, b) => a - b));
  return out.size ? out : null;
}

/**
 * What the shift was measured on (contracts/api.md v3.8), when the answer says so and it hangs
 * together (dt_yr is the gap between its two median times). Null otherwise: older files and cached
 * answers have none, and the card never takes it from the player's frames instead.
 */
export function measureSource(m: Measure): MeasureSource | null {
  const s = m.source;
  if (!s) return null;
  const pair = (a: unknown, ok: (x: number) => boolean): boolean =>
    Array.isArray(a) && a.length === 2 && a.every((x) => typeof x === "number" && ok(x));
  const count = (x: number) => Number.isInteger(x) && x >= 1;
  if (!count(s.det) || s.det > 6) return null;
  if (!pair(s.visits, count) || s.visits[1] <= s.visits[0] || !pair(s.n_exposures, count)) return null;
  if (!pair(s.mjd, Number.isFinite) || s.mjd[1] <= s.mjd[0]) return null;
  return Math.abs((s.mjd[1] - s.mjd[0]) / 365.25 - m.dt_yr) <= 0.001 ? s : null;
}

/**
 * How many visits the measured detector has, so the card can say "between visits 1 and N", for a
 * measure without `source` (above). Take the detectors whose first-to-last span matches dt_yr
 * (within 0.1 yr: the server times stacks by their median exposure, not mjd_mid) and answer only
 * when they agree. Null when unsure: the card then says "the first and last visits".
 */
export function measuredVisitCount(m: Measure, times: Map<number, number[]> | null): number | null {
  if (!times) return null;
  const counts = new Set<number>();
  for (const t of times.values()) {
    if (t.length < 2) continue;
    const span = (Math.max(...t) - Math.min(...t)) / 365.25;
    if (Math.abs(span - m.dt_yr) <= 0.1) counts.add(t.length);
  }
  return counts.size === 1 ? [...counts][0] : null;
}

/** "visits 2 and 4" from the measured pair, "visits 1 and 3" from a visit count, or "the first and last visits". */
export function visitPair(n: number | [number, number] | null): string {
  if (Array.isArray(n)) return `visits ${n[0]} and ${n[1]}`;
  return n != null && n >= 2 ? `visits 1 and ${n}` : "the first and last visits";
}

/** The card's "Measured in" line: detector, visit pair and exposures, and the player's detector when it differs. */
export function measuredIn(src: MeasureSource, playerDet: Det): string {
  const n = src.n_exposures;
  const other = playerDet !== src.det ? `, not the D${playerDet} the player shows` : "";
  return `D${src.det}, ${visitPair(src.visits)} (${n[0]} and ${n[1]} exposures)${other}`;
}

/** Years for prose: "0.99 years", "1 year", "1.42 years". */
export function proseYears(yr: number): string {
  const s = yr.toFixed(2);
  return s === "1.00" ? "1 year" : `${s} years`;
}

/** The sentence after the measured number: when, then how it compares with the prediction. */
export function measureSentence(m: Measure, opts: { visits: number | [number, number] | null; who: string }): string {
  const when = `between ${visitPair(opts.visits)} (${proseYears(m.dt_yr)})`;
  const seen = isDetectedMotion(m);
  const first = seen
    ? `moved ${when}.`
    : `${when}: below the ${arcsec(detectionLimit(m))} we can see (3σ), so it stays put.`;
  const nsig = sigmaDiff(m);
  if (m.predicted_arcsec == null || nsig == null) return `${first} There is no catalogue prediction here to compare with.`;
  const n = Math.abs(nsig).toFixed(1);
  const a = agreement(nsig);
  const verdict =
    a === "agrees" ? `they agree within ${n}σ.` : a === "marginal" ? `they are ${n}σ apart, close but not a clean match.` : `they are ${n}σ apart and disagree.`;
  return `${first} ${opts.who} predicts ${arcsec(m.predicted_arcsec)}: ${verdict}`;
}
