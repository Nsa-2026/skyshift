// The blink player (P4). Draws SPHEREx frames of one detector on a canvas and blinks them on
// the months clock (visit stacks) or the hours clock (single exposures). Seam: src/app/modules.ts.
import "./blink.css";
import { ALLOWED_FOV, DEFAULT_FOV, detectorsIn, epochsFor, loadFrameData, loadTargetIndex, toMJySr } from "../api/client";
import type { Clock, Det, Epoch, FramesResponse, SkyPos, TargetIndex } from "../api/types";
import { DETECTORS, SAME_MOMENT, formatMjd, formatRaDec, gaiaAt, pixelToSky, skyToPixel } from "../app/astro";
import type { BlinkHandle, BlinkOptions, MountBlink, View } from "../app/modules";
import { buildAxis, formatGap, nearestIndex, type Axis } from "./axis";
import { median, mean, niceArcsec, offset, robustLimits, subtract, symmetricLimits, type Limits, type StretchKind } from "./math";
import { buildLut, paint, readPalette, type Palette } from "./paint";

type Mode = "blink" | "subtract" | "add";
const DETS: Det[] = [1, 2, 3, 4, 5, 6];
const FPS = [1, 2, 4, 8];
const PSF_FWHM_ARCSEC = 7.53;
const FAST_STAR_MAS_YR = 300;
const html = String.raw;

interface Frame {
  epoch: Epoch;
  vals: Float32Array;
  sky: number;
}

// Featured targets list their precomputed clocks and fields; any other spot goes to the live API.
const indexCache = new Map<string, Promise<TargetIndex | null>>();
function targetIndex(slug: string): Promise<TargetIndex | null> {
  let p = indexCache.get(slug);
  if (!p) {
    p = loadTargetIndex(slug);
    indexCache.set(slug, p);
  }
  return p;
}

// Frames are small (≤ 300 × 300 × 2 bytes); keep recent ones so switching back is instant.
const frameCache = new Map<string, Promise<Uint16Array>>();
function cachedFrame(epoch: Epoch, jsonUrl: string): Promise<Uint16Array> {
  const key = new URL(epoch.data_url, jsonUrl).href;
  let p = frameCache.get(key);
  if (!p) {
    p = loadFrameData(epoch, jsonUrl);
    p.catch(() => frameCache.delete(key));
    frameCache.set(key, p);
    if (frameCache.size > 400) frameCache.delete(frameCache.keys().next().value as string);
  }
  return p;
}

async function pool<T, R>(items: T[], n: number, fn: (t: T) => Promise<R>, done: () => void): Promise<(R | Error)[]> {
  const out: (R | Error)[] = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      try {
        out[i] = await fn(items[i]);
      } catch (e) {
        out[i] = e as Error;
      }
      done();
    }
  };
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
  return out;
}

const pct = (f: number) => (f >= 0.1 ? `${(f * 100).toFixed(0)}%` : f > 0 ? `${(f * 100).toFixed(f >= 0.01 ? 1 : 2)}%` : "0%");
const um = (n: number) => n.toFixed(2);
const icon = {
  prev: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 4h2v12H5zM16 4v12L8 10z"/></svg>`,
  next: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M13 4h2v12h-2zM4 4v12l8-6z"/></svg>`,
  play: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 3.5v13L16.5 10z"/></svg>`,
  pause: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 4h3.5v12H5zM11.5 4H15v12h-3.5z"/></svg>`,
};

export const mountBlink: MountBlink = (el: HTMLElement, opts: BlinkOptions): BlinkHandle => {
  el.innerHTML = html`
    <div class="bk">
      <div class="bk-bar">
        <div class="bk-seg" role="group" aria-label="Clock">
          <button class="bk-btn" type="button" data-clock="months" aria-pressed="false"
            title="One median stack per visit, visits about six months apart">Months</button>
          <button class="bk-btn" type="button" data-clock="hours" aria-pressed="false"
            title="Single exposures in time order, minutes to days apart">Hours</button>
        </div>
        <div class="bk-seg bk-fov" role="group" aria-label="Field of view"></div>
        <div class="bk-ramp" role="group" aria-label="Detector">
          ${DETS.map(
            (d) => html`<button class="bk-det det-${d}" type="button" data-det="${d}" aria-pressed="false"
              title="D${d}, ${DETECTORS[d].min}–${DETECTORS[d].max} µm (${DETECTORS[d].band}). Exposed at the same moment as D${SAME_MOMENT[d]}: a change between them is colour, not motion.">
              <span class="bk-det-name">D${d}</span><span class="bk-det-um">${DETECTORS[d].min}–${DETECTORS[d].max}</span>
            </button>`,
          ).join("")}
        </div>
      </div>

      <div class="bk-body">
        <div class="bk-stage-wrap">
          <div class="bk-stage">
            <canvas class="bk-canvas" aria-label="SPHEREx frame. Click a dot to flag it."></canvas>
            <canvas class="bk-loupe" width="136" height="136" hidden aria-hidden="true"></canvas>
            <p class="bk-tag" hidden></p>
            <div class="bk-veil" hidden>
              <div class="bk-veil-box" role="status" aria-live="polite">
                <p class="bk-veil-msg"></p>
                <div class="bk-progress" hidden><i></i></div>
                <div class="bk-veil-actions"></div>
              </div>
            </div>
          </div>
          <p class="bk-read"><span class="bk-read-pos">Point at the image to read a pixel; click a dot to flag it.</span><span class="bk-read-val"></span></p>
        </div>

        <div class="bk-rail">
          <section class="bk-group">
            <h3 class="bk-h">View</h3>
            <div class="bk-seg bk-wide" role="group" aria-label="View mode">
              <button class="bk-btn" type="button" data-mode="blink" aria-pressed="true">Blink</button>
              <button class="bk-btn" type="button" data-mode="subtract" aria-pressed="false">Subtract</button>
              <button class="bk-btn" type="button" data-mode="add" aria-pressed="false">Add</button>
            </div>
            <p class="bk-hint bk-mode-hint"></p>
          </section>
          <section class="bk-group">
            <h3 class="bk-h"><label for="bk-ref">Reference</label></h3>
            <select id="bk-ref" class="bk-select bk-ref"></select>
            <button class="bk-btn bk-hold" type="button" title="Hold (or hold C) to show the reference frame">Hold to compare</button>
          </section>
          <section class="bk-group">
            <h3 class="bk-h">Stretch</h3>
            <div class="bk-seg bk-wide" role="group" aria-label="Stretch">
              <button class="bk-btn" type="button" data-stretch="linear" aria-pressed="false">Linear</button>
              <button class="bk-btn" type="button" data-stretch="asinh" aria-pressed="true">Asinh</button>
            </div>
            <label class="bk-range"><span>Contrast</span>
              <input class="bk-contrast" type="range" min="0" max="1" step="0.01" value="0.45" /></label>
            <div class="bk-toggles">
              <button class="bk-tog" type="button" data-opt="invert" aria-pressed="false">Invert</button>
              <button class="bk-tog" type="button" data-opt="level" aria-pressed="true"
                title="Subtract each frame's median, so the changing zodiacal glow doesn't flicker">Level sky</button>
            </div>
          </section>
          <section class="bk-group">
            <h3 class="bk-h">Overlays</h3>
            <div class="bk-toggles">
              <button class="bk-tog" type="button" data-ov="gaia" aria-pressed="true"><i class="sw sw-gaia"></i>Gaia</button>
              <button class="bk-tog" type="button" data-ov="known" aria-pressed="true"><i class="sw sw-known"></i>Known</button>
              <button class="bk-tog" type="button" data-ov="target" aria-pressed="true"><i class="sw sw-mover"></i>Target</button>
              <button class="bk-tog" type="button" data-ov="guides" aria-pressed="true"><i class="sw sw-guide"></i>Compass</button>
            </div>
          </section>
          <section class="bk-group">
            <h3 class="bk-h">Zoom</h3>
            <div class="bk-seg bk-wide" role="group" aria-label="Zoom">
              <button class="bk-btn" type="button" data-zoom="1" aria-pressed="true">1×</button>
              <button class="bk-btn" type="button" data-zoom="2" aria-pressed="false">2×</button>
              <button class="bk-btn" type="button" data-zoom="4" aria-pressed="false">4×</button>
            </div>
          </section>
          <p class="bk-keys">Space plays. ← → step. Hold C to compare.</p>
        </div>
      </div>

      <div class="bk-time">
        <div class="bk-transport">
          <button class="bk-icon bk-prev" type="button" aria-label="Previous frame">${icon.prev}</button>
          <button class="bk-icon bk-play" type="button" aria-label="Play">${icon.play}</button>
          <button class="bk-icon bk-next" type="button" aria-label="Next frame">${icon.next}</button>
          <label class="bk-speed"><span>Speed</span>
            <select class="bk-select bk-fps">${FPS.map((f) => `<option value="${f}">${f}/s</option>`).join("")}</select>
          </label>
        </div>
        <div class="bk-legend">
          <p class="bk-which"></p>
          <p class="bk-when"><span class="bk-date"></span><span class="bk-clocktime"></span></p>
          <dl class="bk-facts"></dl>
        </div>
        <div class="bk-axis" role="slider" tabindex="0" aria-label="Epoch">
          <div class="bk-axis-track"></div>
          <div class="bk-axis-labels"></div>
        </div>
      </div>
    </div>
  `;

  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const $$ = <T extends Element>(sel: string) => [...el.querySelectorAll<T>(sel)];
  const root = $<HTMLDivElement>(".bk");
  const stage = $<HTMLDivElement>(".bk-stage");
  const canvas = $<HTMLCanvasElement>(".bk-canvas");
  const ctx = canvas.getContext("2d");
  const loupe = $<HTMLCanvasElement>(".bk-loupe");
  const lctx = loupe.getContext("2d");
  const tag = $<HTMLParagraphElement>(".bk-tag");
  const veil = $<HTMLDivElement>(".bk-veil");
  const veilMsg = $<HTMLParagraphElement>(".bk-veil-msg");
  const veilBar = $<HTMLDivElement>(".bk-progress");
  const veilActions = $<HTMLDivElement>(".bk-veil-actions");
  const refSel = $<HTMLSelectElement>(".bk-ref");
  const fpsSel = $<HTMLSelectElement>(".bk-fps");
  const contrastIn = $<HTMLInputElement>(".bk-contrast");
  const axisEl = $<HTMLDivElement>(".bk-axis");
  const track = $<HTMLDivElement>(".bk-axis-track");
  const axisLabels = $<HTMLDivElement>(".bk-axis-labels");
  const playBtn = $<HTMLButtonElement>(".bk-play");
  const off = document.createElement("canvas");
  const offCtx = off.getContext("2d");

  // ---- state ------------------------------------------------------------------------------
  let palette: Palette = readPalette(el);
  let fontFamily = getComputedStyle(el).fontFamily || "system-ui";
  let view: View | null = null;
  let F: FramesResponse | null = null;
  let jsonUrl = "";
  let frames: Frame[] = [];
  let failed = 0;
  let idx = 0;
  let ref = 0;
  let mode: Mode = "blink";
  let stretchKind: StretchKind = "asinh";
  let contrast = 0.45;
  let invert = false;
  let levelSky = true;
  const overlays = { gaia: true, known: true, target: true, guides: true };
  let zoom = 1;
  let centre = { u: 0, v: 0 }; // view centre in image edge coordinates (pixel i spans [i, i+1])
  let playing = false;
  let fps = 2;
  let timer = 0;
  let comparing = false;
  let pick: SkyPos | null = null;
  let hover: { u: number; v: number; X: number; Y: number } | null = null;
  let loadCtl = 0; // generation counter: a newer show() wins
  let ownBusy = false;
  let appStatus: { kind: "loading" | "error"; message: string } | null = null;
  let axis: Axis | null = null;
  let shown: Float32Array | null = null;
  let shownSigned = false;
  let firstShow = true;
  const leveled = new Map<number, Float32Array>();
  let meanVals: Float32Array | null = null;
  const limitCache = new Map<string, Limits>();
  let lut: { key: string; data: Uint8ClampedArray } = { key: "", data: new Uint8ClampedArray(0) };
  let cssSize = 0;
  let dpr = 1;
  fpsSel.value = String(fps);

  // ---- data helpers -------------------------------------------------------------------------
  const size = (): [number, number] => F?.size ?? [1, 1];
  const nLoaded = () => frames.length;
  const cur = (): Frame | null => frames[idx] ?? null;
  const shownEpoch = (): Epoch | null => (comparing ? frames[ref]?.epoch : cur()?.epoch) ?? null;
  const centreOf = (e: Epoch | null): SkyPos => e?.center ?? F?.center ?? { ra: 0, dec: 0 };
  const unit = () => (view?.clock === "hours" ? "exposure" : "visit");

  function level(i: number): Float32Array {
    const f = frames[i];
    if (!levelSky) return f.vals;
    let a = leveled.get(i);
    if (!a) {
      a = offset(f.vals, f.sky);
      leveled.set(i, a);
    }
    return a;
  }
  function meanFrame(): Float32Array {
    meanVals ??= mean(frames.map((_, i) => level(i)));
    return meanVals;
  }
  function resetCaches() {
    leveled.clear();
    meanVals = null;
    limitCache.clear();
  }
  function limitsFor(kind: Mode): Limits {
    const key = `${kind}|${kind === "subtract" ? ref : ""}|${contrast}|${levelSky}`;
    let l = limitCache.get(key);
    if (!l) {
      if (kind === "subtract") {
        const others = frames.map((_, i) => i).filter((i) => i !== ref);
        const diffs = (others.length ? others : [ref]).map((i) => subtract(level(i), level(ref)));
        l = symmetricLimits(diffs, contrast);
      } else if (kind === "add") {
        l = robustLimits([meanFrame()], contrast);
      } else {
        l = robustLimits(frames.map((_, i) => level(i)), contrast);
      }
      limitCache.set(key, l);
    }
    return l;
  }

  // ---- painting ----------------------------------------------------------------------------
  function paintImage() {
    if (!F || !frames.length || !offCtx) return;
    const [nx, ny] = size();
    let values: Float32Array;
    let lim: Limits;
    let signed = false;
    if (comparing || mode === "blink") {
      values = level(comparing ? ref : idx);
      lim = limitsFor("blink");
    } else if (mode === "subtract") {
      values = subtract(level(idx), level(ref));
      lim = limitsFor("subtract");
      signed = true;
    } else {
      values = meanFrame();
      lim = limitsFor("add");
    }
    const key = `${signed}|${invert}`;
    if (lut.key !== key) lut = { key, data: buildLut(palette, signed, invert) };
    if (off.width !== nx || off.height !== ny) {
      off.width = nx;
      off.height = ny;
    }
    const img = offCtx.createImageData(nx, ny);
    paint(values, nx, img.data, lim, stretchKind, signed, lut.data, palette);
    offCtx.putImageData(img, 0, 0);
    shown = values;
    shownSigned = signed;
    draw();
  }

  /** Image edge coordinates (u, v) of the visible window and screen pixels per image pixel. */
  function viewport() {
    const [nx, ny] = size();
    const W = cssSize;
    const vw = nx / zoom;
    const vh = ny / zoom;
    const s = Math.min(W / vw, W / vh);
    const u0 = Math.min(Math.max(centre.u - vw / 2, 0), nx - vw);
    const v0 = Math.min(Math.max(centre.v - vh / 2, 0), ny - vh);
    const ox = (W - vw * s) / 2;
    const oy = (W - vh * s) / 2;
    return { u0, v0, vw, vh, s, ox, oy };
  }
  const toScreen = (px: { x: number; y: number }, vp = viewport()) => ({
    X: vp.ox + (px.x + 0.5 - vp.u0) * vp.s,
    Y: vp.oy + (px.y + 0.5 - vp.v0) * vp.s,
  });
  const toImage = (X: number, Y: number, vp = viewport()) => ({ u: vp.u0 + (X - vp.ox) / vp.s, v: vp.v0 + (Y - vp.oy) / vp.s });

  function cssColor(name: string): string {
    return getComputedStyle(el).getPropertyValue(name).trim() || "#fff";
  }

  function draw() {
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = cssColor("--plate");
    ctx.fillRect(0, 0, cssSize, cssSize);
    if (!F || !frames.length) return;
    const vp = viewport();
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(off, vp.u0, vp.v0, vp.vw, vp.vh, vp.ox, vp.oy, vp.vw * vp.s, vp.vh * vp.s);
    drawOverlays(vp);
    drawLoupe(vp);
  }

  function halo(width: number) {
    if (!ctx) return;
    ctx.strokeStyle = "rgba(15, 26, 44, 0.75)";
    ctx.lineWidth = width + 2;
    ctx.stroke();
  }

  function drawOverlays(vp: ReturnType<typeof viewport>) {
    if (!ctx || !F || !view) return;
    const e = mode === "add" && !comparing ? null : shownEpoch();
    const mjd = e?.mjd_mid ?? (frames[0].epoch.mjd_mid + frames[frames.length - 1].epoch.mjd_mid) / 2;
    const c = centreOf(e ?? cur()?.epoch ?? null);
    const inView = (p: { X: number; Y: number }, m = 20) =>
      p.X > vp.ox - m && p.X < vp.ox + vp.vw * vp.s + m && p.Y > vp.oy - m && p.Y < vp.oy + vp.vh * vp.s + m;
    const psfR = (PSF_FWHM_ARCSEC / F.pixel_arcsec) * vp.s * 0.85;
    const W = cssSize;

    ctx.save();
    ctx.beginPath();
    ctx.rect(vp.ox, vp.oy, vp.vw * vp.s, vp.vh * vp.s);
    ctx.clip();

    if (overlays.gaia) {
      const gaia = cssColor("--gaia");
      const r = Math.max(5, Math.min(14, psfR * 0.7));
      const moving = !frames.some((f) => f.epoch.center);
      // Single exposures reach about G 16.5, visit stacks deeper. Crowded fields drop the faint ones.
      const faintG = view.clock === "hours" ? 16.5 : 18;
      const skipFaint = F.gaia.length > 600;
      for (const star of F.gaia) {
        const p = toScreen(skyToPixel(gaiaAt(star, mjd), F, c), vp);
        if (!inView(p)) continue;
        // Faint Gaia stars are mostly below SPHEREx's reach: draw them small and quiet.
        const g = star.g ?? 20;
        const faint = g > faintG;
        if (faint && skipFaint) continue;
        ctx.globalAlpha = faint ? 0.22 : g <= 13 ? 0.95 : 0.95 - ((g - 13) / 5) * 0.45;
        const pm = Math.hypot(star.pmra ?? 0, star.pmdec ?? 0);
        if (pm > FAST_STAR_MAS_YR && moving && frames.length > 1) {
          // Fast star: Gaia's predicted track through every epoch of this detector.
          ctx.beginPath();
          frames.forEach((f, i) => {
            const q = toScreen(skyToPixel(gaiaAt(star, f.epoch.mjd_mid), F!, c), vp);
            if (i) ctx.lineTo(q.X, q.Y);
            else ctx.moveTo(q.X, q.Y);
          });
          ctx.setLineDash([2, 3]);
          ctx.strokeStyle = gaia;
          ctx.lineWidth = 1;
          ctx.stroke();
          ctx.setLineDash([]);
          for (const f of frames) {
            const q = toScreen(skyToPixel(gaiaAt(star, f.epoch.mjd_mid), F, c), vp);
            ctx.beginPath();
            ctx.arc(q.X, q.Y, 1.8, 0, Math.PI * 2);
            ctx.fillStyle = gaia;
            ctx.fill();
          }
        }
        ctx.beginPath();
        ctx.arc(p.X, p.Y, faint ? Math.max(3, r * 0.45) : r, 0, Math.PI * 2);
        ctx.strokeStyle = gaia;
        ctx.lineWidth = faint ? 1 : 1.25;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    if (overlays.known && e) {
      const known = cssColor("--known");
      const list = F.known.filter((k) => k.epoch_id === e.id);
      ctx.font = `500 12px ${fontFamily}`;
      for (const k of list) {
        const p = toScreen(skyToPixel(k, F, c), vp);
        if (!inView(p, 0)) continue;
        const r = Math.max(7, Math.min(24, psfR * 1.1));
        ctx.beginPath();
        ctx.moveTo(p.X, p.Y - r);
        ctx.lineTo(p.X + r, p.Y);
        ctx.lineTo(p.X, p.Y + r);
        ctx.lineTo(p.X - r, p.Y);
        ctx.closePath();
        halo(1.5);
        ctx.strokeStyle = known;
        ctx.lineWidth = 1.5;
        ctx.stroke();
        if (list.length <= 12) label(k.name, p.X + r + 4, p.Y + 4, known);
      }
    }

    if (overlays.target) {
      const t = e?.center ?? (view.slug ? { ra: view.ra, dec: view.dec } : F.center);
      const p = toScreen(skyToPixel(t, F, c), vp);
      const r = Math.max(9, Math.min(26, psfR * 1.3));
      ctx.beginPath();
      for (const [dx, dy] of [
        [1, 0],
        [-1, 0],
        [0, 1],
        [0, -1],
      ]) {
        ctx.moveTo(p.X + dx * r, p.Y + dy * r);
        ctx.lineTo(p.X + dx * (r + 9), p.Y + dy * (r + 9));
      }
      halo(2);
      ctx.strokeStyle = cssColor("--mover");
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    if (pick) {
      const p = toScreen(skyToPixel(pick, F, c), vp);
      const mover = cssColor("--mover");
      ctx.beginPath();
      ctx.arc(p.X, p.Y, 11, 0, Math.PI * 2);
      halo(2);
      ctx.strokeStyle = mover;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(p.X, p.Y, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = mover;
      ctx.fill();
    }
    ctx.restore();

    if (overlays.guides) {
      const ink = cssColor("--emulsion");
      ctx.font = `600 12px ${fontFamily}`;
      // Scale bar, bottom left.
      const { arcsec, label: text } = niceArcsec((W * 0.22 * F.pixel_arcsec) / vp.s);
      const len = (arcsec / F.pixel_arcsec) * vp.s;
      const x0 = 14;
      const y0 = W - 16;
      ctx.beginPath();
      ctx.moveTo(x0, y0 - 5);
      ctx.lineTo(x0, y0);
      ctx.lineTo(x0 + len, y0);
      ctx.lineTo(x0 + len, y0 - 5);
      halo(1.5);
      ctx.strokeStyle = ink;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      label(text, x0, y0 - 9, ink);
      // Compass, bottom right: north up, east left.
      const cx = W - 18;
      const cy = W - 18;
      const L = 30;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx, cy - L);
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx - L, cy);
      ctx.moveTo(cx - 4, cy - L + 6);
      ctx.lineTo(cx, cy - L);
      ctx.lineTo(cx + 4, cy - L + 6);
      ctx.moveTo(cx - L + 6, cy - 4);
      ctx.lineTo(cx - L, cy);
      ctx.lineTo(cx - L + 6, cy + 4);
      halo(1.5);
      ctx.strokeStyle = ink;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      label("N", cx - 4, cy - L - 6, ink);
      label("E", cx - L - 13, cy + 4, ink);
    }
  }

  function label(text: string, x: number, y: number, color: string) {
    if (!ctx) return;
    ctx.lineJoin = "round";
    ctx.strokeStyle = "rgba(15, 26, 44, 0.85)";
    ctx.lineWidth = 3;
    ctx.strokeText(text, x, y);
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
  }

  function drawLoupe(vp: ReturnType<typeof viewport>) {
    if (!lctx || !hover || !F) {
      loupe.hidden = true;
      return;
    }
    const [nx, ny] = size();
    const half = 7.5;
    const u = Math.floor(hover.u) + 0.5;
    const v = Math.floor(hover.v) + 0.5;
    if (u < 0 || v < 0 || u > nx || v > ny) {
      loupe.hidden = true;
      return;
    }
    loupe.hidden = false;
    const L = loupe.width;
    lctx.imageSmoothingEnabled = false;
    lctx.fillStyle = cssColor("--plate");
    lctx.fillRect(0, 0, L, L);
    const k = L / (2 * half);
    const sx = u - half;
    const sy = v - half;
    // drawImage needs a source rect inside the image; shift the destination for edges.
    const cx0 = Math.max(0, sx);
    const cy0 = Math.max(0, sy);
    const cx1 = Math.min(nx, sx + 2 * half);
    const cy1 = Math.min(ny, sy + 2 * half);
    lctx.drawImage(off, cx0, cy0, cx1 - cx0, cy1 - cy0, (cx0 - sx) * k, (cy0 - sy) * k, (cx1 - cx0) * k, (cy1 - cy0) * k);
    lctx.strokeStyle = cssColor("--mover");
    lctx.lineWidth = 1.5;
    lctx.strokeRect(L / 2 - k / 2, L / 2 - k / 2, k, k);
    // Keep the loupe away from the cursor.
    const right = hover.X < vp.ox + vp.vw * vp.s * 0.6 || hover.Y > cssSize * 0.45;
    loupe.classList.toggle("is-left", !right);
  }

  // ---- UI sync -----------------------------------------------------------------------------
  function press(sel: string, attr: string, value: string) {
    $$<HTMLButtonElement>(sel).forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute(attr) === value)));
  }

  function renderBar() {
    if (!view) return;
    press("[data-clock]", "data-clock", view.clock);
    const fovs = ALLOWED_FOV[view.clock];
    $(".bk-fov").innerHTML = fovs
      .map(
        (f) => html`<button class="bk-btn" type="button" data-fov="${f}" aria-pressed="${f === view!.fov}"
          title="${f} arcminute field">${f}′</button>`,
      )
      .join("");
    const have = F ? detectorsIn(F) : [];
    $$<HTMLButtonElement>("[data-det]").forEach((b) => {
      const d = Number(b.dataset.det) as Det;
      b.setAttribute("aria-pressed", String(d === view!.det));
      b.disabled = !!F && !have.includes(d);
    });
    root.style.setProperty("--det", `var(--d${view.det})`);
    markAvailable();
  }

  /** Featured targets: disable the clocks and fields that have no precomputed frames. */
  async function markAvailable() {
    const v = view;
    if (!v?.slug) return;
    const idx = await targetIndex(v.slug);
    if (!idx || view !== v) return;
    $$<HTMLButtonElement>("[data-clock]").forEach((b) => {
      const ok = idx.frames.some((f) => f.clock === b.dataset.clock);
      b.disabled = !ok && b.dataset.clock !== v.clock;
      b.dataset.tip ??= b.title;
      b.title = ok ? b.dataset.tip : `No ${b.dataset.clock} frames are prepared for ${idx.name}.`;
    });
    $$<HTMLButtonElement>("[data-fov]").forEach((b) => {
      const ok = idx.frames.some((f) => f.clock === v.clock && f.fov === Number(b.dataset.fov));
      b.disabled = !ok && Number(b.dataset.fov) !== v.fov;
    });
  }

  function renderControls() {
    press("[data-mode]", "data-mode", mode);
    press("[data-stretch]", "data-stretch", stretchKind);
    press("[data-zoom]", "data-zoom", String(zoom));
    $$<HTMLButtonElement>("[data-opt]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.opt === "invert" ? invert : levelSky)),
    );
    $$<HTMLButtonElement>("[data-ov]").forEach((b) =>
      b.setAttribute("aria-pressed", String(overlays[b.dataset.ov as keyof typeof overlays])),
    );
    const n = nLoaded();
    const hint: Record<Mode, string> = {
      blink: `Each ${unit()} in time order. Anything that moves jumps between frames.`,
      subtract: `Each ${unit()} minus the reference. Orange: light arrived. Blue: light left.`,
      add: `Mean of all ${n} ${unit()}s. Steady stars sharpen; movers smear or fade.`,
    };
    $(".bk-mode-hint").textContent = hint[mode];
    const stepDisabled = mode === "add" || n < 2 || ownBusy;
    for (const b of [playBtn, $<HTMLButtonElement>(".bk-prev"), $<HTMLButtonElement>(".bk-next")]) b.disabled = stepDisabled;
    $<HTMLButtonElement>(".bk-hold").disabled = n < 2;
    refSel.disabled = n < 2;
    playBtn.innerHTML = playing ? icon.pause : icon.play;
    playBtn.setAttribute("aria-label", playing ? "Pause" : "Play");
    playBtn.setAttribute("aria-pressed", String(playing));
    root.dataset.mode = mode;
  }

  function renderRefSelect() {
    refSel.innerHTML = frames
      .map(
        (f, i) =>
          `<option value="${i}">${
            view?.clock === "hours"
              ? `Exposure ${i + 1}, ${formatMjd(f.epoch.mjd_mid, true).replace(/ \d{4},/, "").replace(" UTC", "")}`
              : `Visit ${i + 1}, ${formatMjd(f.epoch.mjd_mid)}`
          }</option>`,
      )
      .join("");
    refSel.value = String(ref);
  }

  function renderLegend() {
    const which = $(".bk-which");
    const date = $(".bk-date");
    const time = $(".bk-clocktime");
    const facts = $(".bk-facts");
    const n = nLoaded();
    const e = shownEpoch();
    if (!e || !view) {
      which.textContent = "";
      date.textContent = "";
      time.textContent = "";
      facts.innerHTML = "";
      return;
    }
    const U = view.clock === "hours" ? "Exposure" : "Visit";
    const i = comparing ? ref : idx;
    if (mode === "add" && !comparing) which.textContent = `Mean of ${n} ${unit()}s`;
    else if (mode === "subtract" && !comparing) which.textContent = `${U} ${idx + 1} minus ${unit()} ${ref + 1} (reference)`;
    else which.textContent = `${U} ${i + 1} of ${n}${comparing ? " (reference)" : ""}`;
    if (mode === "add" && !comparing) {
      date.textContent = `${formatMjd(frames[0].epoch.mjd_start)} to ${formatMjd(frames[n - 1].epoch.mjd_end)}`;
      time.textContent = "";
    } else if (view.clock === "hours") {
      const d = formatMjd(e.mjd_mid, true);
      const cut = d.lastIndexOf(",");
      date.textContent = cut > 0 ? d.slice(0, cut) : d;
      time.textContent = cut > 0 ? d.slice(cut + 1).trim() : "";
    } else {
      date.textContent = formatMjd(e.mjd_mid);
      time.textContent = `${formatGap(Math.max(e.mjd_end - e.mjd_start, 1 / 1440))} visit`;
    }
    const dt = e.mjd_mid - frames[0].epoch.mjd_mid;
    const rows: [string, string][] = [
      ["Detector", `<i class="sw sw-det"></i>D${e.det} <span class="bk-um">${um(e.um_min)}–${um(e.um_max)} µm</span>`],
      ["Exposures", String(mode === "add" && !comparing ? frames.reduce((s, f) => s + f.epoch.n_exposures, 0) : e.n_exposures)],
      ["Flagged", pct(e.flag_frac)],
      ["Saturated", pct(e.sat_frac)],
    ];
    if (mode !== "add" && i > 0) rows.push(["Since first", formatGap(dt)]);
    if (failed) rows.push(["Missing", `${failed} failed to load`]);
    facts.innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  }

  function renderAxis() {
    track.innerHTML = "";
    axisLabels.innerHTML = "";
    if (!frames.length || !view) return;
    const eps = frames.map((f) => f.epoch);
    const width = axisEl.clientWidth || 600;
    axis = buildAxis(
      eps.map((e) => e.mjd_mid),
      view.clock === "hours" ? "gaps" : "linear",
      view.clock === "months" ? eps.map((e) => [e.mjd_start, e.mjd_end] as [number, number]) : [],
      Math.max(3, Math.min(8, Math.floor(width / 84))),
    );
    const A = axis;
    const parts: string[] = [`<span class="bk-baseline"></span>`];
    for (const t of A.ticks) parts.push(`<span class="bk-tick${t.major ? " is-major" : ""}" style="left:${t.x * 100}%"></span>`);
    if (view.clock === "months") {
      for (const e of eps) {
        const a = A.x(e.mjd_start);
        const b = A.x(e.mjd_end);
        parts.push(`<span class="bk-band" style="left:${a * 100}%;width:${Math.max(0.4, (b - a) * 100)}%"></span>`);
      }
    }
    eps.forEach((e, i) => parts.push(`<span class="bk-mark" data-i="${i}" style="left:${A.x(e.mjd_mid) * 100}%"></span>`));
    for (const g of A.gaps)
      if (g.days >= 0.5) parts.push(`<span class="bk-break" style="left:${g.x * 100}%" aria-hidden="true"></span>`);
    parts.push(`<span class="bk-refmark" aria-hidden="true">ref</span>`);
    parts.push(`<span class="bk-flag" aria-hidden="true"></span>`);
    track.innerHTML = parts.join("");
    const lab: string[] = [];
    for (const t of A.ticks) if (t.label) lab.push(`<span class="bk-tlabel" style="left:${t.x * 100}%">${t.label}</span>`);
    for (const g of A.gaps) lab.push(`<span class="bk-gap" style="left:${g.x * 100}%">${g.label}</span>`);

    axisLabels.innerHTML = lab.join("");
    axisEl.classList.toggle("is-gaps", A.kind === "gaps");
    // Hide gap labels that would overlap their neighbours.
    requestAnimationFrame(() => {
      thinLabels(axisLabels, ".bk-tlabel");
      thinLabels(axisLabels, ".bk-gap");
    });
    renderAxisState();
  }

  function thinLabels(box: HTMLElement, sel: string) {
    let lastRight = -Infinity;
    const items = [...box.querySelectorAll<HTMLElement>(sel)].sort(
      (a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left,
    );
    for (const it of items) {
      it.hidden = false;
      const r = it.getBoundingClientRect();
      if (r.left < lastRight + 4) {
        it.hidden = true;
        continue;
      }
      lastRight = r.right;
    }
  }

  function renderAxisState() {
    if (!axis || !frames.length) return;
    const A = axis;
    const all = mode === "add" && !comparing;
    track.querySelectorAll<HTMLElement>(".bk-mark").forEach((m) => {
      const i = Number(m.dataset.i);
      m.classList.toggle("is-current", all || i === (comparing ? ref : idx));
      m.classList.toggle("is-ref", mode === "subtract" && i === ref);
    });
    const refmark = track.querySelector<HTMLElement>(".bk-refmark");
    if (refmark) {
      refmark.hidden = mode !== "subtract" && !comparing;
      refmark.style.left = `${A.x(frames[ref].epoch.mjd_mid) * 100}%`;
    }
    const flag = track.querySelector<HTMLElement>(".bk-flag");
    const e = shownEpoch();
    if (flag && e) {
      flag.hidden = all;
      const x = A.x(e.mjd_mid);
      flag.style.left = `${x * 100}%`;
      flag.style.setProperty("--shift", `${-x * 100}%`);
      flag.textContent =
        view?.clock === "hours" ? formatMjd(e.mjd_mid, true).replace(/^\d+ \w+ \d+, /, "") : formatMjd(e.mjd_mid);
    }
    axisEl.setAttribute("aria-valuemin", "1");
    axisEl.setAttribute("aria-valuemax", String(frames.length));
    axisEl.setAttribute("aria-valuenow", String(idx + 1));
    axisEl.setAttribute("aria-valuetext", e ? formatMjd(e.mjd_mid, view?.clock === "hours") : "");
  }

  function renderTag() {
    let text = "";
    if (comparing && frames[ref]) text = `Reference: ${formatMjd(frames[ref].epoch.mjd_mid, view?.clock === "hours")}`;
    else if (mode === "subtract") text = idx === ref ? "This is the reference: pick another frame" : "Difference from the reference";
    else if (mode === "add") text = `Mean of ${nLoaded()} ${unit()}s`;
    tag.textContent = text;
    tag.hidden = !text;
  }

  function renderVeil() {
    const kind = ownBusy ? "loading" : appStatus?.kind;
    veil.hidden = !kind && !(F && !frames.length && view);
    veil.dataset.kind = kind ?? "empty";
    if (kind === "error") {
      veilMsg.textContent = appStatus?.message ?? "Something went wrong.";
      veilBar.hidden = true;
    } else if (kind === "loading" && !ownBusy) {
      veilMsg.textContent = appStatus?.message ?? "Loading frames…";
      veilBar.hidden = false;
      veilBar.classList.add("is-indeterminate");
    }
    if (kind) veilActions.innerHTML = "";
    if (!kind && F && view && !frames.length) {
      const have = detectorsIn(F);
      veilBar.hidden = true;
      if (!F.epochs.length) {
        veilMsg.textContent = `SPHEREx has no frames here on the ${view.clock} clock yet. Try the other clock or a featured target.`;
        veilActions.innerHTML = "";
      } else if (failed) {
        veilMsg.textContent = `None of the ${failed} frames loaded. Check your connection, then try again.`;
        veilActions.innerHTML = `<button class="bk-btn" type="button" data-retry>Try again</button>`;
      } else {
        veilMsg.textContent = `No D${view.det} frames on the ${view.clock} clock here. Pick a detector that has them:`;
        veilActions.innerHTML = have
          .map((d) => `<button class="bk-btn det-${d} bk-veil-det" type="button" data-goto-det="${d}">D${d}</button>`)
          .join("");
      }
    }
  }

  function renderAll() {
    renderControls();
    renderLegend();
    renderAxisState();
    renderTag();
  }

  // ---- actions -----------------------------------------------------------------------------
  function goTo(i: number, emit = true) {
    if (!frames.length) return;
    const n = frames.length;
    const next = ((i % n) + n) % n;
    const changed = next !== idx;
    idx = next;
    paintImage();
    renderAll();
    if (emit && changed) emitEpoch();
  }
  function emitEpoch() {
    const e = cur()?.epoch;
    if (e) opts.onEpoch?.({ id: e.id, mjd_mid: e.mjd_mid, det: e.det });
  }
  function setPlaying(on: boolean) {
    playing = on && frames.length > 1 && mode !== "add" && !ownBusy;
    clearTimeout(timer);
    if (playing) {
      const tick = () => {
        goTo(idx + 1);
        timer = window.setTimeout(tick, 1000 / fps);
      };
      timer = window.setTimeout(tick, 1000 / fps);
    }
    renderControls();
  }
  function setCompare(on: boolean) {
    if (on === comparing || frames.length < 2) return;
    comparing = on;
    $<HTMLButtonElement>(".bk-hold").setAttribute("aria-pressed", String(on));
    paintImage();
    renderAll();
  }
  function setZoom(z: number) {
    const [nx, ny] = size();
    if (z !== zoom) {
      if (pick && F) {
        const p = skyToPixel(pick, F, centreOf(cur()?.epoch ?? null));
        centre = { u: p.x + 0.5, v: p.y + 0.5 };
      } else if (hover) centre = { u: hover.u, v: hover.v };
      else centre = { u: nx / 2, v: ny / 2 };
    }
    zoom = z;
    if (z === 1) centre = { u: nx / 2, v: ny / 2 };
    canvas.classList.toggle("is-zoomed", z > 1);
    renderControls();
    draw();
  }

  // ---- loading -----------------------------------------------------------------------------
  async function load(next: View, resp: FramesResponse, url: string): Promise<void> {
    const gen = ++loadCtl;
    const wasPlaying = playing || firstShow;
    setPlaying(false);
    const prev = view;
    const prevMjd = cur()?.epoch.mjd_mid;
    const prevRefMjd = frames[ref]?.epoch.mjd_mid;
    const sameClock = prev?.clock === next.clock;
    if (!prev || Math.abs(prev.ra - next.ra) > 1e-6 || Math.abs(prev.dec - next.dec) > 1e-6) pick = null;
    view = next;
    F = resp;
    jsonUrl = url;
    appStatus = null;
    comparing = false;
    renderBar();
    const eps = epochsFor(resp, next.det);
    if (!eps.length) {
      frames = [];
      failed = 0;
      ownBusy = false;
      resetCaches();
      renderVeil();
      renderAll();
      renderAxis();
      draw();
      return;
    }
    ownBusy = true;
    let done = 0;
    const progress = () => {
      if (gen !== loadCtl) return;
      veilMsg.textContent = `Loading D${next.det} frames: ${done} of ${eps.length}`;
      veilBar.hidden = false;
      veilBar.classList.remove("is-indeterminate");
      veilBar.style.setProperty("--p", String(done / eps.length));
    };
    renderVeil();
    renderControls();
    progress();
    const results = await pool(
      eps,
      6,
      async (e) => toMJySr(await cachedFrame(e, url), e),
      () => {
        done++;
        progress();
      },
    );
    if (gen !== loadCtl) return;
    const [nx, ny] = resp.size;
    const loaded: Frame[] = [];
    failed = 0;
    results.forEach((r, i) => {
      if (r instanceof Error || r.length !== nx * ny) failed++;
      else loaded.push({ epoch: eps[i], vals: r, sky: median(r) });
    });
    frames = loaded;
    resetCaches();
    ownBusy = false;
    const near = (m: number | undefined) =>
      m == null || !sameClock ? 0 : nearestIndex(frames.map((f) => f.epoch.mjd_mid), m);
    idx = near(prevMjd);
    ref = near(prevRefMjd);
    if (prev?.ra !== next.ra || prev?.dec !== next.dec || prev?.fov !== next.fov) {
      zoom = 1;
      centre = { u: nx / 2, v: ny / 2 };
    }
    renderVeil();
    renderRefSelect();
    readout(-1, -1);
    renderAxis();
    if (frames.length) {
      paintImage();
      renderAll();
      emitEpoch();
      const reduce = matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      if (wasPlaying && !reduce && mode !== "add") setPlaying(true);
      firstShow = false;
    } else {
      renderAll();
      draw();
    }
  }

  // ---- events ------------------------------------------------------------------------------
  root.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button");
    if (!b || b.disabled || !view) return;
    const d = b.dataset;
    if (d.clock && d.clock !== view.clock) {
      const clock = d.clock as Clock;
      opts.onViewChange({ clock, fov: DEFAULT_FOV[clock] });
    } else if (d.fov && Number(d.fov) !== view.fov) opts.onViewChange({ fov: Number(d.fov) });
    else if (d.det && Number(d.det) !== view.det) opts.onViewChange({ det: Number(d.det) as Det });
    else if (d.gotoDet) opts.onViewChange({ det: Number(d.gotoDet) as Det });
    else if (d.retry !== undefined && F) void load(view, F, jsonUrl);
    else if (d.mode) {
      mode = d.mode as Mode;
      if (mode === "add") setPlaying(false);
      paintImage();
      renderAll();
    } else if (d.stretch) {
      stretchKind = d.stretch as StretchKind;
      paintImage();
      renderControls();
    } else if (d.opt === "invert") {
      invert = !invert;
      paintImage();
      renderControls();
    } else if (d.opt === "level") {
      levelSky = !levelSky;
      resetCaches();
      paintImage();
      renderControls();
    } else if (d.ov) {
      const k = d.ov as keyof typeof overlays;
      overlays[k] = !overlays[k];
      renderControls();
      draw();
    } else if (d.zoom) setZoom(Number(d.zoom));
    else if (b === playBtn) setPlaying(!playing);
    else if (b.classList.contains("bk-prev")) {
      setPlaying(false);
      goTo(idx - 1);
    } else if (b.classList.contains("bk-next")) {
      setPlaying(false);
      goTo(idx + 1);
    }
  });

  const hold = $<HTMLButtonElement>(".bk-hold");
  hold.addEventListener("pointerdown", (ev) => {
    hold.setPointerCapture?.(ev.pointerId);
    setCompare(true);
  });
  for (const t of ["pointerup", "pointercancel", "lostpointercapture"]) hold.addEventListener(t, () => setCompare(false));
  hold.addEventListener("keydown", (ev) => {
    if ((ev.key === "Enter" || ev.key === " ") && !ev.repeat) {
      ev.preventDefault();
      setCompare(true);
    }
  });
  hold.addEventListener("keyup", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") setCompare(false);
  });

  refSel.addEventListener("change", () => {
    ref = Number(refSel.value);
    paintImage();
    renderAll();
  });
  fpsSel.addEventListener("change", () => {
    fps = Number(fpsSel.value);
    if (playing) setPlaying(true);
  });
  contrastIn.addEventListener("input", () => {
    contrast = Number(contrastIn.value);
    paintImage();
  });

  // Timeline: click or drag to scrub.
  let scrubbing = false;
  const scrub = (ev: PointerEvent) => {
    if (!axis || !frames.length) return;
    const r = track.getBoundingClientRect();
    const p = (ev.clientX - r.left) / r.width;
    const i = nearestIndex(frames.map((f) => axis!.x(f.epoch.mjd_mid)), p);
    if (i !== idx) goTo(i);
  };
  axisEl.addEventListener("pointerdown", (ev) => {
    if (mode === "add") return;
    scrubbing = true;
    axisEl.setPointerCapture?.(ev.pointerId);
    setPlaying(false);
    scrub(ev);
  });
  axisEl.addEventListener("pointermove", (ev) => scrubbing && scrub(ev));
  for (const t of ["pointerup", "pointercancel"]) axisEl.addEventListener(t, () => (scrubbing = false));
  axisEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Home" || ev.key === "End") {
      ev.preventDefault();
      setPlaying(false);
      goTo(ev.key === "Home" ? 0 : frames.length - 1);
    }
  });

  // Image: hover reads a pixel, click flags a dot, drag pans when zoomed.
  let down: { X: number; Y: number; u: number; v: number; moved: boolean } | null = null;
  const local = (ev: PointerEvent) => {
    const r = canvas.getBoundingClientRect();
    return { X: ev.clientX - r.left, Y: ev.clientY - r.top };
  };
  canvas.addEventListener("pointerdown", (ev) => {
    if (!frames.length) return;
    const { X, Y } = local(ev);
    down = { X, Y, u: centre.u, v: centre.v, moved: false };
    canvas.setPointerCapture?.(ev.pointerId);
  });
  canvas.addEventListener("pointermove", (ev) => {
    if (!F || !frames.length) return;
    const { X, Y } = local(ev);
    if (down) {
      const dx = X - down.X;
      const dy = Y - down.Y;
      if (Math.hypot(dx, dy) > 5) down.moved = true;
      if (down.moved && zoom > 1) {
        const s = viewport().s;
        centre = { u: down.u - dx / s, v: down.v - dy / s };
        const vp = viewport();
        centre = { u: vp.u0 + vp.vw / 2, v: vp.v0 + vp.vh / 2 };
        canvas.classList.add("is-panning");
      }
    }
    const { u, v } = toImage(X, Y);
    hover = ev.pointerType === "mouse" ? { u, v, X, Y } : null;
    readout(u, v);
    draw();
  });
  canvas.addEventListener("pointerup", (ev) => {
    canvas.classList.remove("is-panning");
    if (!down || !F || !view) return;
    const wasDrag = down.moved;
    down = null;
    if (wasDrag) return;
    const { X, Y } = local(ev);
    const { u, v } = toImage(X, Y);
    const [nx, ny] = size();
    if (u < 0 || v < 0 || u > nx || v > ny) return;
    const e = cur()?.epoch;
    if (!e) return;
    const sky = pixelToSky({ x: u - 0.5, y: v - 0.5 }, F, centreOf(e));
    pick = sky;
    readout(u, v);
    draw();
    opts.onPick({ ra: sky.ra, dec: sky.dec, mjd: e.mjd_mid, clock: view.clock, det: e.det, epoch_id: e.id });
  });
  canvas.addEventListener("pointerleave", () => {
    hover = null;
    draw();
  });

  function readout(u: number, v: number) {
    const pos = $(".bk-read-pos");
    const val = $(".bk-read-val");
    const [nx, ny] = size();
    if (!F || !shown || u < 0 || v < 0 || u >= nx || v >= ny) {
      pos.textContent = "Point at the image to read a pixel; click a dot to flag it.";
      val.textContent = "";
      return;
    }
    const x = Math.floor(u);
    const y = Math.floor(v);
    const sky = pixelToSky({ x: u - 0.5, y: v - 0.5 }, F, centreOf(shownEpoch()));
    pos.textContent = formatRaDec(sky);
    const value = shown[y * nx + x];
    val.textContent = Number.isNaN(value)
      ? `Pixel ${x}, ${y}: no data`
      : `Pixel ${x}, ${y}: ${shownSigned ? "Δ " : ""}${value.toFixed(Math.abs(value) < 1 ? 4 : 2)} MJy/sr${levelSky ? " above sky" : ""}`;
  }

  // Keyboard: space plays, arrows step, hold C compares. Ignored while typing.
  const typing = (t: EventTarget | null) =>
    t instanceof HTMLElement && !!t.closest("input, textarea, select, [contenteditable='true']");
  const visible = () => el.isConnected && el.getClientRects().length > 0;
  const onKey = (ev: KeyboardEvent) => {
    if (!visible() || typing(ev.target) || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.key === " ") {
      if (ev.target instanceof HTMLElement && ev.target.closest("button")) return;
      ev.preventDefault();
      setPlaying(!playing);
    } else if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
      if (!frames.length || mode === "add") return;
      ev.preventDefault();
      setPlaying(false);
      goTo(idx + (ev.key === "ArrowLeft" ? -1 : 1));
    } else if ((ev.key === "c" || ev.key === "C") && !ev.repeat) setCompare(true);
  };
  const onKeyUp = (ev: KeyboardEvent) => {
    if (ev.key === "c" || ev.key === "C") setCompare(false);
  };
  document.addEventListener("keydown", onKey);
  document.addEventListener("keyup", onKeyUp);
  const onBlur = () => setCompare(false);
  window.addEventListener("blur", onBlur);

  // Size the canvas to the stage, at device resolution.
  const ro = new ResizeObserver(() => {
    const w = stage.clientWidth;
    if (!w) return;
    dpr = Math.min(3, window.devicePixelRatio || 1);
    if (w !== cssSize || canvas.width !== Math.round(w * dpr)) {
      cssSize = w;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(w * dpr);
      draw();
    }
    if (axis) renderAxis();
  });
  ro.observe(stage);
  ro.observe(axisEl);

  // Fonts and tokens may arrive after mount.
  document.fonts?.ready.then(() => {
    fontFamily = getComputedStyle(el).fontFamily || fontFamily;
    palette = readPalette(el);
    lut.key = "";
    paintImage();
  });

  renderControls();

  return {
    async show(next, resp, url) {
      await load(next, resp, url);
    },
    status(kind, message) {
      appStatus = kind === "idle" ? null : { kind, message: message ?? (kind === "loading" ? "Loading frames…" : "Something went wrong.") };
      if (kind === "error") {
        loadCtl++;
        ownBusy = false;
        setPlaying(false);
      }
      renderVeil();
    },
    destroy() {
      loadCtl++;
      clearTimeout(timer);
      ro.disconnect();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
      el.innerHTML = "";
    },
  };
};
