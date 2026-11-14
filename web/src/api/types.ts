// Shapes from contracts/api.md (v2). Units: degrees, arcmin (fov, r), arcsec (shifts),
// µm, MJD (UTC), µJy, frame values in MJy/sr. A missing value is null, never omitted.

export type Clock = "months" | "hours";
export type Det = 1 | 2 | 3 | 4 | 5 | 6;

export interface SkyPos {
  ra: number;
  dec: number;
}

export interface Epoch {
  id: string;
  kind: "visit" | "exposure";
  mjd_mid: number;
  mjd_start: number;
  mjd_end: number;
  det: Det;
  um_min: number;
  um_max: number;
  n_exposures: number;
  flag_frac: number;
  sat_frac: number;
  /** Resolve with new URL(data_url, jsonUrl). */
  data_url: string;
  scale: number;
  zero: number;
  /** Not in the contract: featured "follows_object" targets (comet, asteroids) move the frame centre per epoch. */
  center?: SkyPos;
}

export interface GaiaStar {
  /** String: Gaia ids exceed 2^53. */
  id: string;
  ra: number;
  dec: number;
  /** mas/yr, already × cos dec. */
  pmra: number | null;
  pmdec: number | null;
  /** mas */
  parallax: number | null;
  g: number | null;
}

export interface KnownObject {
  name: string;
  epoch_id: string | null;
  ra: number;
  dec: number;
  v: number | null;
  source: "mpc" | "most" | "irsa-3i" | string;
}

export interface FramesResponse {
  clock: Clock;
  center: SkyPos;
  pixel_arcsec: number;
  /** [nx, ny], same for every epoch, each ≤ 300. */
  size: [number, number];
  /** Sorted by mjd_mid. May hold several detectors: filter by det. */
  epochs: Epoch[];
  /** Gaia DR3 at J2016.0 (MJD 57388.5). Move to each epoch's mjd_mid with pmra, pmdec. */
  gaia: GaiaStar[];
  known: KnownObject[];
  data_release: string;
  archive_checked: string;
}

export interface SpectrumPoint {
  um: number;
  flux_ujy: number;
  err_ujy: number;
  det: Det;
  mjd: number;
  visit: number;
  saturated: boolean;
}

/** Proposed additive metadata in contracts/api.md; old static results may omit it. */
export interface MeasurementSource {
  det: Det;
  visits: [number, number];
  /** UTC MJDs actually used by the fit (stack median exposure times). */
  mjd: [number, number];
  n_exposures: [number, number];
  /** Nominal measurement field, arcmin. */
  fov: number;
  pixel_arcsec: number;
  core_filled: [boolean, boolean];
}

export interface Measure {
  source?: MeasurementSource | null;
  shift_arcsec: number;
  pa_deg: number;
  dt_yr: number;
  rate_arcsec_yr: number;
  sigma_arcsec: number;
  n_ref_stars: number;
  predicted_arcsec: number | null;
}

export interface KnownUcd {
  name: string;
  spt: string;
  ref: string;
}

export interface ObjectResponse {
  name: string | null;
  gaia: GaiaStar | null;
  known_ucd: KnownUcd | null;
  spectrum: SpectrumPoint[];
  measure: Measure | null;
}

export interface ResolveResponse {
  q: string;
  name: string | null;
  ra: number;
  dec: number;
  slug: string | null;
  source: "coords" | "builtin";
}

export type CheckStatus = "pass" | "partial" | "fail" | "pending";

export interface ValidationCheck {
  id: string;
  claim: string;
  status: CheckStatus;
  headline: string;
  value: number | null;
  unit: string | null;
  expected: string;
  note: string | null;
  rows: Record<string, unknown>[];
}

export interface ValidationResponse {
  generated: string;
  data_release: string;
  checks: ValidationCheck[];
}

export interface Suspect {
  id: string;
  ra: number;
  dec: number;
  mjd: number;
  clock: "hours";
  det: Det;
  evidence: "two_exposures" | "two_detectors";
  n_dots: number;
  snr: number;
  flux_ujy: number;
  /** 6″ sky-cell id (contracts/dots.md), an integer below 2^53. */
  cell: number;
  nearest_known_arcsec: number | null;
}

export interface SuspectsResponse {
  generated: string;
  data_release: string;
  zone: string;
  suspects: Suspect[];
}

/** /data/<slug>/index.json: written by scripts/precompute (P2), lists the frame sets of a featured target. */
export interface TargetIndex {
  slug: string;
  name: string;
  center: SkyPos;
  own: string;
  follows_object?: string;
  frames: { file: string; clock: Clock; fov: number; dets: Det[]; epochs: number; bytes: number }[];
}

export interface ApiError {
  error: { code: "bad_request" | "not_found" | "upstream" | "internal"; message: string };
}

/** Frames with their decoded pixel data, ready for the blink player. */
export interface LoadedFrames {
  frames: FramesResponse;
  /** URL the JSON came from; data_url resolves against it. */
  jsonUrl: string;
  /** Where it came from: static file of a featured target, or the live API. */
  origin: "static" | "api";
}

// ---- Community discovery: candidates for people to review (contracts/api.md, candidates.json, proposal v2.2)

/** mover: moves within hours (possible new asteroid or comet); high_pm: moves between visits
 *  (possible nearby star or brown dwarf); spectral: molecule bands or colours worth a look. */
export type CandidateKind = "mover" | "high_pm" | "spectral";

export interface Candidate {
  id: string;
  kind: CandidateKind;
  ra: number;
  dec: number;
  /** MJD of the frame that shows it best. */
  mjd: number;
  clock: Clock;
  det: Det;
  /** Pipeline zone it came from, e.g. "pilot", "hunt". */
  zone: string;
  /** Pipeline's promise score 0–1 (higher = more promising), or null when the step has none. */
  score: number | null;
  snr: number | null;
  flux_ujy: number | null;
  /** Movers: apparent rate in ″/h. */
  rate_arcsec_h: number | null;
  /** High-PM: total proper motion and its 1σ error, mas/yr; position angle east of north. */
  pm_mas_yr: number | null;
  pm_err_mas_yr: number | null;
  pa_deg: number | null;
  /** Distance to the nearest known solar-system object at that time, arcsec (null = none checked). */
  nearest_known_arcsec: number | null;
  /** A Gaia / CatWISE source sits here (its catalogue motion may still disagree: see reasons). */
  gaia_match: boolean;
  catwise_match: boolean;
  /** Galactic latitude, degrees (crowding: |b| < 10 is the Milky Way plane). */
  galactic_b: number;
  /** Short machine labels for filters: "red", "ch4", "h2o_ice", "co2_ice", "co_ice", "h2o_vapour", "brown_dwarf_like", … */
  labels: string[];
  /** Pipeline reason codes, e.g. "two_exposures", "gaia_pm_disagrees", "few_dots", "bad_fit". */
  reasons: string[];
  /** One plain sentence: why this is on the list. */
  why: string;
  /** Artefact flags near it (ghost, halo, crosstalk, …): a reason for caution. */
  flags: string[];
  /** Static blink frames for this candidate (frames.json shape), or null: then the live API is used. */
  frames_url: string | null;
}

export interface CandidatesResponse {
  generated: string;
  data_release: string;
  zones: string[];
  counts: Record<CandidateKind, number>;
  candidates: Candidate[];
}
