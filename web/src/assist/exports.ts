// Downloads of any list of candidates, shared by the Tools page and the candidate page: CSV,
// VOTable 1.4 (with UCDs and units, opens in TOPCAT and Aladin) and JSON. One column list drives
// CSV and VOTable so names, units and order always agree. Units as the contracts: degrees, MJD
// (UTC), µm, µJy, arcsec, mas/yr.
import type { Candidate } from "../api/types";
import { DETECTORS } from "../app/astro";
import { consensus, csvCell, emptyCounts, total, type Counts } from "../vote/model";
import { formatDec, formatRa, mjdToIso } from "./format";
import { permalink, siteBase } from "./reports";

export interface ExportOptions {
  /** Vote counts by candidate id: adds votes_* and consensus columns. */
  votes?: Map<string, Counts> | null;
  /** Site address for the url column. Default: this page, else the public site. */
  base?: string;
  /** ISO time the list was made (JSON and VOTable header). Default: now. */
  generated?: string;
  /** Collections searched, e.g. "DR1+QR3". */
  dataRelease?: string;
}

type Datatype = "char" | "double" | "float" | "short" | "int" | "boolean";
type Cell = string | number | boolean | null;

export interface Column {
  name: string;
  datatype: Datatype;
  unit?: string;
  ucd: string;
  description: string;
  /** VOTable: COOSYS or TIMESYS this column refers to. */
  ref?: "icrs" | "utc";
  xtype?: string;
  get(c: Candidate, ctx: { base: string; votes: Counts | null }): Cell;
}

const num = (x: number | null | undefined): number | null => (typeof x === "number" && Number.isFinite(x) ? x : null);

/** The columns of every export, in order. */
export const COLUMNS: Column[] = [
  { name: "id", datatype: "char", ucd: "meta.id;meta.main", description: "Candidate id (opaque)", get: (c) => c.id },
  { name: "kind", datatype: "char", ucd: "meta.code.class", description: "mover (moves within hours), high_pm (moves between visits) or spectral", get: (c) => c.kind },
  { name: "ra", datatype: "double", unit: "deg", ucd: "pos.eq.ra;meta.main", ref: "icrs", description: "Right ascension, ICRS, at mjd", get: (c) => num(c.ra) },
  { name: "dec", datatype: "double", unit: "deg", ucd: "pos.eq.dec;meta.main", ref: "icrs", description: "Declination, ICRS, at mjd", get: (c) => num(c.dec) },
  { name: "ra_hms", datatype: "char", ucd: "pos.eq.ra", description: "Right ascension, sexagesimal hours", get: (c) => formatRa(c.ra, 2, "colon") },
  { name: "dec_dms", datatype: "char", ucd: "pos.eq.dec", description: "Declination, sexagesimal degrees", get: (c) => formatDec(c.dec, 1, "colon") },
  { name: "mjd", datatype: "double", unit: "d", ucd: "time.epoch", ref: "utc", description: "Time of the frame that shows it best, MJD (UTC)", get: (c) => num(c.mjd) },
  { name: "date_utc", datatype: "char", ucd: "time.epoch", xtype: "timestamp", description: "The same time, ISO 8601 UTC", get: (c) => (num(c.mjd) == null ? null : mjdToIso(c.mjd, 3)) },
  { name: "clock", datatype: "char", ucd: "meta.code", description: "Clock that shows it: hours (exposures) or months (visits)", get: (c) => c.clock },
  { name: "det", datatype: "short", ucd: "meta.id;instr.det", description: "SPHEREx detector 1-6 of its best detection", get: (c) => c.det },
  { name: "um_min", datatype: "float", unit: "um", ucd: "em.wl;stat.min", description: "Short end of that detector's wavelength range", get: (c) => DETECTORS[c.det]?.min ?? null },
  { name: "um_max", datatype: "float", unit: "um", ucd: "em.wl;stat.max", description: "Long end of that detector's wavelength range", get: (c) => DETECTORS[c.det]?.max ?? null },
  { name: "zone", datatype: "char", ucd: "meta.dataset", description: "Pipeline zone it came from", get: (c) => c.zone },
  { name: "score", datatype: "float", ucd: "meta.code.qual", description: "Pipeline promise score 0-1 (higher is more promising)", get: (c) => num(c.score) },
  { name: "snr", datatype: "float", ucd: "stat.snr", description: "Signal-to-noise of its best detection", get: (c) => num(c.snr) },
  { name: "flux_ujy", datatype: "float", unit: "uJy", ucd: "phot.flux.density", description: "Brightness in that detector", get: (c) => num(c.flux_ujy) },
  { name: "rate_arcsec_h", datatype: "float", unit: "arcsec/h", ucd: "pos.pm", description: "Movers: apparent rate", get: (c) => num(c.rate_arcsec_h) },
  { name: "pm_mas_yr", datatype: "float", unit: "mas/yr", ucd: "pos.pm", description: "Moving stars: total proper motion", get: (c) => num(c.pm_mas_yr) },
  { name: "pm_err_mas_yr", datatype: "float", unit: "mas/yr", ucd: "stat.error;pos.pm", description: "1 sigma error of pm_mas_yr", get: (c) => num(c.pm_err_mas_yr) },
  { name: "pa_deg", datatype: "float", unit: "deg", ucd: "pos.posAng", description: "Direction of motion, east of north", get: (c) => num(c.pa_deg) },
  { name: "nearest_known_arcsec", datatype: "float", unit: "arcsec", ucd: "pos.angDistance", description: "Distance to the nearest known solar-system object at that time", get: (c) => num(c.nearest_known_arcsec) },
  { name: "gaia_match", datatype: "boolean", ucd: "meta.code", description: "A Gaia DR3 source sits here", get: (c) => c.gaia_match },
  { name: "catwise_match", datatype: "boolean", ucd: "meta.code", description: "A CatWISE2020 source sits here", get: (c) => c.catwise_match },
  { name: "galactic_b", datatype: "double", unit: "deg", ucd: "pos.galactic.lat", description: "Galactic latitude", get: (c) => num(c.galactic_b) },
  { name: "labels", datatype: "char", ucd: "src.class", description: "Colour and spectrum labels, separated by semicolons", get: (c) => (c.labels ?? []).join(";") },
  { name: "reasons", datatype: "char", ucd: "meta.code", description: "Pipeline reason codes, separated by semicolons", get: (c) => (c.reasons ?? []).join(";") },
  { name: "flags", datatype: "char", ucd: "meta.code.qual", description: "Artefact flags near it, separated by semicolons", get: (c) => (c.flags ?? []).join(";") },
  { name: "why", datatype: "char", ucd: "meta.note", description: "Why it is on the list, in one sentence", get: (c) => c.why },
  { name: "url", datatype: "char", ucd: "meta.ref.url", description: "SkyShift page of this candidate", get: (c, x) => permalink(c, x.base) },
];

/** Extra columns when vote counts are given. */
export const VOTE_COLUMNS: Column[] = [
  ...(["mover", "changing", "junk", "unsure"] as const).map(
    (a): Column => ({ name: `votes_${a}`, datatype: "int", ucd: "meta.number", description: `Votes for ${a}`, get: (_c, x) => (x.votes ?? emptyCounts())[a] }),
  ),
  { name: "votes_total", datatype: "int", ucd: "meta.number", description: "All votes", get: (_c, x) => total(x.votes ?? emptyCounts()) },
  {
    name: "consensus",
    datatype: "char",
    ucd: "meta.code",
    description: "Answer with at least 5 votes and 70% agreement, else empty",
    get: (_c, x) => consensus(x.votes ?? emptyCounts())?.answer ?? null,
  },
];

function columns(opts: ExportOptions): Column[] {
  return opts.votes ? [...COLUMNS, ...VOTE_COLUMNS] : COLUMNS;
}

function rows(cands: Candidate[], opts: ExportOptions): { cols: Column[]; cells: Cell[][] } {
  const base = opts.base ?? siteBase();
  const cols = columns(opts);
  const cells = cands.map((c) => {
    const ctx = { base, votes: opts.votes ? opts.votes.get(c.id) ?? emptyCounts() : null };
    return cols.map((col) => col.get(c, ctx));
  });
  return { cols, cells };
}

/** Sexagesimal coordinates ("+17:00:12.9") are data, not formulas: keep their sign. */
const SEXAGESIMAL = /^[+-]?\d{2}:\d{2}:\d{2}(\.\d+)?$/;

/** One CSV cell: RFC 4180 quoting, formula injection defused (src/vote/model.ts csvCell). */
export function cell(v: Cell): string {
  if (typeof v === "boolean") return String(v);
  if (typeof v === "string" && SEXAGESIMAL.test(v)) return v;
  return csvCell(v);
}

/** RFC 4180 CSV with a header row, CRLF line ends. Units are those of the VOTable columns (COLUMNS). */
export function toCSV(cands: Candidate[], opts: ExportOptions = {}): string {
  const { cols, cells } = rows(cands, opts);
  const head = cols.map((c) => c.name).join(",");
  const body = cells.map((r) => r.map(cell).join(","));
  return [head, ...body].join("\r\n") + "\r\n";
}

/** XML text and attribute escaping; drops characters XML 1.0 forbids. */
export function xmlEscape(s: string): string {
  return s
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F￾￿]/g, "")
    .replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[ch]!);
}

function tdValue(v: Cell, col: Column): string {
  if (v == null) return "";
  if (col.datatype === "boolean") return v ? "T" : "F";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
  return xmlEscape(String(v));
}

/**
 * VOTable 1.4 (IVOA), TABLEDATA serialisation: one FIELD per column with datatype, unit (VOUnits)
 * and UCD; positions refer to an ICRS COOSYS, times to a UTC TIMESYS. Missing values are empty cells.
 */
export function toVOTable(cands: Candidate[], opts: ExportOptions = {}): string {
  const { cols, cells } = rows(cands, opts);
  const generated = opts.generated ?? new Date().toISOString();
  const attr = (k: string, v: string | undefined) => (v == null ? "" : ` ${k}="${xmlEscape(v)}"`);
  const fields = cols.map(
    (c) =>
      `      <FIELD${attr("name", c.name)}${attr("ID", `col_${c.name}`)}${attr("datatype", c.datatype)}${c.datatype === "char" ? ' arraysize="*"' : ""}${attr("unit", c.unit)}${attr("ucd", c.ucd)}${attr("ref", c.ref)}${attr("xtype", c.xtype)}>\n        <DESCRIPTION>${xmlEscape(c.description)}</DESCRIPTION>\n      </FIELD>`,
  );
  const trs = cells.map((r) => `        <TR>${r.map((v, i) => `<TD>${tdValue(v, cols[i])}</TD>`).join("")}</TR>`);
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.ivoa.net/xml/VOTable/v1.3 http://www.ivoa.net/xml/VOTable/votable-1.4.xsd">',
    "  <DESCRIPTION>SkyShift candidates from NASA SPHEREx images (NASA/IPAC IRSA). Positions ICRS degrees, times MJD (UTC).</DESCRIPTION>",
    '  <COOSYS ID="icrs" system="ICRS"/>',
    '  <TIMESYS ID="utc" timeorigin="MJD-origin" timescale="UTC" refposition="TOPOCENTER"/>',
    '  <RESOURCE type="results" name="skyshift">',
    `    <INFO name="generated" value="${xmlEscape(generated)}"/>`,
    ...(opts.dataRelease ? [`    <INFO name="data_release" value="${xmlEscape(opts.dataRelease)}"/>`] : []),
    `    <TABLE name="candidates" nrows="${cells.length}">`,
    "      <DESCRIPTION>One row per candidate. Votes and consensus are a snapshot at the generated time.</DESCRIPTION>",
    ...fields,
    "      <DATA>",
    "      <TABLEDATA>",
    ...trs,
    "      </TABLEDATA>",
    "      </DATA>",
    "    </TABLE>",
    "  </RESOURCE>",
    "</VOTABLE>",
  ].join("\n") + "\n";
}

/** JSON: { generated, data_release, count, candidates } with each candidate's url (and votes when given). */
export function toJSON(cands: Candidate[], opts: ExportOptions = {}): string {
  const base = opts.base ?? siteBase();
  const out = {
    generated: opts.generated ?? new Date().toISOString(),
    data_release: opts.dataRelease ?? null,
    count: cands.length,
    candidates: cands.map((c) => {
      const row: Record<string, unknown> = { ...c, url: permalink(c, base) };
      if (opts.votes) {
        const v = opts.votes.get(c.id) ?? emptyCounts();
        row.votes = v;
        row.consensus = consensus(v)?.answer ?? null;
      }
      return row;
    }),
  };
  return JSON.stringify(out, null, 1) + "\n";
}
