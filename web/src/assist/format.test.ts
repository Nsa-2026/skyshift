import { describe, expect, it } from "vitest";
import { arcsecText, degDec, degRa, formatDec, formatRa, grouped, mjdDate, mjdToIso, raw } from "./format";

describe("sexagesimal", () => {
  // The challenge's own string for WISE 0855−0714: 08h55m10.831s -7d14m42.53s.
  const ra = 15 * (8 + 55 / 60 + 10.831 / 3600);
  const dec = -(7 + 14 / 60 + 42.53 / 3600);

  it("round-trips the challenge's WISE 0855 position", () => {
    expect(formatRa(ra, 3)).toBe("08h55m10.831s");
    expect(formatDec(dec, 2)).toBe("-07°14′42.53″");
  });

  it("writes colon and space styles", () => {
    expect(formatRa(ra, 2, "colon")).toBe("08:55:10.83");
    expect(formatRa(ra, 2, "space")).toBe("08 55 10.83");
    expect(formatDec(dec, 1, "colon")).toBe("-07:14:42.5");
    expect(formatDec(17.5, 1, "space")).toBe("+17 30 00.0");
  });

  it("carries rounded seconds into minutes, hours and degrees, never printing 60", () => {
    expect(formatRa(15 * (59 / 60 + 59.9996 / 3600), 2)).toBe("01h00m00.00s");
    expect(formatRa(359.9999999, 2)).toBe("00h00m00.00s");
    expect(formatDec(10 + 59 / 60 + 59.96 / 3600, 1)).toBe("+11°00′00.0″");
    expect(formatDec(-(89 + 59 / 60 + 59.99 / 3600), 1)).toBe("-90°00′00.0″");
    for (let x = 0; x < 360; x += 7.77) expect(formatRa(x, 2)).not.toMatch(/60\.00s|m60|h60/);
  });

  it("wraps RA and keeps the sign of a declination that rounds to zero positive", () => {
    expect(formatRa(-15, 0)).toBe("23h00m00s");
    expect(formatRa(375, 1)).toBe("01h00m00.0s");
    expect(formatDec(-1e-9, 1)).toBe("+00°00′00.0″");
    expect(formatDec(-0.5, 0)).toBe("-00°30′00″");
  });

  it("formats decimal degrees with a signed declination", () => {
    expect(degRa(-0.5, 3)).toBe("359.500");
    expect(degDec(17.0035, 6)).toBe("+17.003500");
    expect(degDec(-7.245147, 6)).toBe("-7.245147");
    expect(degDec(-1e-9, 6)).toBe("+0.000000");
  });
});

describe("times", () => {
  it("turns MJD into ISO 8601 UTC with a Z", () => {
    expect(mjdToIso(60974.093562563, 3)).toBe("2025-10-26T02:14:43.805Z");
    expect(mjdToIso(51544.5, 0)).toBe("2000-01-01T12:00:00Z");
    expect(mjdToIso(51544.5, 6)).toBe("2000-01-01T12:00:00.000000Z");
  });

  it("rounds into the next day without printing 60 seconds", () => {
    const almost = 60000 + (86400 - 0.0004) / 86400;
    expect(mjdToIso(almost, 3)).toBe("2023-02-26T00:00:00.000Z");
  });

  it("caps the decimals at 6 (ADES)", () => {
    expect(mjdToIso(60000.123456789, 9)).toMatch(/\.\d{6}Z$/);
  });

  it("writes short dates", () => {
    expect(mjdDate(60974.093562563)).toBe("26 Oct 2025");
    expect(mjdDate(60974.093562563, true)).toBe("26 Oct 2025, 02:14 UTC");
  });
});

describe("numbers", () => {
  it("prints angles with sensible precision and a double prime", () => {
    expect(arcsecText(338.333)).toBe("338″");
    expect(arcsecText(12.34)).toBe("12.3″");
    expect(arcsecText(2.5)).toBe("2.5″");
    expect(arcsecText(6)).toBe("6″");
    expect(arcsecText(0.2)).toBe("0.20″");
    expect(arcsecText(0.0812)).toBe("0.08″");
  });

  it("groups thousands and spells out nulls in raw values", () => {
    expect(grouped(171694.5)).toBe("171,695");
    expect(raw(null)).toBe("null");
    expect(raw(Number.NaN)).toBe("null");
    expect(raw(2.5, 3)).toBe("2.5");
  });
});
