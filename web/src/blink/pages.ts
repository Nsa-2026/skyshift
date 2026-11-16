// More photos of one visit on the hours clock (contracts/api.md v3.23, GET /api/frames `page`).
// A live hours answer holds at most 6 exposures (Firebase's 60 s limit); a visit has 55-60. The player
// shows page 0 at once and adds further pages behind it, each up to 6 more exposures of the same visit
// on the same grid. Static files hold every exposure already and have no `more`: never paged. Pure: no DOM.
import { mergeMeasured } from "./allwave";
import type { FramesResponse } from "../api/types";

/**
 * Photos the player loads by itself before it asks the person ("More photos"): page 0 and three more
 * pages. 24 single exposures blink in 12 s at 2 a second and spread over 7-10 of a visit's ~21
 * sessions (days apart), enough to follow anything that moves; it costs 3 extra live answers (each
 * about 6 cutouts) and ~2 MB of frames in the browser. The whole visit (55-60) stays a click away.
 */
export const AUTO_PHOTOS = 24;
/** A page the archive was too busy for (503: the server keeps the cutouts that arrived) is asked
 *  again once, this long after. */
export const PAGE_RETRY_MS = 3000;

const base = (): string => (typeof location !== "undefined" ? location.href : "http://localhost/");

/** A live, raw hours answer of the API (not a static file, a derived view or frames that follow a mover). */
export function pageable(resp: FramesResponse, url: string): boolean {
  if (resp.clock !== "hours" || resp.more === false || resp.epochs.some((e) => !!e.center)) return false;
  try {
    const u = new URL(url, base());
    return u.pathname === "/api/frames" && (u.searchParams.get("view") ?? "raw") === "raw" && !u.searchParams.has("page");
  } catch {
    return false;
  }
}

/** The same request's page k. */
export function pageUrl(url: string, page: number): string {
  const u = new URL(url, base());
  u.searchParams.set("page", String(page));
  return u.href;
}

/** Frames can only be put side by side on one grid: same clock, size, sampling and centre. */
export function sameGrid(a: FramesResponse, b: FramesResponse): boolean {
  if (a.clock !== b.clock || a.size[0] !== b.size[0] || a.size[1] !== b.size[1]) return false;
  if (Math.abs(a.pixel_arcsec - b.pixel_arcsec) > 1e-6) return false;
  const dra = (a.center.ra - b.center.ra) * Math.cos((b.center.dec * Math.PI) / 180);
  return Math.hypot(dra, a.center.dec - b.center.dec) * 3600 < 0.25 * b.pixel_arcsec;
}

/**
 * The answer so far with page k added (`page` came from `url`): its new exposures in time order,
 * their data_url made absolute (each page's frame files resolve against its own JSON), their known
 * objects, and the measured layers joined (each page's dots are of its own photos). `more` and
 * `visit_exposures` from the page. null when the page is on another grid or brings no new exposure
 * (an API without pages answers page 0 again).
 */
export function mergePage(have: FramesResponse, page: FramesResponse, url: string): FramesResponse | null {
  if (!sameGrid(have, page)) return null;
  const ids = new Set(have.epochs.map((e) => e.id));
  const add = page.epochs.filter((e) => !ids.has(e.id)).map((e) => ({ ...e, data_url: new URL(e.data_url, url).href }));
  if (!add.length) return null;
  const added = new Set(add.map((e) => e.id));
  const known = [...have.known, ...page.known.filter((k) => k.epoch_id != null && added.has(k.epoch_id))];
  const measured = mergeMeasured([have.measured, page.measured]);
  const out: FramesResponse = {
    ...have,
    epochs: [...have.epochs, ...add].sort((a, b) => a.mjd_mid - b.mjd_mid),
    known,
    gaia: have.gaia.length ? have.gaia : page.gaia,
    measured: measured ?? have.measured,
    measured_reason: measured ? null : have.measured_reason,
    more: page.more === true,
  };
  const n = page.visit_exposures ?? have.visit_exposures;
  if (n != null) out.visit_exposures = n;
  return out;
}

/** "12 of 57 photos of this visit" (photos of the shown detector). */
export function pagesText(shown: number, total: number | null | undefined): string {
  if (!total || total < shown) return `${shown} photo${shown === 1 ? "" : "s"} of this visit`;
  return `${shown} of ${total} photos of this visit`;
}
