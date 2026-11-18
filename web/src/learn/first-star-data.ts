import { epochsFor, loadFrames, loadObject } from "../api/client";
import { gaiaAt, skyToPixel } from "../app/astro";
import type { View } from "../app/modules";
import { targetBySlug } from "../app/state";
import { psfSigmaPx, robustLimits, sigmaLimits, smooth, subtract } from "../blink/math";
import { buildLut, diffIndex, paint, readPalette } from "../blink/paint";
import { compose, type Channel } from "../colour/rgb";
import { loadVisits, visitOf } from "../colour/visit";
import { drawObservation, observation } from "../product/observations";

export const FIRST_STAR = targetBySlug("rwaur")!;
export const FIRST_STAR_DETECTOR = 2;

export async function loadFirstStar(signal: AbortSignal) {
  const view: View = { ...FIRST_STAR, clock: "months", fov: 5, det: FIRST_STAR_DETECTOR };
  const [set, object, colours] = await Promise.all([
    loadFrames(view, signal),
    loadObject(view, signal),
    loadVisits(view, "nir", signal, () => {}),
  ]);
  const frames = epochsFor(set.frames, view.det);
  const [width, height] = set.frames.size;
  if (frames.length !== 3 || width !== colours.size[0] || height !== colours.size[1] ||
    set.frames.pixel_arcsec !== colours.pixelArcsec) {
    throw new Error("The three aligned SPHEREx visits are unavailable.");
  }
  const originals = await Promise.all(frames.map(epoch => observation(set, epoch, signal)));
  // Follow the Viewer's default Difference processing, using the same detector for every visit.
  const blur = psfSigmaPx(7.53, set.frames.pixel_arcsec);
  const smoothed = originals.map(o => smooth(o.values, width, height, blur));
  const differences = smoothed.map(values => subtract(values, smoothed[0]));
  const positions = frames.map(epoch => {
    const at = object.gaia ? gaiaAt(object.gaia, epoch.mjd_mid) : view;
    const point = skyToPixel(at, set.frames, epoch.center ?? set.frames.center);
    return { x: point.x + .5, y: point.y + .5 };
  });
  return { originals, differences, colours, positions, archivePosition: object.gaia ?? view };
}

export type FirstStarData = Awaited<ReturnType<typeof loadFirstStar>>;
export type StarTool = "original" | "colours" | "difference";

export function firstStarPictures(data: FirstStarData, canvas: HTMLCanvasElement) {
  const [width, height] = data.originals[0].set.frames.size;
  const limits = robustLimits(data.originals.map(o => o.values), .08);
  const differenceLimits = sigmaLimits(data.differences.slice(1), .45);
  const lut = buildLut(readPalette(canvas), true, false);

  function picture(draw: (ctx: CanvasRenderingContext2D) => void) {
    const image = document.createElement("canvas");
    image.width = width;
    image.height = height;
    const ctx = image.getContext("2d");
    if (ctx) draw(ctx);
    return image;
  }

  return {
    original: data.originals.map(o => {
      const image = document.createElement("canvas");
      drawObservation(image, o, limits);
      return image;
    }),
    colours: data.originals.map(o => {
      const visit = visitOf(o.epoch.id);
      const channels = visit == null ? undefined : data.colours.visits.get(visit)?.channels;
      if (!channels || channels.some(c => !c)) throw new Error("Star colours are unavailable for one of the visits.");
      const [red, green, blue] = channels as [Channel, Channel, Channel];
      return picture(ctx => {
        const pixels = ctx.createImageData(width, height);
        pixels.data.set(compose(red, green, blue).rgba);
        ctx.putImageData(pixels, 0, 0);
      });
    }),
    difference: data.differences.map(values => picture(ctx => {
      const pixels = ctx.createImageData(width, height);
      paint(values, pixels.data, lut, diffIndex(differenceLimits));
      ctx.putImageData(pixels, 0, 0);
    })),
  };
}
