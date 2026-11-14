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
  /** v3.3, optional: what SkyShift's own pipeline detected in these photos. null: nothing measured
   *  here (measured_reason says why). Absent (older files and answers): not computed. */
  measured?: Measured | null;
  /** v3.3, optional: why `measured` is null; null when it is set. */
  measured_reason?: MeasuredReason | null;
}

// ---- The measured layer (contracts/api.md v3.3, "measured: what SkyShift measured") ------------

/** First rule that applies, per clock (contracts/api.md). `known` and `mover` occur on the hours clock only. */
export type MeasuredClass = "star" | "known" | "mover" | "suspect" | "artifact" | "other";
export type MeasuredReason = "not_processed" | "processing" | "no_dots" | "unavailable";

/** Hours: one per dot of a photo. Months: one per linked source and visit (that visit's dots). */
export interface MeasuredDetection {
  /** The epochs[].id it was measured in. */
  epoch_id: string;
  /** deg, ICRS, raw. */
  ra: number;
  dec: number;
  snr: number;
  /** µJy, r = 2 px aperture, raw (× 1.033 for the total). */
  flux_ujy: number;
  n_dots: number;
  class: MeasuredClass;
  /** The pipeline's linked source, an integer below 2^53. Months: always set. */
  source_id: number | null;
  /** Gaia DR3 source_id as a string, like gaia[].id. */
  gaia_id: string | null;
  /** known[].name it matched (class known). */
  known_name: string | null;
  /** tracks[].id of the mover it belongs to. */
  track_id: string | null;
  /** Suspects only, when graded by the vetting. */
  grade: "A" | "B" | "C" | null;
  /** Codes, shown as given: artefact reasons, a suspect's failed gates, `near_gaia` for a mover. */
  reasons: string[];
}

export interface MeasuredTrackDot {
  ra: number;
  dec: number;
  /** The photo's MJD-AVG. */
  mjd: number;
  epoch_id: string;
}

/** Hours clock: one pipeline mover, its dots in this response in time order. */
export interface MeasuredTrack {
  id: string;
  class: "suspect" | "known" | "artifact" | "mover";
  suspect_id: string | null;
  known_name: string | null;
  evidence: "two_exposures" | "two_detectors";
  dots: MeasuredTrackDot[];
}

/** Months clock: a fitted motion with pm_snr ≥ 3. The arrow starts at (ra, dec) at mjd_from and
 *  ends at (ra, dec) + (dra_arcsec, ddec_arcsec). */
export interface MeasuredShift {
  source_id: number;
  gaia_id: string | null;
  class: MeasuredClass;
  /** deg: the fit's position at mjd_from. */
  ra: number;
  dec: number;
  mjd_from: number;
  mjd_to: number;
  dt_yr: number;
  /** arcsec: east (already × cos dec) and north, over dt_yr. */
  dra_arcsec: number;
  ddec_arcsec: number;
  shift_arcsec: number;
  /** 1σ per axis, arcsec. */
  err_arcsec: number;
  /** East of north, 0–360. */
  pa_deg: number;
  /** mas/yr (pmra × cos dec), 1σ per axis, and pm / error. */
  pmra: number;
  pmdec: number;
  pm_err: number;
  pm_snr: number;
}

export interface Measured {
  /** Pipeline zone: "hunt", "ecl13", "t_barnard", … */
  zone: string;
  /** "extracted" (coming): every extracted photo's detections classed against Gaia only, before the
   *  zone is combined. Other strings may appear later: show them, never break on them. */
  stage: "provisional" | "final" | "extracted" | (string & {});
  /** deg. Outside it nothing is linked: dots there are `other`. */
  zone_box: { ra: [number, number]; dec: [number, number] };
  clock: Clock;
  /** Photos of this response whose dot file was read / had none. */
  photos: number;
  photos_missing: number;
  /** true when detections was capped (250 per epoch, non-star classes first). */
  truncated: boolean;
  /** Detections per class, before the cap. */
  counts: Record<MeasuredClass, number>;
  /** Sorted by epoch_id, then snr descending. */
  detections: MeasuredDetection[];
  /** Hours: movers with a dot in this response. Months: []. */
  tracks: MeasuredTrack[];
  /** Months: fitted motions, highest pm_snr first. Hours: []. */
  shifts: MeasuredShift[];
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

export interface Measure {
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

/** Why `measure` is null (contracts/api.md v3.2). */
export type MeasureReason = "few_visits" | "not_detected" | "edge" | "different_sources" | "no_time" | "moved_away";

/** With `moved_away`: the fast star catalogued at this position, and where it is at `mjd` (the middle of SPHEREx's visits). */
export interface MeasureHint {
  name: string | null;
  ra: number;
  dec: number;
  mjd: number;
}

export interface ObjectResponse {
  name: string | null;
  gaia: GaiaStar | null;
  known_ucd: KnownUcd | null;
  spectrum: SpectrumPoint[];
  measure: Measure | null;
  /** Optional (v3.2): why `measure` is null. Static files and older answers leave it out: read as null. */
  measure_reason?: MeasureReason | null;
  /** Optional (v3.2): set with `measure_reason` "moved_away" only. */
  measure_hint?: MeasureHint | null;
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

/** The pipeline's vetting grade (contracts/api.md, added 14 Nov). Words and ranking: ./grades.ts. */
export type Grade = "A" | "B" | "C";

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
  /** Vetting (added 14 Nov). Absent in older files: then the suspect is not vetted yet, never B. */
  grade?: Grade;
  /** The gates it fails, in contract order (empty for A and B). */
  reasons?: string[];
  /** Nearest catalogue or fixed source within 10″, arcsec: a warning, not a gate. */
  near_source_arcsec?: number | null;
}

export interface SuspectsResponse {
  generated: string;
  data_release: string;
  zone: string;
  suspects: Suspect[];
  /** Vetting (added 14 Nov), per zone. Absent in older files. */
  vetoes?: string[];
  provisional?: boolean;
  grades?: Record<Grade, number>;
  gates?: Record<string, number>;
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
  /** Movers: the vetting grade (added 14 Nov). null or absent: not graded (other kinds, older exports). */
  grade?: Grade | null;
}

export interface CandidatesResponse {
  generated: string;
  data_release: string;
  zones: string[];
  counts: Record<CandidateKind, number>;
  candidates: Candidate[];
}
