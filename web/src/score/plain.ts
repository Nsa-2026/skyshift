// Results in plain words (Advanced off, the default): a few short sentences per section, each number
// read from the published files, nothing made up. The coded checks, tables and methods stay in the
// Advanced view (render.ts, distant.ts). Pure functions: strings in, strings out.
import { gradeOf } from "../api/grades";
import type { CandidatesResponse, CheckStatus, DistantResponse, ValidationCheck, ValidationResponse } from "../api/types";
import { countStatuses, normStatus } from "./checks";
import { bigValue } from "./format";

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

// ---- What we found ------------------------------------------------------------------------------

/** Fast movers the vetting put in grade A: seen on one steady path at different times. */
export function strongestMovers(c: CandidatesResponse | null): number {
  const list = c && Array.isArray(c.candidates) ? c.candidates : [];
  return list.filter((x) => x.kind === "mover" && gradeOf(x) === "A").length;
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
