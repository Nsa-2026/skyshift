// The eleven checks the scoreboard always accounts for: claims, units and targets from
// contracts/api.md ("validation.json"), plain words from docs/skyshift-hackathon-plan.html
// ("How we know it works"). Used for the pending state before scripts/validate publishes
// /data/validation.json, for ids the file leaves out, and as the plain-language line under a
// published check that carries no note.
import type { CheckStatus, ValidationCheck } from "../api/types";

export interface CheckInfo {
  id: string;
  claim: string;
  /** What the check does, in words a judge can follow. */
  plain: string;
  /** The target set before the run. */
  expected: string;
  /** Unit of `value` once published (contracts/api.md), null when the check is cut. */
  unit: string | null;
  /** Cut from the one-day build (S2, S3): stays pending. */
  cut?: boolean;
}

export const CHECKS: CheckInfo[] = [
  {
    id: "V1",
    claim: "Five test objects recovered",
    plain:
      "In every visit we look for each of the five test objects where Gaia (or published astrometry for WISE 0855) says it should be, and count a find within 2 pixels.",
    expected: "5 of 5",
    unit: "targets",
  },
  {
    id: "V2",
    claim: "Motion matches Gaia",
    plain:
      "We measure how far Barnard's Star moved between its first and last visit and compare the distance and direction with what Gaia predicts for the same dates.",
    expected: "Barnard's Star within 15% and 10° of Gaia",
    unit: "arcsec",
  },
  {
    id: "V3",
    claim: "Position error floor",
    plain:
      "Quiet Gaia stars that barely move should not shift between visits. How much they seem to shift is our error bar: smaller motions can't be trusted.",
    expected: "~0.25″, 10 or more reference stars",
    unit: "arcsec",
  },
  {
    id: "V4",
    claim: "Asteroids re-found, targeted",
    plain:
      "For asteroids 1–60 we look where NASA's MOST service predicts them and count the ones we find. The same search 3′ away gives the chance rate.",
    expected: "N of 60; median offset under 1 px; control under 5%",
    unit: "asteroids",
  },
  {
    id: "V5",
    claim: "Asteroids re-found, blind (S1)",
    plain:
      "Our blind search for moving dots, scored against known asteroids by brightness. Every prediction moved 2′ away gives the chance rate.",
    expected: "Recall at V < 16 ≥ 50% (gate G2); control near 0",
    unit: "recall",
  },
  {
    id: "V6",
    claim: "Fast stars (S2)",
    plain:
      "The 40 fastest Gaia stars fainter than G 12: measured motion against Gaia's, which should line up with a slope near 1.",
    expected: "Slope near 1",
    unit: null,
    cut: true,
  },
  {
    id: "V7",
    claim: "Brown dwarfs (S3)",
    plain: "Known brown dwarfs moving faster than 1″ a year: how many shift in the direction they should.",
    expected: "A count",
    unit: null,
    cut: true,
  },
  {
    id: "V8",
    claim: "Spectra and speed",
    plain:
      "Our spectra of catalogue stars against their 2MASS Ks and WISE W1 brightness, and the time to the first frame for random coordinates.",
    expected: "Spectra within 20% of 2MASS Ks / WISE W1; first frame under 15 s",
    unit: "ratio",
  },
  {
    id: "V9",
    claim: "Astrometry matches Gaia",
    plain:
      "A proper motion for every star in the zone, fitted over all its dots, compared with Gaia's: the bias, the scatter, and whether our error bars are honest.",
    expected: "Proper-motion bias near 0; pull scatter near 1",
    unit: "mas/yr",
  },
  {
    id: "V10",
    claim: "Galaxy distances match SDSS",
    plain:
      "Galaxy redshifts from SPHEREx's 102-channel spectra, fitted with templates, against the spectroscopic redshifts SDSS measured.",
    expected: "σ_NMAD ~0.02 for bright galaxies (AB < 17)",
    unit: "dz/(1+z)",
  },
  {
    id: "V11",
    claim: "Asteroid sizes match published values",
    plain:
      "Reflected sunlight and heat glow fitted with a thermal model give each asteroid's diameter and albedo, compared with published values.",
    expected: "Diameter ratio near 1",
    unit: "ratio",
  },
];

export function checkInfo(id: string): CheckInfo | undefined {
  return CHECKS.find((c) => c.id === id);
}

function pendingCheck(c: CheckInfo): ValidationCheck {
  return {
    id: c.id,
    claim: c.claim,
    status: "pending",
    headline: c.cut ? "Cut from the one-day build" : "Not run yet",
    value: null,
    unit: null,
    expected: c.expected,
    note: null,
    rows: [],
  };
}

/** The checks shown before validation.json exists: all eleven, pending. */
export function pendingChecks(): ValidationCheck[] {
  return CHECKS.map(pendingCheck);
}

/**
 * The published checks, in file order, plus any of V1–V11 the file leaves out (as pending),
 * so the page always accounts for every check (contracts/api.md).
 */
export function withAllChecks(published: ValidationCheck[]): ValidationCheck[] {
  const have = new Set(published.map((c) => c.id));
  return [...published, ...CHECKS.filter((c) => !have.has(c.id)).map(pendingCheck)];
}

export const STATUSES: CheckStatus[] = ["pass", "partial", "fail", "pending"];

export function normStatus(s: unknown): CheckStatus {
  return STATUSES.includes(s as CheckStatus) ? (s as CheckStatus) : "pending";
}

export type StatusCounts = Record<CheckStatus, number>;

export function countStatuses(checks: Pick<ValidationCheck, "status">[]): StatusCounts {
  const n: StatusCounts = { pass: 0, partial: 0, fail: 0, pending: 0 };
  for (const c of checks) n[normStatus(c.status)]++;
  return n;
}

const SMALL = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];

/** One clause of the summary sentence; `status` is the mark drawn in front of it. */
export interface SummaryClause {
  status: CheckStatus;
  text: string;
  /** What follows the clause: ", ", " and ", "." or a following sentence. */
  after: string;
}

/**
 * "8 of 11 checks pass, 1 passes in part and 2 have not run." as clauses, so the page can put
 * each status mark in front of its count. Every count comes from the data.
 */
export function summaryClauses(n: StatusCounts): SummaryClause[] {
  const total = n.pass + n.partial + n.fail + n.pending;
  if (total === 0) return [{ status: "pending", text: "No checks are published yet", after: "." }];
  if (n.pending === total) {
    const all = total <= 12 ? SMALL[total] : String(total);
    return [
      {
        status: "pending",
        text: `None of the ${all} checks has run yet`,
        after: ". Each one fills in here when the validation run publishes its result.",
      },
    ];
  }
  if (n.pass === total) return [{ status: "pass", text: `All ${total} checks pass`, after: "." }];
  const parts: Omit<SummaryClause, "after">[] = [{ status: "pass", text: `${n.pass} of ${total} checks pass` }];
  if (n.partial) parts.push({ status: "partial", text: `${n.partial} ${n.partial === 1 ? "passes" : "pass"} in part` });
  if (n.fail) parts.push({ status: "fail", text: `${n.fail} ${n.fail === 1 ? "fails" : "fail"}` });
  if (n.pending) parts.push({ status: "pending", text: `${n.pending} ${n.pending === 1 ? "has" : "have"} not run` });
  return parts.map((p, i) => ({
    ...p,
    after: i === parts.length - 1 ? "." : i === parts.length - 2 ? " and " : ", ",
  }));
}

export function summarySentence(n: StatusCounts): string {
  return summaryClauses(n)
    .map((c) => c.text + c.after)
    .join("");
}
