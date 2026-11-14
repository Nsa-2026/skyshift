// Small table exporters for the Tools page: CSV (RFC 4180) and VOTable 1.4 (TABLEDATA).
// src/assist has its own toCSV/toVOTable in progress; these are to be deduplicated later.
// Column names are the contract's field names (contracts/api.md), so a file round-trips to JSON.
import type { Candidate, CandidatesResponse, CandidateKind } from "../api/types";

/** VOTable datatypes we write. */
export type VoType = "char" | "boolean" | "short" | "int" | "long" | "double";

export interface Column<T> {
  name: string;
  datatype: VoType;
  get(row: T): unknown;
  /** VOUnit, e.g. "deg", "arcsec", "mas/yr", "uJy", "d". */
  unit?: string;
  ucd?: string;
  description?: string;
  /** RA/Dec columns point at the ICRS COOSYS. */
  icrs?: boolean;
}

// ---- CSV ---------------------------------------------------------------------------------

/**
 * One CSV field (RFC 4180). Quoted when it holds a comma, quote, CR or LF, or starts or ends
 * with a space; quotes doubled. Text starting with = @ a tab, or a + or - not followed by only
 * digits, dots and colons (+17:00:00.0 stays as it is), gets a leading ' so spreadsheets don't
 * run it as a formula. Lists are joined with ";".
 */
export function csvField(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
  if (typeof v === "boolean") return v ? "true" : "false";
  let s = Array.isArray(v) ? v.map((x) => (x == null ? "" : String(x))).join(";") : String(v);
  if (/^[=@\t\r]/.test(s) || /^[+-](?![\d.:]+$)/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) || /^\s|\s$/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Rows to CSV text: a header of column names, CRLF line ends (RFC 4180), no BOM. */
export function toCSV<T>(rows: readonly T[], cols: readonly Column<T>[]): string {
  const lines = [cols.map((c) => csvField(c.name)).join(",")];
  for (const r of rows) lines.push(cols.map((c) => csvField(c.get(r))).join(","));
  return lines.join("\r\n") + "\r\n";
}

// ---- VOTable -----------------------------------------------------------------------------

/** Text safe inside XML 1.0 content and attributes: escaped, characters XML forbids removed. */
export function xmlText(s: string): string {
  return s
    .replace(/[^\t\n\r -퟿-�\u{10000}-\u{10FFFF}]/gu, "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

const INTS: VoType[] = ["short", "int", "long"];

/** One TD's content. Empty means null (VOTable 1.3+), for every datatype. */
export function voCell(v: unknown, datatype: VoType): string {
  if (v === null || v === undefined) return "";
  if (datatype === "boolean") return v === true ? "T" : v === false ? "F" : "";
  if (datatype === "double") return typeof v === "number" && Number.isFinite(v) ? String(v) : "";
  if (INTS.includes(datatype)) return typeof v === "number" && Number.isInteger(v) ? String(v) : "";
  const s = Array.isArray(v) ? v.map((x) => (x == null ? "" : String(x))).join(";") : String(v);
  return xmlText(s);
}

export interface VoMeta {
  /** TABLE name, e.g. "candidates". */
  table: string;
  description?: string;
  /** RESOURCE INFO elements, e.g. { data_release: "DR1+QR3", generated: "…" }. Nulls are left out. */
  info?: Record<string, string | number | null | undefined>;
}

const attr = (name: string, v: string | undefined) => (v ? ` ${name}="${xmlText(v)}"` : "");

/** Rows to a VOTable 1.4 document (one RESOURCE, one TABLE, TABLEDATA), ICRS positions. */
export function toVOTable<T>(rows: readonly T[], cols: readonly Column<T>[], meta: VoMeta): string {
  const out: string[] = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3"' +
      ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' +
      ' xsi:schemaLocation="http://www.ivoa.net/xml/VOTable/v1.3 http://www.ivoa.net/xml/VOTable/v1.3">',
    '<RESOURCE type="results">',
  ];
  if (meta.description) out.push(`<DESCRIPTION>${xmlText(meta.description)}</DESCRIPTION>`);
  for (const [k, v] of Object.entries(meta.info ?? {})) {
    if (v === null || v === undefined) continue;
    out.push(`<INFO name="${xmlText(k)}" value="${xmlText(String(v))}"/>`);
  }
  const icrs = cols.some((c) => c.icrs);
  if (icrs) out.push('<COOSYS ID="icrs" system="ICRS"/>');
  out.push(`<TABLE name="${xmlText(meta.table)}" nrows="${rows.length}">`);
  for (const c of cols) {
    const head =
      `<FIELD name="${xmlText(c.name)}" datatype="${c.datatype}"` +
      (c.datatype === "char" ? ' arraysize="*"' : "") +
      attr("unit", c.unit) +
      attr("ucd", c.ucd) +
      (c.icrs ? ' ref="icrs"' : "");
    out.push(c.description ? `${head}><DESCRIPTION>${xmlText(c.description)}</DESCRIPTION></FIELD>` : `${head}/>`);
  }
  out.push("<DATA><TABLEDATA>");
  for (const r of rows) {
    out.push(`<TR>${cols.map((c) => `<TD>${voCell(c.get(r), c.datatype)}</TD>`).join("")}</TR>`);
  }
  out.push("</TABLEDATA></DATA>", "</TABLE>", "</RESOURCE>", "</VOTABLE>");
  return out.join("\n") + "\n";
}

// ---- Candidates (contracts/api.md, candidates.json; type Candidate) ------------------------

export const KIND_NAMES: Record<CandidateKind, { one: string; many: string }> = {
  mover: { one: "fast mover", many: "fast movers" },
  high_pm: { one: "moving star", many: "moving stars" },
  spectral: { one: "odd spectrum", many: "odd spectra" },
};

/** Every candidate field, in the JSON's order, plus page_url (the candidate's SkyShift page). */
export function candidateColumns(siteBase: string): Column<Candidate>[] {
  const page = (c: Candidate) => new URL(`#/c/${encodeURIComponent(c.id)}`, siteBase).href;
  return [
    { name: "id", datatype: "char", get: (c) => c.id, ucd: "meta.id;meta.main", description: "Candidate id (opaque)" },
    { name: "kind", datatype: "char", get: (c) => c.kind, ucd: "meta.code.class", description: "mover: moves within hours; high_pm: moves between visits; spectral: odd spectrum" },
    { name: "ra", datatype: "double", get: (c) => c.ra, unit: "deg", ucd: "pos.eq.ra;meta.main", icrs: true, description: "Right ascension, ICRS" },
    { name: "dec", datatype: "double", get: (c) => c.dec, unit: "deg", ucd: "pos.eq.dec;meta.main", icrs: true, description: "Declination, ICRS" },
    { name: "mjd", datatype: "double", get: (c) => c.mjd, unit: "d", ucd: "time.epoch", description: "MJD (UTC) of the frame that shows it best" },
    { name: "clock", datatype: "char", get: (c) => c.clock, description: "Blink clock: months (visits) or hours (exposures)" },
    { name: "det", datatype: "short", get: (c) => c.det, description: "SPHEREx detector 1–6 (D1 0.75–1.1 µm … D6 4.4–5.0 µm)" },
    { name: "zone", datatype: "char", get: (c) => c.zone, description: "Pipeline zone" },
    { name: "score", datatype: "double", get: (c) => c.score, description: "Pipeline promise score 0-1, or null" },
    { name: "snr", datatype: "double", get: (c) => c.snr, ucd: "stat.snr", description: "Signal-to-noise ratio" },
    { name: "flux_ujy", datatype: "double", get: (c) => c.flux_ujy, unit: "uJy", ucd: "phot.flux.density", description: "Brightness" },
    { name: "rate_arcsec_h", datatype: "double", get: (c) => c.rate_arcsec_h, unit: "arcsec/h", ucd: "pos.pm", description: "Movers: apparent rate" },
    { name: "pm_mas_yr", datatype: "double", get: (c) => c.pm_mas_yr, unit: "mas/yr", ucd: "pos.pm", description: "Moving stars: total proper motion" },
    { name: "pm_err_mas_yr", datatype: "double", get: (c) => c.pm_err_mas_yr, unit: "mas/yr", ucd: "stat.error;pos.pm", description: "1 sigma error of pm_mas_yr" },
    { name: "pa_deg", datatype: "double", get: (c) => c.pa_deg, unit: "deg", ucd: "pos.posAng", description: "Direction of motion, east of north" },
    { name: "nearest_known_arcsec", datatype: "double", get: (c) => c.nearest_known_arcsec, unit: "arcsec", ucd: "pos.angDistance", description: "Nearest known solar-system object at that time, or null" },
    { name: "gaia_match", datatype: "boolean", get: (c) => c.gaia_match, description: "A Gaia DR3 source sits here" },
    { name: "catwise_match", datatype: "boolean", get: (c) => c.catwise_match, description: "A CatWISE2020 source sits here" },
    { name: "galactic_b", datatype: "double", get: (c) => c.galactic_b, unit: "deg", ucd: "pos.galactic.lat", description: "Galactic latitude" },
    { name: "labels", datatype: "char", get: (c) => c.labels, description: "Filter labels, separated by ;" },
    { name: "reasons", datatype: "char", get: (c) => c.reasons, description: "Pipeline reason codes, separated by ;" },
    { name: "flags", datatype: "char", get: (c) => c.flags, ucd: "meta.code.qual", description: "Artefact cautions near it, separated by ;" },
    { name: "why", datatype: "char", get: (c) => c.why, ucd: "meta.note", description: "Why it is on the list, in one sentence" },
    { name: "frames_url", datatype: "char", get: (c) => c.frames_url, ucd: "meta.ref.url", description: "Static blink frames (frames.json shape), or null: the live API is used" },
    { name: "page_url", datatype: "char", get: page, ucd: "meta.ref.url", description: "This candidate's SkyShift page" },
  ];
}

/** The list limited to one kind (or all), with counts that match what is in it. */
export function candidateSubset(data: CandidatesResponse, kind: CandidateKind | "all"): CandidatesResponse {
  const candidates = kind === "all" ? data.candidates : data.candidates.filter((c) => c.kind === kind);
  const counts = { mover: 0, high_pm: 0, spectral: 0 } as Record<CandidateKind, number>;
  for (const c of candidates) if (c.kind in counts) counts[c.kind] += 1;
  return { ...data, counts, candidates };
}

export function candidatesCSV(data: CandidatesResponse, siteBase: string): string {
  return toCSV(data.candidates, candidateColumns(siteBase));
}

export function candidatesVOTable(data: CandidatesResponse, siteBase: string): string {
  return toVOTable(data.candidates, candidateColumns(siteBase), {
    table: "candidates",
    description:
      "SkyShift candidates for community review: possible new movers, moving stars and odd spectra in NASA SPHEREx images.",
    info: {
      data_release: data.data_release,
      generated: data.generated,
      zones: (data.zones ?? []).join(";"),
      source: new URL("data/candidates.json", siteBase).href,
    },
  });
}

export function candidatesJSON(data: CandidatesResponse): string {
  return JSON.stringify(data, null, 1) + "\n";
}

/** File name stem: skyshift-candidates-2026-11-14[-fast-movers]. */
export function candidatesFileStem(data: CandidatesResponse, kind: CandidateKind | "all"): string {
  const day = /^\d{4}-\d{2}-\d{2}/.exec(data.generated ?? "")?.[0];
  const k = kind === "all" ? "" : `-${KIND_NAMES[kind].many.replace(/ /g, "-")}`;
  return `skyshift-candidates${day ? `-${day}` : ""}${k}`;
}
