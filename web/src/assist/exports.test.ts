import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import type { Candidate } from "../api/types";
import type { Counts } from "../vote/model";
import { COLUMNS, VOTE_COLUMNS, cell, toCSV, toJSON, toVOTable, xmlEscape } from "./exports";

const all = fixture.candidates as unknown as Candidate[];
const mover = all.find((c) => c.id === "pilot-642020070317-87802695")!;
const star = all.find((c) => c.id === "pilot-pm-0000")!;
const BASE = "https://skyshift.web.app/";
const GEN = "2026-11-14T15:00:00.000Z";
const VOTES = new Map<string, Counts>([[mover.id, { mover: 6, changing: 0, junk: 1, unsure: 0 }]]);

/** Tricky text in every free-text field: quotes, commas, a line break, XML specials and a formula. */
const nasty: Candidate = {
  ...star,
  id: "x,1",
  dec: -7.245147,
  why: 'Says "hi", <b>&</b>\nthen =1+1',
  labels: ["red", "ch4"],
  reasons: ["bad_fit", "few_dots"],
  gaia_match: false,
  score: null,
};

/** Minimal RFC 4180 reader (quoted fields may hold commas, quotes and line breaks). */
function readCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let f = "";
  let q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') (f += '"'), i++;
      else if (ch === '"') q = false;
      else f += ch;
    } else if (ch === '"') q = true;
    else if (ch === ",") row.push(f), (f = "");
    else if (ch === "\r" && text[i + 1] === "\n") row.push(f), rows.push(row), (row = []), (f = ""), i++;
    else f += ch;
  }
  return rows;
}

describe("columns", () => {
  it("names, units and UCDs follow the contracts", () => {
    const by = Object.fromEntries(COLUMNS.map((c) => [c.name, c]));
    expect(COLUMNS.map((c) => c.name).slice(0, 4)).toEqual(["id", "kind", "ra", "dec"]);
    expect(by.ra).toMatchObject({ unit: "deg", ucd: "pos.eq.ra;meta.main", ref: "icrs", datatype: "double" });
    expect(by.dec).toMatchObject({ unit: "deg", ucd: "pos.eq.dec;meta.main", ref: "icrs" });
    expect(by.mjd).toMatchObject({ unit: "d", ucd: "time.epoch", ref: "utc" });
    expect(by.flux_ujy.unit).toBe("uJy");
    expect(by.um_min.unit).toBe("um");
    expect(by.pm_mas_yr.unit).toBe("mas/yr");
    expect(by.rate_arcsec_h.unit).toBe("arcsec/h");
    expect(by.nearest_known_arcsec.unit).toBe("arcsec");
    expect(by.pm_err_mas_yr.ucd).toBe("stat.error;pos.pm");
    for (const c of [...COLUMNS, ...VOTE_COLUMNS]) {
      expect(c.ucd, c.name).toMatch(/^[a-z]+(\.[a-zA-Z]+)*(;[a-z]+(\.[a-zA-Z]+)*)*$/);
      expect(c.name, c.name).toMatch(/^[a-z][a-z0-9_]*$/);
      if (c.unit) expect(c.unit, c.name).not.toMatch(/[µ″°]/); // VOUnits are ASCII
    }
  });
});

describe("CSV", () => {
  it("writes a header and one row per candidate with CRLF line ends", () => {
    const csv = toCSV([mover, star], { base: BASE });
    expect(csv.endsWith("\r\n")).toBe(true);
    const rows = readCsv(csv);
    expect(rows[0]).toEqual(COLUMNS.map((c) => c.name));
    expect(rows).toHaveLength(3);
    for (const r of rows) expect(r).toHaveLength(COLUMNS.length);
  });

  it("writes numbers in full, sexagesimal with its sign, booleans as words and nulls empty", () => {
    const [head, row] = readCsv(toCSV([mover], { base: BASE }));
    const v = Object.fromEntries(head.map((h, i) => [h, row[i]]));
    expect(Number(v.ra)).toBe(mover.ra);
    expect(v.ra_hms).toBe("08:10:13.00");
    expect(v.dec_dms).toBe("+17:00:12.9");
    expect(v.date_utc).toBe("2025-10-26T02:14:43.805Z");
    expect(v.um_min).toBe("1.6");
    expect(v.um_max).toBe("2.4");
    expect(v.gaia_match).toBe("false");
    expect(v.score).toBe("");
    expect(v.pm_mas_yr).toBe("");
    expect(v.reasons).toBe("two_exposures");
    expect(v.url).toBe(`${BASE}#/c/${mover.id}`);
  });

  it("quotes commas, quotes and line breaks, and defuses formulas", () => {
    const [head, row] = readCsv(toCSV([nasty], { base: BASE }));
    const v = Object.fromEntries(head.map((h, i) => [h, row[i]]));
    expect(v.id).toBe("x,1");
    expect(v.why).toBe('Says "hi", <b>&</b>\nthen =1+1');
    expect(v.dec_dms).toBe("-07:14:42.5");
    expect(v.labels).toBe("red;ch4");
    expect(v.reasons).toBe("bad_fit;few_dots");
    expect(cell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(cell("-1+2")).toBe("'-1+2");
    expect(cell("-07:14:42.5")).toBe("-07:14:42.5");
    expect(cell(true)).toBe("true");
    expect(cell(null)).toBe("");
    expect(cell(Number.NaN)).toBe("");
  });

  it("adds vote columns and the consensus when counts are given", () => {
    const rows = readCsv(toCSV([mover, star], { base: BASE, votes: VOTES }));
    expect(rows[0].slice(-6)).toEqual(["votes_mover", "votes_changing", "votes_junk", "votes_unsure", "votes_total", "consensus"]);
    expect(rows[1].slice(-6)).toEqual(["6", "0", "1", "0", "7", "mover"]);
    expect(rows[2].slice(-6)).toEqual(["0", "0", "0", "0", "0", ""]);
  });

  it("writes only the header for an empty list", () => {
    expect(toCSV([], { base: BASE })).toBe(COLUMNS.map((c) => c.name).join(",") + "\r\n");
  });
});

describe("VOTable 1.4", () => {
  const parse = (xml: string) => new DOMParser().parseFromString(xml, "application/xml");
  const NS = "http://www.ivoa.net/xml/VOTable/v1.3";

  it("is well-formed XML with the VOTable 1.4 root, namespace and schema", () => {
    const xml = toVOTable([mover, star, nasty], { base: BASE, generated: GEN, dataRelease: "DR1+QR3" });
    const doc = parse(xml);
    expect(doc.getElementsByTagName("parsererror")).toHaveLength(0);
    const root = doc.documentElement;
    expect(root.localName).toBe("VOTABLE");
    expect(root.namespaceURI).toBe(NS);
    expect(root.getAttribute("version")).toBe("1.4");
    expect(root.getAttribute("xsi:schemaLocation")).toContain("votable-1.4.xsd");
    expect(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true);
  });

  it("orders the elements as the schema wants and declares ICRS and UTC", () => {
    const doc = parse(toVOTable([mover], { base: BASE, generated: GEN, dataRelease: "DR1+QR3" }));
    const kids = [...doc.documentElement.children].map((e) => e.localName);
    expect(kids).toEqual(["DESCRIPTION", "COOSYS", "TIMESYS", "RESOURCE"]);
    expect(doc.querySelector("COOSYS")!.getAttribute("system")).toBe("ICRS");
    const ts = doc.querySelector("TIMESYS")!;
    expect([ts.getAttribute("timeorigin"), ts.getAttribute("timescale"), ts.getAttribute("refposition")]).toEqual(["MJD-origin", "UTC", "TOPOCENTER"]);
    const table = doc.querySelector("TABLE")!;
    expect([...table.children].map((e) => e.localName)).toEqual(["DESCRIPTION", ...COLUMNS.map(() => "FIELD"), "DATA"]);
    expect(table.getAttribute("nrows")).toBe("1");
    const infos = [...doc.querySelectorAll("RESOURCE > INFO")].map((e) => [e.getAttribute("name"), e.getAttribute("value")]);
    expect(infos).toEqual([["generated", GEN], ["data_release", "DR1+QR3"]]);
  });

  it("gives every FIELD its datatype, unit, UCD and references", () => {
    const doc = parse(toVOTable([mover], { base: BASE, generated: GEN, votes: VOTES }));
    const fields = [...doc.querySelectorAll("FIELD")];
    const cols = [...COLUMNS, ...VOTE_COLUMNS];
    expect(fields.map((f) => f.getAttribute("name"))).toEqual(cols.map((c) => c.name));
    fields.forEach((f, i) => {
      const c = cols[i];
      expect(f.getAttribute("datatype"), c.name).toBe(c.datatype);
      expect(f.getAttribute("ucd"), c.name).toBe(c.ucd);
      expect(f.getAttribute("unit"), c.name).toBe(c.unit ?? null);
      expect(f.getAttribute("arraysize"), c.name).toBe(c.datatype === "char" ? "*" : null);
      expect(f.querySelector("DESCRIPTION")!.textContent, c.name).toBe(c.description);
    });
    const ids = new Set([...doc.querySelectorAll("[ID]")].map((e) => e.getAttribute("ID")));
    for (const f of fields) if (f.getAttribute("ref")) expect(ids.has(f.getAttribute("ref"))).toBe(true);
    expect(doc.querySelector('FIELD[name="date_utc"]')!.getAttribute("xtype")).toBe("timestamp");
  });

  it("writes one TD per FIELD, T/F booleans, empty nulls and escaped text", () => {
    const doc = parse(toVOTable([mover, nasty], { base: BASE, generated: GEN }));
    const trs = [...doc.querySelectorAll("TR")];
    expect(trs).toHaveLength(2);
    for (const tr of trs) expect(tr.querySelectorAll("TD")).toHaveLength(COLUMNS.length);
    const td = (r: number, name: string) => trs[r].children[COLUMNS.findIndex((c) => c.name === name)].textContent;
    expect(td(0, "gaia_match")).toBe("F");
    expect(td(1, "catwise_match")).toBe("T");
    expect(td(0, "score")).toBe("");
    expect(Number(td(0, "dec"))).toBe(mover.dec);
    expect(td(0, "dec_dms")).toBe("+17:00:12.9");
    expect(td(1, "why")).toBe('Says "hi", <b>&</b>\nthen =1+1');
    expect(td(1, "id")).toBe("x,1");
  });

  it("escapes XML specials and drops characters XML 1.0 forbids", () => {
    expect(xmlEscape(`a<b>&"c'`)).toBe("a&lt;b&gt;&amp;&quot;c&apos;");
    expect(xmlEscape("ok\u0001\u0008\u000B\ttab\nline")).toBe("ok\ttab\nline");
    const doc = parse(toVOTable([{ ...mover, why: "bell\u0007 here" }], { base: BASE, generated: GEN }));
    expect(doc.getElementsByTagName("parsererror")).toHaveLength(0);
  });

  it("is still valid for an empty list", () => {
    const doc = parse(toVOTable([], { base: BASE, generated: GEN }));
    expect(doc.getElementsByTagName("parsererror")).toHaveLength(0);
    expect(doc.querySelectorAll("TR")).toHaveLength(0);
    expect(doc.querySelector("TABLE")!.getAttribute("nrows")).toBe("0");
  });

  it("parses for the whole pilot sample", () => {
    const doc = parse(toVOTable(all, { base: BASE, generated: GEN }));
    expect(doc.getElementsByTagName("parsererror")).toHaveLength(0);
    expect(doc.querySelectorAll("TR")).toHaveLength(all.length);
  });
});

describe("JSON", () => {
  it("wraps the list with its time, release, count and a url per candidate", () => {
    const out = JSON.parse(toJSON([mover, star], { base: BASE, generated: GEN, dataRelease: "DR1+QR3" }));
    expect(out.generated).toBe(GEN);
    expect(out.data_release).toBe("DR1+QR3");
    expect(out.count).toBe(2);
    expect(out.candidates[0].id).toBe(mover.id);
    expect(out.candidates[0].ra).toBe(mover.ra);
    expect(out.candidates[0].url).toBe(`${BASE}#/c/${mover.id}`);
    expect(JSON.parse(toJSON([], { base: BASE })).data_release).toBeNull();
  });

  it("adds counts and consensus when votes are given", () => {
    const out = JSON.parse(toJSON([mover, star], { base: BASE, generated: GEN, votes: VOTES }));
    expect(out.candidates[0].votes).toEqual({ mover: 6, changing: 0, junk: 1, unsure: 0 });
    expect(out.candidates[0].consensus).toBe("mover");
    expect(out.candidates[1].votes).toEqual({ mover: 0, changing: 0, junk: 0, unsure: 0 });
    expect(out.candidates[1].consensus).toBeNull();
  });
});
