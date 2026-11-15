// A candidate for tests: the pilot zone's first mover, with any field overridden.
import type { Candidate } from "../api/types";

export function cand(over: Partial<Candidate> = {}): Candidate {
  return {
    id: "pilot-test-0",
    kind: "mover",
    ra: 122.554,
    dec: 17.0036,
    mjd: 60974.09,
    clock: "hours",
    det: 3,
    zone: "pilot",
    score: null,
    snr: 56.9,
    flux_ujy: 2320.2,
    rate_arcsec_h: null,
    pm_mas_yr: null,
    pm_err_mas_yr: null,
    pa_deg: null,
    nearest_known_arcsec: 338.3,
    gaia_match: false,
    catwise_match: false,
    galactic_b: 24.8,
    labels: [],
    reasons: ["two_exposures"],
    why: "Seen in two exposures of one session at different places, with no known asteroid nearby.",
    flags: [],
    frames_url: null,
    ...over,
  };
}
