// Plate layers: what the Blink plate shows. Observed is the SPHEREx photos; the three derived
// layers are the science workspace's products (contracts/api.md v3.8, "Science workspace"): a
// static-sky model, each visit minus that model, and each visit minus SPHEREx's DR1 all-sky map.
// They exist for the five featured stars at 5′ on the Months clock only (the live API answers 404
// elsewhere), so this file decides where they can be offered and how their frames get the marks.
// Idea and products: PR #214 by 春。 and Codex.
import { hasScience } from "../api/science-client";
import type { Epoch, FramesResponse } from "../api/types";
import { DETECTORS } from "../app/astro";
import type { View } from "../app/modules";

export type Layer = "raw" | "model" | "resid" | "refdiff";

export interface LayerInfo {
  id: Layer;
  /** The button's label. */
  label: string;
  /** In a sentence: "Loading the residual…". */
  name: string;
  /** One sentence for when the layer is on (DESIGN.md: one sentence explains each mode). Empty for Observed. */
  says: string;
}

export const LAYERS: readonly LayerInfo[] = [
  { id: "raw", label: "Observed", name: "observations", says: "" },
  {
    id: "model",
    label: "Sky model",
    name: "sky model",
    says: "The sky model: this field as if nothing moved or changed, fitted to all the photos at each visit's wavelengths.",
  },
  {
    id: "resid",
    label: "Residual",
    name: "residual",
    says: "Each visit minus the sky model: what stays put cancels out, so what's left moved, changed, or is an artefact.",
  },
  {
    id: "refdiff",
    label: "DR1 difference",
    name: "DR1 difference",
    says: "Each visit minus SPHEREx's DR1 map of the whole sky at the same wavelengths: whatever differs from that map stands out.",
  },
];

export const layerInfo = (id: Layer): LayerInfo => LAYERS.find((l) => l.id === id) ?? LAYERS[0];
export const isLayer = (s: string | undefined): s is Layer => LAYERS.some((l) => l.id === s);

/** The derived layers' grid: 5′ on the Months clock (contracts/api.md, GET /api/frames?view=). */
export const DERIVED_CLOCK = "months";
export const DERIVED_FOV = 5;
/** The analysis footprint inside that grid: pixels outside it are raw 0 (contracts/api.md v3.8). */
export const FOOTPRINT_ARCMIN = 4;

export type Availability =
  | { ok: true }
  | { ok: false; why: string; fix: { clock: "months"; fov: number } | null };

/** Can this view show the derived layers? If not, why, and the view change that would allow it. */
export function availability(view: Pick<View, "slug" | "clock" | "fov">): Availability {
  if (!hasScience(view.slug))
    return {
      ok: false,
      why:
        "The sky model, residual and DR1 difference are prepared for five featured stars only: " +
        "Barnard's Star, Eps Indi B, WISE 0855, RW Aur and R Dra. Pick one of them under Try.",
      fix: null,
    };
  if (view.clock !== DERIVED_CLOCK || view.fov !== DERIVED_FOV)
    return {
      ok: false,
      why: `The sky model, residual and DR1 difference are prepared at ${DERIVED_FOV}′ on the Months clock.`,
      fix: { clock: DERIVED_CLOCK, fov: DERIVED_FOV },
    };
  return { ok: true };
}

/** Two epochs are the same visit when their ids, detectors and middle times agree (to about 15 minutes). */
const SAME_TIME_DAYS = 0.01;

export interface Marked {
  frames: FramesResponse;
  /** true: the observations had SkyShift's measurements here, but they couldn't be carried (other visits). */
  dropped: boolean;
}

/**
 * A derived layer's frames with the observations' marks. The prepared files list only the target
 * in `gaia` and nothing in `known` or `measured`, so the plate would lose its rings.
 * - Gaia: the observations' stars. They are sky positions, moved to each epoch's time by the
 *   blink and placed on this grid through its own `center`, so they stay right.
 * - Known objects and SkyShift's measurements are tied to epoch ids: kept only when both sets of
 *   frames hold exactly the same visits. Otherwise they are left out (the measured panel hides),
 *   never moved onto other visits.
 * - Wavelength range: the observation's when it is the same visit, else the detector's nominal
 *   range (the prepared files leave it null).
 */
export function withMarks(derived: FramesResponse, observed: FramesResponse | null): Marked {
  const byId = new Map((observed?.epochs ?? []).map((e) => [e.id, e]));
  const twin = (e: Epoch): Epoch | null => {
    const o = byId.get(e.id);
    return o && o.det === e.det && Math.abs(o.mjd_mid - e.mjd_mid) < SAME_TIME_DAYS ? o : null;
  };
  const epochs = derived.epochs.map((e) => {
    const o = twin(e);
    const d = DETECTORS[e.det] ?? { min: null, max: null };
    return { ...e, um_min: e.um_min ?? o?.um_min ?? d.min, um_max: e.um_max ?? o?.um_max ?? d.max } as Epoch;
  });
  const same =
    !!observed && observed.epochs.length === derived.epochs.length && derived.epochs.every((e) => twin(e) !== null);
  // Leave measured and measured_reason out entirely when the visits differ: "absent" hides the panel.
  const { measured: _m, measured_reason: _r, ...rest } = derived;
  const frames: FramesResponse = {
    ...rest,
    epochs,
    gaia: observed?.gaia?.length ? observed.gaia : derived.gaia ?? [],
    known: same ? observed!.known : [],
  };
  if (same && observed!.measured !== undefined) {
    frames.measured = observed!.measured;
    frames.measured_reason = observed!.measured_reason ?? null;
  }
  return { frames, dropped: !same && !!observed?.measured };
}

/** A share of the frame as a short percentage: 15%, 6.3%, 0.23%, under 0.01%. */
export function percent(f: number): string {
  if (f <= 0) return "0%";
  if (f >= 0.1) return `${(f * 100).toFixed(0)}%`;
  if (f >= 0.01) return `${(f * 100).toFixed(1)}%`;
  if (f >= 0.0001) return `${(f * 100).toFixed(2)}%`;
  return "under 0.01%";
}

/**
 * One sentence on the shown frame's quality. Observed: its masked and saturated shares (whole-frame
 * fractions from the FLAGS layer, contracts/api.md `flag_frac`, `sat_frac`); "" when not recorded.
 * Derived layers: their analysis cube keeps no flags, so say what that means for a leftover dot.
 */
export function qualityLine(
  epoch: Pick<Epoch, "det" | "kind" | "flag_frac" | "sat_frac"> | null | undefined,
  layer: Layer,
): string {
  if (layer !== "raw")
    return (
      `This layer covers only the middle ${FOOTPRINT_ARCMIN}′ (the hatched edge has no data) and is built without ` +
      "SPHEREx's flags, so check any leftover on Observed: it can be a detector artefact."
    );
  if (!epoch) return "";
  const { flag_frac: m, sat_frac: s } = epoch;
  if (m == null || s == null || !Number.isFinite(m) || !Number.isFinite(s)) return "";
  const what = epoch.kind === "exposure" ? "exposure" : "visit";
  return `In D${epoch.det}, this ${what} has ${percent(m)} of its pixels masked and ${percent(s)} saturated, counted over the whole frame.`;
}

/** What to say about the marks on a derived layer. `dropped`: see Marked. */
export function marksLine(layer: Layer, dropped: boolean): string {
  if (layer === "raw") return "";
  const rings = "Blue rings still mark Gaia's stars";
  const first = layer === "model" ? `${rings}.` : `${rings}, so a leftover on a ring is a star that moved or changed brightness.`;
  if (!dropped) return first;
  return `${first} SkyShift's own measurements aren't drawn: this layer splits the visits differently from the photos.`;
}
