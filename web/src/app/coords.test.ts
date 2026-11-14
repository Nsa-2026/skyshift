import { describe, expect, it } from "vitest";
import { parseCoords } from "./coords";
import { pixelToSky, skyToPixel, gaiaAt, sepArcsec } from "./astro";

describe("parseCoords", () => {
  it("reads the challenge's own string", () => {
    const p = parseCoords("08h55m10.831s -7d14m42.53s")!;
    expect(p.ra).toBeCloseTo(133.795129, 5);
    expect(p.dec).toBeCloseTo(-7.245147, 5);
  });
  it("reads colon, space and decimal forms", () => {
    expect(parseCoords("08:55:10.831 -07:14:42.53")!.ra).toBeCloseTo(133.795129, 5);
    expect(parseCoords("8 55 10.831 -7 14 42.53")!.dec).toBeCloseTo(-7.245147, 5);
    expect(parseCoords("269.4462, 4.76883")).toEqual({ ra: 269.4462, dec: 4.76883 });
    expect(parseCoords("269.4462 −4.76883")).toEqual({ ra: 269.4462, dec: -4.76883 });
  });
  it("keeps the sign of −0° declinations", () => {
    expect(parseCoords("12h00m00s -0d30m00s")!.dec).toBeCloseTo(-0.5, 6);
  });
  it("rejects names and nonsense", () => {
    expect(parseCoords("Barnard's Star")).toBeNull();
    expect(parseCoords("400 10")).toBeNull();
    expect(parseCoords("")).toBeNull();
  });
});

describe("astro", () => {
  const frame = { pixel_arcsec: 2.05, size: [146, 146] as [number, number] };
  const c = { ra: 269.4462, dec: 4.76883 };
  it("puts the centre at the middle pixel and round-trips", () => {
    expect(skyToPixel(c, frame, c)).toEqual({ x: 72.5, y: 72.5 });
    const p = pixelToSky({ x: 10, y: 120 }, frame, c);
    const back = skyToPixel(p, frame, c);
    expect(back.x).toBeCloseTo(10, 6);
    expect(back.y).toBeCloseTo(120, 6);
  });
  it("draws north up and east left", () => {
    const north = skyToPixel({ ra: c.ra, dec: c.dec + 10 / 3600 }, frame, c);
    expect(north.y).toBeLessThan(72.5);
    const east = skyToPixel({ ra: c.ra + 10 / 3600, dec: c.dec }, frame, c);
    expect(east.x).toBeLessThan(72.5);
  });
  it("moves Barnard's Star about 10.4″ a year", () => {
    const star = { id: "1", ra: c.ra, dec: c.dec, pmra: -801.551, pmdec: 10362.394, parallax: null, g: null };
    expect(sepArcsec(gaiaAt(star, 57388.5), gaiaAt(star, 57388.5 + 365.25))).toBeCloseTo(10.39, 1);
  });
});
