// Planets on the plate: the cheap ecliptic check, and Saturn's place against a real photo.
import * as A from "astronomy-engine";
import { describe, expect, it } from "vitest";
import { pixelToSky, sepArcsec, skyToPixel } from "../app/astro";
import { eclipticLatitude, mjdToDate, nearEcliptic, planetSpots, planetsNear, separation } from "./planets";

describe("near the ecliptic", () => {
  it("knows the ecliptic's latitude: 0 at the equinox, ±90° at its poles", () => {
    expect(eclipticLatitude({ ra: 0, dec: 0 })).toBeCloseTo(0, 6);
    expect(eclipticLatitude({ ra: 270, dec: 66.5607 })).toBeCloseTo(90, 2);
    expect(eclipticLatitude({ ra: 90, dec: -66.5607 })).toBeCloseTo(-90, 2);
    // The summer solstice point lies on the ecliptic.
    expect(eclipticLatitude({ ra: 90, dec: 23.4393 })).toBeCloseTo(0, 4);
  });

  it("loads no ephemeris far from the ecliptic", async () => {
    expect(nearEcliptic({ ra: 356.38, dec: -4.14 }, 0.2)).toBe(true); // the ecl35 candidates, β ≈ −2°
    expect(nearEcliptic({ ra: 269.45, dec: 4.69 }, 0.2)).toBe(false); // Barnard's Star, β ≈ 28°
    expect(nearEcliptic({ ra: 246.0, dec: 66.8 }, 0.2)).toBe(false); // R Draconis, β ≈ 84°
    expect(await planetsNear({ ra: 246.0, dec: 66.8 }, 0.2, 61000)).toEqual([]);
  });

  it("converts MJD to a date", () => {
    expect(mjdToDate(40587).toISOString()).toBe("1970-01-01T00:00:00.000Z");
    expect(mjdToDate(61021.5).toISOString()).toBe("2025-12-12T12:00:00.000Z");
  });

  it("measures separations in degrees", () => {
    expect(separation({ ra: 10, dec: 0 }, { ra: 11, dec: 0 })).toBeCloseTo(1, 9);
    expect(separation({ ra: 0, dec: 89 }, { ra: 180, dec: 89 })).toBeCloseTo(2, 9);
  });
});

describe("Saturn on a real photo", () => {
  // Candidate ecl35-515160213269-87870596 (its folder left public/data with the movers near a planet,
  // c884cb9; frames.json in git at f253977): three D2 exposures, 195 × 195 px of 6.15″ centred on
  // (356.37841, −4.139208). Saturn's burnt disc: centroid (137.3, 94.0) px, the same within 0.5 px in
  // all three (raw-0 core and >10 MJy/sr plateau alike); this place fell on a raw-0 pixel in each.
  const frame = { pixel_arcsec: 6.15, size: [195, 195] as [number, number] };
  const centre = { ra: 356.37841, dec: -4.139208 };
  const disc = pixelToSky({ x: 137.3, y: 94.0 }, frame, centre);
  const mjds = [61021.246276, 61021.247768, 61021.249261];

  it("puts Saturn on its disc: under 2 px from its centroid, inside its burnt core", () => {
    for (const mjd of mjds) {
      const s = planetSpots(A, mjd).find((p) => p.name === "Saturn")!;
      // Measured 8.5″ (1.4 px): the core is about 16 px wide.
      expect(sepArcsec(s, disc)).toBeLessThan(2 * 6.15);
      const px = skyToPixel(s, frame, centre);
      expect(Math.hypot(px.x - 137.3, px.y - 94.0)).toBeLessThan(2);
    }
  });

  it("finds it near the field, and only it", async () => {
    const near = await planetsNear(centre, 0.3, 61021.246276);
    expect(near.map((p) => p.name)).toEqual(["Saturn"]);
  });
});
