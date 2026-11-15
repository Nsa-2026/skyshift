// Where a candidate should be in every frame of its blink: the spots for the player's red circles
// (BlinkHandle.track). Solid where SkyShift measured it in that frame, dashed where it is only
// predicted, so an empty spot reads as an empty circle (two hits, not one moving object). Pure.
import type { Candidate, FramesResponse, SkyPos } from "../api/types";
import type { TrackMarks, TrackSpot } from "../app/modules";
import { pixelToSky, sepArcsec, skyToPixel } from "../app/astro";

/** Movers: predict no further than this from the nearest measured moment (days); the plate's focus window is a day too. */
export const MOVER_DAYS = 1;
/** A mover with no rate (two detectors at one moment): its spot holds only this long (days). */
export const STILL_DAYS = 1 / 24;
/** Dots closer in time than this (days, 30 s) are one moment: two detectors exposed together. */
const MOMENT_DAYS = 30 / 86400;
/** Other kinds: a detection of the frame this close (arcsec) to where the source should be is it. */
export const MATCH_ARCSEC = 4;
const D2R = Math.PI / 180;

const LABEL: Record<Candidate["kind"], string> = {
  mover: "the fast mover",
  high_pm: "the moving star",
  shape: "the stretched source",
  spectral: "the source",
};

type Cand = Pick<Candidate, "id" | "kind" | "ra" | "dec" | "mjd" | "rate_arcsec_h" | "pa_deg" | "pm_mas_yr">;
/** Tangent-plane offsets at the candidate, arcsec: east and north. */
interface Plane {
  e: number;
  n: number;
}
const UNIT = { pixel_arcsec: 1, size: [1, 1] as [number, number] };
const toPlane = (p: SkyPos, o: SkyPos): Plane => {
  const q = skyToPixel(p, UNIT, o);
  return { e: -q.x, n: -q.y };
};
const fromPlane = (p: Plane, o: SkyPos): SkyPos => pixelToSky({ x: -p.e, y: -p.n }, UNIT, o);
const ok = (x: number | null | undefined): x is number => typeof x === "number" && Number.isFinite(x);
/** A rate along a position angle (east of north) as east and north parts, same units. */
const along = (rate: number, paDeg: number): Plane => ({ e: rate * Math.sin(paDeg * D2R), n: rate * Math.cos(paDeg * D2R) });

/**
 * The spots for the red circles, one per frame of `frames` where the candidate should be; null
 * when there are none. Movers: the track's dots (solid), else a straight-line prediction (dashed)
 * between the nearest dots, or past them with the candidate's rate and direction, up to a day from
 * a dot. Other kinds: the source's detection in that frame (solid), else where it should be
 * (dashed): the candidate's position, moved by its proper motion for a moving star.
 */
export function buildTrackMarks(c: Cand, frames: FramesResponse): TrackMarks | null {
  const spots = c.kind === "mover" ? moverSpots(c, frames) : sourceSpots(c, frames);
  return spots.length ? { label: LABEL[c.kind] ?? "the find", spots } : null;
}

function moverSpots(c: Cand, f: FramesResponse): TrackSpot[] {
  const m = f.measured ?? null;
  const o = { ra: c.ra, dec: c.dec };
  const track = m?.tracks?.find((t) => t.id === c.id || t.suspect_id === c.id) ?? null;
  const mjdOf = new Map(f.epochs.map((e) => [e.id, e.mjd_mid]));
  // Its dots by frame: the track's, and any detection tagged with the track.
  const dots = new Map<string, SkyPos & { mjd: number }>();
  for (const d of track?.dots ?? []) dots.set(d.epoch_id, d);
  for (const d of m?.detections ?? []) {
    const t = mjdOf.get(d.epoch_id);
    if ((d.track_id === c.id || (track && d.track_id === track.id)) && t != null && !dots.has(d.epoch_id)) dots.set(d.epoch_id, { ra: d.ra, dec: d.dec, mjd: t });
  }
  // Moments: dots of one instant (two detectors) averaged, in time order.
  const pts = [...dots.values()].map((d) => ({ t: d.mjd, ...toPlane(d, o) })).sort((a, b) => a.t - b.t);
  const moments: { t: number; e: number; n: number; k: number }[] = [];
  for (const p of pts) {
    const last = moments[moments.length - 1];
    if (last && p.t - last.t <= MOMENT_DAYS) {
      last.e = (last.e * last.k + p.e) / (last.k + 1);
      last.n = (last.n * last.k + p.n) / (last.k + 1);
      last.k++;
    } else moments.push({ ...p, k: 1 });
  }
  const vel = ok(c.rate_arcsec_h) && ok(c.pa_deg) ? along(c.rate_arcsec_h, c.pa_deg) : null; // ″/h
  const pairVel = (a: (typeof moments)[number], b: (typeof moments)[number]): Plane => {
    const h = (b.t - a.t) * 24;
    return { e: (b.e - a.e) / h, n: (b.n - a.n) / h };
  };
  const from = (a: { t: number; e: number; n: number }, v: Plane, t: number): Plane => {
    const h = (t - a.t) * 24;
    return { e: a.e + v.e * h, n: a.n + v.n * h };
  };
  const predict = (t: number): Plane | null => {
    const N = moments.length;
    if (N >= 2) {
      const first = moments[0];
      const last = moments[N - 1];
      if (t >= first.t && t <= last.t) {
        let i = 0;
        while (i < N - 2 && moments[i + 1].t < t) i++;
        const a = moments[i];
        const b = moments[i + 1];
        const w = (t - a.t) / (b.t - a.t);
        return { e: a.e + (b.e - a.e) * w, n: a.n + (b.n - a.n) * w };
      }
      const before = t < first.t;
      const end = before ? first : last;
      if (Math.abs(t - end.t) > MOVER_DAYS) return null;
      return from(end, vel ?? (before ? pairVel(moments[0], moments[1]) : pairVel(moments[N - 2], last)), t);
    }
    const anchor = N === 1 ? moments[0] : ok(c.mjd) && c.mjd > 0 ? { t: c.mjd, e: 0, n: 0 } : null;
    if (!anchor) return null;
    if (!vel) return Math.abs(t - anchor.t) <= STILL_DAYS ? { e: anchor.e, n: anchor.n } : null;
    return Math.abs(t - anchor.t) <= MOVER_DAYS ? from(anchor, vel, t) : null;
  };
  const out: TrackSpot[] = [];
  for (const e of f.epochs) {
    const d = dots.get(e.id);
    if (d) {
      out.push({ epoch_id: e.id, mjd: e.mjd_mid, ra: d.ra, dec: d.dec, measured: true });
      continue;
    }
    const p = predict(e.mjd_mid);
    if (p) out.push({ epoch_id: e.id, mjd: e.mjd_mid, ...fromPlane(p, o), measured: false });
  }
  return out;
}

function sourceSpots(c: Cand, f: FramesResponse): TrackSpot[] {
  const o = { ra: c.ra, dec: c.dec };
  // A moving star moves by its proper motion (mas/yr, east of north) from the candidate's epoch.
  const pm = c.kind === "high_pm" && ok(c.pm_mas_yr) && ok(c.pa_deg) && ok(c.mjd) && c.mjd > 0 ? along(c.pm_mas_yr / 1000, c.pa_deg) : null;
  const at = (t: number): SkyPos => {
    if (!pm) return o;
    const yr = (t - c.mjd) / 365.25;
    return fromPlane({ e: pm.e * yr, n: pm.n * yr }, o);
  };
  const byEpoch = new Map<string, SkyPos[]>();
  for (const d of f.measured?.detections ?? []) {
    const l = byEpoch.get(d.epoch_id);
    if (l) l.push(d);
    else byEpoch.set(d.epoch_id, [d]);
  }
  return f.epochs.map((e) => {
    const want = at(e.mjd_mid);
    let best: SkyPos | null = null;
    let bd = MATCH_ARCSEC;
    for (const d of byEpoch.get(e.id) ?? []) {
      const r = sepArcsec(d, want);
      if (r <= bd) {
        bd = r;
        best = d;
      }
    }
    const p = best ?? want;
    return { epoch_id: e.id, mjd: e.mjd_mid, ra: p.ra, dec: p.dec, measured: !!best };
  });
}
