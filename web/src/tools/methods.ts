// "Data and methods" on the Tools page: where the pixels come from and what happens to them before
// a number reaches the site, each step with the check that tests it. Every fact is from CLAUDE.md
// ("Domain facts"), contracts/ and the notes in /data/validation.json; nothing here is measured
// anew. The checks themselves (targets, numbers, rows) stay on Results ("How we know it works").
// After PR #214's Research → Data & methods (春。 and Codex), cut to what Results and About lack.
import type { ValidationCheck, ValidationResponse } from "../api/types";
import { DETECTORS } from "../app/astro";
import { countStatuses, normStatus, summarySentence, withAllChecks } from "../score/checks";

export interface MethodRow {
  term: string;
  text: string;
  /** Checks on Results that test this step. */
  checks?: string[];
}

export interface MethodGroup {
  title: string;
  rows: MethodRow[];
}

const bands = (Object.entries(DETECTORS) as [string, { min: number; max: number }][])
  .map(([d, b], i, all) => `${i === all.length - 1 ? "and " : ""}D${d} ${b.min}–${b.max.toFixed(1)}`)
  .join(", ");

/** Masked for detection and photometry (contracts/flags.md). */
export const MASKED_FLAGS = [
  "TRANSIENT",
  "OVERFLOW",
  "SUR_ERROR",
  "NONFUNC",
  "DICHROIC",
  "MISSING_DATA",
  "HOT",
  "COLD",
  "PHANMISS",
  "NONLINEAR",
  "PERSIST",
  "STREAK",
  "SNOWBALL",
] as const;

/** Recorded on the dot as a caution, never masked. */
export const RECORDED_FLAGS = ["CROSSTALK", "GHOST", "GHOST_FPA", "GHOST_EXT", "BLOOM", "HALO", "OUTLIER"] as const;

const list = (xs: readonly string[]) => `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

export const METHODS: MethodGroup[] = [
  {
    title: "The data",
    rows: [
      {
        term: "SPHEREx images",
        text: "Level 2 spectral images from NASA's SPHEREx, releases DR1 and QR3 (IRSA collections spherex_dr1 and spherex_qr3; the deep-field collections are left out). Found with IRSA's image search, read from IRSA's public copy on Amazon S3 (s3://nasa-irsa-spherex) or cut out on IRSA's servers.",
      },
      {
        term: "One photo",
        text: "One 113 s exposure on one detector: 2040 × 2040 pixels of 6.15″, in MJy/sr. The same file holds the flags, the variance, NASA's zodiacal-light model, an effective PSF (FWHM 7.53″) and each pixel's wavelength.",
      },
      {
        term: "Detectors",
        text: `Six, each a band of wavelengths: ${bands} µm, 102 channels in all. D1 and D4, D2 and D5, D3 and D6 are exposed at the same moment, so a dot that differs between them shows colour, not motion. The detector comes from the archive's metadata, never from the file name.`,
      },
      {
        term: "Time",
        text: "Each exposure's midpoint: MJD-AVG from its header, in UTC. Search results, which have no header, use the middle of t_min and t_max. A file's creation date is never used.",
      },
      {
        term: "Stars",
        text: "Gaia DR3 positions, proper motions and parallaxes, moved from epoch 2016.0 to the date of each frame (a visit's mid-time on the Months clock). WISE 0855−0714 from published astrometry instead.",
        checks: ["V1", "V2"],
      },
      {
        term: "Asteroids and comets",
        text: "Minor Planet Center orbits, propagated with kete to each photo's MJD-AVG as seen from SPHEREx. The targeted asteroid check uses IRSA's Moving Object Search Tool instead; comet 3I/ATLAS comes from IRSA's own list.",
        checks: ["V4", "V5"],
      },
      {
        term: "Other catalogues",
        text: "2MASS and CatWISE2020, served by IRSA: catalogue brightness to check our fluxes against, and a CatWISE match for every object to check. SDSS DR17 spectroscopic redshifts for galaxy distances.",
        checks: ["V8", "V10"],
      },
    ],
  },
  {
    title: "Visits and the two clocks",
    rows: [
      {
        term: "Visit",
        text: "Sort the exposure times at a position; a gap of more than 60 days starts a new visit. A visit longer than 75 days (near the ecliptic poles) is cut into equal parts of at most 30 days. Mid-latitude seasons of 45–55 days stay one visit.",
      },
      {
        term: "Session",
        text: "A run of exposures less than 10 minutes apart: 1–5 exposures, 2.1 minutes apart. A visit holds about 21 sessions and 55–60 exposures.",
      },
      {
        term: "Months clock",
        text: "One median stack per visit and detector, so asteroids and cosmic rays drop out. The featured targets' files stack every exposure; the live API stacks at most 6 per visit to answer within 60 s.",
      },
      {
        term: "Hours clock",
        text: "Single exposures of one visit, in time order. The live API serves at most 6; the featured targets' files hold them all.",
      },
    ],
  },
  {
    title: "From pixels to dots",
    rows: [
      {
        term: "Masked pixels",
        text: `${MASKED_FLAGS.length} flags keep a pixel out of detection and photometry: ${list(MASKED_FLAGS)}. ${list(RECORDED_FLAGS)} are recorded on the dot as cautions instead. SOURCE, set on most pixels, is never masked.`,
      },
      {
        term: "Detection",
        text: "Dots are found with sep (Source Extractor's algorithms in Python) on the masked image, after subtracting a smooth local background; positions are windowed centroids. The zodiacal-light model in the file is not subtracted: the local background is.",
        checks: ["V1"],
      },
      {
        term: "Brightness",
        text: "An aperture of radius 2 pixels holds 96.8% of a star's light, so its sum is multiplied by 1.033. 1 MJy/sr is 889 µJy in one 6.15″ pixel. Brightness is compared only within one detector.",
        checks: ["V8"],
      },
      {
        term: "Saturation",
        text: "OVERFLOW or NONLINEAR in a source's core marks it saturated. Stars saturate from about J_AB 10.5 in D1, J_AB 10.8 in D2, K_AB 11.0 in D3, W1_AB 11.1 in D4, W2_AB 9.6 in D5 and W2_AB 9.0 in D6: at those magnitudes half of the pilot's stars had OVERFLOW or NONLINEAR at their peak. Saturated targets are centred on their wings, and the core is marked.",
      },
      {
        term: "Position errors",
        text: "A dot's own error estimate is 1.4–1.8× too small against Gaia (a single dot is good to about 0.43″ at best), so the pipeline corrects it against Gaia before fitting motions.",
        checks: ["V9"],
      },
    ],
  },
  {
    title: "Measuring what moved",
    rows: [
      {
        term: "Between visits",
        text: "The target's position in the last visit's stack minus the first, measured against reference stars in the same cutouts. Its error is how much quiet Gaia stars (G 13–17, under 20 mas/yr) in the same field seem to shift. Gaia's prediction for the same dates includes parallax.",
        checks: ["V2", "V3"],
      },
      {
        term: "Across a zone",
        text: "A proper motion for every star, fitted over all its dots with SPHEREx's own position as the observer, and compared with Gaia's.",
        checks: ["V9"],
      },
      {
        term: "Within a visit",
        text: "A mover is a dot that changes place between exposures of one session. Movers on a known asteroid or comet's predicted path are set aside, and so are flagged artefacts and stars Gaia and CatWISE know. What's left goes on the list of objects to check. Most turn out to be detector artefacts, which is why people blink each one and vote.",
        checks: ["V5"],
      },
      {
        term: "Spectra",
        text: "The object measured in every exposure that covers it, 0.75–5.0 µm, each point marked when its core is saturated.",
        checks: ["V8"],
      },
    ],
  },
];

const RESULTS_LINK = `<a href="#/results">How we know it works</a>`;

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** A sentence's first word in lower case, leaving names and acronyms ("SDSS", "Gaia DR3") alone. */
const lower = (t: string) => (/^[A-Z][a-z]/.test(t) && !/^Gaia\b/.test(t) ? t[0].toLowerCase() + t.slice(1) : t);

/** "Fast stars (S2)" → "fast stars": the stretch-goal code means nothing outside the plan. */
function claimWords(c: Pick<ValidationCheck, "claim">): string {
  return lower(String(c.claim ?? "").replace(/\s*\(S\d+\)\s*$/, "").trim());
}

/**
 * One paragraph from /data/validation.json: how many checks pass, and which haven't run and why.
 * Missing file → a plain "not published yet". Every count comes from the data.
 */
export function validationHtml(v: ValidationResponse | null): string {
  if (!v || !Array.isArray(v.checks)) {
    return `<p>The checks haven't been published yet. When they are, each one appears on ${RESULTS_LINK} with the target set before the run.</p>`;
  }
  const checks = withAllChecks(v.checks);
  const sentence = summarySentence(countStatuses(checks));
  const pending = checks.filter((c) => normStatus(c.status) === "pending");
  const reasons = new Set(pending.map((c) => String(c.headline ?? "").trim()));
  const why = reasons.size === 1 ? [...reasons][0] : "";
  const named = pending.map((c) => {
    const own = !why && c.headline ? `: ${lower(String(c.headline).trim())}` : "";
    return `${esc(c.id)} (${esc(claimWords(c))}${esc(own)})`;
  });
  const notRun = pending.length
    ? ` Not run: ${named.length > 1 ? `${named.slice(0, -1).join(", ")} and ${named[named.length - 1]}` : named[0]}${why ? `, ${esc(lower(why))}` : ""}.`
    : "";
  return `<p>${esc(sentence)}${notRun} Each check, with the target set before it ran, the measured number and every row, is on ${RESULTS_LINK}. Failing and partial results stay there with their numbers.</p>`;
}

function checksHtml(ids: string[] | undefined): string {
  if (!ids?.length) return "";
  return ` <span class="tl-tested">Tested by <a href="#/results">${ids.length > 1 ? `${ids.slice(0, -1).join(", ")} and ${ids[ids.length - 1]}` : ids[0]}</a>.</span>`;
}

/** The section's body; the validation paragraph is filled in once validation.json loads. */
export function methodsHtml(): string {
  const groups = METHODS.map(
    (g) => `<h3 class="tl-h3">${esc(g.title)}</h3>
    <dl class="tl-fields">${g.rows.map((r) => `<div><dt>${esc(r.term)}</dt><dd>${esc(r.text)}${checksHtml(r.checks)}</dd></div>`).join("")}</dl>`,
  ).join("");
  return `<p>Where the pixels come from and what happens to them before a number reaches this site. Each step names the check that tests it; the limits are on <a href="#/about">About</a>.</p>
    ${groups}
    <h3 class="tl-h3">How we know it works</h3>
    <div class="tl-validation" aria-live="polite"><p class="muted">Loading the checks…</p></div>`;
}
