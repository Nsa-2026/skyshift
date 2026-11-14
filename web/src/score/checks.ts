// The eight checks the scoreboard always lists, from docs/skyshift-hackathon-plan.html
// ("How we know it works"). Used for the pending state before scripts/validate publishes
// /data/validation.json, and to add a plain-language line under each published claim.
import type { CheckStatus, ValidationCheck } from "../api/types";

export interface CheckInfo {
  id: string;
  claim: string;
  /** What the check does, in words a judge can follow. */
  plain: string;
  expected: string;
}

export const CHECKS: CheckInfo[] = [
  {
    id: "V1",
    claim: "Five test objects recovered",
    plain:
      "In every visit we look for each of the five test objects where Gaia (or published astrometry for WISE 0855) says it should be, and count a find within 2 pixels.",
    expected: "5 of 5; WISE 0855 only in D5–D6",
  },
  {
    id: "V2",
    claim: "Motion matches Gaia",
    plain:
      "We measure how far Barnard's Star moved between its first and last visit and compare the speed and direction with Gaia's proper motion.",
    expected: "Barnard's Star within 15% and 10° of 10.39″/yr",
  },
  {
    id: "V3",
    claim: "Position error floor",
    plain:
      "Quiet Gaia stars that barely move should not shift between visits. How much they seem to shift is our error bar: smaller motions can't be trusted.",
    expected: "about 0.6–1.2″ per visit",
  },
  {
    id: "V4",
    claim: "Asteroids re-found, targeted",
    plain:
      "For asteroids 1–60 we look where NASA's MOST service predicts them and count the ones we find. The same search 3′ away gives the chance rate.",
    expected: "N of 60; median offset under 1 px; control under 5%",
  },
  {
    id: "V5",
    claim: "Asteroids re-found, blind",
    plain:
      "Our blind search for moving dots, scored against known asteroids by brightness. Every prediction moved 2′ away gives the chance rate.",
    expected: "recall by V magnitude; control near 0%",
  },
  {
    id: "V6",
    claim: "Fast stars",
    plain:
      "The 40 fastest Gaia stars fainter than G 12: measured motion against Gaia's, which should line up with a slope near 1.",
    expected: "slope near 1",
  },
  {
    id: "V7",
    claim: "Brown dwarfs",
    plain: "Known brown dwarfs moving faster than 1″ a year: how many shift in the direction they should.",
    expected: "a count",
  },
  {
    id: "V8",
    claim: "Spectra and speed",
    plain:
      "Our spectra of two unsaturated stars against their 2MASS and WISE catalogue brightness, and the time to the first frame for random coordinates.",
    expected: "within 20%; first frame under 15 s",
  },
];

export function checkInfo(id: string): CheckInfo | undefined {
  return CHECKS.find((c) => c.id === id);
}

/** The checks shown before validation.json exists: all eight, pending. */
export function pendingChecks(): ValidationCheck[] {
  return CHECKS.map((c) => ({
    id: c.id,
    claim: c.claim,
    status: "pending",
    headline: "Not run yet",
    value: null,
    unit: null,
    expected: c.expected,
    note: null,
    rows: [],
  }));
}

/**
 * The published checks, in file order, plus any of V1–V8 the file leaves out (as pending),
 * so the page always accounts for all eight.
 */
export function withAllChecks(published: ValidationCheck[]): ValidationCheck[] {
  const have = new Set(published.map((c) => c.id));
  return [...published, ...pendingChecks().filter((c) => !have.has(c.id))];
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
