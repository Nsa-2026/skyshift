// A dot circled in one photo only (core pixelcheck.one_photo, measured in the browser): steady faint
// source, cosmic ray, mover or flash, on synthetic frames with known answers.
import { describe, expect, it } from "vitest";
import type { MeasuredClass, MeasuredDetection, SkyPos } from "../api/types";
import { pixelToSky } from "../app/astro";
import { combine, forcedFlux, momentSize, onePhoto, onePhotoChecks, onePhotoChecksAsync, onePhotoWords, sizeRatio, type OPFrame, type OPGrid } from "./onephoto";

const N = 72;
const grid: OPGrid = { size: [N, N], pixel_arcsec: 6.152 };
const centre: SkyPos = { ra: 150, dec: 2 };
const NOISE = 0.01; // MJy/sr per pixel
const SIGMA_PX = 0.62; // a star's PSF (7.53″ FWHM, resampled)

/** Seeded normal noise. */
function noise(seed: number): () => number {
  let a = seed >>> 0;
  const u = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), a | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return (((t ^ (t >>> 14)) >>> 0) + 1) / 4294967297;
  };
  return () => Math.sqrt(-2 * Math.log(u())) * Math.cos(2 * Math.PI * u());
}
function frame(seed: number, sources: { x: number; y: number; peak: number; sigma?: number }[]): Float32Array {
  const g = noise(seed);
  const v = new Float32Array(N * N);
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++) {
      let s = 0.2 + NOISE * g();
      for (const p of sources) {
        const sg = p.sigma ?? SIGMA_PX;
        s += p.peak * Math.exp(-((x - p.x) ** 2 + (y - p.y) ** 2) / (2 * sg * sg));
      }
      v[y * N + x] = s;
    }
  return v;
}
const det = (epoch_id: string, x: number, y: number, snr: number, cls: MeasuredClass = "other", extra: Partial<MeasuredDetection> = {}): MeasuredDetection => ({
  epoch_id, ...pixelToSky({ x, y }, grid, centre), snr, flux_ujy: snr * 20, n_dots: 1, class: cls,
  source_id: null, gaia_id: null, known_name: null, track_id: null, grade: null, reasons: [], ...extra,
});
/** Twelve stars of S/N ~10 on a ring, in every photo: the size test's comparison stars. */
const STARS = Array.from({ length: 12 }, (_, k) => ({ x: 36 + 24 * Math.cos(k / 2), y: 36 + 24 * Math.sin(k / 2), peak: 0.09 }));

function visit(extra: (i: number) => { x: number; y: number; peak: number; sigma?: number }[], n = 8) {
  const frames: OPFrame[] = Array.from({ length: n }, (_, i) => ({
    id: `d2-e${i}`, det: 2 as const, mjd: 61000 + (i >> 1) * 1.5 + (i & 1) * (2.1 / 1440), centre,
    vals: frame(100 + i, [...STARS, ...extra(i)]),
  }));
  const stars = frames.flatMap((f) => STARS.map((s, k) => det(f.id, s.x, s.y, 10, "star", { gaia_id: `g${k}`, source_id: k + 1 })));
  return { frames, stars };
}

describe("one-photo dots", () => {
  it("measures a star's flux and an empirical error, and sizes a star against a hit", () => {
    const v = frame(1, [{ x: 30, y: 30, peak: 0.1 }, { x: 50, y: 50, peak: 0.3, sigma: 0.25 }]);
    const f = forcedFlux(v, N, N, [{ x: 30, y: 30 }, { x: 10.3, y: 60.2 }]);
    // a 2D Gaussian holds 2π σ² peak MJy/sr; × 889.7 µJy, × 1.033 for the r = 2 px aperture's 96.8%
    const total = 2 * Math.PI * SIGMA_PX ** 2 * 0.1 * 889.7;
    // (a Gaussian this narrow keeps more than 96.8% inside r = 2 px: up to ~4% over)
    expect(f.flux[0]).toBeGreaterThan(0.93 * total);
    expect(f.flux[0]).toBeLessThan(1.1 * total);
    // the noise: ~ NOISE × √(aperture area) × 889.7 × 1.033 (plus the annulus' share)
    expect(f.err).toBeGreaterThan(0.8 * NOISE * Math.sqrt(Math.PI * 4) * 889.7);
    expect(f.err).toBeLessThan(1.6 * NOISE * Math.sqrt(Math.PI * 4) * 889.7);
    expect(Math.abs(f.flux[1])).toBeLessThan(4 * f.err);
    expect(f.masked).toEqual([false, false]);
    const star = momentSize(v, N, N, 30, 30);
    const hit = momentSize(v, N, N, 50, 50);
    expect(star).toBeGreaterThan(0.5);
    expect(hit / star).toBeLessThan(0.7);
    expect(sizeRatio(hit, 10, Array.from({ length: 8 }, () => ({ size: star, snr: 12 })))).toBeCloseTo(hit / star, 6);
    expect(sizeRatio(hit, 10, Array.from({ length: 7 }, () => ({ size: star, snr: 12 })))).toBeNaN();
    // no data at the centre: masked
    const holed = v.slice();
    holed[30 * N + 30] = NaN;
    expect(forcedFlux(holed, N, N, [{ x: 30, y: 30 }]).masked).toEqual([true]);
  });

  it("follows core's rule, first match", () => {
    expect(combine([10, 20, NaN], [1, 1, 1])).toEqual([15, 1 / Math.sqrt(2), 2]);
    expect(onePhoto(100, [50], [10]).verdict).toBe("too_few");
    expect(onePhoto(100, [40, 40], [10, 10]).verdict).toBe("steady"); // S/N 5.7, ratio 0.4
    expect(onePhoto(100, [5, 5], [30, 30]).verdict).toBe("unclear"); // limit (5 + 3 × 21) / 100 = 0.69
    expect(onePhoto(100, [2, 2], [5, 5], { sizeRatio: 0.5 }).verdict).toBe("cosmic_ray");
    expect(onePhoto(100, [2, 2], [5, 5], { transientPx: 2 }).verdict).toBe("cosmic_ray");
    expect(onePhoto(100, [2, 2], [5, 5], { sizeRatio: 0.9, moved: true }).verdict).toBe("moved");
    expect(onePhoto(100, [2, 2], [5, 5], { sizeRatio: 0.9 }).verdict).toBe("flash");
    // its own light not measurable (flagged pixels, or below the background): too_few, said as such
    const own = onePhoto(NaN, [2, 2], [5, 5]);
    expect(own.verdict).toBe("too_few");
    expect(onePhotoWords(own)).toMatch(/its own pixels can't be measured/);
    expect(onePhotoWords(own, true)).toMatch(/not measurable in this one\)$/);
    expect(onePhotoWords(onePhoto(100, [50], [10]))).toBe("in this photo only; too few other photos of this detector to tell");
  });

  it("tells a steady faint source, a cosmic ray, a flash and a mover apart", () => {
    // photo 2 holds: a faint fixed source (S/N ~4 a photo, every photo), a one-pixel hit, a star-wide
    // flash; photo 2 and photo 3 (2.1 min later) hold a fast dot that moved 8″
    const { frames, stars } = visit((i) => [
      { x: 20, y: 40, peak: 0.022 },
      ...(i === 2 ? [{ x: 45, y: 20, peak: 0.35, sigma: 0.22 }, { x: 52, y: 44, peak: 0.08 }, { x: 40, y: 56, peak: 0.08 }] : []),
      ...(i === 3 ? [{ x: 41.3, y: 56, peak: 0.08 }] : []),
    ]);
    const own = frames[2];
    const dots = [
      det(own.id, 20, 40, 5.4),
      det(own.id, 45, 20, 9),
      det(own.id, 52, 44, 9),
      det(own.id, 40, 56, 9),
      det(frames[3].id, 41.3, 56, 9),
      det(own.id, 12, 12, 7, "suspect", { track_id: "t1" }),
      det(own.id, 60, 60, 7, "artifact"),
    ];
    const got = onePhotoChecks(grid, frames, own, [...stars, ...dots]);
    const verdict = (d: MeasuredDetection) => got.get(`${d.ra}|${d.dec}|${d.flux_ujy}|${d.snr}`);
    expect(verdict(dots[0])?.verdict).toBe("steady");
    expect(verdict(dots[0])!.n_other).toBe(7);
    expect(verdict(dots[1])?.verdict).toBe("cosmic_ray");
    expect(verdict(dots[1])!.size_ratio).toBeLessThan(0.7);
    expect(verdict(dots[2])?.verdict).toBe("flash");
    expect(verdict(dots[3])?.verdict).toBe("moved");
    expect(verdict(dots[5])).toMatchObject({ verdict: "moved", track: true });
    expect(verdict(dots[6])).toBeUndefined(); // artefacts are left out
    // the stars are in every photo: not one-photo dots
    expect(verdict(stars.find((s) => s.epoch_id === own.id)!)).toBeUndefined();
    expect(onePhotoWords(verdict(dots[0])!)).toBe("also faintly in the other photos: a steady source below the cut");
    expect(onePhotoWords(verdict(dots[1])!)).toBe("only in this photo and sharp: likely a cosmic ray or flash");
    expect(onePhotoWords(verdict(dots[3])!)).toBe("moves");
    expect(onePhotoWords(verdict(dots[0])!, true)).toMatch(/^also faintly .* \(the other 7 photos: \d+ ± \d+ µJy there, \d+ in this one, [\d.]+ × a star's width\)$/);
  });

  it("gives the same verdicts a photo at a time, and gives up when told", async () => {
    const { frames, stars } = visit((i) => [{ x: 20, y: 40, peak: 0.022 }, ...(i === 2 ? [{ x: 45, y: 20, peak: 0.35, sigma: 0.22 }] : [])]);
    const own = frames[2];
    const dots = [...stars, det(own.id, 20, 40, 5.4), det(own.id, 45, 20, 9)];
    let pauses = 0;
    const got = await onePhotoChecksAsync(grid, frames, own, dots, async () => void pauses++);
    expect(pauses).toBeGreaterThanOrEqual(frames.length);
    expect(got).toEqual(onePhotoChecks(grid, frames, own, dots));
    await expect(onePhotoChecksAsync(grid, frames, own, dots, async () => { throw new Error("frames changed"); })).rejects.toThrow("frames changed");
  });

  it("never mixes detectors, and needs two other photos", () => {
    const { frames, stars } = visit(() => [], 3);
    frames[1] = { ...frames[1], det: 5 };
    const own = frames[0];
    const flash = frame(9, [...STARS, { x: 52, y: 44, peak: 0.08 }]);
    const f0 = { ...own, vals: flash };
    const dot = det(own.id, 52, 44, 9);
    const got = onePhotoChecks(grid, [f0, frames[1], frames[2]], f0, [...stars, dot]);
    expect(got.get(`${dot.ra}|${dot.dec}|${dot.flux_ujy}|${dot.snr}`)).toMatchObject({ verdict: "too_few", n_other: 1 });
  });
});
