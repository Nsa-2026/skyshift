// Results in plain words (Advanced off, the default): a few short sentences per section, each number
// read from the published files, nothing made up. The coded checks, tables and methods stay in the
// Advanced view (render.ts, distant.ts). Pure functions: strings in, strings out.
import { gradeOf } from "../api/grades";
import type { CandidatesResponse, CheckStatus, DistantResponse, ValidationCheck, ValidationResponse } from "../api/types";
import { checkTitle, countStatuses, normStatus } from "./checks";
import { bigValue, prettyText, sentence } from "./format";

/** One result in the plain list under "How sure we are": the check's own status, one sentence. */
export interface PlainResult {
  id: string;
  status: CheckStatus;
  text: string;
}

const int = (n: number): string => Math.round(n).toLocaleString("en-GB");
const pct = (f: number): string => `${Math.round(f * 100)}%`;

/** "5 of 5" when the headline gives the total, else the bare count. */
function countOf(c: ValidationCheck): string | null {
  const b = bigValue(c.value, c.unit, typeof c.headline === "string" ? c.headline : "");
  if (!b) return null;
  if (/^\d[\d,]* of \d[\d,]*$/.test(b.num)) return b.num;
  return typeof c.value === "number" && Number.isInteger(c.value) && c.value >= 0 ? int(c.value) : null;
}

/** "within 6%": the published percentage, rounded up so the claim never overstates. */
const within = (p: number): string => `${Math.max(1, Math.ceil(Math.abs(p) - 1e-9))}%`;

type Writer = (c: ValidationCheck, headline: string) => string | null;

/**
 * The checks the plain view names, in the order a visitor cares about, each as one sentence built
 * from the check's value or headline. A check whose number can't be read is left out, never guessed.
 */
const WRITERS: [string, Writer][] = [
  [
    "V5",
    (c) =>
      typeof c.value === "number" && c.value >= 0 && c.value <= 1
        ? `Found ${pct(c.value)} of bright known asteroids unaided.`
        : null,
  ],
  [
    "V4",
    (c) => {
      const n = countOf(c);
      return n ? `Found ${n} known asteroids where NASA predicted them.` : null;
    },
  ],
  [
    "V2",
    (_c, h) => {
      // "Gaia predicts 10.27" (+5.5%, 2.2°, …)": the gap between measured and predicted motion.
      const m = /Gaia predicts[^(]*\(\s*([+\-−]?\d+(?:\.\d+)?)\s*%/.exec(h);
      if (!m || !/Barnard/.test(h)) return null;
      return `Barnard's Star moved as Gaia predicts, within ${within(Number(m[1].replace("−", "-")))}.`;
    },
  ],
  [
    "V1",
    (c, h) => {
      const n = countOf(c);
      if (!n) return null;
      const v = /\bin (\d+) of (\d+) visits\b/.exec(h);
      const visits = v ? (v[1] === v[2] ? ", in every visit" : `, in ${v[1]} of ${v[2]} visits`) : "";
      return `Found ${n} test stars${visits}.`;
    },
  ],
  [
    "V8",
    (c, h) => {
      // "Ks 0.993 (…); W1 1.029 (…)": brightness against two catalogues, the larger gap counts.
      const ratios = [...h.matchAll(/\b(?:Ks|W1) (\d+\.\d+)\b/g)].map((m) => Number(m[1]));
      if (!ratios.length && c.unit === "ratio" && typeof c.value === "number") ratios.push(c.value);
      if (!ratios.length || ratios.some((r) => !(r > 0))) return null;
      const off = Math.max(...ratios.map((r) => Math.abs(1 - r) * 100));
      return `Star brightness matches older surveys within ${within(off)}.`;
    },
  ],
];

/** The plain results, at most five, from the checks that have run. */
export function plainResults(v: ValidationResponse | null, max = 5): PlainResult[] {
  const checks = Array.isArray(v?.checks) ? v!.checks : [];
  const out: PlainResult[] = [];
  for (const [id, write] of WRITERS) {
    const c = checks.find((x) => x.id === id);
    if (!c) continue;
    const status = normStatus(c.status);
    if (status === "pending") continue;
    const text = write(c, typeof c.headline === "string" ? c.headline : "");
    if (text) out.push({ id, status, text });
    if (out.length >= max) break;
  }
  return out;
}

/**
 * The footnote under the figure (sureFigure says how many passed): what passed only in part, failed
 * or wasn't run, e.g. "2 not run yet." — every count from the file; "" when every check passed.
 */
export function plainTally(checks: Pick<ValidationCheck, "status">[]): string {
  const n = countStatuses(checks);
  const ran = n.pass + n.partial + n.fail;
  if (!ran) return n.pending ? "The checks haven't run yet." : "";
  const parts = [
    n.partial ? `${int(n.partial)} passed in part` : "",
    n.fail ? `${int(n.fail)} failed` : "",
    n.pending ? `${int(n.pending)} not run yet` : "",
  ].filter(Boolean);
  return parts.length ? `${parts.join(", ")}.` : "";
}

// ---- one line per check (the cards with Advanced on) ----------------------------------------------

/** "1.06" → "6%": how far a ratio sits from 1, as a whole percentage. */
const offBy = (r: number): string => `${Math.round(Math.abs(r - 1) * 100)}%`;

/**
 * One short plain sentence per check, from its value and headline; null when the number can't be
 * read (the card then falls back to the headline's first clause).
 */
const LINES: Record<string, Writer> = {
  V1: (c, h) => {
    const n = countOf(c);
    if (!n) return null;
    const v = /\bin (\d+) of (\d+) visits\b/.exec(h);
    const visits = v ? (v[1] === v[2] ? `, in all ${v[2]} visits` : `, in ${v[1]} of ${v[2]} visits`) : "";
    return `Found ${n} test objects where predicted${visits}.`;
  },
  V2: (_c, h) => {
    // "Gaia predicts 10.27" (+5.5%, 2.2°, …)": the gap in distance and in direction.
    const m = /Gaia predicts[^(]*\(\s*([+\-−]?\d+(?:\.\d+)?)\s*%(?:,\s*(\d+(?:\.\d+)?)\s*°)?/.exec(h);
    if (!m || !/Barnard/.test(h)) return null;
    const dist = within(Number(m[1].replace("−", "-")));
    return m[2] != null
      ? `Barnard's Star moved as Gaia predicts: within ${dist} in distance, ${Math.max(1, Math.ceil(Number(m[2]) - 1e-9))}° in direction.`
      : `Barnard's Star moved as Gaia predicts, within ${dist}.`;
  },
  V3: (_c, h) => {
    const e = /Position error (\d+(?:\.\d+)?)"/.exec(h);
    if (!e) return null;
    const m = /motions above (\d+(?:\.\d+)?)"\/yr/.exec(h);
    return m
      ? `Positions repeat to ${e[1]}″, so motions above ${m[1]}″ a year show within a year.`
      : `Positions repeat to ${e[1]}″.`;
  },
  V4: (c, h) => {
    const n = countOf(c);
    if (!n) return null;
    const ctl = /control[^;]*?(\d+(?:\.\d+)?)%/.exec(h);
    return `Found ${n} known asteroids where NASA predicted them${ctl ? `; chance matches ${ctl[1]}%` : ""}.`;
  },
  V5: (c) =>
    typeof c.value === "number" && c.value >= 0 && c.value <= 1
      ? `Found ${pct(c.value)} of bright known asteroids without being told where to look.`
      : null,
  V6: (_c, h) => {
    const m = /^(\d+) of (\d+) fastest Gaia stars measured: slope (\d+(?:\.\d+)?)/.exec(h.trim());
    if (!m) return null;
    const who = m[1] === m[2] ? `All ${m[2]}` : `${m[1]} of ${m[2]}`;
    return `${who} of the fastest Gaia stars moved as predicted, within ${offBy(Number(m[3]))}.`;
  },
  V7: (_c, h) => {
    const m = /^(\d+) of (\d+) brown dwarfs/.exec(h.trim());
    if (!m) return null;
    return `${m[1]} of ${m[2]} brown dwarfs with a visible yearly shift moved the predicted way.`;
  },
  V8: (c, h) => {
    const ratios = [...h.matchAll(/\b(?:Ks|W1) (\d+\.\d+)\b/g)].map((m) => Number(m[1]));
    if (!ratios.length && c.unit === "ratio" && typeof c.value === "number") ratios.push(c.value);
    if (!ratios.length || ratios.some((r) => !(r > 0))) return null;
    const off = Math.max(...ratios.map((r) => Math.abs(1 - r) * 100));
    const t = /time to first frame (\d+(?:\.\d+)?) s\b/.exec(h);
    return `Star brightness matches older surveys within ${within(off)}${t ? `; first image in ${t[1]} s` : ""}.`;
  },
  V9: (c, h) => {
    const m = /^([\d,]+) stars vs Gaia/.exec(h.trim());
    if (!m || normStatus(c.status) !== "pass") return null;
    return `Motions of ${m[1]} stars match Gaia on average, with honest error bars.`;
  },
  V10: (_c, h) => {
    const m = /^([\d,]+) bright galaxies[^:]*:\s*sigma_NMAD (\d+(?:\.\d+)?)(?:, outliers (\d+(?:\.\d+)?%))?/.exec(h.trim());
    if (!m) return null;
    return `Distances of ${m[1]} bright galaxies match SDSS: typical scatter ${m[2]}${m[3] ? `, ${m[3]} far off` : ""}.`;
  },
  V11: (_c, h) => {
    const n = /^(\d+) asteroids\b/.exec(h.trim());
    const d = /diameter (\d+(?:\.\d+)?)x published/.exec(h);
    if (!n || !d) return null;
    const dr = Number(d[1]);
    const a = /albedo (\d+(?:\.\d+)?)x/.exec(h);
    const ar = a ? Number(a[1]) : null;
    const size = `Sizes of ${n[1]} asteroids came out ${offBy(dr)} ${dr >= 1 ? "above" : "below"} published`;
    return ar != null ? `${size}; reflectivity ${offBy(ar)} ${ar >= 1 ? "above" : "below"}.` : `${size}.`;
  },
};

/** The headline's first clause, as a sentence: the card's line when no writer reads the number. */
function firstClause(h: string): string {
  const t = h.split(/;\s/)[0].trim();
  return t ? sentence(prettyText(t)) : "";
}

/**
 * The one line under a check's title: a plain sentence with its numbers, or for a check that
 * hasn't run, why ("Cut for the one-day build.").
 */
export function checkLine(c: ValidationCheck): string {
  const h = typeof c.headline === "string" ? c.headline.trim() : "";
  if (normStatus(c.status) === "pending") return h ? sentence(prettyText(h)) : "Not run yet.";
  const w = LINES[c.id];
  return (w ? w(c, h) : null) ?? firstClause(h);
}

// ---- Known limitations ----------------------------------------------------------------------------

/** One limitation; `key` lets the page update one in place (the sky covered, once the feed is read). */
export interface Limitation {
  key: string;
  text: string;
}

const COUNT_WORD = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven"];

/** How much of the planets' path the search has covered: "12 of 333 areas" once known. */
export function skyLimit(areas: { done: number; total: number } | null = null): string {
  const n = areas && areas.total > 0 && areas.done < areas.total ? `: ${int(areas.done)} of ${int(areas.total)} areas` : "";
  return `So far the search has covered only part of the planets' path across the sky${n}.`;
}

/**
 * What the results don't show, in plain words, at most 20 each. Each comes from the published files
 * or what the checks themselves say; one whose source is missing is left out.
 */
export function limitations(
  v: ValidationResponse | null,
  d: DistantResponse | null,
  areas: { done: number; total: number } | null = null,
): Limitation[] {
  const checks = Array.isArray(v?.checks) ? v!.checks : [];
  const out: Limitation[] = [
    { key: "confirmed", text: "Nothing is confirmed yet: most objects to check are detector glitches, sorted only by people's votes." },
  ];
  if (!areas || areas.done < areas.total) out.push({ key: "sky", text: skyLimit(areas) });
  if (d?.injection && /detection[^.]*not tested/i.test(d.injection.method ?? "")) {
    out.push({
      key: "planet",
      text: "Made-up planets were added to the list of sources, not to the photos, so spotting a faint one is untested.",
    });
  }
  const v5 = checks.find((c) => c.id === "V5");
  if (v5 && normStatus(v5.status) !== "pending") {
    out.push({ key: "speed", text: "Asteroid finding was tested at 10–90″ an hour; slower movers aren't checked against real ones yet." });
  }
  out.push({ key: "bright", text: "Very bright stars, like Barnard's Star, overload their centre pixels; we measure them from the edges." });
  const notRun = checks.filter((c) => normStatus(c.status) === "pending");
  if (checks.length && notRun.length) {
    const names = notRun.map((c) => checkTitle(c.claim));
    const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
    const cut = notRun.every((c) => /\bcut\b/i.test(String(c.headline ?? ""))) ? ", cut from the one-day build" : "";
    const n = notRun.length < COUNT_WORD.length ? COUNT_WORD[notRun.length] : int(notRun.length);
    out.push({ key: "notrun", text: `${n} ${notRun.length === 1 ? "check was" : "checks were"} not run: ${list}${cut}.` });
  }
  return out;
}

// ---- What we found ------------------------------------------------------------------------------

/** Fast movers the vetting put in grade A: seen on one steady path at different times. */
export function strongestMovers(c: CandidatesResponse | null): number {
  const list = c && Array.isArray(c.candidates) ? c.candidates : [];
  return list.filter((x) => x.kind === "mover" && gradeOf(x) === "A").length;
}

/** Moving stars Gaia already lists (kind high_pm with gaia_match): found again, on our own. */
export function knownMovingStars(c: CandidatesResponse | null): number {
  const list = c && Array.isArray(c.candidates) ? c.candidates : [];
  return list.filter((x) => x.kind === "high_pm" && x.gaia_match === true).length;
}

/** The distant-planet search in one or two sentences; "" until the file is there. */
export function distantPlain(d: DistantResponse | null): string {
  const head = distantVerdict(d);
  if (!d || !head) return "";
  const inj = d.injection;
  const added = inj && Array.isArray(inj.distances) ? inj.distances.reduce((a, r) => a + r.injected, 0) : 0;
  const found = inj && Array.isArray(inj.distances) ? inj.distances.reduce((a, r) => a + r.recovered, 0) : 0;
  const share = inj?.recovered_frac ?? (added ? found / added : null);
  const test =
    share != null && added > 0
      ? ` The search found ${pct(share)} of ${int(added)} test planets hidden in the photos.`
      : "";
  return `${head}${test}`;
}

/** The distant-planet search's verdict alone (the overview's line); "" until the file is there. */
export function distantVerdict(d: DistantResponse | null): string {
  if (!d || !d.counts || !d.control) return "";
  return d.control.beyond_chance
    ? "More pairs than chance explains: worth a closer look."
    : "No sign of one in the area searched.";
}

// ---- the overview (#/results): one figure and one sentence per card ------------------------------

/** "9 of 9" checks we ran passed, with the mark of the weakest result; null until one has run. */
export function sureFigure(checks: Pick<ValidationCheck, "status">[]): { num: string; label: string; status: CheckStatus } | null {
  const n = countStatuses(checks);
  const ran = n.pass + n.partial + n.fail;
  if (!ran) return null;
  return {
    num: `${int(n.pass)} of ${int(ran)}`,
    label: ran === 1 ? "check we ran passed" : "checks we ran passed",
    status: n.fail ? "fail" : n.partial ? "partial" : "pass",
  };
}

/**
 * One short sentence (at most ten words) for the overview's "How sure we are": the known asteroids
 * found where NASA predicted them and Barnard's Star (only when its check passed), else the first
 * plain result; "" when none ran.
 */
export function sureLine(v: ValidationResponse | null): string {
  const checks = Array.isArray(v?.checks) ? v!.checks : [];
  const ran = (id: string) => checks.find((c) => c.id === id && normStatus(c.status) !== "pending");
  const parts: string[] = [];
  const v4 = ran("V4");
  const n4 = v4 ? countOf(v4) : null;
  if (n4) parts.push(`${n4} known asteroids found`);
  const v2 = ran("V2");
  if (v2 && normStatus(v2.status) === "pass" && /Barnard/.test(String(v2.headline ?? ""))) parts.push("Barnard's Star matches Gaia");
  if (parts.length) return `${parts.join("; ")}.`;
  return plainResults(v, 1)[0]?.text ?? "";
}

/** One short sentence under the count of objects to check: the strongest leads, or what they are. */
export function foundLine(c: CandidatesResponse | null): string {
  const best = strongestMovers(c);
  if (best === 1) return "Strongest lead: 1 fast mover on a steady path.";
  if (best > 1) return `Strongest leads: ${int(best)} fast movers on steady paths.`;
  return "Things Gaia, WISE and known asteroids don't explain.";
}
