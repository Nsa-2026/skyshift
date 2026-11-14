// CSV and JSON of the filtered set: every field of the candidate plus the community's counts.
import type { Candidate } from "../api/types";
import { formatMjd } from "../app/astro";
import { gradeOf, type Row } from "./filters";

/** One CSV cell (RFC 4180), with spreadsheet formula injection defused for text. */
export function csvCell(v: string | number | boolean | null | undefined): string {
  if (v == null) return "";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
  let s = String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export const CSV_COLUMNS = [
  "id", "kind", "ra_deg", "dec_deg", "mjd", "date_utc", "clock", "det", "zone",
  "score", "snr", "flux_ujy", "rate_arcsec_h", "pm_mas_yr", "pm_err_mas_yr", "pa_deg",
  "nearest_known_arcsec", "gaia_match", "catwise_match", "galactic_b",
  "labels", "grade", "reasons", "flags", "why",
  "votes", "mover", "changing", "junk", "unsure", "verdict", "url",
] as const;

const round = (v: number | null, d: number) => (v == null ? null : +v.toFixed(d));

export function candidateUrl(c: Pick<Candidate, "id">, origin: string): string {
  return `${origin}/#/c/${encodeURIComponent(c.id)}`;
}

export function toCsv(rows: Row[], origin: string): string {
  const lines = [CSV_COLUMNS.join(",")];
  for (const { c, votes, total, verdict } of rows) {
    lines.push(
      [
        c.id, c.kind, round(c.ra, 6), round(c.dec, 6), round(c.mjd, 6), formatMjd(c.mjd, true), c.clock, c.det, c.zone,
        round(c.score, 4), round(c.snr, 2), round(c.flux_ujy, 1), round(c.rate_arcsec_h, 3), round(c.pm_mas_yr, 1),
        round(c.pm_err_mas_yr, 1), c.pa_deg, round(c.nearest_known_arcsec, 1), c.gaia_match, c.catwise_match, c.galactic_b,
        c.labels.join(";"), gradeOf(c), c.reasons.join(";"), c.flags.join(";"), c.why,
        total, votes.mover, votes.changing, votes.junk, votes.unsure, verdict ?? "", candidateUrl(c, origin),
      ]
        .map(csvCell)
        .join(","),
    );
  }
  return lines.join("\r\n") + "\r\n";
}

export interface ExportMeta {
  generated: string | null;
  data_release: string | null;
  /** The Discover URL that reproduces this set. */
  query_url: string;
  exported: string;
}

export function toJson(rows: Row[], meta: ExportMeta): string {
  return (
    JSON.stringify(
      {
        ...meta,
        count: rows.length,
        // grade: null when the file had none (exported before the vetting), as the contract writes it.
        candidates: rows.map(({ c, votes, total, verdict }) => ({ ...c, grade: gradeOf(c), votes: { ...votes, total, verdict } })),
      },
      null,
      1,
    ) + "\n"
  );
}

/** Hand the browser a file to save. */
export function download(name: string, text: string, type: string): void {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
