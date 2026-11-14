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
  source: "skybot" | "most" | "irsa-3i" | string;
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
  cell: string;
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
