import { psfSigmaPx, robustSigma, sigmaLimits, smooth, subtract } from "../blink/math";
import { buildLut, diffIndex, lerp, paint, plainIndex, readPalette, type RGB } from "../blink/paint";
import { medianPlanes } from "../colour/rgb";
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
type BaseTool = "original" | "contrast" | "difference";
type CometTool = "intensity" | "negative" | "background" | "motion";
export type StarTool = BaseTool | CometTool;
type TrackingPictures = Record<BaseTool, HTMLCanvasElement[]> & Partial<Record<CometTool, HTMLCanvasElement[]>>;

const INTENSITY_STOPS: RGB[] = [
  [18, 12, 33], [80, 29, 109], [159, 56, 104], [230, 109, 68], [252, 244, 162],
];
export const INTENSITY_GRADIENT = `linear-gradient(90deg, ${INTENSITY_STOPS.map(rgb => `rgb(${rgb.join(", ")})`).join(", ")})`;

function intensityLut() {
  const lut = new Uint8ClampedArray(256 * 3);
  for (let index = 0; index < 256; index++) {
    const position = index * (INTENSITY_STOPS.length - 1) / 255;
    const segment = Math.min(Math.floor(position), INTENSITY_STOPS.length - 2);
    lut.set(lerp(INTENSITY_STOPS[segment], INTENSITY_STOPS[segment + 1], position - segment), index * 3);
  }
  return lut;
}

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

export function trackingPictures(source: TrackingImages, data: TrackingData, canvas: HTMLCanvasElement): TrackingPictures {
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

  const pictures = {
    original: data.originals.map(values => picture(values, positive, originalIndex)),
    contrast: data.smoothed.map(values => picture(values, positive, contrastIndex)),
    difference: data.differences.map(values => picture(values, signed, differenceIndex)),
  };
  if (source !== TRACKING_IMAGES.comet) return pictures;

  const intensity = intensityLut();
  const negative = buildLut(palette, false, false);
  const background = medianPlanes(data.smoothed);
  const backgroundPicture = picture(background, positive, contrastIndex);
  const residuals = data.smoothed.map(values => subtract(values, background));
  const residualNoise = robustSigma(residuals);
  const residualIndex = plainIndex({ lo: 2 * residualNoise, hi: 18 * residualNoise }, "asinh");

  return {
    ...pictures,
    intensity: data.originals.map(values => picture(values, intensity, originalIndex)),
    negative: data.originals.map(values => picture(values, negative, originalIndex)),
    background: data.originals.map(() => backgroundPicture),
    motion: residuals.map(values => picture(values, positive, residualIndex)),
  };
}
