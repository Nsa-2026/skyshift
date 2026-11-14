// Shapes the check assistant reads and returns. A Candidate (src/api/types.ts) is enough; the
// optional extras below are used when an exporter adds them, and never guessed when it doesn't.
import type { Candidate, Det } from "../api/types";

/** One position of the candidate in one photo (contracts/dots.md, one dot). Optional extra. */
export interface Detection {
  /** ICRS degrees, as measured. */
  ra: number;
  dec: number;
  /** MJD-AVG (UTC) of the exposure. */
  mjd: number;
  det: Det;
  /** 1σ per axis, arcsec, already calibrated (raw dot pos_err_arcsec is 1.4–1.8× too small). */
  pos_err_arcsec?: number | null;
  /** µJy, same aperture as the dot table. */
  flux_ujy?: number | null;
  flux_err_ujy?: number | null;
  snr?: number | null;
  /** IRSA observation id, e.g. "2025W18_1B_0074_2". */
  obs_id?: string | null;
  /** SPHEREx's geocentric equatorial position at mid-exposure, km (IMAGE header X_SC, Y_SC, Z_SC). */
  sc_pos_km?: [number, number, number] | null;
}

/** Extra measurements an exporter may add to a candidate. All optional; null means "not measured". */
export interface CandidateExtras {
  /** Each dot of a mover, or each visit position of a moving star, in time order. */
  detections?: Detection[] | null;
  /** Dots behind the candidate (suspects.json n_dots). */
  n_dots?: number | null;
  /** Moving-star pairs: brightness ratio between the two visits, same detector (contracts/dots.md flux_ratio). */
  flux_ratio?: number | null;
  /** D6 / D2 flux (4.4–5.0 µm over 1.1–1.6 µm), contracts/dots.md red_ratio. */
  red_ratio?: number | null;
}

/** A candidate as the assistant reads it: the contract's fields plus any extras present. */
export type AssistCandidate = Candidate & CandidateExtras;

export type StepId = "motion" | "known" | "artefact" | "same" | "colour" | "call";

/**
 * How a step comes out for "this is something real and new":
 * pass (a good sign), caution (look closer), fail (a bad sign), unknown (no data, and no quick check),
 * ask (the data can't settle it; the person can, by looking).
 */
export type StepStatus = "pass" | "caution" | "fail" | "unknown" | "ask";

/** The person's answer to an "ask" step. yes is always the good-sign answer. */
export type Reply = "yes" | "no" | "unsure";

export interface CheckStep {
  id: StepId;
  /** The question, as a person would ask it. */
  question: string;
  status: StepStatus;
  /** One plain sentence, with numbers and units where the data has them. */
  finding: string;
  /** Professional mode: field names and raw values. */
  details: string;
  /** "How to check this yourself": short steps. */
  howTo: string[];
  /** For ask steps: what to look at, ending in a yes/no question whose "yes" is the good sign. */
  prompt?: string;
}

/** A step after the person's reply was applied (ask steps only). */
export interface AnsweredStep extends CheckStep {
  /** What the data alone said. */
  dataStatus: StepStatus;
  reply: Reply | null;
}
