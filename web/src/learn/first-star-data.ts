import { getReal, loadFrameData, toMJySr } from "../api/client";
import type { FramesResponse } from "../api/types";
import { median, psfSigmaPx, robustSigma, sigmaLimits, smooth, subtract } from "../blink/math";
import { buildLut, diffIndex, lerp, paint, plainIndex, readPalette, type RGB } from "../blink/paint";
import { medianPlanes } from "../colour/rgb";
import cutoutUrl from "./wise0855/f110w.f32?url";
import hubbleMetadata from "./wise0855/observations.json";
import tessUrl from "./comet/frames.f32?url";
import tessMetadata from "./comet/observations.json";

export const ELPIS_NAME = "(59) Elpis";
export const ELPIS_ORBIT = "https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=59";
export const IRSA_MOST = "https://irsa.ipac.caltech.edu/applications/MOST/";
export const SPHEREX_ELPIS_VIEW = "?t=elpis&det=2#/explore";
export const WISE_NAME = "WISE 0855−0714";
export const TESS_COMET = "https://science.nasa.gov/blogs/3iatlas/2026/01/27/nasas-tess-reobserves-comet-3i-atlas/";
export const TESS_EPHEMERIS = "https://github.com/tessgi/tess-3i/blob/main/data/2026/3iatlas_ephem_sector1751_20260326.csv";
export const ASTROMETRY_ARTICLE = "https://doi.org/10.1051/0004-6361/201628662";
export const WISE_DISCOVERY = "https://www.jpl.nasa.gov/images/pia18002-cold-and-quick-a-fast-moving-brown-dwarf/";
export const SPHEREX_WISE_VIEW = "?t=wise0855&det=6#/explore";
export const mastImage = (collection: "HST" | "TESS", filename: string) =>
  `https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:${collection}/product/${filename}`;

const TRACKING_IMAGES = {
  star: { field: hubbleMetadata, url: cutoutUrl, smoothingArcsec: .22 },
  comet: { field: tessMetadata, url: tessUrl, smoothingArcsec: 33 },
};
export type TrackingLesson = keyof typeof TRACKING_IMAGES | "asteroid";

interface TrackingObservation {
  filename: string;
  startMjd: number;
  endMjd: number;
  midpointMjd?: number;
  exposureSeconds: number;
  calibration: string;
  mark: { x: number; y: number };
  wavelength?: [number, number];
}

interface TrackingField {
  width: number;
  height: number;
  arcsecPerPixel: number;
  observations: TrackingObservation[];
}

const ELPIS_MANIFEST = "/data/elpis/frames_hours_20.json";
const ELPIS_CROP = { x: 50, y: 42, size: 116 };
// Measured light peaks in the aligned D2 images, using IRSA MOST positions to identify Elpis.
const ELPIS_EXPOSURES = [
  { id: "d2-2026W38_2A_0334_4", x: 123.640, y: 92.149 },
  { id: "d2-2026W38_2A_0353_1", x: 113.368, y: 94.282 },
  { id: "d2-2026W38_2A_0383_1", x: 91.816, y: 98.646 },
] as const;
type BaseTool = "original" | "contrast" | "difference";
type MovingTool = "intensity" | "negative" | "background" | "motion";
export type StarTool = BaseTool | MovingTool;
type TrackingPictures = Record<BaseTool, HTMLCanvasElement[]> & Partial<Record<MovingTool, HTMLCanvasElement[]>>;

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

export async function loadTrackingImages(lesson: TrackingLesson, signal: AbortSignal) {
  if (lesson === "asteroid") return loadElpis(signal);
  const { field, url, smoothingArcsec } = TRACKING_IMAGES[lesson];
  const { width, height, observations } = field;
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error("The image cutouts are unavailable.");
  const buffer = await response.arrayBuffer();
  const planeBytes = width * height * Float32Array.BYTES_PER_ELEMENT;
  if (buffer.byteLength !== observations.length * planeBytes) {
    throw new Error("The image cutouts have an unexpected size.");
  }
  const originals = observations.map((_, index) => new Float32Array(buffer, index * planeBytes, width * height));
  return prepareImages(field, originals, smoothingArcsec);
}

async function loadElpis(signal: AbortSignal) {
  const url = new URL(ELPIS_MANIFEST, window.location.href).href;
  const manifest = await getReal<FramesResponse>(url, signal);
  const [width, height] = manifest.size;
  const crop = ELPIS_CROP;
  if (width !== 195 || height !== 195 || manifest.pixel_arcsec !== 6.15) {
    throw new Error("The SPHEREx images have an unexpected grid.");
  }
  const selected = await Promise.all(ELPIS_EXPOSURES.map(async measured => {
    const epoch = manifest.epochs.find(item => item.id === measured.id && item.det === 2);
    if (!epoch) throw new Error("A SPHEREx exposure is unavailable.");
    const raw = await loadFrameData(epoch, url, signal);
    if (raw.length !== width * height) throw new Error("A SPHEREx exposure has an unexpected size.");
    const values = toMJySr(raw, epoch);
    const image = new Float32Array(crop.size ** 2);
    for (let row = 0; row < crop.size; row++) {
      const offset = (crop.y + row) * width + crop.x;
      image.set(values.subarray(offset, offset + crop.size), row * crop.size);
    }
    const sky = median(image);
    const observation: TrackingObservation = {
      filename: epoch.id,
      startMjd: epoch.mjd_start,
      endMjd: epoch.mjd_end,
      midpointMjd: epoch.mjd_mid,
      exposureSeconds: (epoch.mjd_end - epoch.mjd_start) * 86400,
      calibration: manifest.data_release,
      mark: { x: measured.x - crop.x, y: measured.y - crop.y },
      wavelength: [epoch.um_min, epoch.um_max],
    };
    return { image: image.map(value => value - sky), observation };
  }));
  return prepareImages({
    width: crop.size,
    height: crop.size,
    arcsecPerPixel: manifest.pixel_arcsec,
    observations: selected.map(({ observation }) => observation),
  }, selected.map(({ image }) => image), 7.53);
}

function prepareImages(field: TrackingField, originals: Float32Array[], smoothingArcsec: number) {
  const { width, height, arcsecPerPixel, observations } = field;
  const sigma = psfSigmaPx(smoothingArcsec, arcsecPerPixel);
  const smoothed = originals.map(values => smooth(values, width, height, sigma));
  return {
    field,
    originals,
    smoothed,
    differences: smoothed.map(values => subtract(values, smoothed[0])),
    positions: observations.map(o => o.mark),
  };
}

export type TrackingData = Awaited<ReturnType<typeof loadTrackingImages>>;

export function trackingPictures(lesson: TrackingLesson, data: TrackingData, canvas: HTMLCanvasElement): TrackingPictures {
  const { width, height } = data.field;
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
  if (lesson === "star") return pictures;

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
