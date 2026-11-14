// Words and numbers for the Live search page. Times show in the reader's own zone and in UTC.

const MINUS = "−";

/** 61204 → "61,204"; 48213904 → "48.2 million"; null → "not yet". */
export function fmtCount(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "not yet";
  const a = Math.abs(n);
  if (a >= 1e9) return `${trim(n / 1e9)} billion`;
  if (a >= 1e6) return `${trim(n / 1e6)} million`;
  return Math.round(n).toLocaleString("en-US");
}

/** Three significant figures, no trailing zeros: 48.21 → "48.2", 4.205 → "4.21", 120.4 → "120". */
function trim(x: number): string {
  const digits = Math.abs(x) >= 100 ? 0 : Math.abs(x) >= 10 ? 1 : 2;
  const s = x.toFixed(digits);
  return digits ? s.replace(/\.?0+$/, "") : s;
}

/** "about 9,800" for rates: two significant figures. */
export function fmtAbout(n: number): string {
  if (!Number.isFinite(n)) return "–";
  if (n < 10) return n.toFixed(1).replace(/\.0$/, "");
  const p = 10 ** Math.max(0, Math.floor(Math.log10(n)) - 1);
  return (Math.round(n / p) * p).toLocaleString("en-US");
}

/** 0.9037 → "90%". */
export function fmtPct(x: number | null, digits = 0): string {
  return x === null || !Number.isFinite(x) ? "not yet" : `${(x * 100).toFixed(digits)}%`;
}

/** A length of time: "under a minute", "45 min", "6 h", "6 h 12 min". Sign ignored. */
export function fmtDuration(ms: number): string {
  const min = Math.floor(Math.abs(ms) / 60e3);
  if (min < 1) return "under a minute";
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** "Updated just now", "Updated 1 min ago", "Updated 2 h 5 min ago". */
export function fmtAgo(ms: number): string {
  return ms < 60e3 ? "just now" : `${fmtDuration(ms)} ago`;
}

/** Days since 1970 of a moment's calendar date, in UTC or in the reader's zone. */
const dayKey = (d: Date, utc: boolean) =>
  (utc ? Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) : Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5;

const hhmm = (d: Date, timeZone?: string) =>
  new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone }).format(d);

/**
 * A clock time: "16:42" today, "07:00 tomorrow", "yesterday 23:10", else "Sun 01:10".
 * timeZone: "UTC" or the reader's own (undefined).
 */
export function fmtClock(ms: number, now: number, timeZone?: string): string {
  const d = new Date(ms);
  const utc = timeZone === "UTC";
  const time = hhmm(d, timeZone);
  const days = dayKey(d, utc) - dayKey(new Date(now), utc);
  if (days === 0) return time;
  if (days === 1) return `${time} tomorrow`;
  if (days === -1) return `yesterday ${time}`;
  return `${new Intl.DateTimeFormat("en-GB", { weekday: "short", timeZone }).format(d)} ${time}`;
}

/** "07:00 tomorrow (06:00 UTC)": the reader's time, then UTC (with a weekday only when the UTC date differs). */
export function fmtTimePair(ms: number, now: number): string {
  const d = new Date(ms);
  const utcDay = dayKey(d, true) === dayKey(d, false) ? "" : `${new Intl.DateTimeFormat("en-GB", { weekday: "short", timeZone: "UTC" }).format(d)} `;
  return `${fmtClock(ms, now)} (${utcDay}${hhmm(d, "UTC")} UTC)`;
}

/** RA in degrees: "130.5°". */
export function fmtRa(ra: number): string {
  return `${round(ra)}°`;
}

/** Dec in degrees with its sign: "+11.9°", "−4.1°". */
export function fmtDec(dec: number): string {
  const r = round(Math.abs(dec));
  return `${dec < 0 && r !== "0" ? MINUS : dec > 0 ? "+" : ""}${r}°`;
}

function round(x: number): string {
  return String(Math.round(x * 100) / 100);
}

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
/** 3 → "Three"; 12 → "12". */
export function numberWord(n: number): string {
  return WORDS[n] ?? String(n);
}

/** Escape text for HTML. */
export function esc(s: unknown): string {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
