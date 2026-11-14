// Test helpers for the review pages (not shipped: only tests import this).
import type { Candidate } from "../api/types";

/** A candidate with every field set; override what the test is about. */
export function cand(id: string, over: Partial<Candidate> = {}): Candidate {
  return {
    id,
    kind: "high_pm",
    ra: 10,
    dec: -5,
    mjd: 60900,
    clock: "months",
    det: 2,
    zone: "pilot",
    score: null,
    snr: null,
    flux_ujy: null,
    rate_arcsec_h: null,
    pm_mas_yr: null,
    pm_err_mas_yr: null,
    pa_deg: null,
    nearest_known_arcsec: null,
    gaia_match: false,
    catwise_match: false,
    galactic_b: 30,
    labels: [],
    reasons: [],
    why: "",
    flags: [],
    frames_url: null,
    ...over,
  };
}
