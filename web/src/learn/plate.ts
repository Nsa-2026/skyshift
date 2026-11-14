// A small lesson plate: real SPHEREx frames drawn as a plate negative (DESIGN.md "Plate drawing"),
// blinked at 2 Hz, pausable, still when the reader prefers reduced motion. No-data pixels
// (raw 0) are transparent so the plate's hatched glass shows through. Marks are grease pencil
// on an SVG laid over the canvas, in frame pixel coordinates.
import { median, niceArcsec, robustLimits, stretch, type Limits } from "../blink/math";
import { esc } from "./glossary";

export type MarkKind = "reticle" | "gaia" | "known" | "flag";

export interface PlateMark {
  kind: MarkKind;
  /** Frame pixel (0,0 = centre of the top-left pixel; north up, east left). */
  x: number;
  y: number;
  /** Radius in frame pixels. */
  r: number;
  /** Accessible name, read in the figure's description. */
  label?: string;
}

export interface PlateFrame {
  /** MJy/sr; NaN = no data. */
  values: Float32Array;
  /** Printed on the plate edge: "Visit 2 of 3, 18 Mar 2026". */
  label: string;
  /** Time strip button: "Visit 2", "+1.7 h". */
  step: string;
  marks?: PlateMark[];
}

export interface PlateSpec {
  nx: number;
  ny: number;
  frames: PlateFrame[];
  /** Part of the frame to show (frame pixels). Default: all. */
  crop?: { x: number; y: number; w: number; h: number };
  /** Marks on every frame. */
  marks?: PlateMark[];
  /** 0 keeps almost the whole range, 1 clips hard (src/blink/math contrastToPercentiles). */
  contrast?: number;
  /** One sentence for screen readers: what the plate shows. */
  description: string;
  pixelArcsec: number;
  /** Blink the frames (default when there are two or more). */
  blink?: boolean;
  /** Milliseconds per frame: 500 = 2 Hz. */
  interval?: number;
  /** Compact: no time strip, no scale label (for small multiples). */
  compact?: boolean;
}

export interface PlateHandle {
  el: HTMLElement;
  play(): void;
  pause(): void;
  show(i: number): void;
  /** Pause because the page is hidden; resume() restores what the reader chose. */
  suspend(): void;
  resume(): void;
  setMarks(visible: boolean): void;
  destroy(): void;
}

type RGB = [number, number, number];
const FALLBACK = { glass: [237, 240, 239] as RGB, ink: [24, 32, 46] as RGB };

function hex(s: string, fb: RGB): RGB {
  const m = /^#?([0-9a-f]{6})$/i.exec(s.trim());
  if (!m) return fb;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function palette(): { glass: RGB; ink: RGB } {
  try {
    const cs = getComputedStyle(document.documentElement);
    return { glass: hex(cs.getPropertyValue("--glass"), FALLBACK.glass), ink: hex(cs.getPropertyValue("--ink"), FALLBACK.ink) };
  } catch {
    return FALLBACK;
  }
}

/** Subtract each frame's sky level so visits with different zodiacal light blink evenly. */
export function skySubtracted(frames: Float32Array[]): Float32Array[] {
  return frames.map((f) => {
    const sky = median(f);
    const out = new Float32Array(f.length);
    for (let i = 0; i < f.length; i++) out[i] = f[i] - sky;
    return out;
  });
}

/**
 * Paint a crop of one frame as a plate negative into RGBA: sky → glass, stars → ink,
 * no data → transparent (the hatch behind shows). Pure, for tests.
 */
export function paintNegative(
  values: Float32Array,
  nx: number,
  crop: { x: number; y: number; w: number; h: number },
  lim: Limits,
  pal: { glass: RGB; ink: RGB } = FALLBACK,
): Uint8ClampedArray<ArrayBuffer> {
  const out = new Uint8ClampedArray(new ArrayBuffer(crop.w * crop.h * 4));
  for (let row = 0; row < crop.h; row++) {
    for (let col = 0; col < crop.w; col++) {
      const v = values[(crop.y + row) * nx + crop.x + col];
      const o = (row * crop.w + col) * 4;
      if (v === undefined || Number.isNaN(v)) {
        out[o + 3] = 0;
        continue;
      }
      const t = stretch(v, lim, "asinh");
      out[o] = pal.glass[0] + (pal.ink[0] - pal.glass[0]) * t;
      out[o + 1] = pal.glass[1] + (pal.ink[1] - pal.glass[1]) * t;
      out[o + 2] = pal.glass[2] + (pal.ink[2] - pal.glass[2]) * t;
      out[o + 3] = 255;
    }
  }
  return out;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function markSvg(m: PlateMark): string {
  const x = m.x + 0.5;
  const y = m.y + 0.5;
  const r = m.r;
  const a = `vector-effect="non-scaling-stroke"`;
  switch (m.kind) {
    case "gaia":
      return `<circle class="lp-m lp-m-gaia" cx="${x}" cy="${y}" r="${r}" ${a}/>`;
    case "known":
      return `<path class="lp-m lp-m-known" d="M${x} ${y - r}L${x + r} ${y}L${x} ${y + r}L${x - r} ${y}Z" ${a}/>`;
    case "flag": {
      const k = r * 0.7071;
      return (
        `<circle class="lp-m lp-m-flag" cx="${x}" cy="${y}" r="${r}" ${a}/>` +
        `<line class="lp-m lp-m-flag" x1="${x + k}" y1="${y - k}" x2="${x + k * 1.6}" y2="${y - k * 1.6}" ${a}/>`
      );
    }
    case "reticle": {
      // An open reticle: four ticks with a gap where the target sits.
      const g = r * 0.55;
      return [
        [x, y - r, x, y - g],
        [x, y + g, x, y + r],
        [x - r, y, x - g, y],
        [x + g, y, x + r, y],
      ]
        .map(([x1, y1, x2, y2]) => `<line class="lp-m lp-m-target" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" ${a}/>`)
        .join("");
    }
  }
}

const icons = {
  play: `<svg viewBox="0 0 20 20" aria-hidden="true" width="14" height="14"><path d="M6 3.5v13L16.5 10z" fill="currentColor"/></svg>`,
  pause: `<svg viewBox="0 0 20 20" aria-hidden="true" width="14" height="14"><path d="M5 4h3.5v12H5zM11.5 4H15v12h-3.5z" fill="currentColor"/></svg>`,
};

export function prefersReducedMotion(): boolean {
  try {
    return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/** Mount a plate into el. The frames must already be loaded (see figures.ts). */
export function mountPlate(el: HTMLElement, spec: PlateSpec): PlateHandle {
  const crop = spec.crop ?? { x: 0, y: 0, w: spec.nx, h: spec.ny };
  const n = spec.frames.length;
  const canBlink = (spec.blink ?? n > 1) && n > 1;
  const interval = spec.interval ?? 500;
  const reduced = prefersReducedMotion();

  const vals = skySubtracted(spec.frames.map((f) => f.values));
  const lim = robustLimits(vals, spec.contrast ?? 0.3);
  const pal = palette();

  const scale = niceArcsec((crop.w * spec.pixelArcsec) / 4);
  const scalePct = ((scale.arcsec / spec.pixelArcsec) / crop.w) * 100;

  el.classList.add("lp-plate");
  if (spec.compact) el.classList.add("lp-plate-compact");
  el.innerHTML = `
    <div class="lp-mat">
      <div class="lp-glass" style="aspect-ratio:${crop.w} / ${crop.h}">
        <canvas width="${crop.w}" height="${crop.h}" role="img" aria-label="${esc(spec.description)}"></canvas>
        <svg class="lp-marks" xmlns="${SVG_NS}" viewBox="${crop.x} ${crop.y} ${crop.w} ${crop.h}" preserveAspectRatio="none" aria-hidden="true"></svg>
        ${
          spec.compact
            ? ""
            : `<div class="lp-scale" aria-hidden="true"><span style="width:${scalePct.toFixed(2)}%"></span>${scale.label}</div>
               <div class="lp-compass" aria-hidden="true"><span class="lp-n">N</span><span class="lp-e">E</span></div>`
        }
      </div>
      <p class="lp-edge"></p>
    </div>
    ${
      canBlink
        ? `<div class="lp-strip">
            <button type="button" class="btn lp-play" aria-pressed="false">${icons.play}<span>Blink</span></button>
            <div class="seg lp-steps" role="group" aria-label="Frames">
              ${spec.frames.map((f, i) => `<button type="button" data-i="${i}" aria-pressed="false">${esc(f.step)}</button>`).join("")}
            </div>
          </div>
          <p class="visually-hidden lp-live" aria-live="polite"></p>`
        : ""
    }`;

  const canvas = el.querySelector("canvas")!;
  const svg = el.querySelector<SVGSVGElement>(".lp-marks")!;
  const edge = el.querySelector<HTMLParagraphElement>(".lp-edge")!;
  const playBtn = el.querySelector<HTMLButtonElement>(".lp-play");
  const steps = [...el.querySelectorAll<HTMLButtonElement>(".lp-steps button")];
  const live = el.querySelector<HTMLParagraphElement>(".lp-live");
  const g = canvas.getContext("2d");

  const images: ImageData[] = vals.map((v) => {
    const px = paintNegative(v, spec.nx, crop, lim, pal);
    if (typeof ImageData === "function") {
      try {
        return new ImageData(px, crop.w, crop.h);
      } catch {
        /* jsdom without canvas */
      }
    }
    return { data: px, width: crop.w, height: crop.h, colorSpace: "srgb" } as ImageData;
  });

  let i = 0;
  let timer: ReturnType<typeof setInterval> | null = null;
  let wantPlay = canBlink && !reduced;
  let suspended = false;
  let visible = true;
  let marksOn = true;

  function draw() {
    try {
      g?.putImageData(images[i], 0, 0);
    } catch {
      /* no canvas (tests) */
    }
    const f = spec.frames[i];
    edge.textContent = f.label;
    const marks = [...(spec.marks ?? []), ...(f.marks ?? [])];
    svg.innerHTML = marksOn ? marks.map(markSvg).join("") : "";
    steps.forEach((b, k) => b.setAttribute("aria-pressed", String(k === i)));
  }

  function tick() {
    i = (i + 1) % n;
    draw();
  }

  function sync() {
    const run = wantPlay && !suspended && visible;
    if (run && !timer) timer = setInterval(tick, interval);
    if (!run && timer) {
      clearInterval(timer);
      timer = null;
    }
    if (playBtn) {
      playBtn.setAttribute("aria-pressed", String(wantPlay));
      playBtn.innerHTML = wantPlay ? `${icons.pause}<span>Pause</span>` : `${icons.play}<span>Blink</span>`;
      playBtn.setAttribute("aria-label", wantPlay ? "Pause the blink" : "Blink the frames, two a second");
    }
  }

  playBtn?.addEventListener("click", () => {
    wantPlay = !wantPlay;
    sync();
  });
  steps.forEach((b) =>
    b.addEventListener("click", () => {
      wantPlay = false;
      i = Number(b.dataset.i);
      draw();
      sync();
      if (live) live.textContent = spec.frames[i].label;
    }),
  );

  // Off-screen plates rest.
  let io: IntersectionObserver | null = null;
  if (typeof IntersectionObserver === "function") {
    io = new IntersectionObserver((entries) => {
      visible = entries.some((e) => e.isIntersecting);
      sync();
    });
    io.observe(el);
  }

  draw();
  sync();

  return {
    el,
    play() {
      if (!canBlink) return;
      wantPlay = true;
      sync();
    },
    pause() {
      wantPlay = false;
      sync();
    },
    show(k: number) {
      i = ((k % n) + n) % n;
      draw();
    },
    suspend() {
      suspended = true;
      sync();
    },
    resume() {
      suspended = false;
      sync();
    },
    setMarks(on: boolean) {
      marksOn = on;
      draw();
    },
    destroy() {
      wantPlay = false;
      sync();
      io?.disconnect();
      el.innerHTML = "";
    },
  };
}

/** Exposed for tests: the mark shapes. */
export const _markSvg = markSvg;
