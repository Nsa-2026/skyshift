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
        ? `Found ${pct(c.value)} of bright known asteroids without being told where to look.`
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
      return `Barnard's Star moved as far as Gaia predicts, to within ${within(Number(m[1].replace("−", "-")))}.`;
    },
  ],
  [
    "V1",
    (c, h) => {
      const n = countOf(c);
      if (!n) return null;
      const v = /\bin (\d+) of (\d+) visits\b/.exec(h);
      const visits = v ? (v[1] === v[2] ? ", in every visit" : `, in ${v[1]} of ${v[2]} visits`) : "";
      return `Found ${n} test stars and brown dwarfs where they should be${visits}.`;
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
      return `Star brightness agrees with older sky surveys to within ${within(off)}.`;
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

/** "All 9 checks we ran passed; 2 more weren't run." — every count from the file. */
export function plainTally(checks: Pick<ValidationCheck, "status">[]): string {
  const n = countStatuses(checks);
  const ran = n.pass + n.partial + n.fail;
  if (!ran) return n.pending ? "The checks haven't run yet." : "";
  const head =
    n.pass === ran
      ? `${ran === 1 ? "The one check" : `All ${int(ran)} checks`} we ran passed`
      : [
          `${int(n.pass)} of the ${int(ran)} checks we ran passed`,
          n.partial ? `${int(n.partial)} in part` : "",
          n.fail ? `${int(n.fail)} failed` : "",
        ]
          .filter(Boolean)
          .join(", ");
  const rest = n.pending ? `; ${int(n.pending)} more ${n.pending === 1 ? "wasn't" : "weren't"} run` : "";
  return `${head}${rest}.`;
}

// ---- What we found ------------------------------------------------------------------------------

/** Fast movers the vetting put in grade A: seen on one steady path at different times. */
export function strongestMovers(c: CandidatesResponse | null): number {
  const list = c && Array.isArray(c.candidates) ? c.candidates : [];
  return list.filter((x) => x.kind === "mover" && gradeOf(x) === "A").length;
}

/** The distant-planet search in one or two sentences; "" until the file is there. */
export function distantPlain(d: DistantResponse | null): string {
  if (!d || !d.counts || !d.control) return "";
  const head = d.control.beyond_chance
    ? "More possible pairs turned up than chance explains: worth a closer look, not yet a discovery."
    : "No sign of one in the patch of sky we searched.";
  const inj = d.injection;
  const added = inj && Array.isArray(inj.distances) ? inj.distances.reduce((a, r) => a + r.injected, 0) : 0;
  const found = inj && Array.isArray(inj.distances) ? inj.distances.reduce((a, r) => a + r.recovered, 0) : 0;
  const share = inj?.recovered_frac ?? (added ? found / added : null);
  const test =
    share != null && added > 0
      ? ` The same search found ${pct(share)} of ${int(added)} made-up planets we hid in the real photos.`
      : "";
  return `${head}${test}`;
}
