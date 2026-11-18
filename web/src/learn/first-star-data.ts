import { psfSigmaPx, robustSigma, sigmaLimits, smooth, subtract } from "../blink/math";
import { buildLut, diffIndex, paint, plainIndex, readPalette } from "../blink/paint";
import cutoutUrl from "./wise0855/f110w.f32?url";
import metadata from "./wise0855/observations.json";

export const WISE_NAME = "WISE 0855−0714";
export const HST_FIELD = metadata;
export const ASTROMETRY_ARTICLE = "https://doi.org/10.1051/0004-6361/201628662";
export const WISE_DISCOVERY = "https://www.jpl.nasa.gov/images/pia18002-cold-and-quick-a-fast-moving-brown-dwarf/";
export const SPHEREX_WISE_VIEW = "?t=wise0855&det=6#/explore";
export const mastImage = (filename: string) =>
  `https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:HST/product/${filename}`;

export type StarTool = "original" | "contrast" | "difference";

export async function loadFirstStar(signal: AbortSignal) {
  const { width, height, observations, arcsecPerPixel } = HST_FIELD;
  const response = await fetch(cutoutUrl, { signal });
  if (!response.ok) throw new Error("The Hubble image cutouts are unavailable.");
  const buffer = await response.arrayBuffer();
  const planeBytes = width * height * Float32Array.BYTES_PER_ELEMENT;
  if (buffer.byteLength !== observations.length * planeBytes) {
    throw new Error("The Hubble cutouts have an unexpected size.");
  }
  const originals = observations.map((_, index) => new Float32Array(buffer, index * planeBytes, width * height));
  const hstPsfArcsec = .22;
  const smoothed = originals.map(values => smooth(values, width, height, psfSigmaPx(hstPsfArcsec, arcsecPerPixel)));
  return {
    originals,
    smoothed,
    differences: smoothed.map(values => subtract(values, smoothed[0])),
    positions: observations.map(o => o.mark),
  };
}

export type FirstStarData = Awaited<ReturnType<typeof loadFirstStar>>;

export function firstStarPictures(data: FirstStarData, canvas: HTMLCanvasElement) {
  const { width, height } = HST_FIELD;
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
