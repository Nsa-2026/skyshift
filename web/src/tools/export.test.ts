import { describe, expect, it } from "vitest";
import type { CandidatesResponse } from "../api/types";
import candidatesFx from "../../../fixtures/api/candidates.json";
import {
  type Column,
  candidateColumns,
  candidateSubset,
  candidatesCSV,
  candidatesFileStem,
  candidatesJSON,
  candidatesVOTable,
  csvField,
  toCSV,
  toVOTable,
  voCell,
  xmlText,
} from "./export";

const FIXTURE = candidatesFx as unknown as CandidatesResponse;
const SITE = "https://skyshift.web.app/";

/** A tiny RFC 4180 reader, to check that what we write reads back. */
function readCSV(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cur = "";
  let q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') (cur += '"'), i++;
      else if (ch === '"') q = false;
      else cur += ch;
    } else if (ch === '"') q = true;
    else if (ch === ",") row.push(cur), (cur = "");
    else if (ch === "\r" && text[i + 1] === "\n") {
      row.push(cur), rows.push(row), (row = []), (cur = ""), i++;
    } else cur += ch;
  }
  if (cur || row.length) row.push(cur), rows.push(row);
  return rows;
}

describe("csvField", () => {
  it("writes numbers, booleans and nulls plainly", () => {
    expect(csvField(133.795)).toBe("133.795");
    expect(csvField(-7.245)).toBe("-7.245");
    expect(csvField(NaN)).toBe("");
    expect(csvField(null)).toBe("");
    expect(csvField(undefined)).toBe("");
    expect(csvField(true)).toBe("true");
  });
  it("quotes commas, quotes, line breaks and edge spaces", () => {
    expect(csvField("a,b")).toBe('"a,b"');
    expect(csvField('say "hi"')).toBe('"say ""hi"""');
    expect(csvField("two\nlines")).toBe('"two\nlines"');
    expect(csvField(" pad")).toBe('" pad"');
    expect(csvField("plain")).toBe("plain");
  });
  it("defuses spreadsheet formulas but keeps negative numbers as text", () => {
    expect(csvField("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvField("+SUM(A1)")).toBe("'+SUM(A1)");
    expect(csvField("@sum")).toBe("'@sum");
    expect(csvField("-cmd")).toBe("'-cmd");
    expect(csvField("-7.245")).toBe("-7.245");
    expect(csvField("+17:00:00.0")).toBe("+17:00:00.0");
    expect(csvField("-07:14:42.5")).toBe("-07:14:42.5");
  });
  it("joins lists with semicolons", () => {
    expect(csvField(["red", "ch4"])).toBe("red;ch4");
    expect(csvField([])).toBe("");
  });
});

describe("toCSV", () => {
  const cols: Column<{ a: string; b: number | null }>[] = [
    { name: "a", datatype: "char", get: (r) => r.a },
    { name: "b", datatype: "double", get: (r) => r.b },
  ];
  it("writes a header and CRLF rows that read back", () => {
    const text = toCSV([{ a: 'x, "y"', b: 1.5 }, { a: "z", b: null }], cols);
    expect(text.endsWith("\r\n")).toBe(true);
    expect(readCSV(text)).toEqual([
      ["a", "b"],
      ['x, "y"', "1.5"],
      ["z", ""],
    ]);
  });
});

describe("VOTable", () => {
  it("escapes XML and drops characters XML forbids", () => {
    expect(xmlText(`a<b & "c" 'd'>`)).toBe("a&lt;b &amp; &quot;c&quot; &apos;d&apos;&gt;");
    expect(xmlText("bell\u0007 ok")).toBe("bell ok");
    expect(xmlText("µm ″ −")).toBe("µm ″ −");
  });
  it("writes null cells empty for every datatype", () => {
    expect(voCell(null, "double")).toBe("");
    expect(voCell(NaN, "double")).toBe("");
    expect(voCell(null, "boolean")).toBe("");
    expect(voCell(true, "boolean")).toBe("T");
    expect(voCell(false, "boolean")).toBe("F");
    expect(voCell(2, "short")).toBe("2");
    expect(voCell(2.5, "int")).toBe("");
  });
  it("is well-formed VOTable 1.4 with fields, units and ICRS", () => {
    const xml = candidatesVOTable(FIXTURE, SITE);
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    expect(doc.getElementsByTagName("parsererror").length).toBe(0);
    const root = doc.documentElement;
    expect(root.localName).toBe("VOTABLE");
    expect(root.getAttribute("version")).toBe("1.4");
    expect(root.namespaceURI).toBe("http://www.ivoa.net/xml/VOTable/v1.3");
    const fields = [...doc.getElementsByTagName("FIELD")];
    const names = fields.map((f) => f.getAttribute("name"));
    expect(names.slice(0, 4)).toEqual(["id", "kind", "ra", "dec"]);
    const ra = fields[2];
    expect(ra.getAttribute("unit")).toBe("deg");
    expect(ra.getAttribute("ucd")).toBe("pos.eq.ra;meta.main");
    expect(ra.getAttribute("ref")).toBe("icrs");
    expect(doc.getElementsByTagName("COOSYS")[0].getAttribute("system")).toBe("ICRS");
    const table = doc.getElementsByTagName("TABLE")[0];
    expect(table.getAttribute("nrows")).toBe(String(FIXTURE.candidates.length));
    const trs = doc.getElementsByTagName("TR");
    expect(trs.length).toBe(FIXTURE.candidates.length);
    const tds = trs[0].getElementsByTagName("TD");
    expect(tds.length).toBe(fields.length);
    expect(tds[0].textContent).toBe(FIXTURE.candidates[0].id);
    expect(Number(tds[2].textContent)).toBeCloseTo(FIXTURE.candidates[0].ra, 10);
    const info = [...doc.getElementsByTagName("INFO")].find((i) => i.getAttribute("name") === "data_release");
    expect(info?.getAttribute("value")).toBe(FIXTURE.data_release);
  });
  it("stays well-formed with hostile text", () => {
    const xml = toVOTable(
      [{ s: `</TD><TR> & ]]> "q" \u0000` }],
      [{ name: 'we"ird<name>', datatype: "char", get: (r: { s: string }) => r.s, description: "a & b" }],
      { table: "t&t", info: { note: "<x>" } },
    );
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    expect(doc.getElementsByTagName("parsererror").length).toBe(0);
    expect(doc.getElementsByTagName("TD")[0].textContent).toBe(`</TD><TR> & ]]> "q" `);
    expect(doc.getElementsByTagName("FIELD")[0].getAttribute("name")).toBe('we"ird<name>');
  });
});

describe("candidate exports", () => {
  it("CSV has every field, a page link, and one row per candidate", () => {
    const rows = readCSV(candidatesCSV(FIXTURE, SITE));
    expect(rows[0]).toEqual(candidateColumns(SITE).map((c) => c.name));
    expect(rows.length).toBe(FIXTURE.candidates.length + 1);
    const c0 = FIXTURE.candidates[0];
    const r0 = Object.fromEntries(rows[0].map((k, i) => [k, rows[1][i]]));
    expect(r0.id).toBe(c0.id);
    expect(Number(r0.ra)).toBe(c0.ra);
    expect(r0.gaia_match).toBe(String(c0.gaia_match));
    expect(r0.reasons).toBe(c0.reasons.join(";"));
    expect(r0.page_url).toBe(`https://skyshift.web.app/#/c/${encodeURIComponent(c0.id)}`);
    expect(r0.score).toBe("");
  });
  it("subsets by kind with matching counts, and names files by date and kind", () => {
    const movers = candidateSubset(FIXTURE, "mover");
    expect(movers.candidates.every((c) => c.kind === "mover")).toBe(true);
    expect(movers.counts).toEqual({ mover: movers.candidates.length, high_pm: 0, spectral: 0, shape: 0 });
    expect(candidateSubset(FIXTURE, "all").candidates.length).toBe(FIXTURE.candidates.length);
    expect(candidatesFileStem(FIXTURE, "all")).toBe("skyshift-candidates-2026-11-14");
    expect(candidatesFileStem(FIXTURE, "high_pm")).toBe("skyshift-candidates-2026-11-14-moving-stars");
  });
  it("JSON round-trips", () => {
    expect(JSON.parse(candidatesJSON(FIXTURE))).toEqual(FIXTURE);
  });
});
