// Small frames for the plate-layer tests (shapes from contracts/api.md, GET /api/frames).
import type { Epoch, FramesResponse } from "../api/types";

export function epoch(id: string, det: number, mjd: number, extra: Partial<Epoch> = {}): Epoch {
  return {
    id,
    kind: "visit",
    mjd_mid: mjd,
    mjd_start: mjd - 5,
    mjd_end: mjd + 5,
    det: det as Epoch["det"],
    um_min: 1.1,
    um_max: 1.6,
    n_exposures: 18,
    flag_frac: 0.025,
    sat_frac: 0.0023,
    data_url: `frames/${id}.bin`,
    scale: 1,
    zero: 0,
    ...extra,
  };
}

export function frames(epochs: Epoch[], extra: Partial<FramesResponse> = {}): FramesResponse {
  return {
    clock: "months",
    center: { ra: 269.4462, dec: 4.76883 },
    pixel_arcsec: 2.05,
    size: [146, 146],
    epochs,
    gaia: [],
    known: [],
    data_release: "DR1+QR3",
    archive_checked: "2026-11-14",
    ...extra,
  };
}
