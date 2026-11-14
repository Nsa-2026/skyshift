import { candidateFramesUrl } from "../api/client";
// Row thumbnails: the candidate's first frame as a small plate negative (DESIGN.md "Plate
// drawing"), drawn lazily when the row scrolls into view. Without frames_url: an empty plate with
// the target reticle, so every row has the same shape.
import { epochsFor, loadFrameData, toMJySr } from "../api/client";
import type { Candidate, FramesResponse } from "../api/types";
import { skyToPixel } from "../app/astro";

const GLASS = [0xed, 0xf0, 0xef];
const INK = [0x18, 0x20, 0x2e];
const NODATA = [0xe3, 0xe8, 0xe7];
const RED = "rgba(207, 35, 56, 0.85)";

/** Percentile of a sorted array (0–1). */
function pct(sorted: Float32Array, p: number): number {
  if (!sorted.length) return NaN;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))));
  return sorted[i];
}

/** Stretch for a negative: sky near the 35th percentile → glass, bright cores → ink (asinh). */
export function stretch(values: Float32Array): (v: number) => number {
  const finite = Float32Array.from(values.filter((v) => Number.isFinite(v))).sort();
  const lo = pct(finite, 0.35);
  const hi = Math.max(pct(finite, 0.995), lo + 1e-6);
  const a = 8;
  const norm = Math.asinh(a);
  return (v) => {
    const t = Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
    return Math.asinh(a * t) / norm;
  };
}

/** Paint a crop of a frame (MJy/sr) as a negative into the canvas, with a red reticle at (px, py). */
export function paintNegative(
  canvas: HTMLCanvasElement,
  values: Float32Array,
  size: [number, number],
  at: { x: number; y: number },
  cropPx: number,
): void {
  const [nx, ny] = size;
  const half = Math.floor(cropPx / 2);
  const x0 = Math.round(at.x) - half;
  const y0 = Math.round(at.y) - half;
  const crop = new Float32Array(cropPx * cropPx).fill(NaN);
  for (let j = 0; j < cropPx; j++) {
    const y = y0 + j;
    if (y < 0 || y >= ny) continue;
    for (let i = 0; i < cropPx; i++) {
      const x = x0 + i;
      if (x >= 0 && x < nx) crop[j * cropPx + i] = values[y * nx + x];
    }
  }
  if (!crop.some((v) => Number.isFinite(v))) throw new Error("the candidate is outside its frame");
  const f = stretch(crop);
  const img = new ImageData(cropPx, cropPx);
  for (let p = 0; p < crop.length; p++) {
    const v = crop[p];
    const rgb = Number.isFinite(v)
      ? (() => {
          const t = f(v);
          return [0, 1, 2].map((k) => Math.round(GLASS[k] + (INK[k] - GLASS[k]) * t));
        })()
      : NODATA;
    img.data[p * 4] = rgb[0];
    img.data[p * 4 + 1] = rgb[1];
    img.data[p * 4 + 2] = rgb[2];
    img.data[p * 4 + 3] = 255;
  }
  const off = document.createElement("canvas");
  off.width = cropPx;
  off.height = cropPx;
  off.getContext("2d")?.putImageData(img, 0, 0);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(off, 0, 0, canvas.width, canvas.height);
  reticle(ctx, canvas.width / 2 + ((at.x - Math.round(at.x)) * canvas.width) / cropPx, canvas.height / 2 + ((at.y - Math.round(at.y)) * canvas.height) / cropPx, canvas.width);
}

function reticle(ctx: CanvasRenderingContext2D, x: number, y: number, w: number) {
  const r = w * 0.16;
  const gap = r * 0.45;
  ctx.strokeStyle = RED;
  ctx.lineWidth = Math.max(1.5, w / 56);
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(x - r - gap, y);
  ctx.lineTo(x - gap, y);
  ctx.moveTo(x + gap, y);
  ctx.lineTo(x + r + gap, y);
  ctx.moveTo(x, y - r - gap);
  ctx.lineTo(x, y - gap);
  ctx.moveTo(x, y + gap);
  ctx.lineTo(x, y + r + gap);
  ctx.stroke();
}

const framesCache = new Map<string, Promise<FramesResponse>>();

async function framesFor(url: string, signal?: AbortSignal): Promise<FramesResponse> {
  let p = framesCache.get(url);
  if (!p) {
    p = fetch(url, { signal }).then((r) => {
      if (!r.ok) throw new Error(`${r.status} for ${url}`);
      return r.json() as Promise<FramesResponse>;
    });
    framesCache.set(url, p);
    p.catch(() => framesCache.delete(url));
  }
  return p;
}

/** Load the candidate's first frame (its own detector first) and draw it. Throws on failure. */
export async function drawThumb(canvas: HTMLCanvasElement, c: Candidate, signal?: AbortSignal): Promise<void> {
  if (!c.frames_url) throw new Error("no frames");
  const jsonUrl = candidateFramesUrl(c.frames_url);
  const frames = await framesFor(jsonUrl, signal);
  const ep = epochsFor(frames, c.det)[0] ?? frames.epochs[0];
  if (!ep) throw new Error("no epochs");
  const raw = await loadFrameData(ep, jsonUrl, signal);
  const values = toMJySr(raw, ep);
  const at = skyToPixel(c, frames, ep.center ?? frames.center);
  const [nx, ny] = frames.size;
  // About a third of the field, at least 24 px, so the dot reads at thumbnail size.
  const cropPx = Math.max(24, Math.min(nx, ny, Math.round(Math.min(nx, ny) * 0.4)));
  paintNegative(canvas, values, frames.size, at, cropPx);
}

/** Draw thumbnails as their rows come into view, three at a time. */
export function thumbLoader(find: (id: string) => Candidate | undefined) {
  const queue: HTMLElement[] = [];
  let running = 0;
  const ctl = new AbortController();
  const io =
    typeof IntersectionObserver !== "undefined"
      ? new IntersectionObserver(
          (entries) => {
            for (const e of entries)
              if (e.isIntersecting) {
                io?.unobserve(e.target);
                queue.push(e.target as HTMLElement);
              }
            pump();
          },
          { rootMargin: "200px" },
        )
      : null;

  function pump() {
    while (running < 3 && queue.length) {
      const box = queue.shift()!;
      const c = find(box.dataset.thumb ?? "");
      if (!c || !box.isConnected) continue;
      running++;
      const canvas = document.createElement("canvas");
      canvas.width = 112;
      canvas.height = 112;
      canvas.className = "dv-thumb-img";
      drawThumb(canvas, c, ctl.signal)
        .then(() => {
          if (box.isConnected) {
            box.querySelector(".dv-thumb-empty")?.remove();
            box.prepend(canvas);
            box.dataset.state = "drawn";
          }
        })
        .catch(() => {
          box.dataset.state = "failed";
        })
        .finally(() => {
          running--;
          pump();
        });
    }
  }

  return {
    observe(box: HTMLElement) {
      if (io) io.observe(box);
    },
    destroy() {
      io?.disconnect();
      ctl.abort();
    },
  };
}
