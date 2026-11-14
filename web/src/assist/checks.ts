// The check assistant's checklist (web/DESIGN.md, "The check assistant"): six questions that walk
// anyone through vetting one candidate. Each is answered by the data where it can be, and handed to
// the person ("ask") where it can't. Pure: no DOM, no storage. Never invents a value: a null field
// becomes a sentence saying what we don't know and how to look.
import { failedGates, gatesPhrase, gradeOf } from "../api/grades";
import type { CandidateKind, Det } from "../api/types";
import { DETECTORS, SAME_MOMENT } from "../app/astro";
import { SPHEREX_V_LIMIT } from "../blink/known";
import { paWord } from "../card/format";
import { ANSWERS, ANSWER_TEXT } from "../vote/model";
import { arcsecText, grouped, raw, trimZeros } from "./format";
import type { AnsweredStep, AssistCandidate, CheckStep, Detection, Reply, StepId, StepStatus } from "./types";
import { COLD_LABELS, cap, flagWords, labelWords, listWords, reasonWords } from "./words";

// ---- thresholds, each from the pipeline or the contracts --------------------------------------

/** Catalogue match radii (pipeline config.py): movers are cut at 6″ from Gaia and CatWISE (suspects);
 *  moving stars and fixed sources use the cross-match radii, 2.5″ for Gaia and 3″ for CatWISE. */
export const MATCH_RADIUS_ARCSEC: Record<CandidateKind, { gaia: number; catwise: number }> = {
  mover: { gaia: 6, catwise: 6 },
  high_pm: { gaia: 2.5, catwise: 3 },
  spectral: { gaia: 2.5, catwise: 3 },
};
/** A known object this close is probably the same object (suspects cut, config KNOWN_ARCSEC). */
export const KNOWN_SAME_ARCSEC = 30;
/** Closer than this is worth a second look (DESIGN.md "Unknown fast movers": no known object within 60″). */
export const KNOWN_NEAR_ARCSEC = 60;
/** Movers: nearest_known_arcsec is null when nothing was predicted within this radius (contracts/api.md). */
export const KNOWN_SEARCH_ARCSEC = 600;
/** Proper motion: the pipeline's fit needs 5σ (HIGHPM_FIT_MIN_SNR); under 3σ it is not a detection. */
export const PM_GOOD_SIGMA = 5;
export const PM_MIN_SIGMA = 3;
/** Faster than Barnard's Star (10.4″/yr, the fastest known star): the pipeline's fit cap (HIGHPM_FIT_MAX_PM_MAS_YR). */
export const FASTEST_STAR_MAS_YR = 10_500;
/** SPHEREx pixel (CLAUDE.md). */
export const PIXEL_ARCSEC = 6.15;
/** Single-dot position floor against Gaia, per axis (CLAUDE.md), used when a dot has no error. */
export const POS_FLOOR_ARCSEC = 0.43;
/** Galactic latitude under which the Milky Way is crowded (DESIGN.md filters). */
export const CROWDED_B_DEG = 10;
/** Brightness: within 3σ in one detector is steady; between visits, beyond ×/÷1.25 is variable (contracts/dots.md). */
export const FLUX_SIGMA = 3;
export const FLUX_RATIO_VARIABLE = 1.25;

const STEP_ORDER: StepId[] = ["motion", "known", "artefact", "same", "colour", "call"];
const SEVERITY: Record<StepStatus, number> = { pass: 0, unknown: 1, ask: 1, caution: 2, fail: 3 };
const worst = (...s: StepStatus[]): StepStatus => s.reduce((a, b) => (SEVERITY[b] > SEVERITY[a] ? b : a), "pass");

const detName = (d: Det | number) => {
  const r = DETECTORS[d as Det];
  return r ? `D${d} (${trimZeros(r.min.toFixed(2))}–${trimZeros(r.max.toFixed(2))} µm)` : `D${d}`;
};
const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);
const noStop = (s: string) => s.replace(/[.\s]+$/, "");
const fmtList = (xs: string[]) => `[${xs.join(", ")}]`;
const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

/** The candidate's own positions with finite values, in time order (optional extra). */
export function detectionsOf(c: AssistCandidate): Detection[] {
  const ds = Array.isArray(c.detections) ? c.detections : [];
  return ds.filter((d) => d && isNum(d.ra) && isNum(d.dec) && isNum(d.mjd)).slice().sort((a, b) => a.mjd - b.mjd);
}

/** Relative link that opens the candidate in the Blink page (src/app/state.ts reads ?ra=&dec=&clock=&det=). */
export function blinkHref(c: Pick<AssistCandidate, "ra" | "dec" | "clock" | "det">): string {
  const p = new URLSearchParams({ ra: c.ra.toFixed(5), dec: c.dec.toFixed(5), clock: c.clock, det: String(c.det) });
  return `?${p}#/explore`;
}

// ---- straight-line fit over detections ----------------------------------------------------------

export interface TrackFit {
  n: number;
  /** ″/h on the sky. */
  rateArcsecH: number;
  /** Position angle of the motion, degrees east of north. */
  paDeg: number;
  /** RMS of the residuals per axis, arcsec. */
  rmsArcsec: number;
  /** Median 1σ position error per axis, arcsec (the floor when the dots carry none). */
  errArcsec: number;
  spanH: number;
}

/** Fit a constant-rate straight line to ≥ 3 positions at ≥ 3 distinct times. Null otherwise. */
export function fitTrack(ds: Detection[]): TrackFit | null {
  const times = new Set(ds.map((d) => Math.round(d.mjd * 86400)));
  if (ds.length < 3 || times.size < 3) return null;
  const ra0 = ds[0].ra;
  const dec0 = ds[0].dec;
  const cosd = Math.cos((dec0 * Math.PI) / 180);
  const t0 = ds.reduce((s, d) => s + d.mjd, 0) / ds.length;
  const pts = ds.map((d) => {
    let dra = d.ra - ra0;
    if (dra > 180) dra -= 360;
    if (dra < -180) dra += 360;
    return { t: (d.mjd - t0) * 24, x: dra * cosd * 3600, y: (d.dec - dec0) * 3600 };
  });
  const line = (k: "x" | "y") => {
    const n = pts.length;
    const mt = pts.reduce((s, p) => s + p.t, 0) / n;
    const mv = pts.reduce((s, p) => s + p[k], 0) / n;
    const stt = pts.reduce((s, p) => s + (p.t - mt) ** 2, 0);
    const slope = stt > 0 ? pts.reduce((s, p) => s + (p.t - mt) * (p[k] - mv), 0) / stt : 0;
    return { slope, at: (t: number) => mv + slope * (t - mt) };
  };
  const lx = line("x");
  const ly = line("y");
  const ss = pts.reduce((s, p) => s + (p.x - lx.at(p.t)) ** 2 + (p.y - ly.at(p.t)) ** 2, 0);
  const errs = ds.map((d) => d.pos_err_arcsec).filter((e): e is number => isNum(e) && e > 0).sort((a, b) => a - b);
  const errArcsec = errs.length ? errs[Math.floor((errs.length - 1) / 2)] : POS_FLOOR_ARCSEC;
  const pa = ((Math.atan2(lx.slope, ly.slope) * 180) / Math.PI + 360) % 360;
  return {
    n: ds.length,
    rateArcsecH: Math.hypot(lx.slope, ly.slope),
    paDeg: pa,
    rmsArcsec: Math.sqrt(ss / (2 * ds.length)),
    errArcsec,
    spanH: pts[pts.length - 1].t - pts[0].t,
  };
}

/** Rate and direction from the first and last positions at different times. */
function twoPointRate(ds: Detection[]): { rate: number; pa: number; dtMin: number; sep: number } | null {
  if (ds.length < 2) return null;
  const a = ds[0];
  const b = ds[ds.length - 1];
  const dtH = (b.mjd - a.mjd) * 24;
  if (!(dtH > 0)) return null;
  const cosd = Math.cos((((a.dec + b.dec) / 2) * Math.PI) / 180);
  let dra = b.ra - a.ra;
  if (dra > 180) dra -= 360;
  if (dra < -180) dra += 360;
  const x = dra * cosd * 3600;
  const y = (b.dec - a.dec) * 3600;
  const sep = Math.hypot(x, y);
  return { rate: sep / dtH, pa: ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360, dtMin: dtH * 60, sep };
}

// ---- the six steps ----------------------------------------------------------------------------

function motionStep(c: AssistCandidate): CheckStep {
  const question = "Does it move in a straight line at a steady rate?";
  const reasons = new Set(c.reasons);
  if (c.kind === "mover") {
    const ds = detectionsOf(c);
    const fit = fitTrack(ds);
    const howTo = [
      "Open it on the Hours clock and play the exposures of one session.",
      "A real asteroid or comet steps the same distance each exposure, along one line.",
      "A dot that shows once, or jumps about, is noise or a cosmic ray.",
    ];
    const prompt = "Blink it on the Hours clock. Does it step along one line by the same amount each exposure?";
    const baseDetails = `rate_arcsec_h=${raw(c.rate_arcsec_h, 2)}; reasons=${fmtList(c.reasons)}; detections=${ds.length}`;
    if (fit) {
      const ratio = fit.rmsArcsec / fit.errArcsec;
      const ok = ratio <= 2;
      const dir = `towards the ${paWord(fit.paDeg)} (position angle ${fit.paDeg.toFixed(0)}°)`;
      const finding = ok
        ? `Its ${fit.n} positions fall on a straight line within ${arcsecText(fit.rmsArcsec)} RMS, moving ${trimZeros(fit.rateArcsecH.toFixed(1))}″ per hour ${dir}.`
        : `Its ${fit.n} positions stray from a straight line by ${arcsecText(fit.rmsArcsec)} RMS, ${ratio.toFixed(1)}× their position error, so the steps aren't steady.`;
      return {
        id: "motion",
        question,
        status: ok ? "pass" : "caution",
        finding,
        details: `${baseDetails}; fit rate=${fit.rateArcsecH.toFixed(2)}″/h, pa=${fit.paDeg.toFixed(1)}°, rms=${fit.rmsArcsec.toFixed(3)}″, err=${fit.errArcsec.toFixed(3)}″, span=${(fit.spanH * 60).toFixed(1)} min`,
        howTo,
      };
    }
    const two = twoPointRate(ds);
    let finding: string;
    if (two) {
      finding = `Two positions ${two.dtMin.toFixed(1)} min apart put it at ${trimZeros(two.rate.toFixed(1))}″ per hour towards the ${paWord(two.pa)}, but two points always make a line: a third is needed to test it.`;
    } else if (isNum(c.rate_arcsec_h)) {
      finding = `The pipeline measured ${trimZeros(c.rate_arcsec_h.toFixed(1))}″ per hour, but not how straight its track is.`;
    } else if (reasons.has("two_detectors")) {
      finding = "It was seen at one moment in two detectors, which shows it's real but not how it moves; its track isn't on record.";
    } else if (reasons.has("two_exposures")) {
      finding = "The pipeline paired it in two exposures of one session at different places; its rate and track aren't on record.";
    } else {
      finding = "Its rate and track aren't on record.";
    }
    return { id: "motion", question, status: "ask", finding, details: baseDetails, howTo, prompt };
  }

  const howTo = [
    "Open it on the Months clock and blink the visits, about six months apart.",
    "Switch to Difference: a moving star leaves blue where its light left and red where it arrived, along its direction.",
    "With three or more visits, the steps should line up and be the same size.",
  ];
  const pm = c.pm_mas_yr;
  const err = c.pm_err_mas_yr;
  const details = `pm_mas_yr=${raw(pm, 1)}, pm_err_mas_yr=${raw(err, 1)}, pa_deg=${raw(c.pa_deg, 1)}, pm/err=${isNum(pm) && isNum(err) && err > 0 ? (pm / err).toFixed(2) : "null"}; reasons=${fmtList(c.reasons)}`;
  if (!isNum(pm)) {
    return {
      id: "motion",
      question,
      status: c.kind === "spectral" ? "unknown" : "ask",
      finding:
        c.kind === "spectral"
          ? "It was picked for its spectrum; the pipeline measured no motion for it."
          : "Its motion isn't on record.",
      details,
      howTo,
      ...(c.kind === "spectral" ? {} : { prompt: "Blink it on the Months clock. Does it step the same way at each visit?" }),
    };
  }
  const sigma = isNum(err) && err > 0 ? pm / err : null;
  const halfYear = pm / 2000; // arcsec in six months
  const share = halfYear / PIXEL_ARCSEC;
  const scale =
    share < 1
      ? `about ${arcsecText(halfYear)} in six months, ${share * 100 < 10 ? (share * 100).toFixed(1) : (share * 100).toFixed(0)}% of a ${PIXEL_ARCSEC}″ pixel`
      : `about ${arcsecText(halfYear)} in six months, ${share < 10 ? share.toFixed(1) : share.toFixed(0)} SPHEREx pixels`;
  const dir = isNum(c.pa_deg) ? ` towards the ${paWord(c.pa_deg)} (position angle ${c.pa_deg.toFixed(0)}°)` : "";
  const size = sigma != null ? `${grouped(pm)} ± ${grouped(err!)} mas/yr (${sigma.toFixed(1)}σ)` : `${grouped(pm)} mas/yr (no error on record)`;
  let status: StepStatus = sigma == null ? "caution" : sigma >= PM_GOOD_SIGMA ? "pass" : sigma >= PM_MIN_SIGMA ? "caution" : "fail";
  const notes: string[] = [];
  if (sigma != null && sigma < PM_MIN_SIGMA) notes.push(`under ${PM_MIN_SIGMA}σ the motion could be noise`);
  if (pm > FASTEST_STAR_MAS_YR && !reasons.has("over_pm_cap")) {
    notes.push(`that is faster than any known star (Barnard's Star moves 10.4″ a year), so two unrelated sources are likelier than one very close object`);
    status = worst(status, "caution");
  }
  for (const r of ["over_pm_cap", "bad_fit", "few_dots", "short_span"]) {
    if (!reasons.has(r)) continue;
    const w = reasonWords(r);
    notes.push(lowerFirst(noStop(w.plain)));
    status = worst(status, w.tone === "bad" ? "fail" : "caution");
  }
  const finding = `Moves ${size}${dir}, ${scale}${notes.length ? `; ${notes.join("; ")}` : ""}.`;
  return { id: "motion", question, status, finding, details, howTo };
}

function knownStep(c: AssistCandidate): CheckStep {
  const question = "Is it already known?";
  const r = MATCH_RADIUS_ARCSEC[c.kind] ?? MATCH_RADIUS_ARCSEC.high_pm;
  const reasons = new Set(c.reasons);
  const parts: string[] = [];
  let status: StepStatus = "pass";
  const cats = [
    { on: c.gaia_match, name: "Gaia DR3", radius: r.gaia, clash: reasons.has("gaia_pm_disagrees") },
    { on: c.catwise_match, name: "CatWISE2020", radius: r.catwise, clash: reasons.has("catwise_pm_disagrees") },
  ];
  const hits = cats.filter((x) => x.on);
  const within = (xs: typeof cats) => listWords(xs.map((x) => `${x.name} (within ${arcsecText(x.radius)})`));

  if (c.kind === "mover") {
    if (hits.length) {
      parts.push(`${within(hits)} list${hits.length === 1 ? "s" : ""} a source at this spot, so the dot may be a star flickering, not a mover`);
      status = worst(status, "caution");
    } else {
      parts.push(`No Gaia or CatWISE source within ${arcsecText(r.gaia)}`);
    }
    const d = c.nearest_known_arcsec;
    if (isNum(d)) {
      const far = d >= 120 ? `${arcsecText(d)} (${(d / 60).toFixed(1)}′)` : arcsecText(d);
      if (d <= KNOWN_SAME_ARCSEC) {
        parts.push(`a known asteroid or comet was predicted ${far} away at that time, so it's probably that object`);
        status = worst(status, "fail");
      } else if (d <= KNOWN_NEAR_ARCSEC) {
        parts.push(`a known asteroid or comet was predicted ${far} away, close enough to be the same object with a poor orbit`);
        status = worst(status, "caution");
      } else {
        parts.push(`the nearest known asteroid or comet was predicted ${far} away`);
      }
    } else {
      parts.push(`no known asteroid or comet was predicted within ${KNOWN_SEARCH_ARCSEC}″ (${KNOWN_SEARCH_ARCSEC / 60}′) in that session`);
    }
  } else {
    // Per catalogue: a match whose motion the pipeline didn't dispute, a motion clash (the
    // counterpart may sit at either end of a pair, so a clash can come without a match here), or none.
    const agree = cats.filter((x) => x.on && !x.clash);
    const clash = cats.filter((x) => x.clash);
    const none = cats.filter((x) => !x.on && !x.clash);
    if (none.length) {
      const words = none.map((x) => `${x.name === "Gaia DR3" ? "Gaia DR3 star" : "CatWISE2020 source"} within ${arcsecText(x.radius)}`);
      parts.push(`No ${words.join(" and no ")}${none.length === cats.length ? ": neither catalogue lists it" : ""}`);
    }
    if (agree.length) {
      parts.push(
        `${within(agree)} list${agree.length === 1 ? "s" : ""} a source here and the pipeline found no clash with its motion, so it's probably a known star: compare the catalogue's motion with ours`,
      );
      status = worst(status, "caution");
    }
    if (clash.length) {
      parts.push(
        `${listWords(clash.map((x) => x.name))} list${clash.length === 1 ? "s" : ""} a source ${clash.every((x) => x.on) ? "here" : "near its positions"} with a different motion; the pipeline reads that as a slower star there, so our motion may be off`,
      );
      status = worst(status, "caution");
    }
  }
  const finding = cap(parts.join("; ")) + ".";
  const howTo = [
    "On the plate, blue rings are Gaia stars moved to the photo's date: a ring on the dot means Gaia knows it.",
    `Violet diamonds are known asteroids (V ${SPHEREX_V_LIMIT} or brighter: what SPHEREx can see) and comets, predicted for that time.`,
    c.kind === "mover"
      ? "Astronomers: run the MPC's MPChecker for the time and position below (SPHEREx is MPC observatory code 288; use the geocentre, 500, where a tool can't take a spacecraft)."
      : "Astronomers: look the position up in Gaia DR3 and CatWISE2020 and compare their proper motions with ours.",
  ];
  const details = `gaia_match=${c.gaia_match}, catwise_match=${c.catwise_match}, nearest_known_arcsec=${raw(c.nearest_known_arcsec, 1)}; radii Gaia ${r.gaia}″, CatWISE ${r.catwise}″; reasons=${fmtList(c.reasons)}`;
  return { id: "known", question, status, finding, details, howTo };
}

function artefactStep(c: AssistCandidate): CheckStep {
  const question = "Could it be an artefact?";
  const words = c.flags.map((f) => ({ name: f, ...flagWords(f) })).filter((w) => w.policy !== "never");
  const mask = words.filter((w) => w.policy === "mask");
  const record = words.filter((w) => w.policy === "record");
  const reasons = new Set(c.reasons);
  const parts: string[] = [];
  let status: StepStatus = "pass";
  if (mask.length) {
    parts.push(`It sits on ${listWords([...new Set(mask.map((w) => w.plain))])}, pixels the pipeline doesn't trust`);
    status = "fail";
  }
  if (record.length) {
    parts.push(`${mask.length ? "it" : "It"} lies near ${listWords([...new Set(record.map((w) => w.plain))])}: look closely before trusting it`);
    status = worst(status, "caution");
  }
  if (!words.length) parts.push("No artefact flags near it");
  const snr = isNum(c.snr) ? ` (S/N up to ${c.snr.toFixed(0)})` : "";
  if (reasons.has("two_detectors")) {
    const other = SAME_MOMENT[c.det];
    parts.push(
      other
        ? `it was seen at the same moment in D${Math.min(c.det, other)} and D${Math.max(c.det, other)}${snr}, so it's real light`
        : `it was seen at the same moment in two detectors${snr}, so it's real light`,
    );
  } else if (reasons.has("two_exposures")) {
    parts.push(`it shows up in two exposures${snr}, which a single cosmic-ray hit can't do`);
  }
  const b = c.galactic_b;
  if (isNum(b)) {
    if (Math.abs(b) < CROWDED_B_DEG) {
      parts.push(`it lies ${Math.abs(b).toFixed(1)}° from the Milky Way's plane, where crowded stars blend`);
      status = worst(status, "caution");
    } else if (!reasons.has("two_detectors") && !reasons.has("two_exposures")) {
      parts.push(`it lies ${Math.abs(b).toFixed(0)}° from the Milky Way's plane, away from the crowded fields`);
    }
  }
  // The pipeline's vetting (grade C): its failed checks settle this step.
  const gates = failedGates(c);
  if (gates.length) {
    parts.push(`the pipeline's vetting failed it: ${gatesPhrase(gates)}`);
    status = "fail";
  }
  const first = parts[0];
  const rest = parts.slice(1);
  const finding = `${first}${rest.length ? (rest.length === 1 ? ", and " : "; ") + rest.join(rest.length === 1 ? "" : "; ") : ""}.`;
  const details = `flags=${fmtList(c.flags)} (mask: ${fmtList(mask.map((w) => w.name))}, record: ${fmtList(record.map((w) => w.name))}); galactic_b=${raw(b, 2)}; snr=${raw(c.snr, 1)}; reasons=${fmtList(c.reasons)}; grade=${gradeOf(c) ?? "null"}`;
  const howTo = [
    "Look at the dot's shape: a real source is a soft round blob about 7.5″ (1.2 pixels) across; a cosmic ray is a sharp single pixel.",
    "Compare the detectors exposed together (D1 with D4, D2 with D5, D3 with D6): real light shows in both, allowing for colour.",
    "Ghosts and crosstalk copy a bright star nearby; persistence is a faint copy where a bright star sat in the photo before.",
  ];
  return { id: "artefact", question, status, finding, details, howTo };
}

function sameStep(c: AssistCandidate): CheckStep {
  const question = "Is it the same object in every frame?";
  const reasons = new Set(c.reasons);
  const howTo = [
    "Stay on one detector: brightness changes with wavelength, so D2 against D5 shows colour, not change.",
    "Blink it: the dot should look equally dark in every frame.",
    "If it fades in and out, it may be two unrelated sources, a variable star or noise.",
  ];
  const ds = detectionsOf(c).filter((d) => isNum(d.flux_ujy) && isNum(d.flux_err_ujy) && d.flux_err_ujy! > 0);
  const byDet = new Map<number, Detection[]>();
  for (const d of ds) byDet.set(d.det, [...(byDet.get(d.det) ?? []), d]);
  const groups = [...byDet.entries()].filter(([, g]) => g.length >= 2).sort((a, b) => b[1].length - a[1].length);
  const baseDetails = `flux_ujy=${raw(c.flux_ujy, 1)}, snr=${raw(c.snr, 1)}, det=${c.det}, flux_ratio=${raw(c.flux_ratio, 3)}; detections with flux=${ds.length}; reasons=${fmtList(c.reasons)}`;

  if (groups.length) {
    const [det, g] = groups[0];
    const w = g.map((d) => 1 / d.flux_err_ujy! ** 2);
    const mean = g.reduce((s, d, i) => s + d.flux_ujy! * w[i], 0) / w.reduce((s, x) => s + x, 0);
    const dev = Math.max(...g.map((d) => Math.abs(d.flux_ujy! - mean) / d.flux_err_ujy!));
    const ok = dev <= FLUX_SIGMA;
    const unit = c.kind === "mover" ? "exposures" : "frames";
    return {
      id: "same",
      question,
      status: ok ? "pass" : "caution",
      finding: ok
        ? `Across ${g.length} ${unit} in ${detName(det)} its brightness stays within ${dev.toFixed(1)}σ of ${grouped(mean)} µJy.`
        : `In ${detName(det)} its brightness strays up to ${dev.toFixed(1)}σ from ${grouped(mean)} µJy, so these may be different sources.`,
      details: `${baseDetails}; D${det}: n=${g.length}, weighted mean=${mean.toFixed(1)} µJy, max |Δ|/σ=${dev.toFixed(2)}`,
      howTo,
    };
  }
  if (isNum(c.flux_ratio) && c.flux_ratio > 0) {
    const f = c.flux_ratio >= 1 ? c.flux_ratio : 1 / c.flux_ratio;
    const ok = f <= FLUX_RATIO_VARIABLE;
    return {
      id: "same",
      question,
      status: ok ? "pass" : "caution",
      finding: ok
        ? `Its brightness matches within ×${f.toFixed(2)} between the two visits in the same detector.`
        : `Its brightness changed ×${f.toFixed(2)} between the two visits in the same detector: it may be variable, or two different sources.`,
      details: baseDetails,
      howTo,
    };
  }
  if (reasons.has("at_control_level")) {
    return {
      id: "same",
      question,
      status: "caution",
      finding: `${noStop(reasonWords("at_control_level").plain)}, so the two dots may be different sources.`,
      details: baseDetails,
      howTo,
      prompt: "Blink it on the Months clock. Does one dot move, equally dark at each visit, rather than one fading as another appears?",
    };
  }
  if (c.kind === "mover" && reasons.has("two_detectors") && !reasons.has("two_exposures")) {
    return {
      id: "same",
      question,
      status: "unknown",
      finding: `It was caught in two detectors at one moment, and brightness can only be compared within one detector${isNum(c.flux_ujy) ? `; its median brightness is ${grouped(c.flux_ujy)} µJy` : ""}.`,
      details: baseDetails,
      howTo,
    };
  }
  const brightness = isNum(c.flux_ujy) ? `Its median brightness is ${grouped(c.flux_ujy)} µJy in ${detName(c.det)}, but` : "";
  const missing = c.kind === "mover" ? "each dot's brightness isn't on record" : "its brightness at each visit isn't on record";
  return {
    id: "same",
    question,
    status: "ask",
    finding: brightness ? `${brightness} ${missing}.` : `${cap(missing)}.`,
    details: baseDetails,
    howTo,
    prompt:
      c.kind === "mover"
        ? `Blink it on D${c.det} only. Does the dot look equally dark in each exposure?`
        : `Blink it on the Months clock in D${c.det}. Is the moving dot equally dark at each visit?`,
  };
}

function colourStep(c: AssistCandidate): CheckStep {
  const question = "What does its colour say?";
  const labels = c.labels ?? [];
  const howTo = [
    "Open it in Blink and read the spectrum under the plate (sources that stay put have one).",
    "Cold brown dwarfs rise steeply towards 4–5 µm, with a methane dip near 3.3 µm.",
    "Asteroids reflect sunlight at 1–2.5 µm and can glow brighter at 3–5 µm from their own warmth.",
  ];
  const details = `labels=${fmtList(labels)}, red_ratio=${raw(c.red_ratio, 3)}, det=${detName(c.det)}`;
  if (labels.length) {
    const ws = labels.map(labelWords);
    const cold = labels.some((l) => COLD_LABELS.has(l));
    const fits = c.kind === "high_pm" && cold ? ". With its motion, that fits a nearby brown dwarf" : "";
    return {
      id: "colour",
      question,
      status: "pass",
      finding: `The pipeline marked it ${listWords(ws.map((w) => w.short.toLowerCase()))}: ${listWords(ws.map((w) => w.plain))}${fits}.`,
      details,
      howTo,
    };
  }
  if (isNum(c.red_ratio) && c.red_ratio > 0) {
    const red = c.red_ratio >= 1;
    return {
      id: "colour",
      question,
      status: red ? "pass" : "unknown",
      finding: red
        ? `Its 4.4–5.0 µm brightness is ${c.red_ratio.toFixed(1)}× its 1.1–1.6 µm brightness (D6/D2): most stars give well under 1, cold brown dwarfs more.`
        : `Its 4.4–5.0 µm brightness is ${c.red_ratio.toFixed(2)}× its 1.1–1.6 µm brightness (D6/D2), like most stars: no sign of a cold brown dwarf.`,
      details,
      howTo,
    };
  }
  return {
    id: "colour",
    question,
    status: "unknown",
    finding:
      c.kind === "mover"
        ? `No colour on record: spectra are built for sources that stay put, and its best detection is in ${detName(c.det)}.`
        : `The pipeline gave it no colour labels (red, methane, water); its best detection is in ${detName(c.det)}.`,
    details,
    howTo,
  };
}

function callStep(c: AssistCandidate): CheckStep {
  if (gradeOf(c) === "C")
    return {
      id: "call",
      question: "Your call: what is it?",
      status: "fail",
      finding: "Not put to a vote: the pipeline graded it C because it fails a check. Votes go to grade A and B objects; you can still blink it.",
      details: `candidate_id=${c.id}; grade=C; reasons=${fmtList(c.reasons)}`,
      howTo: ANSWERS.map((a) => `${ANSWER_TEXT[a].label}: ${lowerFirst(ANSWER_TEXT[a].meaning)}`),
    };
  return {
    id: "call",
    question: "Your call: what is it?",
    status: "ask",
    finding: "Vote mover, changing, junk or not sure: five votes with 70% on one answer make a consensus.",
    details: `candidate_id=${c.id}; consensus = at least 5 votes and at least 70% on one answer (src/vote/model.ts)`,
    howTo: ANSWERS.map((a) => `${ANSWER_TEXT[a].label}: ${lowerFirst(ANSWER_TEXT[a].meaning)}`),
  };
}

/** The ordered checklist for one candidate (web/DESIGN.md, "The check assistant"). */
export function checklist(c: AssistCandidate): CheckStep[] {
  const steps = [motionStep(c), knownStep(c), artefactStep(c), sameStep(c), colourStep(c), callStep(c)];
  return STEP_ORDER.map((id) => steps.find((s) => s.id === id)!);
}

/** What a reply does to a step whose data can't settle it: yes → pass, no → fail, can't tell → unknown. */
export const REPLY_STATUS: Record<Reply, StepStatus> = { yes: "pass", no: "fail", unsure: "unknown" };

/** Steps the person can answer with yes / no / can't tell (not the vote: that has its own control). */
export function canReply(s: CheckStep): boolean {
  return s.id !== "call" && !!s.prompt;
}

/** Apply the person's replies. Only steps with a prompt change; the data's own status is kept. */
export function withReplies(steps: CheckStep[], replies: Partial<Record<StepId, Reply>>): AnsweredStep[] {
  return steps.map((s) => {
    const reply = canReply(s) ? replies[s.id] ?? null : null;
    const status = reply ? (s.status === "ask" ? REPLY_STATUS[reply] : worst(s.status, REPLY_STATUS[reply] === "fail" ? "fail" : s.status)) : s.status;
    return { ...s, dataStatus: s.status, reply, status };
  });
}

/** One sentence: "2 good signs, 1 caution and 3 for you to check." */
export function summarize(steps: Pick<CheckStep, "status">[]): string {
  const n = (st: StepStatus) => steps.filter((s) => s.status === st).length;
  const parts: string[] = [];
  const add = (k: number, one: string, many: string) => k && parts.push(`${k} ${k === 1 ? one : many}`);
  add(n("pass"), "good sign", "good signs");
  add(n("caution"), "caution", "cautions");
  add(n("fail"), "bad sign", "bad signs");
  add(n("ask"), "for you to check", "for you to check");
  add(n("unknown"), "unknown", "unknown");
  return parts.length ? `${cap(listWords(parts))}.` : "";
}
