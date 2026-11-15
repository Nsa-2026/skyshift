// Sky layers, by IRSA HiPS URL only (CDS ids and tiles are down; P/DSS2/color is broken).
// Each URL's /properties answered 200 on 14 Nov 2026 (IRSA HiPS list, https://irsa.ipac.caltech.edu/data/hips/list).
import type { Det } from "../api/types";

export interface SkyLayer {
  id: "2mass" | "allwise" | "spherex";
  label: string;
  url: string;
  format: "jpeg" | "png";
  /** Plain description for the button's title. */
  about: string;
  /** When the background survey photographed the sky: moving stars sit away from their image in it. */
  years?: string;
}

export const TWO_MASS: SkyLayer = {
  id: "2mass",
  label: "2MASS",
  url: "https://irsa.ipac.caltech.edu/data/hips/CDS/2MASS/Color",
  format: "jpeg",
  about: "2MASS colour: J 1.23 µm, H 1.66 µm, K 2.16 µm (IRSA)",
  years: "1997–2001",
};

export const ALLWISE: SkyLayer = {
  id: "allwise",
  label: "AllWISE",
  url: "https://irsa.ipac.caltech.edu/data/hips/CDS/AllWISE/RGB-W4-W2-W1",
  format: "jpeg",
  about: "AllWISE colour: W1 3.4 µm, W2 4.6 µm, W4 22 µm (IRSA)",
  years: "2010–2011",
};

export const SPHEREX: SkyLayer = {
  id: "spherex",
  label: "SPHEREx",
  url: "https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube",
  format: "png",
  about: "SPHEREx DR1 all-sky cube, 102 wavelengths, about 12.9″ per pixel (IRSA)",
};

export const BASE_LAYERS = [TWO_MASS, ALLWISE] as const;

/**
 * SPHEREx cube frames: frame = 17 × (det − 1) + (channel − 1), ascending wavelength
 * (cube properties, obs_description). Frames 50 and 51 (dichroic channels) are blank.
 */
export const CUBE_UM = [
  0.753, 0.771, 0.79, 0.809, 0.828, 0.848, 0.868, 0.889, 0.91, 0.932, 0.955, 0.978, 1.001, 1.025, 1.05, 1.075,
  1.102, 1.113, 1.139, 1.167, 1.195, 1.224, 1.254, 1.284, 1.315, 1.346, 1.378, 1.412, 1.446, 1.481, 1.517, 1.554,
  1.592, 1.631, 1.656, 1.695, 1.735, 1.775, 1.818, 1.861, 1.905, 1.949, 1.995, 2.041, 2.089, 2.139, 2.189, 2.24,
  2.292, 2.345, 2.399, 2.445, 2.506, 2.575, 2.646, 2.719, 2.794, 2.871, 2.951, 3.033, 3.118, 3.204, 3.293, 3.383,
  3.476, 3.57, 3.667, 3.768, 3.833, 3.867, 3.901, 3.935, 3.97, 4.005, 4.04, 4.076, 4.112, 4.148, 4.184, 4.22, 4.257,
  4.294, 4.331, 4.368, 4.405, 4.435, 4.467, 4.5, 4.533, 4.566, 4.6, 4.635, 4.669, 4.704, 4.739, 4.773, 4.808, 4.843,
  4.879, 4.915, 4.951, 4.988,
] as const;

/** The middle channel (9 of 17) of a detector: never one of the blank dichroic frames. */
export function cubeFrameForDet(det: Det): number {
  return 17 * (det - 1) + 8;
}

export function cubeFrameUm(frame: number): number {
  return CUBE_UM[Math.max(0, Math.min(CUBE_UM.length - 1, Math.round(frame)))];
}
