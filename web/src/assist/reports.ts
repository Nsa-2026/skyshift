// "What next" drafts: an MPC observation report in ADES PSV, a Backyard Worlds forum post, a
// citation and a permalink. Pure text builders. Nothing here sends anything anywhere: every draft
// is for a person to check, copy and submit themselves.
//
// ADES PSV follows the IAU ADES description (github.com/IAU-ADES/ADES-Master, ADES_Description.pdf)
// and the MPC's field lists (minorplanetcenter.net/iau/info/ADESFieldValues.html), read 14 Nov 2026:
// - SPHEREx is MPC observatory code 288 (ObsCodes list: "288 SPHEREx", a space-based code without
//   coordinates), so every line needs the Location Group: sys ICRF_KM, ctr 399 (geocentre) and
//   pos1–pos3 = SPHEREx's geocentric equatorial position in km (IMAGE header X_SC, Y_SC, Z_SC).
// - An unlinked object goes in trkSub (up to 8 alphanumeric characters, unique in the batch).
// - notes "Z": astrometry from a survey reported by a non-survey measurer (archival astrometry).
// - mode "CMO": SPHEREx's HgCdTe arrays have CMOS readouts; the MPC's list has no infrared mode.
// - No mag/band: the MPC's band list has no SPHEREx band, so brightness goes in remarks.
import type { CandidateKind } from "../api/types";
import { DETECTORS } from "../app/astro";
import { ANSWERS, ANSWER_TEXT, consensus, total, type Counts } from "../vote/model";
import { checklist, detectionsOf, withReplies } from "./checks";
import { degDec, degRa, formatDec, formatRa, grouped, isoDay, mjdDate, mjdToIso, trimZeros } from "./format";
import type { AssistCandidate, Reply, StepId } from "./types";
import { KIND_TEXT, REPLY_TEXT, reasonWords } from "./words";

export const SITE_URL = "https://skyshift.web.app/";
export const REPO_URL = "https://github.com/cucuradu/skyshift";
/** The MPC's ADES page: format, validation and the PSV submission form. */
export const MPC_ADES_URL = "https://www.minorplanetcenter.net/iau/info/ADES.html";
/** Backyard Worlds: Planet 9, Talk, Science board (Zooniverse board 694, active Nov 2026). */
export const BYW_FORUM_URL = "https://www.zooniverse.org/projects/marckuchner/backyard-worlds-planet-9/talk/694";
/** MPC observatory code for SPHEREx (minorplanetcenter.net/iau/lists/ObsCodes.html). */
export const SPHEREX_MPC_CODE = "288";
export const DRAFT_LINE = "DRAFT - NOT SUBMITTED. Check every field before sending.";
export const DEFAULT_RELEASE = "DR1+QR3";

/** SPHEREx data sets on IRSA (README.md, "Acknowledgements"; titles and years from DataCite). */
export const DATA_DOIS = [
  { key: "SPHEREx_DR1_images", title: "SPHEREx Data Release 1 (DR1) Spectral Images", doi: "10.26131/IRSA678" },
  { key: "SPHEREx_QR3_images", title: "SPHEREx Quick Release Spectral Images - QR3", doi: "10.26131/IRSA662" },
  { key: "SPHEREx_DR1_cube", title: "SPHEREx Data Release 1 (DR1) Spectral All-Sky Cube", doi: "10.26131/IRSA679" },
] as const;

/** Acknowledgements the SPHEREx, IRSA and Gaia teams ask for (README.md). */
export const ACKNOWLEDGEMENTS = [
  "This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration.",
  "This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by the National Aeronautics and Space Administration and operated by the California Institute of Technology.",
  "This work has made use of data from the European Space Agency (ESA) mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis Consortium (DPAC, https://www.cosmos.esa.int/web/gaia/dpac/consortium). Funding for the DPAC has been provided by national institutions, in particular the institutions participating in the Gaia Multilateral Agreement.",
];

export interface ReportOptions {
  /** Site address the permalink starts with. Default: this page's origin and path, else SITE_URL. */
  base?: string;
  /** Collections searched, e.g. "DR1+QR3". */
  dataRelease?: string;
  /** Votes on this candidate so far, or null when not loaded. */
  votes?: Counts | null;
  /** The person's replies to the checklist (stored per candidate in this browser). */
  replies?: Partial<Record<StepId, Reply>>;
  /** Today, for "accessed" dates (tests pass a fixed date). */
  today?: Date;
}

/** This page's address without query or hash, or the public site. */
export function siteBase(): string {
  try {
    if (typeof location !== "undefined" && /^https?:$/.test(location.protocol)) return `${location.origin}${location.pathname}`;
  } catch {
    /* no location (workers, tests) */
  }
  return SITE_URL;
}

/** Shareable link to the candidate page, #/c/<id>. */
export function permalink(c: Pick<AssistCandidate, "id">, base: string = siteBase()): string {
  return `${base.replace(/#.*$/, "")}#/c/${encodeURIComponent(c.id)}`;
}

/** An ADES trkSub: "S" + 7 base-36 characters of a stable hash of the id (≤ 8 alphanumerics). */
export function trackletId(id: string): string {
  let h = 0x811c9dc5; // FNV-1a, 32 bit
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `S${h.toString(36).toUpperCase().padStart(7, "0").slice(-7)}`;
}

const bandText = (det: number) => {
  const r = DETECTORS[det as 1];
  return r ? `D${det} ${trimZeros(r.min.toFixed(2))}-${trimZeros(r.max.toFixed(2))} um` : `D${det}`;
};
const psvSafe = (s: string) => s.replace(/\|/g, "/").replace(/[\r\n]+/g, " ");

/** Pad the columns of a PSV block so the pipes line up (ADES allows blank padding). */
function psvTable(head: string[], rows: string[][]): string[] {
  const w = head.map((h, i) => Math.max(h.length, ...rows.map((r) => (r[i] ?? "").length)));
  const line = (cells: string[]) => cells.map((v, i) => (i === cells.length - 1 ? v : v.padEnd(w[i]))).join("|");
  return [line(head), ...rows.map(line)];
}

export interface MpcDraft {
  /** The whole PSV text, header to last observation. */
  text: string;
  /** Observation lines in it. */
  lines: number;
  /** True when the only line is the candidate's mean position (no per-exposure positions on record). */
  fromMean: boolean;
  /** What a person must fill in or confirm before sending, in plain words. */
  missing: string[];
}

export const MPC_COLUMNS = ["trkSub", "mode", "stn", "obsTime", "ra", "dec", "rmsRA", "rmsDec", "astCat", "sys", "ctr", "pos1", "pos2", "pos3", "notes", "remarks"] as const;

/**
 * A draft MPC observation report in ADES PSV for a mover: one line per position on record (each
 * dot when the candidate carries `detections`, else its mean position). Marked as a draft that was
 * not submitted. Fields SkyShift can't know (the submitter, SPHEREx's position when the dots don't
 * carry it) are left empty and listed in `missing`.
 */
export function mpcDraft(c: AssistCandidate, opts: ReportOptions = {}): MpcDraft {
  const release = opts.dataRelease ?? DEFAULT_RELEASE;
  const trk = trackletId(c.id);
  const ds = detectionsOf(c);
  const fromMean = ds.length === 0;
  const nDots = typeof c.n_dots === "number" ? c.n_dots : null;
  const obs = fromMean
    ? [{ ra: c.ra, dec: c.dec, mjd: c.mjd, det: c.det, pos_err_arcsec: null, flux_ujy: c.flux_ujy, snr: c.snr, sc_pos_km: null }]
    : ds;
  const rows = obs.map((o) => {
    const err = typeof o.pos_err_arcsec === "number" && o.pos_err_arcsec > 0 ? o.pos_err_arcsec.toFixed(3).slice(0, 7) : "";
    const sc = Array.isArray(o.sc_pos_km) && o.sc_pos_km.length === 3 && o.sc_pos_km.every(Number.isFinite) ? o.sc_pos_km : null;
    const remark = [
      bandText(o.det),
      typeof o.flux_ujy === "number" ? `${Math.round(o.flux_ujy)} uJy` : null,
      typeof o.snr === "number" ? `S/N ${o.snr.toFixed(0)}` : null,
      fromMean ? (nDots ? `mean of ${nDots} dots` : "mean of the pipeline's dots") : null,
    ]
      .filter(Boolean)
      .join(", ");
    return [
      trk,
      "CMO",
      SPHEREX_MPC_CODE,
      mjdToIso(o.mjd, 3),
      degRa(o.ra, 6),
      degDec(o.dec, 6),
      err,
      err,
      "Gaia3",
      "ICRF_KM",
      "399",
      sc ? sc[0].toFixed(3) : "",
      sc ? sc[1].toFixed(3) : "",
      sc ? sc[2].toFixed(3) : "",
      "Z",
      psvSafe(remark),
    ];
  });
  const missingPos = rows.some((r) => !r[11]);
  const missing: string[] = ["Your name as submitter and measurer, initials then surname (lines ! name)."];
  if (fromMean)
    missing.push(
      "Each exposure's own position and time: this draft has only the mean of the pipeline's dots. The MPC wants two or more positions from one night, and prefers three.",
    );
  if (missingPos) missing.push("SPHEREx's position for every line: pos1, pos2, pos3 = X_SC, Y_SC, Z_SC in km from that photo's IMAGE header.");
  if (rows.some((r) => !r[6])) missing.push("Position errors (rmsRA, rmsDec in arcsec) if you have them; they are optional.");
  missing.push("That the reference catalogue (astCat Gaia3) and mode (CMO) suit SPHEREx's calibration and HgCdTe arrays.");
  missing.push("That no known object explains it: run the MPC's MPChecker for each time and position.");

  const head = [
    "# version=2017",
    "# observatory",
    `! mpcCode ${SPHEREX_MPC_CODE}`,
    "! name SPHEREx",
    "# submitter",
    "! name [Your name: initials then surname]",
    "# measurers",
    "! name [Measurer: initials then surname]",
    "# telescope",
    "! name SPHEREx",
    "! design Three-mirror anastigmat",
    "! aperture 0.2",
    "! detector HgCdTe",
    "! pixelScale 6.15",
    "# comment",
    `! line ${DRAFT_LINE}`,
    `! line Measured with SkyShift from NASA SPHEREx Level 2 spectral images (${psvSafe(release)}, NASA/IPAC IRSA).`,
    `! line SkyShift candidate ${psvSafe(c.id)}.`,
  ];
  if (fromMean) head.push("! line This line is a mean position. Replace it with one line per exposure before sending.");
  if (missingPos) head.push("! line pos1-pos3 are empty: fill them from each photo's IMAGE header (X_SC, Y_SC, Z_SC, km).");
  const text = [...head, ...psvTable([...MPC_COLUMNS], rows)].join("\n") + "\n";
  return { text, lines: rows.length, fromMean, missing };
}

/** "3 mover, 1 junk (4 votes; no consensus yet)" or null without votes. */
export function votesSentence(votes: Counts | null | undefined): string | null {
  if (!votes) return null;
  const n = total(votes);
  if (!n) return "No votes yet.";
  const parts = ANSWERS.filter((a) => votes[a]).map((a) => `${votes[a]} ${ANSWER_TEXT[a].label.toLowerCase()}`);
  const con = consensus(votes);
  const verdict = con
    ? `consensus: ${ANSWER_TEXT[con.answer].label.toLowerCase()}, ${(con.share * 100).toFixed(0)}%`
    : "no consensus yet";
  return `${parts.join(", ")} (${n} vote${n === 1 ? "" : "s"}; ${verdict}).`;
}

const TITLE: Record<CandidateKind, string> = {
  mover: "Possible new asteroid or comet",
  high_pm: "Possible moving star",
  spectral: "Odd spectrum",
  shape: "One object or two?",
};

/**
 * A forum post draft for the Backyard Worlds: Planet 9 science board: position (sexagesimal and
 * decimal), motion, what the checklist found, the person's own checks, votes and the permalink.
 * Placeholders in [brackets] are for the person to fill in.
 */
export function forumDraft(c: AssistCandidate, opts: ReportOptions = {}): string {
  const release = opts.dataRelease ?? DEFAULT_RELEASE;
  const steps = withReplies(checklist(c), opts.replies ?? {});
  const step = (id: StepId) => steps.find((s) => s.id === id)!;
  const det = DETECTORS[c.det];
  const lines: string[] = [];
  lines.push(`${TITLE[c.kind]} in NASA SPHEREx data: SkyShift candidate ${c.id}`, "");
  lines.push(`Position (ICRS) on ${mjdDate(c.mjd, true)} (MJD ${c.mjd.toFixed(5)}):`);
  lines.push(`RA ${formatRa(c.ra, 2)}, Dec ${formatDec(c.dec, 1)}`);
  lines.push(`RA ${degRa(c.ra, 6)}°, Dec ${degDec(c.dec, 6)}°`);
  if (typeof c.galactic_b === "number") lines.push(`Galactic latitude ${c.galactic_b >= 0 ? "+" : ""}${c.galactic_b.toFixed(1)}°`);
  lines.push("");
  if (typeof c.pm_mas_yr === "number") {
    const err = typeof c.pm_err_mas_yr === "number" ? ` ± ${grouped(c.pm_err_mas_yr)}` : "";
    const sig = typeof c.pm_err_mas_yr === "number" && c.pm_err_mas_yr > 0 ? ` (${(c.pm_mas_yr / c.pm_err_mas_yr).toFixed(1)}σ)` : "";
    const pa = typeof c.pa_deg === "number" ? `, position angle ${c.pa_deg.toFixed(0)}° east of north` : "";
    lines.push(`Motion measured by SkyShift between SPHEREx visits: ${grouped(c.pm_mas_yr)}${err} mas/yr${sig}${pa}.`);
  } else if (typeof c.rate_arcsec_h === "number") {
    lines.push(`Motion measured by SkyShift within one SPHEREx session: ${trimZeros(c.rate_arcsec_h.toFixed(1))}″ per hour.`);
  } else {
    lines.push(`Motion: ${step("motion").finding}`);
  }
  lines.push(`Best seen in SPHEREx detector D${c.det}${det ? ` (${trimZeros(det.min.toFixed(2))}–${trimZeros(det.max.toFixed(2))} µm)` : ""}${typeof c.flux_ujy === "number" ? `, ${grouped(c.flux_ujy)} µJy` : ""}${typeof c.snr === "number" ? `, S/N ${c.snr.toFixed(0)}` : ""}.`);
  lines.push(`Catalogues: ${step("known").finding}`);
  lines.push(`Artefacts: ${step("artefact").finding}`);
  lines.push(`Colour: ${step("colour").finding}`);
  if (c.reasons.length) lines.push(`Pipeline notes: ${c.reasons.map((r) => reasonWords(r).plain).join(" ")}`);
  const v = votesSentence(opts.votes);
  if (v) lines.push(`Votes on SkyShift: ${v}`);
  lines.push("");
  const answered = steps.filter((s) => s.reply);
  lines.push("What I checked in the blink:");
  if (answered.length) for (const s of answered) lines.push(`- ${s.prompt} ${REPLY_TEXT[s.reply!]}.`);
  else lines.push("- [Say what you saw when you blinked it: does it step along a line? Is it equally dark in each frame?]");
  lines.push("", `Blink it yourself: ${permalink(c, opts.base)}`, "");
  lines.push(
    `Data: NASA SPHEREx ${release} spectral images from NASA/IPAC IRSA. Found with SkyShift (NASA Space Apps Challenge 2026). This is a candidate to check, not a confirmed discovery.`,
  );
  return lines.join("\n") + "\n";
}

export interface Citation {
  plain: string;
  bibtex: string;
}

const bibEscape = (s: string) => s.replace(/([&%$#_{}])/g, "\\$1");

/**
 * How to cite SkyShift and the SPHEREx data (DOIs from README.md), with the acknowledgements, and
 * the candidate's permalink when one is given.
 */
export function citation(c: Pick<AssistCandidate, "id" | "kind"> | null = null, opts: ReportOptions = {}): Citation {
  const accessed = isoDay(opts.today ?? new Date());
  const site = (opts.base ?? siteBase()).replace(/#.*$/, "");
  const link = c ? permalink(c, opts.base) : null;
  const plain = [
    `SkyShift team (2026). SkyShift: blink NASA's SPHEREx sky and check what moved. NASA Space Apps Challenge 2026. ${site} (accessed ${accessed}). Code: ${REPO_URL}`,
    ...(c && link ? [`Candidate ${c.id} (${KIND_TEXT[c.kind]?.short.toLowerCase() ?? c.kind}): ${link}`] : []),
    "",
    ...DATA_DOIS.map((d) => `SPHEREx Team (2026). ${d.title}. IPAC. https://doi.org/${d.doi}`),
    "",
    ...ACKNOWLEDGEMENTS,
  ].join("\n");
  const note = c && link ? `Candidate ${bibEscape(c.id)}: \\url{${link}}. ` : "";
  const bib = [
    "@misc{SkyShift2026,",
    "  author       = {{SkyShift team}},",
    "  title        = {{SkyShift}: blink {NASA}'s {SPHEREx} sky and check what moved},",
    "  year         = {2026},",
    `  howpublished = {\\url{${site}}},`,
    `  note         = {NASA Space Apps Challenge 2026. ${note}Code: \\url{${REPO_URL}}. Accessed ${accessed}}`,
    "}",
    ...DATA_DOIS.flatMap((d) => [
      "",
      `@misc{${d.key},`,
      "  author    = {{SPHEREx Team}},",
      `  title     = {{${d.title}}},`,
      "  publisher = {IPAC},",
      "  year      = {2026},",
      `  doi       = {${d.doi}},`,
      `  url       = {https://doi.org/${d.doi}}`,
      "}",
    ]),
  ].join("\n");
  return { plain: plain + "\n", bibtex: bib + "\n" };
}

