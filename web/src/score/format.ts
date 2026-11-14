// Formatting for the scoreboard: words, figures, table cells, suspects, CSV. Pure functions, tested.
import type { Suspect } from "../api/types";
import { formatMjd, formatRaDec } from "../app/astro";
import { targetBySlug } from "../app/state";

export function esc(s: unknown): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** A number for a table: integers as they are, decimals trimmed to a sensible precision. */
export function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  if (Number.isInteger(n)) return String(n).replace("-", "−");
  const a = Math.abs(n);
  const digits = a >= 100 ? 2 : a >= 1 ? 3 : 4;
  return n.toLocaleString("en-US", { maximumFractionDigits: digits, useGrouping: false }).replace("-", "−");
}

/** One value from a validation row, as text. null → em dash. */
export function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return formatNumber(v);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

/** A row value in its column: a featured target's slug reads as its name. */
export function formatRowCell(key: string, v: unknown): string {
  if (key === "slug" && typeof v === "string") return targetBySlug(v)?.name ?? v;
  return formatCell(v);
}

/** Column order for flat rows: keys in order of first appearance. */
export function rowKeys(rows: Record<string, unknown>[]): string[] {
  const keys: string[] = [];
  for (const r of rows) for (const k of Object.keys(r)) if (!keys.includes(k)) keys.push(k);
  return keys;
}

/** A column is numeric when every non-null value is a number (right-aligned). */
export function isNumericColumn(rows: Record<string, unknown>[], key: string): boolean {
  const vals = rows.map((r) => r[key]).filter((v) => v !== null && v !== undefined);
  return vals.length > 0 && vals.every((v) => typeof v === "number");
}

// ---- words for the page ------------------------------------------------------------------

/** En dash with a word joiner, so "D5–D6" and "1–2 px" never break across lines. */
const RANGE = "–⁠";

/**
 * Typography for text from the JSON: ASCII stand-ins become the symbols the rest of the site
 * uses (10.83" → 10.83″, 3' → 3′, >= → ≥, 10 deg → 10°, 5 sigma → 5σ, 1.06x → 1.06×, number
 * and detector ranges with an en dash). The meaning never changes.
 */
export function prettyText(s: string): string {
  return s
    .replace(/(\d)"/g, "$1″")
    .replace(/(\d)'/g, "$1′")
    .replace(/>=/g, "≥")
    .replace(/<=/g, "≤")
    .replace(/(\d) ?deg\b/g, "$1°")
    .replace(/(\d) sigma\b/g, "$1σ")
    .replace(/\bsigma_NMAD\b/g, "σ_NMAD")
    .replace(/(\d)x\b/g, "$1×")
    .replace(/(\d) um\b/g, "$1 µm")
    .replace(/\b(D[1-6])-(D[1-6])\b/g, `$1${RANGE}$2`)
    .replace(/\bWISE (\d{4})-(\d{4})\b/g, "WISE $1−$2")
    .replace(/(?<![\d.\-])(\d{1,3}(?:\.\d+)?)-(\d{1,3}(?:\.\d+)?)(?![\d.])/g, `$1${RANGE}$2`);
}

/** A full sentence: a first a–z letter up (never σ → Σ), a full stop at the end unless it has one. */
export function sentence(s: string): string {
  const t = s.trim();
  if (!t) return t;
  const up = /^[a-z]/.test(t) ? t[0].toUpperCase() + t.slice(1) : t;
  return /[.!?]$/.test(up) ? up : `${up}.`;
}

/**
 * The note without its "Rows: key, key, …" list (P1 names the row keys there for the record;
 * the table's headers say the same). Empty → null.
 */
export function noteText(note: string | null | undefined): string | null {
  if (!note) return null;
  const t = note
    .replace(/(^|\s)rows:\s[^.]*(?:\.(?=\s|$)|$)/i, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
  return t || null;
}

const WORDS: Record<string, string> = {
  pa: "PA",
  dt: "Δt",
  sigma: "σ",
  nmad: "NMAD",
  snr: "S/N",
  ra: "RA",
  dec: "Dec",
  vmag: "V",
  pv: "pV",
  d: "D",
  ref: "ref.",
  "3sigma": "3σ",
};

const SUFFIX: [RegExp, string][] = [
  [/_arcsec_yr$/, "″/yr"],
  [/_mas_yr$/, "mas/yr"],
  [/_arcsec$/, "″"],
  [/_deg$/, "°"],
  [/_ujy$/, "µJy"],
  [/_um$/, "µm"],
  [/_px$/, "px"],
  [/_pct$/, "%"],
  [/_yr$/, "yr"],
  [/_km$/i, "km"],
  [/_s$/, "s"],
];

/** snake_case key → a readable header with its unit: "median_offset_px" → "Median offset (px)". */
export function keyLabel(key: string): string {
  if (key === "slug") return "Target";
  let k = key;
  let unit = "";
  for (const [re, u] of SUFFIX) {
    if (re.test(k)) {
      k = k.replace(re, "");
      unit = u;
      break;
    }
  }
  let label = k
    .split("_")
    .filter(Boolean)
    .map((w) => WORDS[w.toLowerCase()] ?? w)
    .join(" ");
  if (/^[a-z]/.test(label)) label = label[0].toUpperCase() + label.slice(1);
  return unit ? `${label} (${unit})` : label;
}

const UNITS: Record<string, string> = {
  arcsec: "″",
  "arcsec/yr": "″/yr",
  deg: "°",
  ujy: "µJy",
  um: "µm",
  "dz/(1+z)": "Δz/(1+z)",
};

/** The large figure of a check: the number, and the word unit that goes beside it (may be ""). */
export interface BigValue {
  num: string;
  unit: string;
}

/**
 * The large figure for a published value. A count reads "5 of 5" when the headline gives the
 * total; a recall reads as a percentage, a ratio as "1.01×"; symbols (″, °) sit on the number.
 */
export function bigValue(value: number | null, unit: string | null, headline = ""): BigValue | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const raw = (unit ?? "").trim();
  const lu = raw.toLowerCase();
  if (Number.isInteger(value) && value >= 0) {
    const m = new RegExp(`(?<![\\d.,])${value} of (\\d[\\d,]*)(?![\\d.])`).exec(headline);
    if (m) return { num: `${value} of ${m[1]}`, unit: raw };
  }
  if ((lu === "recall" || lu === "fraction") && value >= 0 && value <= 1) {
    const pct = value * 100;
    return { num: `${pct < 10 ? pct.toFixed(1) : Math.round(pct)}%`, unit: lu === "recall" ? "recall" : "" };
  }
  if (lu === "ratio") return { num: `${formatNumber(value)}×`, unit: "" };
  const u = UNITS[lu] ?? raw;
  if (/^[″°%]/.test(u)) return { num: `${formatNumber(value)}${u}`, unit: "" };
  return { num: formatNumber(value), unit: u };
}

/** Measured value with its unit as one string: "10.21″/yr", "4 targets", "—". */
export function formatValue(value: number | null, unit: string | null): string {
  const b = bigValue(value, unit);
  return b ? (b.unit ? `${b.num} ${b.unit}` : b.num) : "—";
}

/** "2026-11-14T09:30:00Z" → "14 Nov 2026, 09:30 UTC"; "2026-11-14" → "14 Nov 2026". */
export function formatGenerated(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso.trim())) return date;
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return `${date}, ${time} UTC`;
}

// ---- suspects ----------------------------------------------------------------------------

export const EVIDENCE: Record<Suspect["evidence"], string> = {
  two_exposures: "Moved between two exposures of one session",
  two_detectors: "Two dots in detectors exposed at the same moment",
};

export function evidenceText(e: string): string {
  return EVIDENCE[e as Suspect["evidence"]] ?? e.replace(/_/g, " ");
}

export function formatPos(s: Pick<Suspect, "ra" | "dec">): string {
  return formatRaDec(s);
}

export function formatDecimalPos(s: Pick<Suspect, "ra" | "dec">): string {
  return `${s.ra.toFixed(5)} ${s.dec >= 0 ? "+" : "−"}${Math.abs(s.dec).toFixed(5)}`;
}

export function suspectDate(mjd: number): string {
  return formatMjd(mjd, true);
}

/** The Explore (blink) page centred on a suspect, on the Hours clock, 20′ field, its own detector. */
export function blinkHref(s: Pick<Suspect, "ra" | "dec" | "det">): string {
  const p = new URLSearchParams({
    ra: s.ra.toFixed(5),
    dec: s.dec.toFixed(5),
    clock: "hours",
    fov: "20",
    det: String(s.det),
  });
  return `?${p}#/explore`;
}

/** The suspect's candidate page: suspects and mover candidates share the id (/data/candidates.json). */
export function candidateHref(id: string): string {
  return `#/c/${encodeURIComponent(id)}`;
}

// ---- CSV ---------------------------------------------------------------------------------

/**
 * One CSV field (RFC 4180): quoted when it holds a comma, quote, CR/LF or edge spaces; quotes
 * doubled. Text starting with = + @ or a tab is prefixed with ' so spreadsheets don't run it.
 */
export function csvField(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
  let s = typeof v === "string" ? v : typeof v === "boolean" ? String(v) : JSON.stringify(v);
  if (/^[=+@\t\r]/.test(s) || (/^-/.test(s) && !/^-\d/.test(s))) s = `'${s}`;
  return /[",\r\n]|^\s|\s$/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export const SUSPECT_COLUMNS = [
  "id",
  "ra",
  "dec",
  "mjd",
  "date_utc",
  "clock",
  "det",
  "evidence",
  "n_dots",
  "snr",
  "flux_ujy",
  "cell",
  "nearest_known_arcsec",
] as const;

export function suspectsCsv(suspects: Suspect[]): string {
  const lines = [SUSPECT_COLUMNS.join(",")];
  for (const s of suspects) {
    const rec: Record<string, unknown> = {
      ...s,
      date_utc: Number.isFinite(s.mjd) ? new Date((s.mjd - 40587) * 86400000).toISOString() : null,
    };
    lines.push(SUSPECT_COLUMNS.map((k) => csvField(rec[k])).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

/** Client-side download through a Blob URL. */
export function downloadText(text: string, filename: string, type = "text/csv;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
