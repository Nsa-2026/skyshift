// What the field sections say, as plain data: the signals a prepared analysis found in a field
// (ranked, with their false-alarm chips and next checks) and how deep the field goes for a distant
// planet. No DOM here, so every sentence can be tested. Shapes: contracts/api.md, "Science workspace".
// Signals and sensitivity come from a cached analysis that is not part of the scored pipeline:
// everything here is "to check", never a discovery or an exclusion.
import type { Analysis, Candidate } from "../api/science-client";
import type { GaiaStar, SkyPos } from "../api/types";
import { formatRaDec, gaiaAt, sepArcsec } from "../app/astro";
import { paWord } from "../card/format";

// ---------------------------------------------------------------------------------------------
// False alarms

export type Tone = "good" | "caution" | "risk" | "unknown";
export interface FalseAlarm {
  tone: Tone;
  /** The chip: "0.99 % false alarms". */
  text: string;
  /** One sentence on what the chip means for this signal. */
  note: string;
}

/** Percent with the precision the trials support: 0.99 %, 1.98 %, 12 %, 100 %. */
export function percent(p: number): string {
  const v = 100 * p;
  if (v >= 10) return `${Math.round(v)} %`;
  return `${Number(v.toFixed(2))} %`;
}

/**
 * The background false-alarm chip. p is an empirical rate over nSlides scrambled backgrounds
 * ((1 + hits) / (1 + trials), contracts/api.md): green under 2 %, amber 2–10 %, red over 10 %.
 * Null (cold sources) means nobody measured it. Never a probability that the object is real.
 */
export function falseAlarm(p: number | null | undefined, nSlides: number): FalseAlarm {
  if (p == null || !Number.isFinite(p) || p < 0 || p > 1) {
    return {
      tone: "unknown",
      text: "False alarms not measured",
      note: "",
    };
  }
  const tone: Tone = p < 0.02 ? "good" : p <= 0.1 ? "caution" : "risk";
  const floor = nSlides > 0 && p <= 1 / (nSlides + 1) + 1e-9;
  const note = floor
    ? `No scrambled copy matched it, the lowest rate ${nSlides} trials can show.`
    : tone === "good"
      ? "Noise rarely makes a signal this strong: worth a closer look."
      : tone === "caution"
        ? "Noise sometimes makes a signal this strong: it could be chance."
        : "Noise often makes a signal this strong: check for artefacts first.";
  return { tone, text: `${percent(p)} false alarms`, note };
}

// ---------------------------------------------------------------------------------------------
// Signals

export type Kind = "slow" | "fast" | "cold";

/** Candidate fields the cached analysis adds beyond the contract's minimum (all optional). */
interface Extra {
  n_slides?: number;
  pm_sig?: number;
  sig_v?: number;
  t0?: number;
  n?: number;
  detections?: { snr?: number; snr_long?: number; snr_short?: number; visit?: number }[];
}
export type AnyCandidate = Candidate & Extra;

/** A Gaia DR3 star near a signal, moved to the signal's time. */
export interface GaiaNear {
  sep: number;
  g: number | null;
  /** Total proper motion, ″/yr. */
  pm: number | null;
}

export interface SignalRow {
  kind: Kind;
  /** Stable within one analysis: kind and index. */
  key: string;
  ra: number;
  dec: number;
  /** The headline number with its unit: "4.53″/yr", "7.55″/day", "Seen in 2 visits". */
  value: string;
  /** Compass word for slow movers ("south-east"), else null. */
  direction: string | null;
  /** Short sentences under the number. */
  facts: string[];
  alarm: FalseAlarm;
  /** For ranking: the false-alarm rate (null last) and the detection strength. */
  p: number | null;
  strength: number;
  /** Slow movers: motion to follow the blink's epochs (″/yr, μα* includes cos δ). */
  pmra: number | null;
  pmdec: number | null;
  mjdRef: number | null;
  /** Gaia star within GAIA_MATCH_ARCSEC: a match, null for none, undefined when not checked. */
  gaia: GaiaNear | null | undefined;
  next: string[];
  /** Details mode: raw position, status, p and trials. */
  raw: string;
}

export interface SignalGroups {
  slow: SignalRow[];
  fast: SignalRow[];
  cold: SignalRow[];
  /** Cold sources seen in one visit only: left out (a real one would be seen again). */
  coldSingle: number;
  total: number;
}

/** Radius for "a Gaia star is here": two thirds of a 6.15″ SPHEREx pixel. */
export const GAIA_MATCH_ARCSEC = 4;
/** A signal this close to a featured field's centre is probably the target itself (PSF FWHM 7.53″). */
export const CENTRE_ARCSEC = 8;

export interface SignalContext {
  /** Field centre and the featured target's name (null for plain coordinates). */
  centre?: SkyPos | null;
  name?: string | null;
  /** Gaia DR3 stars of the field (epoch 2016.0), or null when they couldn't be loaded. */
  gaia?: GaiaStar[] | null;
  /** A time for signals without one (cold sources): the middle of the field's visits. */
  mjdMid?: number | null;
}

const STATUS: Record<string, string> = {
  unvetted: "not yet checked against catalogues",
  single_visit: "seen in one visit only",
  persistent: "seen again in later visits",
  artifact_warning: "an artefact warning is set",
};

const fin = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

/** The nearest Gaia star within GAIA_MATCH_ARCSEC at that time, null for none, undefined if no list. */
export function gaiaNear(pos: SkyPos, mjd: number | null | undefined, stars: GaiaStar[] | null | undefined): GaiaNear | null | undefined {
  if (!stars) return undefined;
  let best: GaiaNear | null = null;
  for (const s of stars) {
    const at = fin(mjd) ? gaiaAt(s, mjd) : s;
    const sep = sepArcsec(pos, at);
    if (sep <= GAIA_MATCH_ARCSEC && (!best || sep < best.sep)) {
      const pm = s.pmra != null && s.pmdec != null ? Math.hypot(s.pmra, s.pmdec) / 1000 : null;
      best = { sep, g: s.g, pm };
    }
  }
  return best;
}

function gaiaFact(g: GaiaNear | null | undefined): string | null {
  if (g === undefined) return null;
  if (g === null) return `No Gaia DR3 star within ${GAIA_MATCH_ARCSEC}″.`;
  const parts = [g.g != null ? `G ${g.g.toFixed(1)}` : null, g.pm != null && g.pm >= 0.05 ? `moving ${g.pm.toFixed(2)}″/yr` : null]
    .filter(Boolean)
    .join(", ");
  return `Gaia DR3 has a star ${g.sep.toFixed(1)}″ away${parts ? ` (${parts})` : ""}.`;
}

function nextChecks(kind: Kind, c: AnyCandidate, gaia: GaiaNear | null | undefined, flagsComplete: boolean): string[] {
  const look = flagsComplete
    ? "Look at the dot in every frame for ghosts, saturation and the field's edge."
    : "The bad-pixel flags weren't kept for this field: look at the dot in every frame for ghosts, saturation and the field's edge.";
  const second =
    kind === "fast"
      ? "Check known asteroids and comets (Minor Planet Center orbits) at the time it was seen."
      : kind === "cold"
        ? "Compare D3 and D6 from the same exposure: no D3 data is not the same as no D3 light."
        : gaia
          ? "Check that the Gaia star's own motion agrees with this one."
          : gaia === null
            ? "Look for it in CatWISE, the infrared catalogue: Gaia has no star here."
            : "Compare with Gaia and CatWISE positions moved to each visit's time.";
  const third =
    c.p_false == null
      ? "Its false-alarm rate hasn't been measured, so treat it as unproven."
      : "Look for it again in another visit: a low false-alarm rate never confirms a discovery.";
  return [look, second, third];
}

function rawLine(c: AnyCandidate, nSlides: number): string {
  const p = fin(c.p_false) ? `p_false ${c.p_false.toFixed(4)} over ${nSlides} trials` : "p_false null";
  return `${formatRaDec(c)} (${c.ra.toFixed(5)}°, ${c.dec.toFixed(5).replace("-", "−")}°). Status ${c.status}. ${p}.`;
}

/** Ranking within a kind: lowest false-alarm rate first (unmeasured last), then the strongest. */
export function rank(rows: SignalRow[]): SignalRow[] {
  return [...rows].sort((a, b) => {
    const pa = a.p ?? Infinity;
    const pb = b.p ?? Infinity;
    if (pa !== pb) return pa - pb;
    return b.strength - a.strength;
  });
}

/** The analysis as three ranked groups of rows. Cold sources need two visits or more. */
export function signalRows(a: Analysis, ctx: SignalContext = {}): SignalGroups {
  const flagsComplete = a.source?.flags_complete === true;
  const nAll = fin(a.n_slides) ? a.n_slides : 0;
  const centre = ctx.centre ?? null;
  const atCentre = (c: SkyPos) =>
    centre && ctx.name && sepArcsec(c, centre) <= CENTRE_ARCSEC
      ? `Within ${CENTRE_ARCSEC}″ of the field's centre: probably ${ctx.name} itself.`
      : null;
  const status = (c: AnyCandidate) =>
    c.status && c.status !== "unvetted" && STATUS[c.status] ? `Status: ${STATUS[c.status]}.` : null;

  const slow = (a.slow_movers ?? []).map((raw, i): SignalRow => {
    const c = raw as AnyCandidate;
    const n = c.n_slides ?? nAll;
    const gaia = gaiaNear(c, c.mjd_ref, ctx.gaia);
    const pa = fin(c.pmra_arcsec_yr) && fin(c.pmdec_arcsec_yr) ? (Math.atan2(c.pmra_arcsec_yr, c.pmdec_arcsec_yr) * 180) / Math.PI : null;
    return {
      kind: "slow",
      key: `slow-${i}`,
      ra: c.ra,
      dec: c.dec,
      value: fin(c.pm_arcsec_yr) ? `${c.pm_arcsec_yr.toFixed(2)}″/yr` : "Motion not given",
      direction: pa == null ? null : paWord(pa),
      facts: [
        fin(c.n_visits) ? `Measured across ${c.n_visits} visits.` : null,
        atCentre(c),
        gaiaFact(gaia),
        status(c),
      ].filter((s): s is string => !!s),
      alarm: falseAlarm(c.p_false, n),
      p: fin(c.p_false) ? c.p_false : null,
      strength: fin(c.pm_sig) ? c.pm_sig : fin(c.pm_arcsec_yr) ? c.pm_arcsec_yr : 0,
      pmra: fin(c.pmra_arcsec_yr) ? c.pmra_arcsec_yr : null,
      pmdec: fin(c.pmdec_arcsec_yr) ? c.pmdec_arcsec_yr : null,
      mjdRef: fin(c.mjd_ref) ? c.mjd_ref : null,
      gaia,
      next: nextChecks("slow", c, gaia, flagsComplete),
      raw: rawLine(c, n),
    };
  });

  const fast = (a.fast_movers ?? []).map((raw, i): SignalRow => {
    const c = raw as AnyCandidate;
    const n = c.n_slides ?? nAll;
    const t = fin(c.t0) ? c.t0 : null;
    const gaia = gaiaNear(c, t ?? ctx.mjdMid, ctx.gaia);
    const seen = fin(c.n) ? c.n : c.detections?.length;
    return {
      kind: "fast",
      key: `fast-${i}`,
      ra: c.ra,
      dec: c.dec,
      value: fin(c.speed_arcsec_day) ? `${c.speed_arcsec_day.toFixed(2)}″/day` : "Speed not given",
      direction: null,
      facts: [
        seen ? `Linked across ${seen} exposures.` : null,
        atCentre(c),
        gaiaFact(gaia),
        status(c),
      ].filter((s): s is string => !!s),
      alarm: falseAlarm(c.p_false, n),
      p: fin(c.p_false) ? c.p_false : null,
      strength: fin(c.sig_v) ? c.sig_v : 0,
      pmra: null,
      pmdec: null,
      mjdRef: null,
      gaia,
      next: nextChecks("fast", c, gaia, flagsComplete),
      raw: rawLine(c, n),
    };
  });

  const coldAll = (a.cold_candidates ?? []) as AnyCandidate[];
  const coldRepeat = coldAll.filter((c) => (c.n_visits ?? 0) >= 2);
  const cold = coldRepeat.map((c, i): SignalRow => {
    const best = (c.detections ?? []).reduce<{ snr_long?: number } | null>(
      (m, d) => (fin(d.snr_long) && (!m || d.snr_long > (m.snr_long ?? -Infinity)) ? d : m),
      null,
    );
    const gaia = gaiaNear(c, ctx.mjdMid, ctx.gaia);
    return {
      kind: "cold",
      key: `cold-${i}`,
      ra: c.ra,
      dec: c.dec,
      value: `Seen in ${c.n_visits} visits`,
      direction: null,
      facts: [
        // The analysis keeps a detection only when D3, exposed at the same moment, is below S/N 2.
        best && fin(best.snr_long)
          ? `Up to S/N ${best.snr_long.toFixed(0)} in D6 (4.4–5.0 µm), below S/N 2 in D3 (1.6–2.4 µm) in the same exposure.`
          : null,
        atCentre(c),
        gaiaFact(gaia),
        status(c),
      ].filter((s): s is string => !!s),
      alarm: falseAlarm(c.p_false, 0),
      p: fin(c.p_false) ? c.p_false : null,
      strength: best && fin(best.snr_long) ? best.snr_long : 0,
      pmra: null,
      pmdec: null,
      mjdRef: null,
      gaia,
      next: nextChecks("cold", c, gaia, flagsComplete),
      raw: rawLine(c, 0),
    };
  });

  const groups = { slow: rank(slow), fast: rank(fast), cold: rank(cold) };
  return { ...groups, coldSingle: coldAll.length - coldRepeat.length, total: slow.length + fast.length + cold.length };
}

/** Where a signal is at an MJD: slow movers follow their motion, the rest stay put. */
export function signalAt(row: Pick<SignalRow, "ra" | "dec" | "pmra" | "pmdec" | "mjdRef">, mjd: number | null): SkyPos {
  if (mjd == null || row.mjdRef == null || row.pmra == null || row.pmdec == null) return { ra: row.ra, dec: row.dec };
  const dt = (mjd - row.mjdRef) / 365.25;
  return {
    ra: row.ra + (row.pmra * dt) / (3600 * Math.cos((row.dec * Math.PI) / 180)),
    dec: row.dec + (row.pmdec * dt) / 3600,
  };
}

/** "1 slow mover, 2 fast movers and no cold sources". */
export function countSentence(g: Pick<SignalGroups, "slow" | "fast" | "cold">): string {
  const one = (n: number, s: string, p: string) => (n === 0 ? `no ${p}` : `${n} ${n === 1 ? s : p}`);
  return `${one(g.slow.length, "slow mover", "slow movers")}, ${one(g.fast.length, "fast mover", "fast movers")} and ${one(
    g.cold.length,
    "cold source",
    "cold sources",
  )}`;
}

// ---------------------------------------------------------------------------------------------
// Planet X: sensitivity in this field

export interface Sensitivity {
  limits: { um: number; flux_ujy_5sigma: number; ab_mag_5sigma: number; n_frames?: number }[];
  rmin: Record<string, number>[];
  p9: { V: number; best_snr: number | null }[];
  wise0855_twin_reach_pc: number | null;
  note?: string;
}
export interface ScienceDoc {
  source?: { n_frames?: number; coverage_arcmin?: number; flags_complete?: boolean };
  sensitivity?: Sensitivity | null;
}

/** Earth radii of Neptune and Jupiter (equatorial). */
export const NEPTUNE_R_EARTH = 3.88;
export const JUPITER_R_EARTH = 11.2;
/** The albedo the size estimate uses (the analysis also gives 0.5, which needs smaller planets). */
export const ALBEDO = 0.3;
/** Distances shown for the size estimate, AU. */
export const SIZE_AU = [100, 300, 500, 1000];
/** Detection threshold the models are held to. */
export const SNR_NEEDED = 5;

export interface PlanetSummary {
  /** hidden: no reference model reaches S/N 5 here; reachable: some would; unknown: no models. */
  verdict: "hidden" | "reachable" | "unknown";
  answer: string;
  bestSnr: number | null;
  vRange: [number, number] | null;
  sizes: { au: number; r: number; text: string; compare: string }[];
  depths: { um: number; ujy: number; ab: number; n: number | null; text: string }[];
  deepest: { um: number; ujy: number; ab: number } | null;
  twin: string | null;
}

/** Earth radii as text: 0.25, 2.2, 25. */
export function earthRadii(r: number): string {
  return r < 1 ? r.toFixed(2) : r < 10 ? r.toFixed(1) : String(Math.round(r));
}

export function sizeWord(r: number): string {
  if (r < 1) return "smaller than Earth";
  if (r < NEPTUNE_R_EARTH) return "between Earth and Neptune";
  if (r < JUPITER_R_EARTH) return "between Neptune and Jupiter";
  return "bigger than Jupiter";
}

/** µJy as text: 68, 512, 1,034, 49,640. */
export function microJy(x: number): string {
  return x < 100 ? x.toFixed(0) : Math.round(x).toLocaleString("en-US");
}

/**
 * What a distant planet could do in this field, from the sensitivity block of science.json.
 * Reflected sunlight only, albedo ALBEDO, every exposure stacked: optimistic for a real planet,
 * which drifts between visits, so the answer leans towards "it could hide".
 */
export function planetSummary(s: Sensitivity | null | undefined): PlanetSummary | null {
  if (!s) return null;
  const models = (s.p9 ?? []).filter((m) => fin(m.best_snr) && fin(m.V)) as { V: number; best_snr: number }[];
  const vs = models.map((m) => m.V);
  const vRange: [number, number] | null = models.length ? [Math.min(...vs), Math.max(...vs)] : null;
  const bestSnr = models.length ? Math.max(...models.map((m) => m.best_snr)) : null;
  const reach = models.filter((m) => m.best_snr >= SNR_NEEDED);
  const verdict: PlanetSummary["verdict"] = !models.length ? "unknown" : reach.length ? "reachable" : "hidden";
  const range = vRange ? (vRange[0] === vRange[1] ? `V ${vRange[0]}` : `V ${vRange[0]} to ${vRange[1]}`) : "";
  const answer =
    verdict === "unknown"
      ? "Not known here: no reference planet models were tested for this field."
      : verdict === "hidden"
        ? `Yes. A planet like the Planet Nine models (${range}) would reach at most S/N ${bestSnr!.toFixed(1)} here, and SPHEREx needs S/N ${SNR_NEEDED} to see it.`
        : `Only if it is fainter than V ${Math.max(...reach.map((m) => m.V))}. Brighter Planet Nine models would reach S/N ${bestSnr!.toFixed(1)} here, enough to show if one were in this small patch.`;

  const key = `Rmin_earth_p${ALBEDO}`;
  const sizes = SIZE_AU.flatMap((au) => {
    const row = (s.rmin ?? []).find((r) => r.d_au === au);
    const r = row?.[key];
    return fin(r) ? [{ au, r, text: `${earthRadii(r)} R⊕`, compare: sizeWord(r) }] : [];
  });

  const depths = (s.limits ?? [])
    .filter((l) => fin(l.um) && fin(l.flux_ujy_5sigma) && fin(l.ab_mag_5sigma))
    .sort((a, b) => a.um - b.um)
    .map((l) => ({
      um: l.um,
      ujy: l.flux_ujy_5sigma,
      ab: l.ab_mag_5sigma,
      n: fin(l.n_frames) ? l.n_frames : null,
      text: `${microJy(l.flux_ujy_5sigma)} µJy (AB ${l.ab_mag_5sigma.toFixed(1)})`,
    }));
  const deepest = depths.length ? depths.reduce((m, d) => (d.ujy < m.ujy ? d : m)) : null;

  const pc = s.wise0855_twin_reach_pc;
  const twin = fin(pc)
    ? `A twin of WISE 0855, the coldest known brown dwarf, would show at 4.4–5.0 µm out to ${pc < 1 ? pc.toFixed(2) : pc.toFixed(1)} pc (${(pc * 3.26156).toFixed(pc * 3.26156 < 10 ? 1 : 0)} light-years).`
    : null;

  return { verdict, answer, bestSnr, vRange, sizes, depths, deepest: deepest && { um: deepest.um, ujy: deepest.ujy, ab: deepest.ab }, twin };
}
