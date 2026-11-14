// The live mini blink in the preview drawer: two or more epochs of the candidate's spot,
// alternating at 2 Hz, drawn as a plate negative (dark stars on light glass) with the candidate
// in an open red reticle. Same detector only: a change of detector is colour, not motion.
import type { Candidate, Clock, Epoch, FramesResponse } from "../api/types";
import { epochsFor, loadFrameData, toMJySr } from "../api/client";
import { formatMjd, skyToPixel } from "../app/astro";
import { robustLimits, stretch, type Limits } from "../blink/math";

export const BLINK_MS = 500; // 2 Hz
export const MAX_EPOCHS = 4;
/** Side of the cut-out around the candidate, frame pixels (6.15″ each: about 7′). */
export const CROP_PX = 72;

/**
 * The epochs to blink: the candidate's detector (or the best-covered one), at most `max`.
 * Months: spread over all visits. Hours: consecutive exposures round the one nearest `mjd`.
 */
export function pickEpochs(frames: Pick<FramesResponse, "epochs">, det: number, clock: Clock, mjd: number, max = MAX_EPOCHS): Epoch[] {
  let list = epochsFor(frames as FramesResponse, det as Epoch["det"]);
  if (list.length < 2) {
    const byDet = new Map<number, Epoch[]>();
    for (const e of frames.epochs) byDet.set(e.det, [...(byDet.get(e.det) ?? []), e]);
    const best = [...byDet.values()].sort((a, b) => b.length - a.length)[0];
    if (best && best.length > list.length) list = [...best].sort((a, b) => a.mjd_mid - b.mjd_mid);
  }
  const n = list.length;
  if (n <= max) return list;
  if (clock === "hours" && Number.isFinite(mjd)) {
    let near = 0;
    for (let i = 1; i < n; i++) if (Math.abs(list[i].mjd_mid - mjd) < Math.abs(list[near].mjd_mid - mjd)) near = i;
    const start = Math.max(0, Math.min(n - max, near - Math.floor((max - 1) / 2)));
    return list.slice(start, start + max);
  }
  const idx = new Set<number>();
  for (let i = 0; i < max; i++) idx.add(Math.round((i * (n - 1)) / (max - 1)));
  return [...idx].sort((a, b) => a - b).map((i) => list[i]);
}

export interface Window {
  x0: number;
  y0: number;
  w: number;
  h: number;
}

/** A size × size window round (cx, cy), slid to stay inside an nx × ny frame. */
export function cropWindow(nx: number, ny: number, cx: number, cy: number, size = CROP_PX): Window {
  const w = Math.min(nx, size);
  const h = Math.min(ny, size);
  const x0 = Math.max(0, Math.min(nx - w, Math.round(cx - w / 2)));
  const y0 = Math.max(0, Math.min(ny - h, Math.round(cy - h / 2)));
  return { x0, y0, w, h };
}

export function cropValues(values: Float32Array, nx: number, win: Window): Float32Array {
  const out = new Float32Array(win.w * win.h);
  for (let y = 0; y < win.h; y++) {
    const row = (win.y0 + y) * nx + win.x0;
    out.set(values.subarray(row, row + win.w), y * win.w);
  }
  return out;
}

/** "Visit 2 of 3, 18 Mar 2026" or "Exposure 1 of 4, 18 Mar 2026, 14:05 UTC". */
export function epochCaption(e: Pick<Epoch, "kind" | "mjd_mid">, i: number, n: number): string {
  const what = e.kind === "exposure" ? "Exposure" : "Visit";
  return `${what} ${i + 1} of ${n}, ${formatMjd(e.mjd_mid, e.kind === "exposure")}`;
}

type RGB = [number, number, number];

function hex(s: string, fb: RGB): RGB {
  const m = /^#?([0-9a-f]{6})$/i.exec(s.trim());
  if (!m) return fb;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export interface PlateInk {
  glass: RGB;
  ink: RGB;
  nodata: RGB;
  hatch: RGB;
}

export function readPlateInk(): PlateInk {
  let cs: CSSStyleDeclaration | null = null;
  try {
    cs = getComputedStyle(document.documentElement);
  } catch {
    cs = null;
  }
  const v = (name: string, fb: RGB) => (cs ? hex(cs.getPropertyValue(name), fb) : fb);
  return {
    glass: v("--glass", [237, 240, 239]),
    ink: v("--ink", [24, 32, 46]),
    nodata: v("--sheet-2", [227, 232, 231]),
    hatch: v("--hair", [205, 211, 214]),
  };
}

/** Paint values as a negative: high → ink, sky → glass; no data flat with a fine diagonal hatch. */
export function paintNegative(values: Float32Array, w: number, h: number, lim: Limits, ink: PlateInk): Uint8ClampedArray<ArrayBuffer> {
  const out = new Uint8ClampedArray(new ArrayBuffer(w * h * 4));
  for (let i = 0; i < w * h; i++) {
    const v = values[i];
    let c: RGB;
    if (Number.isNaN(v)) {
      const x = i % w;
      const y = Math.floor(i / w);
      c = (x + y) % 4 === 0 ? ink.hatch : ink.nodata;
    } else {
      const t = stretch(v, lim, "asinh");
      c = [
        Math.round(ink.glass[0] + (ink.ink[0] - ink.glass[0]) * t),
        Math.round(ink.glass[1] + (ink.ink[1] - ink.glass[1]) * t),
        Math.round(ink.glass[2] + (ink.ink[2] - ink.glass[2]) * t),
      ];
    }
    out[i * 4] = c[0];
    out[i * 4 + 1] = c[1];
    out[i * 4 + 2] = c[2];
    out[i * 4 + 3] = 255;
  }
  return out;
}

export interface MiniBlinkHandle {
  play(): void;
  pause(): void;
  destroy(): void;
}

export interface MiniBlinkOptions {
  candidate: Candidate;
  frames: FramesResponse;
  jsonUrl: string;
  /** Start paused (prefers-reduced-motion): a play button starts it. */
  paused: boolean;
  red: string;
  signal?: AbortSignal;
}

/** Load the epochs, then blink them in `host`. Rejects with a plain message when frames can't load. */
export async function mountMiniBlink(host: HTMLElement, o: MiniBlinkOptions): Promise<MiniBlinkHandle> {
  const { candidate: c, frames } = o;
  const epochs = pickEpochs(frames, c.det, c.clock, c.mjd);
  if (epochs.length < 2) throw new Error("SPHEREx has only one usable frame here, so there is nothing to blink.");
  const raws = await Promise.all(epochs.map((e) => loadFrameData(e, o.jsonUrl, o.signal)));
  if (o.signal?.aborted) throw new DOMException("aborted", "AbortError");
  const [nx, ny] = frames.size;
  const crops: { values: Float32Array; win: Window; px: { x: number; y: number } }[] = [];
  for (let i = 0; i < epochs.length; i++) {
    const e = epochs[i];
    const center = e.center ?? frames.center;
    const px = skyToPixel(c, frames, center);
    const win = cropWindow(nx, ny, px.x, px.y);
    crops.push({ values: cropValues(toMJySr(raws[i], e), nx, win), win, px });
  }
  const lim = robustLimits(
    crops.map((k) => k.values),
    0.35,
  );
  const ink = readPlateInk();
  const images = crops.map((k) => {
    const cv = document.createElement("canvas");
    cv.width = k.win.w;
    cv.height = k.win.h;
    const ctx = cv.getContext("2d");
    if (ctx) ctx.putImageData(new ImageData(paintNegative(k.values, k.win.w, k.win.h, lim, ink), k.win.w, k.win.h), 0, 0);
    return cv;
  });

  host.innerHTML = `
    <div class="atl-mini-plate"><canvas class="atl-mini-canvas" aria-hidden="true"></canvas></div>
    <div class="atl-mini-bar">
      <button class="btn btn-ghost atl-mini-play" type="button"></button>
      <p class="atl-mini-cap" aria-live="off"></p>
    </div>`;
  const canvas = host.querySelector<HTMLCanvasElement>(".atl-mini-canvas")!;
  const cap = host.querySelector<HTMLParagraphElement>(".atl-mini-cap")!;
  const btn = host.querySelector<HTMLButtonElement>(".atl-mini-play")!;
  const plate = host.querySelector<HTMLDivElement>(".atl-mini-plate")!;
  plate.setAttribute("role", "img");
  plate.setAttribute(
    "aria-label",
    `${epochs.length} SPHEREx frames of this spot, ${epochs[0].kind === "exposure" ? "hours" : "months"} apart, shown in turn. The object is in the red reticle.`,
  );

  const css = 260;
  const dpr = Math.max(1, Math.min(3, globalThis.devicePixelRatio || 1));
  canvas.width = Math.round(css * dpr);
  canvas.height = Math.round(css * dpr);
  let i = 0;
  let timer: ReturnType<typeof setInterval> | null = null;

  function draw() {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const k = crops[i];
    const img = images[i];
    const scale = canvas.width / Math.max(k.win.w, k.win.h);
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = `rgb(${ink.nodata.join(",")})`;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, k.win.w * scale, k.win.h * scale);
    // The candidate: an open reticle with a gap, so the dot itself stays visible.
    const x = (k.px.x - k.win.x0 + 0.5) * scale;
    const y = (k.px.y - k.win.y0 + 0.5) * scale;
    ctx.strokeStyle = o.red;
    ctx.globalAlpha = 0.85;
    ctx.lineWidth = 1.5 * dpr;
    ctx.lineCap = "round";
    const r0 = Math.max(7 * dpr, scale * 1.6);
    const r1 = r0 + 9 * dpr;
    ctx.beginPath();
    for (const [dx, dy] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ]) {
      ctx.moveTo(x + dx * r0, y + dy * r0);
      ctx.lineTo(x + dx * r1, y + dy * r1);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
    cap.textContent = epochCaption(epochs[i], i, epochs.length);
  }

  function setPlaying(on: boolean) {
    if (timer) clearInterval(timer);
    timer = null;
    if (on) {
      timer = setInterval(() => {
        i = (i + 1) % epochs.length;
        draw();
      }, BLINK_MS);
    }
    btn.textContent = on ? "Pause" : "Play the blink";
  }
  btn.addEventListener("click", () => setPlaying(!timer));
  draw();
  setPlaying(!o.paused);

  return {
    play: () => setPlaying(true),
    pause: () => setPlaying(false),
    destroy() {
      setPlaying(false);
      host.innerHTML = "";
    },
  };
}
