// Small sky geometry shared by blink, sky and card. Degrees, arcsec, MJD.
import type { FramesResponse, GaiaStar, SkyPos } from "../api/types";

export const GAIA_EPOCH_MJD = 57388.5; // J2016.0
export const DAYS_PER_YEAR = 365.25;
const D2R = Math.PI / 180;

/** Move a Gaia star from J2016.0 to an MJD with its proper motion (no parallax). */
export function gaiaAt(star: GaiaStar, mjd: number): SkyPos {
  const dtYr = (mjd - GAIA_EPOCH_MJD) / DAYS_PER_YEAR;
  const pmra = star.pmra ?? 0; // mas/yr, already × cos dec
  const pmdec = star.pmdec ?? 0;
  const dec = star.dec + (pmdec * dtYr) / 3.6e6;
  const ra = star.ra + (pmra * dtYr) / 3.6e6 / Math.cos(star.dec * D2R);
  return { ra, dec };
}

/**
 * Gnomonic projection onto the frame: pixel (x, y) with x to the right (west, since east is
 * left) and y down (south, since north is up). The centre sits at ((nx−1)/2, (ny−1)/2).
 */
export function skyToPixel(
  pos: SkyPos,
  frame: Pick<FramesResponse, "pixel_arcsec" | "size">,
  center: SkyPos,
): { x: number; y: number } {
  const ra = pos.ra * D2R;
  const dec = pos.dec * D2R;
  const ra0 = center.ra * D2R;
  const dec0 = center.dec * D2R;
  const cosc = Math.sin(dec0) * Math.sin(dec) + Math.cos(dec0) * Math.cos(dec) * Math.cos(ra - ra0);
  const xi = (Math.cos(dec) * Math.sin(ra - ra0)) / cosc; // east positive
  const eta = (Math.cos(dec0) * Math.sin(dec) - Math.sin(dec0) * Math.cos(dec) * Math.cos(ra - ra0)) / cosc;
  const scale = frame.pixel_arcsec / 3600 / (180 / Math.PI); // radians per pixel
  const [nx, ny] = frame.size;
  return { x: (nx - 1) / 2 - xi / scale, y: (ny - 1) / 2 - eta / scale };
}

export function pixelToSky(
  px: { x: number; y: number },
  frame: Pick<FramesResponse, "pixel_arcsec" | "size">,
  center: SkyPos,
): SkyPos {
  const [nx, ny] = frame.size;
  const scale = frame.pixel_arcsec / 3600 / (180 / Math.PI);
  const xi = -(px.x - (nx - 1) / 2) * scale;
  const eta = -(px.y - (ny - 1) / 2) * scale;
  const ra0 = center.ra * D2R;
  const dec0 = center.dec * D2R;
  const rho = Math.hypot(xi, eta);
  if (rho === 0) return { ...center };
  const c = Math.atan(rho);
  const dec = Math.asin(Math.cos(c) * Math.sin(dec0) + (eta * Math.sin(c) * Math.cos(dec0)) / rho);
  const ra = ra0 + Math.atan2(xi * Math.sin(c), rho * Math.cos(dec0) * Math.cos(c) - eta * Math.sin(dec0) * Math.sin(c));
  return { ra: (((ra / D2R) % 360) + 360) % 360, dec: dec / D2R };
}

/** Angular separation in arcsec. */
export function sepArcsec(a: SkyPos, b: SkyPos): number {
  const d1 = a.dec * D2R;
  const d2 = b.dec * D2R;
  const s =
    Math.sin((d2 - d1) / 2) ** 2 + Math.cos(d1) * Math.cos(d2) * Math.sin(((b.ra - a.ra) * D2R) / 2) ** 2;
  return (2 * Math.asin(Math.min(1, Math.sqrt(s))) * 180 * 3600) / Math.PI;
}

/** MJD → Date (UTC). */
export function mjdToDate(mjd: number): Date {
  return new Date((mjd - 40587) * 86400000);
}

export function formatMjd(mjd: number, withTime = false): string {
  const d = mjdToDate(mjd);
  const opts: Intl.DateTimeFormatOptions = withTime
    ? { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }
    : { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" };
  return d.toLocaleString("en-GB", opts) + (withTime ? " UTC" : "");
}

/** Sexagesimal for display: 08h55m10.8s −07°14′42″. */
export function formatRaDec(pos: SkyPos): string {
  const ra = (((pos.ra % 360) + 360) % 360) / 15;
  const h = Math.floor(ra);
  const m = Math.floor((ra - h) * 60);
  const s = ((ra - h) * 60 - m) * 60;
  const sign = pos.dec < 0 ? "−" : "+";
  const ad = Math.abs(pos.dec);
  const d = Math.floor(ad);
  const am = Math.floor((ad - d) * 60);
  const as = ((ad - d) * 60 - am) * 60;
  const p2 = (n: number) => String(n).padStart(2, "0");
  return `${p2(h)}h${p2(m)}m${s.toFixed(1).padStart(4, "0")}s ${sign}${p2(d)}°${p2(am)}′${as.toFixed(0).padStart(2, "0")}″`;
}

/** Detector wavelength ranges (µm), from CLAUDE.md domain facts. */
export const DETECTORS: Record<1 | 2 | 3 | 4 | 5 | 6, { min: number; max: number; band: "SWIR" | "MWIR" }> = {
  1: { min: 0.75, max: 1.1, band: "SWIR" },
  2: { min: 1.1, max: 1.6, band: "SWIR" },
  3: { min: 1.6, max: 2.4, band: "SWIR" },
  4: { min: 2.4, max: 3.8, band: "MWIR" },
  5: { min: 3.8, max: 4.4, band: "MWIR" },
  6: { min: 4.4, max: 5.0, band: "MWIR" },
};

/** D1+D4, D2+D5, D3+D6 are exposed at the same moment: a flip between them is colour, not motion. */
export const SAME_MOMENT: Record<number, number> = { 1: 4, 2: 5, 3: 6, 4: 1, 5: 2, 6: 3 };
