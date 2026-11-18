import { psfSigmaPx, robustSigma, sigmaLimits, smooth, subtract } from "../blink/math";
import { buildLut, diffIndex, paint, plainIndex, readPalette } from "../blink/paint";
import cutoutUrl from "./wise0855/f110w.f32?url";
import hubbleMetadata from "./wise0855/observations.json";
import tessUrl from "./comet/frames.f32?url";
import tessMetadata from "./comet/observations.json";

export const WISE_NAME = "WISE 0855−0714";
export const TESS_COMET = "https://science.nasa.gov/blogs/3iatlas/2026/01/27/nasas-tess-reobserves-comet-3i-atlas/";
export const TESS_EPHEMERIS = "https://github.com/tessgi/tess-3i/blob/main/data/2026/3iatlas_ephem_sector1751_20260326.csv";
export const ASTROMETRY_ARTICLE = "https://doi.org/10.1051/0004-6361/201628662";
export const WISE_DISCOVERY = "https://www.jpl.nasa.gov/images/pia18002-cold-and-quick-a-fast-moving-brown-dwarf/";
export const SPHEREX_WISE_VIEW = "?t=wise0855&det=6#/explore";
export const mastImage = (collection: "HST" | "TESS", filename: string) =>
  `https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:${collection}/product/${filename}`;

export const TRACKING_IMAGES = {
  star: { field: hubbleMetadata, url: cutoutUrl, smoothingArcsec: .22 },
  comet: { field: tessMetadata, url: tessUrl, smoothingArcsec: 33 },
};
export type TrackingLesson = keyof typeof TRACKING_IMAGES;
export type TrackingImages = (typeof TRACKING_IMAGES)[TrackingLesson];
export type StarTool = "original" | "contrast" | "difference";

export async function loadTrackingImages(source: TrackingImages, signal: AbortSignal) {
  const { width, height, observations, arcsecPerPixel } = source.field;
  const response = await fetch(source.url, { signal });
  if (!response.ok) throw new Error("The image cutouts are unavailable.");
  const buffer = await response.arrayBuffer();
  const planeBytes = width * height * Float32Array.BYTES_PER_ELEMENT;
  if (buffer.byteLength !== observations.length * planeBytes) {
    throw new Error("The image cutouts have an unexpected size.");
  }
  const originals = observations.map((_, index) => new Float32Array(buffer, index * planeBytes, width * height));
  const sigma = psfSigmaPx(source.smoothingArcsec, arcsecPerPixel);
  const smoothed = originals.map(values => smooth(values, width, height, sigma));
  return {
    originals,
    smoothed,
    differences: smoothed.map(values => subtract(values, smoothed[0])),
    positions: observations.map(o => o.mark),
  };
}

export type TrackingData = Awaited<ReturnType<typeof loadTrackingImages>>;

export function trackingPictures(source: TrackingImages, data: TrackingData, canvas: HTMLCanvasElement) {
  const { width, height } = source.field;
  const palette = readPalette(canvas);
  const positive = buildLut(palette, false, true);
  const signed = buildLut(palette, true, false);
  const sceneNoise = robustSigma(data.originals);
  const smoothedNoise = robustSigma(data.smoothed);
  const originalIndex = plainIndex({ lo: 1.35 * sceneNoise, hi: 30 * sceneNoise }, "asinh");
  const contrastIndex = plainIndex({ lo: .9 * smoothedNoise, hi: 14 * smoothedNoise }, "asinh");
  const differenceIndex = diffIndex(sigmaLimits([data.differences[1]], .26));

  function picture(values: Float32Array, lut: Uint8ClampedArray, index: (value: number) => number) {
    const image = document.createElement("canvas");
    image.width = width;
    image.height = height;
    const ctx = image.getContext("2d");
    if (ctx) {
      const pixels = ctx.createImageData(width, height);
      paint(values, pixels.data, lut, index);
      ctx.putImageData(pixels, 0, 0);
    }
    return image;
  }

  return {
    original: data.originals.map(values => picture(values, positive, originalIndex)),
    contrast: data.smoothed.map(values => picture(values, positive, contrastIndex)),
    difference: data.differences.map(values => picture(values, signed, differenceIndex)),
  };
}
