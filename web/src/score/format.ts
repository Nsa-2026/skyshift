// Formatting for the scoreboard: table cells, suspects, CSV. Pure functions, tested.
import type { Suspect } from "../api/types";
import { formatMjd, formatRaDec } from "../app/astro";

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
  if (Number.isInteger(n)) return String(n);
  const a = Math.abs(n);
  const digits = a >= 1000 ? 2 : a >= 100 ? 2 : a >= 1 ? 3 : 4;
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

/** snake_case key → readable header, with common unit suffixes as symbols. */
export function keyLabel(key: string): string {
  return key
    .replace(/_arcsec_yr$/, " ″/yr")
    .replace(/_arcsec$/, " ″")
    .replace(/_deg$/, " °")
    .replace(/_ujy$/, " µJy")
    .replace(/_um$/, " µm")
    .replace(/_/g, " ");
}

const UNITS: Record<string, string> = {
  arcsec: "″",
  "arcsec/yr": "″/yr",
  deg: "°",
  ujy: "µJy",
  um: "µm",
  ratio: "×",
  "dz/(1+z)": "Δz/(1+z)",
};

/** Measured value with its unit: "10.21″/yr", "1.056×", "4 targets", "—". */
export function formatValue(value: number | null, unit: string | null): string {
  if (value === null || value === undefined) return "—";
  const u = unit ? UNITS[unit.toLowerCase()] ?? unit : "";
  if (!u) return formatNumber(value);
  return /^[″°×]/.test(u) ? `${formatNumber(value)}${u}` : `${formatNumber(value)} ${u}`;
}

/** "2026-11-14T09:30:00Z" → "14 Nov 2026, 09:30 UTC". Unparseable input is returned as is. */
export function formatGenerated(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return `${date}, ${time} UTC`;
}

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

/** The blink page centred on a suspect, on the Hours clock, 20′ field, its own detector. */
export function blinkHref(s: Pick<Suspect, "ra" | "dec" | "det">): string {
  const p = new URLSearchParams({
    ra: s.ra.toFixed(5),
    dec: s.dec.toFixed(5),
    clock: "hours",
    fov: "20",
    det: String(s.det),
  });
  return `?${p}#/`;
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
