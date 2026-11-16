// The whole visit on the hours clock (contracts/api.md v3.23, GET /api/frames `page`). A live hours
// answer holds at most 6 exposures (Firebase's 60 s limit); a visit has 18-60 per detector. The plate
// shows what it was handed at once (a live page 0, a featured target's static file or an object's
// saved frames) and loads the rest of that visit behind it, a few live pages at a time, until the
// API says there is no more. Pure: no DOM.
import { mergeMeasured } from "./allwave";
import type { Det, Epoch, FramesResponse } from "../api/types";

/** Exposures in one live page (server blink.LIVE_HOURS_FRAMES). */
export const PAGE_SIZE = 6;
/**
 * Page numbers asked at once. Each page is a live build of up to ~40 s that holds one of the API's 64
 * request slots (8 instances × 8) while it downloads its cutouts. Three at a time loads a 57-photo
 * visit (9 pages) in three rounds instead of nine; on All wavelengths each page number is asked for
 * both detectors, so two at a time (4 requests). Either way about 15 people can load whole visits
 * together and still leave room for everyone's first answer, which is what makes a plate appear.
 */
export const PAGES_AT_ONCE = 3;
export const PAGES_AT_ONCE_PAIRED = 2;
/** A page that failed (an error, a busy archive, or cutouts that came too late) is asked once more,
 *  this long after; the server keeps the cutouts that arrived, so the second answer is quick. */
export const PAGE_RETRY_MS = 3000;

const base = (): string => (typeof location !== "undefined" ? location.href : "http://localhost/");

/** Where a plate's visit continues live: page 0's URL for `det`, and the first page still to ask. */
export interface VisitSource {
  url0: string;
  /** 1 when the plate was handed live page 0 of url0 itself, else 0 (a static file or saved frames). */
  start: number;
  /** visit_exposures of the answer handed over (live page 0 only), else null until a page says. */
  total: number | null;
  /** The visit is complete in the answer handed over (live page 0 with `more` false). */
  complete: boolean;
}

/** The median time of `det`'s exposures: a time inside the visit they belong to. */
export function visitMjd(resp: FramesResponse, det: Det): number | null {
  const t = resp.epochs.filter((e) => e.det === det).map((e) => e.mjd_mid).sort((a, b) => a - b);
  return t.length ? t[Math.floor((t.length - 1) / 2)] : null;
}

/** The live hours request for this grid's visit: centre and field of `resp`, `det`, at `mjd`. */
export function liveVisitUrl(resp: FramesResponse, fov: number, det: Det, mjd: number | null): string {
  const p = new URLSearchParams({ ra: String(resp.center.ra), dec: String(resp.center.dec), clock: "hours", fov: String(fov), det: String(det) });
  if (mjd != null && Number.isFinite(mjd) && mjd > 0) p.set("mjd", String(mjd));
  return new URL(`/api/frames?${p}`, base()).href;
}

/**
 * Where `resp` (got from `url`) continues: its own URL when it is a live raw hours answer (page 0),
 * else the live request for the same visit (`mjd`, default a time inside the plate's own exposures of
 * `det`), centre and field. null when it can't: not hours, frames that follow a mover (each has its
 * own centre), a derived view, or an answer that already is a later page.
 */
export function visitSource(resp: FramesResponse, url: string, fov: number, det: Det, mjd?: number | null): VisitSource | null {
  if (resp.clock !== "hours" || resp.epochs.some((e) => !!e.center)) return null;
  let u: URL;
  try {
    u = new URL(url, base());
  } catch {
    return null;
  }
  if (u.pathname === "/api/frames") {
    if ((u.searchParams.get("view") ?? "raw") !== "raw" || u.searchParams.has("page")) return null;
    if (Number(u.searchParams.get("det") ?? 2) === det)
      return { url0: u.href, start: 1, total: resp.visit_exposures ?? null, complete: resp.more === false };
    // a live answer of another detector: this one's own request for the same visit
    const at = Number(u.searchParams.get("mjd"));
    return { url0: liveVisitUrl(resp, fov, det, at > 0 ? at : (mjd ?? visitMjd(resp, det))), start: 0, total: null, complete: false };
  }
  if (!resp.epochs.some((e) => e.det === det)) return null;
  return { url0: liveVisitUrl(resp, fov, det, mjd ?? visitMjd(resp, det)), start: 0, total: null, complete: false };
}

/** The same request's page k. */
export function pageUrl(url: string, page: number): string {
  const u = new URL(url, base());
  if (page > 0) u.searchParams.set("page", String(page));
  else u.searchParams.delete("page");
  return u.href;
}

/** Frames can only be put side by side on one grid: same clock, size, sampling and centre. */
export function sameGrid(a: FramesResponse, b: FramesResponse): boolean {
  if (a.clock !== b.clock || a.size[0] !== b.size[0] || a.size[1] !== b.size[1]) return false;
  if (Math.abs(a.pixel_arcsec - b.pixel_arcsec) > 1e-6) return false;
  const dra = (a.center.ra - b.center.ra) * Math.cos((b.center.dec * Math.PI) / 180);
  return Math.hypot(dra, a.center.dec - b.center.dec) * 3600 < 0.25 * b.pixel_arcsec;
}

/** Why two grids differ, in plain words (for the line under the strip). */
export function gridWhy(a: FramesResponse, b: FramesResponse): string {
  if (a.size[0] !== b.size[0] || a.size[1] !== b.size[1] || Math.abs(a.pixel_arcsec - b.pixel_arcsec) > 1e-6)
    return `the saved photos are ${a.size[0]} pixels of ${a.pixel_arcsec}″ across, the live ones ${b.size[0]} of ${b.pixel_arcsec}″`;
  return "the saved photos are centred on another spot than the live ones";
}

/** Photos of `det` in a response (distinct exposures). */
export function photosOf(resp: FramesResponse | null | undefined, det: Det): number {
  return resp ? new Set(resp.epochs.filter((e) => e.det === det).map((e) => e.id)).size : 0;
}

/**
 * The answer so far with a page added (`page` came from `url`): its new exposures in time order (an
 * exposure already here, from a static file or an earlier page, is kept as it was), their data_url
 * made absolute (each page's frame files resolve against its own JSON), their known objects, and the
 * measured layers joined (each page's dots are of its own photos). `added`: exposures new to it.
 * null when the page is on another grid (gridWhy).
 */
export function joinPage(have: FramesResponse, page: FramesResponse, url: string): { resp: FramesResponse; added: number } | null {
  if (!sameGrid(have, page)) return null;
  const ids = new Set(have.epochs.map((e) => e.id));
  const add: Epoch[] = [];
  for (const e of page.epochs)
    if (!ids.has(e.id)) {
      ids.add(e.id);
      add.push({ ...e, data_url: new URL(e.data_url, url).href });
    }
  if (!add.length) return { resp: have, added: 0 };
  const added = new Set(add.map((e) => e.id));
  const known = [...have.known, ...page.known.filter((k) => k.epoch_id != null && added.has(k.epoch_id))];
  const measured = page.measured ? mergeMeasured([have.measured, onlyOf(page.measured, added)]) : null;
  return {
    resp: {
      ...have,
      epochs: [...have.epochs, ...add].sort((a, b) => a.mjd_mid - b.mjd_mid),
      known,
      gaia: have.gaia.length ? have.gaia : page.gaia,
      measured: measured ?? have.measured,
      measured_reason: measured ? null : have.measured_reason,
    },
    added: add.length,
  };
}

/** A page's measured layer kept to its exposures that are new here (the others are measured already). */
function onlyOf(m: NonNullable<FramesResponse["measured"]>, ids: Set<string>): NonNullable<FramesResponse["measured"]> {
  return {
    ...m,
    photos: Math.min(m.photos, ids.size),
    detections: m.detections.filter((d) => ids.has(d.epoch_id)),
    tracks: m.tracks
      .map((t) => ({ ...t, dots: t.dots.filter((d) => ids.has(d.epoch_id)) }))
      .filter((t) => t.dots.length > 0),
  };
}

/** The answer made to stand alone as the plate's base: data_url absolute against its own URL. */
export function absolute(resp: FramesResponse, url: string): FramesResponse {
  return { ...resp, epochs: resp.epochs.map((e) => ({ ...e, data_url: new URL(e.data_url, url).href })) };
}

/** One detector's pages: what was asked and answered, and where the visit ends. */
export interface PageStream {
  det: Det;
  url0: string;
  start: number;
  /** Pages answered (joined, empty or past the end) or given up on, and pages being asked now. */
  got: Set<number>;
  asking: Set<number>;
  /** The last page: one said `more` false, or the next one was 404. null: not known yet. */
  last: number | null;
  /** visit_exposures of `det`: null until an answer says. */
  total: number | null;
  /** Pages that failed twice. */
  failed: number;
}

export function newStream(det: Det, src: VisitSource): PageStream {
  return { det, url0: src.url0, start: src.start, got: new Set(), asking: new Set(), last: src.complete ? 0 : null, total: src.total, failed: 0 };
}

/**
 * The next page this stream should ask, or null. Nothing past the last page; nothing once the photos
 * here (`have`, plus up to PAGE_SIZE for each page on its way) reach the visit's count; while the count
 * is unknown, one page at a time (the first answer tells it).
 */
export function nextPage(s: PageStream, have: number): number | null {
  if (s.total == null) {
    if (s.asking.size) return null;
  } else if (have + PAGE_SIZE * s.asking.size >= s.total) return null;
  for (let k = s.start; s.last == null || k <= s.last; k++) if (!s.got.has(k) && !s.asking.has(k)) return k;
  return null;
}

/** Nothing more will come: nothing asked now and nothing left to ask. */
export function streamDone(s: PageStream, have: number): boolean {
  return !s.asking.size && nextPage(s, have) === null;
}

/**
 * The line under the strip: how much of the visit is loaded, by photos of the shown detector.
 * "6 of 57 photos of this visit loaded · loading…", "57 of 57 photos of this visit", and when some
 * could not be loaded, how many.
 */
export function pagesText(loaded: number, total: number | null | undefined, busy: boolean, failed = 0): string {
  const n = (k: number) => `${k} photo${k === 1 ? "" : "s"}`;
  const t = total && total >= loaded ? total : null;
  if (busy) return `${t ? `${loaded} of ${t} photos` : n(loaded)} of this visit loaded · loading…`;
  if (!t) return `${n(loaded)} of this visit`;
  if (loaded >= t) return `${loaded} of ${t} photos of this visit`;
  const miss = t - loaded;
  return `${loaded} of ${t} photos of this visit loaded; ${miss} could not be loaded${failed ? " (SkyShift's live service did not answer for them)" : ""}`;
}
