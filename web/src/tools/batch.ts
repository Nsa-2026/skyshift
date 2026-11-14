// Batch coordinates: read up to 200 positions from pasted text or a CSV file, all in the browser.
// Each line is one position, in any form parseCoords (src/app/coords.ts) reads, optionally with a
// name before or after it ("WISE 0855, 133.7597, -7.2422"). A CSV with a header row naming its
// ra and dec columns (ra, dec, ra_deg, RAJ2000, …) is read by those columns.
import type { SkyPos } from "../api/types";
import { sepArcsec } from "../app/astro";
import { parseCoords } from "../app/coords";
import { featuredNear } from "../app/state";
import { type Column, toCSV } from "./export";

export const BATCH_LIMIT = 200;

export interface BatchRow {
  /** 1-based line number in the pasted text or file. */
  line: number;
  /** The line as given (trimmed). */
  input: string;
  name: string | null;
  pos: SkyPos | null;
  /** How the position was written, or null when it couldn't be read. */
  format: "decimal" | "sexagesimal" | null;
  /** One plain sentence saying why the line couldn't be read, or null. */
  error: string | null;
  /** Line number of an earlier row within 1″ of this one, or null. */
  dupOf: number | null;
}

export interface BatchResult {
  rows: BatchRow[];
  /** Lines with content past the limit, left out. */
  dropped: number;
  /** The header line, when the text had one naming its ra and dec columns. */
  header: string | null;
}

const RA_NAMES = ["ra", "radeg", "ra_deg", "raj2000", "ra_j2000", "_raj2000", "ra_icrs", "ra_hms", "rahms", "alpha", "right_ascension"];
const DEC_NAMES = ["dec", "decdeg", "dec_deg", "dej2000", "decj2000", "dec_j2000", "_dej2000", "de_icrs", "dec_icrs", "dec_dms", "decdms", "delta", "de", "declination"];
const NAME_NAMES = ["name", "object", "target", "designation", "main_id", "source", "id", "source_id", "objname"];

/** "RA (deg)" → "ra", "RAJ2000" → "raj2000", "Dec [deg]" → "dec". */
function headerKey(s: string): string {
  return s
    .trim()
    .replace(/^["']|["']$/g, "")
    .replace(/[([].*?[)\]]/g, "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
}

/** The field separator a line uses: tab, comma or semicolon; null for spaces only. */
export function delimiterOf(line: string): string | null {
  if (line.includes("\t")) return "\t";
  if (line.includes(",")) return ",";
  if (line.includes(";")) return ";";
  return null;
}

/** Split one CSV line, honouring "quoted, fields" with "" for a quote. Fields are trimmed. */
export function splitFields(line: string, delim: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cur += ch;
    } else if (ch === '"' && cur.trim() === "") {
      quoted = true;
      cur = "";
    } else if (ch === delim) {
      out.push(cur.trim());
      cur = "";
    } else cur += ch;
  }
  out.push(cur.trim());
  return out;
}

function cleanName(s: string | undefined): string | null {
  const t = (s ?? "").trim().replace(/^["']|["']$/g, "").trim();
  return t ? t.slice(0, 80) : null;
}

function formatOf(coords: string): "decimal" | "sexagesimal" {
  const nums = coords.match(/\d+(?:\.\d*)?/g) ?? [];
  return nums.length > 2 || /\d\s*[hms:'′"″]/i.test(coords) ? "sexagesimal" : "decimal";
}

/** Why a line isn't a position, in one sentence that says what to do. */
export function explainLine(text: string): string {
  const s = text.replace(/−/g, "-");
  const nums = (s.match(/[+-]?\d+(?:\.\d*)?/g) ?? []).map(Number);
  if (nums.length === 0) return "No numbers here. Add RA and Dec, e.g. Barnard's Star, 269.4462, 4.76883.";
  // Sexagesimal marks count only right after a number ("Nowhere" has an h but isn't 0h).
  const sexa = /\d\s*[hms:'′"″]/i.test(s);
  if (!sexa && nums.length === 2) {
    const [ra, dec] = nums;
    if (ra < 0 || ra >= 360) return "RA must be from 0° up to 360°.";
    if (Math.abs(dec) > 90) return "Dec must be between −90° and +90°.";
  }
  const hours = /(\d+)\s*(?:h|:)/i.exec(s);
  if (hours && Number(hours[1]) >= 24) return "RA hours must be under 24.";
  if (nums.length === 1) return "Only one number. Give both RA and Dec.";
  if (!sexa && nums.length === 3) return "Three numbers: give RA and Dec as two decimals, or as h m s and d m s.";
  return "Couldn't read this. Use decimal degrees (133.795 −7.245) or sexagesimal (08h55m10.8s −07d14m42s, 08:55:10.8 −07:14:42).";
}

type Parsed = { pos: SkyPos; name: string | null; coords: string };

/** One line without a header: coordinates alone, or a name before or after them. */
function parseFree(line: string): Parsed | null {
  const whole = parseCoords(line);
  if (whole) return { pos: whole, name: null, coords: line };

  const delim = delimiterOf(line);
  if (delim) {
    const f = splitFields(line, delim).filter((x) => x !== "");
    if (f.length >= 2) {
      const after = f.slice(1).join(" ");
      const pa = parseCoords(after);
      if (pa) return { pos: pa, name: cleanName(f[0]), coords: after };
      const before = f.slice(0, -1).join(" ");
      const pb = parseCoords(before);
      if (pb) return { pos: pb, name: cleanName(f[f.length - 1]), coords: before };
    }
  }

  // Spaces only: the longest tail (or head) of words that reads as coordinates.
  const t = line.trim().split(/\s+/);
  for (let i = 1; i < t.length - 1; i++) {
    const coords = t.slice(i).join(" ");
    const p = parseCoords(coords);
    if (p) return { pos: p, name: cleanName(t.slice(0, i).join(" ")), coords };
  }
  for (let j = t.length - 1; j >= 2; j--) {
    const coords = t.slice(0, j).join(" ");
    const p = parseCoords(coords);
    if (p) return { pos: p, name: cleanName(t.slice(j).join(" ")), coords };
  }
  return null;
}

interface Header {
  delim: string | null;
  ra: number;
  dec: number;
  name: number;
}

function readHeader(line: string): Header | null {
  if (parseCoords(line)) return null;
  const delim = delimiterOf(line);
  const keys = (delim ? splitFields(line, delim) : line.trim().split(/\s+/)).map(headerKey);
  const ra = keys.findIndex((k) => RA_NAMES.includes(k));
  const dec = keys.findIndex((k) => DEC_NAMES.includes(k));
  if (ra < 0 || dec < 0 || ra === dec) return null;
  let name = -1;
  for (const n of NAME_NAMES) {
    name = keys.indexOf(n);
    if (name >= 0) break;
  }
  return { delim, ra, dec, name };
}

/** Read pasted text or a file's text. At most `limit` positions; blank and # lines are skipped. */
export function parseBatch(text: string, limit = BATCH_LIMIT): BatchResult {
  const lines = text.replace(/^﻿/, "").split(/\r\n|\r|\n/);
  const rows: BatchRow[] = [];
  let dropped = 0;
  let header: Header | null = null;
  let headerLine: string | null = null;
  let first = true;

  lines.forEach((raw, idx) => {
    const input = raw.trim();
    if (!input || /^(#|\/\/|%)/.test(input)) return;
    if (first) {
      first = false;
      header = readHeader(input);
      if (header) {
        headerLine = input;
        return;
      }
    }
    if (rows.length >= limit) {
      dropped++;
      return;
    }
    const line = idx + 1;
    let parsed: Parsed | null = null;
    let error: string | null = null;
    const h = header as Header | null;
    if (h) {
      const f = h.delim ? splitFields(input, h.delim) : input.split(/\s+/);
      const ra = f[h.ra] ?? "";
      const dec = f[h.dec] ?? "";
      if (!ra || !dec) error = "RA or Dec is missing in this row.";
      else {
        const coords = `${ra} ${dec}`;
        const pos = parseCoords(coords);
        if (pos) parsed = { pos, name: h.name >= 0 ? cleanName(f[h.name]) : null, coords };
        else error = explainLine(coords);
      }
    } else {
      parsed = parseFree(input);
      if (!parsed) error = explainLine(input);
    }
    rows.push({
      line,
      input,
      name: parsed?.name ?? null,
      pos: parsed?.pos ?? null,
      format: parsed ? formatOf(parsed.coords) : null,
      error,
      dupOf: null,
    });
  });

  // Repeats: a later row within 1″ of an earlier one points at it.
  const seen: BatchRow[] = [];
  for (const r of rows) {
    if (!r.pos) continue;
    const twin = seen.find((s) => sepArcsec(s.pos!, r.pos!) < 1);
    if (twin) r.dupOf = twin.line;
    else seen.push(r);
  }
  return { rows, dropped, header: headerLine };
}

// ---- formatting and links ------------------------------------------------------------------

/**
 * Sexagesimal with rounding carried properly: RA to 0.01 s, Dec to 0.1″.
 * unicode: 08h55m10.83s / −07°14′42.5″; ascii: 08:55:10.83 / -07:14:42.5.
 */
export function sexagesimal(pos: SkyPos, style: "unicode" | "ascii" = "unicode"): { ra: string; dec: string } {
  const p2 = (n: number) => String(n).padStart(2, "0");
  const raCs = Math.round(((((pos.ra % 360) + 360) % 360) / 15) * 360000) % (24 * 360000); // centiseconds of time
  const h = Math.floor(raCs / 360000);
  const m = Math.floor((raCs % 360000) / 6000);
  const s = (raCs % 6000) / 100;
  const decDs = Math.round(Math.abs(pos.dec) * 36000); // deci-arcseconds
  const d = Math.floor(decDs / 36000);
  const am = Math.floor((decDs % 36000) / 600);
  const as = (decDs % 600) / 10;
  const neg = pos.dec < 0 && decDs > 0;
  const sec = s.toFixed(2).padStart(5, "0");
  const asec = as.toFixed(1).padStart(4, "0");
  if (style === "ascii") {
    return { ra: `${p2(h)}:${p2(m)}:${sec}`, dec: `${neg ? "-" : "+"}${p2(d)}:${p2(am)}:${asec}` };
  }
  return { ra: `${p2(h)}h${p2(m)}m${sec}s`, dec: `${neg ? "−" : "+"}${p2(d)}°${p2(am)}′${asec}″` };
}

/** Decimal degrees for display: 133.79513 −7.24515 (5 decimals, 0.04″). */
export function decimalText(pos: SkyPos, digits = 5): { ra: string; dec: string } {
  return { ra: pos.ra.toFixed(digits), dec: `${pos.dec < 0 ? "−" : "+"}${Math.abs(pos.dec).toFixed(digits)}` };
}

/** Explore (blink) link: a featured target's static frames when the spot is inside one, else ?ra=&dec=. */
export function blinkHref(pos: SkyPos): string {
  const near = featuredNear(pos);
  if (near) return `?t=${encodeURIComponent(near.slug)}#/explore`;
  const p = new URLSearchParams({ ra: pos.ra.toFixed(5), dec: pos.dec.toFixed(5), clock: "months", fov: "5", det: "2" });
  return `?${p}#/explore`;
}

/** Sky (atlas) link, 0.5° across: #/sky/<ra>,<dec>,0.5 */
export function skyHref(pos: SkyPos): string {
  return `#/sky/${pos.ra.toFixed(5)},${pos.dec.toFixed(5)},0.5`;
}

/** The status sentence for a row (without a coverage check). */
export function rowStatus(r: BatchRow): string {
  if (r.error) return r.error;
  if (r.dupOf != null) return `Same spot as line ${r.dupOf}.`;
  const near = r.pos ? featuredNear(r.pos) : undefined;
  if (near) return `Inside the field of ${near.name}: frames are ready.`;
  return "Ready to blink.";
}

/** The table as CSV: one row per line read, with absolute Blink and Sky links. */
export function batchCSV(rows: readonly BatchRow[], siteBase: string, coverage?: (r: BatchRow) => string | null): string {
  const abs = (href: string) => new URL(href, siteBase).href;
  const cols: Column<BatchRow>[] = [
    { name: "line", datatype: "int", get: (r) => r.line },
    { name: "name", datatype: "char", get: (r) => r.name },
    { name: "ra_deg", datatype: "double", get: (r) => (r.pos ? Number(r.pos.ra.toFixed(7)) : null) },
    { name: "dec_deg", datatype: "double", get: (r) => (r.pos ? Number(r.pos.dec.toFixed(7)) : null) },
    { name: "ra_hms", datatype: "char", get: (r) => (r.pos ? sexagesimal(r.pos, "ascii").ra : null) },
    { name: "dec_dms", datatype: "char", get: (r) => (r.pos ? sexagesimal(r.pos, "ascii").dec : null) },
    { name: "format", datatype: "char", get: (r) => r.format },
    { name: "status", datatype: "char", get: (r) => rowStatus(r) },
    { name: "coverage", datatype: "char", get: (r) => coverage?.(r) ?? null },
    { name: "input", datatype: "char", get: (r) => r.input },
    { name: "blink_url", datatype: "char", get: (r) => (r.pos ? abs(blinkHref(r.pos)) : null) },
    { name: "sky_url", datatype: "char", get: (r) => (r.pos ? abs(skyHref(r.pos)) : null) },
  ];
  return toCSV(rows, cols);
}
