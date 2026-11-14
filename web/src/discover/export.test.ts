import { describe, expect, it } from "vitest";
import type { Candidate } from "../api/types";
import { CSV_COLUMNS, candidateUrl, csvCell, toCsv, toJson } from "./export";
import { toRows } from "./filters";
import { arcsecPerYr, flux, fromSlider, rangeText, rangeValue, roundNice, toSlider } from "./format";
import { RANGES } from "./filters";

const cand: Candidate = {
  id: "pilot-pm-0001", kind: "high_pm", ra: 123.6418631245, dec: 15.0158986, mjd: 61061.198341626696, clock: "months", det: 2,
  zone: "pilot", score: 0.11275, snr: null, flux_ujy: null, rate_arcsec_h: null, pm_mas_yr: 501.44, pm_err_mas_yr: 56.54,
  pa_deg: 150.8, nearest_known_arcsec: null, gaia_match: true, catwise_match: false, galactic_b: 25.033,
  labels: ["red", "ch4"], reasons: ["gaia_pm_disagrees"], flags: [],
  why: 'Moves 0.50″ per year, but "Gaia" lists a different motion, here.', frames_url: null,
};

/** Split one CSV line into its cells (RFC 4180 quotes). */
function cells(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') (cur += '"'), i++;
      else if (ch === '"') quoted = false;
      else cur += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") out.push(cur), (cur = "");
    else cur += ch;
  }
  out.push(cur);
  return out;
}

describe("CSV cells", () => {
  it("quotes commas, quotes and line breaks (RFC 4180)", () => {
    expect(csvCell("plain")).toBe("plain");
    expect(csvCell("a,b")).toBe('"a,b"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell("two\nlines")).toBe('"two\nlines"');
  });

  it("defuses spreadsheet formulas in text but leaves numbers alone", () => {
    expect(csvCell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvCell("+1")).toBe("'+1");
    expect(csvCell("-cmd")).toBe("'-cmd");
    expect(csvCell("@x")).toBe("'@x");
    expect(csvCell(-12.5)).toBe("-12.5");
  });

  it("writes empty cells for missing values and words for booleans", () => {
    expect(csvCell(null)).toBe("");
    expect(csvCell(undefined)).toBe("");
    expect(csvCell(NaN)).toBe("");
    expect(csvCell(true)).toBe("true");
    expect(csvCell(false)).toBe("false");
  });
});

describe("exports", () => {
  const rows = toRows([cand], new Map([["pilot-pm-0001", { mover: 5, changing: 0, junk: 1, unsure: 0 }]]));

  it("writes a header and one escaped line per candidate", () => {
    const csv = toCsv(rows, "https://skyshift.web.app");
    const lines = csv.trimEnd().split("\r\n");
    expect(lines[0]).toBe(CSV_COLUMNS.join(","));
    expect(lines).toHaveLength(2);
    expect(lines[1].startsWith("pilot-pm-0001,high_pm,123.641863,15.015899,61061.198342,")).toBe(true);
    expect(lines[1]).toContain(",red;ch4,,gaia_pm_disagrees,,"); // labels, grade (none: not a mover), reasons, flags
    expect(lines[1]).toContain('"Moves 0.50″ per year, but ""Gaia"" lists a different motion, here."');
    expect(lines[1].endsWith(",6,5,0,1,0,mover,https://skyshift.web.app/#/c/pilot-pm-0001")).toBe(true);
    expect(csv.endsWith("\r\n")).toBe(true);
  });

  it("writes JSON with the candidate's own fields, its votes and how to get the same list again", () => {
    const j = JSON.parse(toJson(rows, { generated: "g", data_release: "DR1+QR3", query_url: "u", exported: "e" }));
    expect(j.count).toBe(1);
    expect(j.query_url).toBe("u");
    expect(j.candidates[0].id).toBe("pilot-pm-0001");
    expect(j.candidates[0].pm_mas_yr).toBe(501.44);
    expect(j.candidates[0].votes).toEqual({ mover: 5, changing: 0, junk: 1, unsure: 0, total: 6, verdict: "mover" });
  });

  it("writes each mover's vetting grade, and an empty grade for a file written before the grades", () => {
    const mover = (id: string, grade: Candidate["grade"], reasons: string[] = ["two_exposures"]): Candidate => ({
      ...cand, id, kind: "mover", clock: "hours", pm_mas_yr: null, pm_err_mas_yr: null, labels: [], reasons, grade,
    });
    const old: Partial<Candidate> = mover("old", null);
    delete old.grade; // an ungraded export has no `grade` key at all
    const graded = toRows([mover("b", "B"), mover("c", "C", ["two_exposures", "hot_pixel", "edge"]), mover("u", null), old as Candidate], new Map());
    const csv = toCsv(graded, "https://x").trimEnd().split("\r\n");
    const col = CSV_COLUMNS.indexOf("grade");
    expect(col).toBe(CSV_COLUMNS.indexOf("labels") + 1); // the contract's order: labels, grade, reasons
    expect(csv.slice(1).map((l) => cells(l)[col])).toEqual(["B", "C", "", ""]);
    expect(cells(csv[2])[col + 1]).toBe("two_exposures;hot_pixel;edge"); // a C keeps its failed checks in reasons
    const j = JSON.parse(toJson(graded, { generated: "g", data_release: "d", query_url: "u", exported: "e" }));
    expect(j.candidates.map((x: { grade: unknown }) => x.grade)).toEqual(["B", "C", null, null]);
  });

  it("links each candidate by its id", () => {
    expect(candidateUrl({ id: "a b/c" }, "https://x")).toBe("https://x/#/c/a%20b%2Fc");
  });
});

describe("numbers with units", () => {
  it("formats motions and brightness", () => {
    expect(arcsecPerYr(501.44)).toBe("0.50″/yr");
    expect(arcsecPerYr(180512)).toBe("181″/yr");
    expect(arcsecPerYr(13377)).toBe("13.4″/yr");
    expect(flux(2320.23)).toBe("2,320 µJy");
  });

  it("maps sliders linearly or logarithmically and back", () => {
    expect(toSlider(0.5, [0, 1], false)).toBe(500);
    expect(fromSlider(500, [0, 1], false)).toBe(0.5);
    expect(toSlider(1000, [100, 10000], true)).toBe(500);
    expect(fromSlider(500, [100, 10000], true)).toBeCloseTo(1000, 6);
    expect(toSlider(1e9, [100, 10000], true)).toBe(1000);
    expect(roundNice(1234.5, RANGES.flux)).toBe(1200);
    expect(roundNice(0.1234, RANGES.score)).toBe(0.12);
  });

  it("describes an active range in words", () => {
    expect(rangeText(RANGES.score, { min: 0.15, max: 1 })).toBe("0.15–1");
    expect(rangeText(RANGES.known, { min: 60, max: null })).toBe("≥ 60″");
    expect(rangeText(RANGES.flux, { min: null, max: 500 })).toBe("≤ 500 µJy");
    expect(rangeValue(RANGES.sig, 5.25)).toBe("5.3");
  });
});
