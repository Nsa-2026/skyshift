import { describe, expect, it } from "vitest";
import {
  BATCH_LIMIT,
  batchCSV,
  blinkHref,
  explainLine,
  parseBatch,
  rowStatus,
  sexagesimal,
  skyHref,
  splitFields,
} from "./batch";

const one = (line: string) => parseBatch(line).rows[0];

describe("parseBatch: formats", () => {
  it("reads every form the coordinate box reads", () => {
    const r = parseBatch(
      [
        "08h55m10.831s -7d14m42.53s",
        "08:55:10.831 -07:14:42.53",
        "8 55 10.831 -7 14 42.53",
        "133.795129 -7.245147",
        "133.795129, -7.245147",
        "133.795129 −7.245147",
      ].join("\n"),
    );
    expect(r.rows).toHaveLength(6);
    for (const row of r.rows) {
      expect(row.error).toBeNull();
      expect(row.pos!.ra).toBeCloseTo(133.795129, 5);
      expect(row.pos!.dec).toBeCloseTo(-7.245147, 5);
    }
    expect(r.rows.map((x) => x.format)).toEqual(["sexagesimal", "sexagesimal", "sexagesimal", "decimal", "decimal", "decimal"]);
  });

  it("reads name, ra, dec (and ra, dec, name)", () => {
    const a = one("WISE 0855, 133.7597, -7.2422");
    expect(a.name).toBe("WISE 0855");
    expect(a.pos).toEqual({ ra: 133.7597, dec: -7.2422 });
    const b = one('"Barnard\'s Star, GJ 699", 17:57:48.5, +04:41:36');
    expect(b.name).toBe("Barnard's Star, GJ 699");
    expect(b.pos!.ra).toBeCloseTo(269.452083, 5);
    const c = one("133.7597, -7.2422, WISE 0855");
    expect(c.name).toBe("WISE 0855");
    const d = one("Eps Indi B\t331.09668\t-56.80067");
    expect(d.name).toBe("Eps Indi B");
    expect(d.pos).toEqual({ ra: 331.09668, dec: -56.80067 });
  });

  it("reads a name and coordinates separated only by spaces", () => {
    const a = one("WISE 0855 133.7597 -7.2422");
    expect(a.name).toBe("WISE 0855");
    expect(a.pos).toEqual({ ra: 133.7597, dec: -7.2422 });
    const b = one("08 55 10.8 -07 14 42 W0855");
    expect(b.name).toBe("W0855");
    expect(b.pos!.dec).toBeCloseTo(-7.245, 3);
  });

  it("uses a CSV header's ra and dec columns, in any order", () => {
    const r = parseBatch(
      ["id,kind,rate_arcsec_h,Dec (deg),RA (deg)", "a1,mover,,17.0036,122.5542", "a2,high_pm,,-91,10"].join("\n"),
    );
    expect(r.header).toBe("id,kind,rate_arcsec_h,Dec (deg),RA (deg)");
    expect(r.rows).toHaveLength(2);
    expect(r.rows[0].name).toBe("a1");
    expect(r.rows[0].pos).toEqual({ ra: 122.5542, dec: 17.0036 });
    expect(r.rows[0].line).toBe(2);
    expect(r.rows[1].error).toBe("Dec must be between −90° and +90°.");
  });

  it("reads our own candidates CSV back (ra/dec columns, id as the name)", () => {
    const r = parseBatch("id,kind,ra,dec,mjd\r\npilot-1,mover,122.55415,17.00357,60974.09\r\n");
    expect(r.rows[0]).toMatchObject({ name: "pilot-1", pos: { ra: 122.55415, dec: 17.00357 } });
  });

  it("skips blank lines, comments and a byte-order mark; keeps line numbers", () => {
    const r = parseBatch("﻿# my list\n\n269.4462 4.76883\n// note\n   \n10 20\n");
    expect(r.rows.map((x) => x.line)).toEqual([3, 6]);
  });
});

describe("parseBatch: errors and limits", () => {
  it("explains lines it can't read", () => {
    expect(one("Barnard's Star").error).toMatch(/^No numbers here/);
    expect(one("400 10").error).toBe("RA must be from 0° up to 360°.");
    expect(one("10 95").error).toBe("Dec must be between −90° and +90°.");
    expect(one("25:00:00 +10:00:00").error).toBe("RA hours must be under 24.");
    expect(one("133.79").error).toMatch(/^Only one number/);
    expect(one("133.79 -7.2 5").error).toBeNull(); // read as ra dec + name "5"
    expect(explainLine("1 2 3")).toMatch(/^Three numbers/);
    expect(one("abc 12x 7y").error).toMatch(/^Couldn't read this/);
  });

  it("keeps rows that can't be read in the table, with no position", () => {
    const r = parseBatch("269.4462 4.76883\nnonsense\n10 20");
    expect(r.rows).toHaveLength(3);
    expect(r.rows[1]).toMatchObject({ line: 2, pos: null, format: null });
  });

  it(`reads at most ${BATCH_LIMIT} positions and counts the rest`, () => {
    const lines = Array.from({ length: 237 }, (_, i) => `${(i * 1.3).toFixed(2)} ${(i % 80) - 40}`);
    const r = parseBatch(["# header comment", ...lines].join("\n"));
    expect(r.rows).toHaveLength(200);
    expect(r.dropped).toBe(37);
    expect(r.rows[199].line).toBe(201);
    expect(parseBatch("1 2\n3 4\n5 6", 2).dropped).toBe(1);
  });

  it("marks repeats within 1″ of an earlier line", () => {
    const r = parseBatch("133.7951292 -7.2451472\n08h55m10.831s -7d14m42.53s\n133.8 -7.2");
    expect(r.rows[1].dupOf).toBe(1);
    expect(r.rows[2].dupOf).toBeNull();
    expect(rowStatus(r.rows[1])).toBe("Same spot as line 1.");
  });
});

describe("helpers", () => {
  it("splits quoted CSV fields", () => {
    expect(splitFields('"a, b",c,"d ""e"""', ",")).toEqual(["a, b", "c", 'd "e"']);
    expect(splitFields(" x ; y ", ";")).toEqual(["x", "y"]);
  });

  it("writes sexagesimal with the rounding carried", () => {
    expect(sexagesimal({ ra: 133.795129, dec: -7.245147 })).toEqual({ ra: "08h55m10.83s", dec: "−07°14′42.5″" });
    expect(sexagesimal({ ra: 359.99999999, dec: 89.99999999 }, "ascii")).toEqual({ ra: "00:00:00.00", dec: "+90:00:00.0" });
    expect(sexagesimal({ ra: 15 * (59.9999 / 3600), dec: -0.00001 }, "ascii")).toEqual({ ra: "00:01:00.00", dec: "+00:00:00.0" });
    expect(sexagesimal({ ra: 0, dec: -0.5 }, "ascii").dec).toBe("-00:30:00.0");
  });

  it("links to the blink (static for featured fields) and the sky", () => {
    expect(blinkHref({ ra: 120, dec: 17 })).toBe("?ra=120.00000&dec=17.00000&clock=months&fov=5&det=2#/explore");
    expect(blinkHref({ ra: 269.4462, dec: 4.76883 })).toBe("?t=barnard#/explore");
    expect(skyHref({ ra: 120, dec: -17.5 })).toBe("#/sky/120.00000,-17.50000,0.5");
    expect(rowStatus(one("269.4462 4.76883"))).toBe("Inside the field of Barnard's Star: frames are ready.");
  });

  it("exports the table as CSV with absolute links", () => {
    const r = parseBatch("Here, 120, 17\nbad line");
    const csv = batchCSV(r.rows, "https://skyshift.web.app/#/tools", (row) => (row.line === 1 ? "3 visits" : null));
    const lines = csv.trim().split("\r\n");
    expect(lines[0]).toBe("line,name,ra_deg,dec_deg,ra_hms,dec_dms,format,status,coverage,input,blink_url,sky_url");
    expect(lines[1]).toContain("1,Here,120,17,08:00:00.00,+17:00:00.0,decimal,Ready to blink.,3 visits");
    expect(lines[1]).toContain("https://skyshift.web.app/?ra=120.00000&dec=17.00000&clock=months&fov=5&det=2#/explore");
    expect(lines[1]).toContain("https://skyshift.web.app/#/sky/120.00000,17.00000,0.5");
    expect(lines[2].startsWith('2,,,,,,,"No numbers here. Add RA and Dec, e.g. Barnard\'s Star, 269.4462')).toBe(true);
    expect(lines[2].endsWith(",bad line,,")).toBe(true);
  });
});
