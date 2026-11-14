// Number and coordinate formatting for the check assistant and its drafts. Pure, no DOM.
// Sexagesimal and ISO times are done in integer units so rounding never prints 60 seconds.

export type SexStyle = "hms" | "colon" | "space";

const pad2 = (n: number) => String(n).padStart(2, "0");

/** Split |x| (in base units) into whole parts and a rounded fraction, carrying at 60. */
function sex(totalUnits: number, decimals: number): [number, number, string] {
  const scale = 10 ** decimals;
  const units = Math.round(totalUnits * scale); // integer count of 10^-decimals of the last field
  const perMin = 60 * scale;
  const perBig = 3600 * scale;
  const big = Math.floor(units / perBig);
  const min = Math.floor((units - big * perBig) / perMin);
  const last = units - big * perBig - min * perMin;
  const whole = Math.floor(last / scale);
  const frac = last - whole * scale;
  const s = decimals > 0 ? `${pad2(whole)}.${String(frac).padStart(decimals, "0")}` : pad2(whole);
  return [big, min, s];
}

/**
 * Right ascension in hours, minutes, seconds: "08h10m13.00s", "08:10:13.00" or "08 10 13.00".
 * RA is wrapped into 0–360°; 23h59m59.999s rounds to 00h00m00.00s, never 24h.
 */
export function formatRa(raDeg: number, decimals = 2, style: SexStyle = "hms"): string {
  const ra = ((raDeg % 360) + 360) % 360;
  let [h, m, s] = sex((ra / 15) * 3600, decimals);
  if (h >= 24) h -= 24;
  if (style === "hms") return `${pad2(h)}h${pad2(m)}m${s}s`;
  const sep = style === "colon" ? ":" : " ";
  return `${pad2(h)}${sep}${pad2(m)}${sep}${s}`;
}

/**
 * Declination in degrees, arcminutes, arcseconds with an explicit sign (ASCII "+" or "-", so the
 * text pastes into catalogues and tools): "+17°00′12.9″", "+17:00:12.9" or "+17 00 12.9".
 */
export function formatDec(decDeg: number, decimals = 1, style: SexStyle = "hms"): string {
  const [d, m, s] = sex(Math.abs(decDeg) * 3600, decimals);
  // A value that rounds to zero keeps "+" (no "-00 00 00.0").
  const zero = d === 0 && m === 0 && Number(s) === 0;
  const sign = decDeg < 0 && !zero ? "-" : "+";
  if (style === "hms") return `${sign}${pad2(d)}°${pad2(m)}′${s}″`;
  const sep = style === "colon" ? ":" : " ";
  return `${sign}${pad2(d)}${sep}${pad2(m)}${sep}${s}`;
}

/** Decimal degrees with a fixed number of places; dec gets an explicit sign. */
export function degRa(raDeg: number, decimals = 6): string {
  return (((raDeg % 360) + 360) % 360).toFixed(decimals);
}
export function degDec(decDeg: number, decimals = 6): string {
  const s = decDeg.toFixed(decimals);
  return decDeg < 0 && Number(s) !== 0 ? s : `+${s.replace(/^-/, "")}`;
}

/** MJD (UTC) → ISO 8601 with a trailing Z, e.g. "2025-10-23T02:14:43.805Z". 0–6 decimals of a second. */
export function mjdToIso(mjd: number, decimals = 3): string {
  const d = Math.max(0, Math.min(6, Math.round(decimals)));
  const scale = 10 ** d;
  const units = Math.round((mjd - 40587) * 86400 * scale);
  const secs = Math.floor(units / scale);
  const frac = units - secs * scale;
  const base = new Date(secs * 1000).toISOString().slice(0, 19);
  return `${base}${d ? "." + String(frac).padStart(d, "0") : ""}Z`;
}

/** MJD → "23 Oct 2025" or "23 Oct 2025, 02:14 UTC". */
export function mjdDate(mjd: number, withTime = false): string {
  const iso = mjdToIso(mjd, 0);
  const [y, mo, day] = iso.slice(0, 10).split("-").map(Number);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const date = `${day} ${months[mo - 1]} ${y}`;
  return withTime ? `${date}, ${iso.slice(11, 16)} UTC` : date;
}

/** Thousands separators, no decimals: 2320.2 → "2,320". */
export function grouped(x: number): string {
  return Math.round(x).toLocaleString("en-US");
}

/** Angles in arcsec with sensible precision: 338.3 → "338″", 12.34 → "12.3″", 2.5 → "2.5″", 0.2 → "0.20″". */
export function arcsecText(x: number): string {
  const a = Math.abs(x);
  const s = a >= 100 ? x.toFixed(0) : a >= 10 ? x.toFixed(1) : a >= 1 ? trimZeros(x.toFixed(2)) : x.toFixed(2);
  return `${s}″`;
}

/** "1.50" → "1.5", "2.00" → "2". */
export function trimZeros(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

/** Fixed decimals for tables and details: null → "null". */
export function raw(x: number | null | undefined, decimals = 3): string {
  if (x == null || !Number.isFinite(x)) return "null";
  return trimZeros(x.toFixed(decimals));
}

/** UTC calendar date "YYYY-MM-DD" of a JS Date. */
export function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}
