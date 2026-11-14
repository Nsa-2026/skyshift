// "Follow the mover": finding the fast mover and the per-epoch shifts (motion.ts).
import { describe, expect, it } from "vitest";
import type { FramesResponse, GaiaStar } from "../api/types";
import { gaiaAt, sepArcsec, skyToPixel } from "../app/astro";
import { MIN_DRIFT_PX, driftPx, findMover, followShifts, starThrough } from "./motion";

const centre = { ra: 269.4462, dec: 4.76883 };
const mid = 61000;
const barnard: GaiaStar = {
  ...starThrough(centre, mid, -802.8, 10362.5),
  id: "4472832130942575872",
  pmra: -802.8,
  pmdec: 10362.5,
  parallax: 546.98,
  g: 8.19,
};
const slow: GaiaStar = { id: "1", ra: centre.ra + 0.001, dec: centre.dec, pmra: 5, pmdec: -3, parallax: null, g: 12 };
const frame = (gaia: GaiaStar[]): Pick<FramesResponse, "gaia" | "center" | "pixel_arcsec" | "size"> => ({
  gaia,
  center: centre,
  pixel_arcsec: 2.05,
  size: [146, 146],
});

describe("finding the mover", () => {
  it("picks the fast Gaia star at the centre, with its rate in arcsec per year", () => {
    const m = findMover(frame([slow, barnard]), mid, "barnard");
    expect(m?.source).toBe("gaia");
    expect(m?.star.id).toBe(barnard.id);
    expect(m?.rate).toBeCloseTo(10.39, 1);
  });
  it("ignores slow stars and fast ones far from the centre", () => {
    const far = { ...barnard, ...starThrough({ ra: centre.ra, dec: centre.dec + 0.05 }, mid, -802.8, 10362.5), id: "2" };
    expect(findMover(frame([slow, far]), mid, null)).toBeNull();
  });
  it("falls back to a published motion for a featured target Gaia cannot see", () => {
    const m = findMover(frame([slow]), mid, "wise0855", centre);
    expect(m?.source).toBe("published");
    expect(m?.rate).toBeCloseTo(8.15, 1);
    // The pseudo star sits on the target at the frames' mid-time.
    expect(sepArcsec(gaiaAt(m!.star, mid), centre)).toBeLessThan(1e-6);
  });
});

describe("follow shifts", () => {
  it("are zero at the alignment time and move the star back onto it at other epochs", () => {
    const mjds = [mid - 182, mid, mid + 182];
    const s = followShifts(barnard, mjds, frame([]), centre, mid);
    expect(s[1].dx).toBeCloseTo(0, 9);
    expect(s[1].dy).toBeCloseTo(0, 9);
    // Six months of 10.4″/yr north is about 5.2″, 2.5 pixels at 2.05″: north is up (−y).
    expect(s[2].dy).toBeLessThan(-2.3);
    expect(s[2].dy).toBeGreaterThan(-2.8);
    expect(s[0].dy).toBeCloseTo(-s[2].dy, 6);
    // Resampling at (aligned + shift) finds the star where it was at that epoch.
    const a = skyToPixel(gaiaAt(barnard, mid), frame([]), centre);
    const p = skyToPixel(gaiaAt(barnard, mjds[2]), frame([]), centre);
    expect(a.x + s[2].dx).toBeCloseTo(p.x, 9);
    expect(a.y + s[2].dy).toBeCloseTo(p.y, 9);
  });
  it("measures the total drift, so tiny ones are not offered", () => {
    const s = followShifts(barnard, [mid - 182, mid + 182], frame([]), centre, mid);
    expect(driftPx(s)).toBeGreaterThan(MIN_DRIFT_PX);
    expect(driftPx(followShifts(slow, [mid - 182, mid + 182], frame([]), centre, mid))).toBeLessThan(MIN_DRIFT_PX);
  });
});
