// The blink player (P4). Draws SPHEREx frames on a plate and blinks them on the months clock (visit
// stacks) or the hours clock (single exposures): by default all wavelengths at once (allwave.ts:
// every detector of one moment combined in grey), or one detector picked on the ramp.
// Seam: src/app/modules.ts. Design: web/DESIGN.md ("Lightbox"). The plate is the hero, the time
// strip sits right under it, one row of primary controls follows, and everything secondary lives
// behind "Adjust".
import "./blink.css";
import { ALLOWED_FOV, DEFAULT_FOV, detectorsIn, epochsFor, loadFrameData, loadFrames, loadTargetIndex, toMJySr } from "../api/client";
import { combineFrames, detectorsNote, groupEpochs, mergeMeasured, remapMeasured, type WaveGroup, type WavePart } from "./allwave";
import type { Clock, Det, Epoch, FramesResponse, GaiaStar, Measured, MeasuredClass, MeasuredDetection, SkyPos, TargetIndex } from "../api/types";
import { DETECTORS, SAME_MOMENT, formatRaDec, gaiaAt, pixelToSky, skyToPixel } from "../app/astro";
import type { BlinkHandle, BlinkOptions, MountBlink, TrackMarks, TrackSpot, View } from "../app/modules";
import { nearestIndex, type Axis } from "./axis";
import { SPHEREX_V_LIMIT, seenBySpherex } from "./known";
import { planMarks, type Plan, type PlanDet, type PlanStar } from "./layout";
import { arrowPath, bigStar, compass, diamond, factorSpot, flagMark, hatchPattern, label, measuredMark, pencil, reticle, ring, scaleBar } from "./marks";
import {
  burntCores,
  fillHoles,
  mean,
  median,
  niceArcsec,
  noDataAt,
  noDataSpot,
  offset,
  psfSigmaPx,
  robustLimits,
  robustSigma,
  shiftBilinear,
  sigmaLimits,
  smooth,
  stretch,
  subtract,
  unstretch,
  visibleExtent,
  type FillSource,
  type Filled,
  type Limits,
  type StretchKind,
} from "./math";
import {
  CLASS_INFO,
  greyed,
  stageLabel,
  CLASS_ORDER,
  classInfo,
  arrowNote,
  blendWords,
  classesPresent,
  describeDetection,
  describeShift,
  detectionNumbers,
  extras,
  gaiaById,
  listItems,
  listJoin,
  measuredState,
  motionArrow,
  offsetWords,
  reasonLine,
  shiftAt,
  shiftNumbers,
  shiftVerdict,
  summarize,
  trackDotsFor,
  type Arrow,
  type MeasuredItem,
} from "./measured";
import { driftPx, findMover, followShifts, type Mover } from "./motion";
import { buildLut, diffIndex, fade, lerp, paint, parseHex, plainIndex, readPalette, type Palette } from "./paint";
import { drawStrip, plateDate, updateStrip } from "./strip";

type Mode = "blink" | "difference" | "stack";
const DETS: Det[] = [1, 2, 3, 4, 5, 6];
const FPS = [1, 2, 4, 8];
const PSF_FWHM_ARCSEC = 7.53;
const FAST_STAR_MAS_YR = 300;
/** At most this many Gaia rings on a 680 px plate (fewer on a smaller one): the brightest win. */
const MAX_RINGS = 80;
/** Gaia stars brighter than this saturate SPHEREx (CLAUDE.md: J_AB 10.5 to W2_AB 9.0 at 50%). */
const SATURATED_G = 9;
/** A source "shows" on the plate down to this grey (0–1 of the stretch), or 2σ over the sky if higher. */
const VISIBLE_GREY = 0.12;
/** How far the part of a photo filled from its other photos is lightened toward the glass. */
const FILL_FADE = 0.35;
/** Search for a source's edge out to this far, arcsec (Barnard's Star in D4 reaches about 55″). */
const MAX_REACH_ARCSEC = 120;
const html = String.raw;
let mounts = 0;

interface Frame {
  epoch: Epoch;
  vals: Float32Array;
  /** Sky level (median) and noise (robust sigma), MJy/sr. */
  sky: number;
  sigma: number;
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

async function pool<T, R>(items: T[], n: number, fn: (t: T) => Promise<R>, done: () => void, current: () => boolean): Promise<(R | Error)[]> {
  const out: (R | Error)[] = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length && current()) {
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

const um = (n: number | null) => n == null || !Number.isFinite(n) ? "unknown" : n.toFixed(2);
/** A field's width against the full Moon (about 30′ across), in plain words: 5 → "about a sixth of…". */
export function fieldVsMoon(arcmin: number): string {
  const r = arcmin / 30;
  if (r >= 1.5) return `about ${Math.round(r)} full Moons wide`;
  if (r >= 0.85) return "about as wide as the full Moon";
  const parts: [number, string][] = [
    [1 / 6, "a sixth"],
    [1 / 4, "a quarter"],
    [1 / 3, "a third"],
    [1 / 2, "half"],
    [2 / 3, "two thirds"],
  ];
  const [, word] = parts.reduce((a, b) => (Math.abs(b[0] - r) < Math.abs(a[0] - r) ? b : a));
  return `about ${word} of the full Moon's width`;
}
const pct = (f: number | null) => f == null ? "unknown" : (f >= 0.1 ? `${(f * 100).toFixed(0)}%` : f > 0 ? `${(f * 100).toFixed(f >= 0.01 ? 1 : 2)}%` : "0%");
const icon = {
  play: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 4.6v10.8c0 .5.6.8 1 .5l8-5.4c.4-.3.4-.8 0-1L8 4.1c-.4-.3-1 0-1 .5z"/></svg>`,
  pause: `<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="5.5" y="4.5" width="3" height="11" rx="1"/><rect x="11.5" y="4.5" width="3" height="11" rx="1"/></svg>`,
  chevron: `<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};

/** The measured marks as small SVGs for the legend and the list: the same shapes as on the plate (marks.ts). */
const GLYPH_PATH: Record<MeasuredClass, string> = {
  star: `<circle cx="9" cy="9" r="5"/>`,
  other: `<circle cx="9" cy="9" r="5" stroke-dasharray="0.01 3" stroke-width="1.8"/>`,
  known: `<circle cx="9" cy="9" r="3.6"/><circle cx="9" cy="9" r="6.6"/>`,
  mover: `<circle cx="9" cy="9" r="4.2"/><path d="M1 9h2.6M14.4 9H17"/>`,
  suspect: `<circle cx="9" cy="9" r="5" stroke-width="2.4"/>`,
  artifact: `<circle cx="9" cy="9" r="5"/><path d="M5.5 12.5l7-7"/>`,
  stack: `<circle cx="9" cy="9" r="5" stroke-dasharray="2.2 2.2" stroke-width="1.3"/>`,
};
const glyph = (c: MeasuredClass, grade?: string | null) =>
  `<svg class="bk-mg" data-cls="${c}"${grade ? ` data-grade="${grade}"` : ""} viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">${GLYPH_PATH[c] ?? GLYPH_PATH.other}</svg>`;
/** A saturated star with no detection of its own: the plate's long-dashed ring (marks.ts `bigStar`). */
const SATURATED_GLYPH = `<svg class="bk-mg" data-cls="star" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="9" cy="9" r="7" stroke-dasharray="4 2.4"/><circle cx="9" cy="9" r="3.2" fill="currentColor" stroke="none" opacity="0.85"/></svg>`;
/** Draw order on the plate: the plainest first, so suspects end on top. */
const DRAW_ORDER: MeasuredClass[] = [...CLASS_ORDER].reverse();
/** Rows shown before "Show all". */
const LIST_SHOWN = 6;
/** The red circle where a candidate is only predicted (BlinkHandle.track): dashed. */
const TRACK_DASH = [4, 3.5];
const TRACK_GLYPH = (dashed: boolean) =>
  `<svg class="bk-mg" data-cls="track" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="9" cy="9" r="6"${dashed ? ` stroke-dasharray="3 2.6"` : ""}/></svg>`;
const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

export const mountBlink: MountBlink = (el: HTMLElement, opts: BlinkOptions): BlinkHandle => {
  const uid = `bk${++mounts}`;
  el.innerHTML = html`
    <div class="bk" data-mode="blink" data-ready="false">
      <div class="bk-col">
        <div class="bk-mat">
          <p class="bk-label">
            <span class="bk-when"><b class="bk-when-main"></b> <span class="bk-when-sub"></span></span>
            <span class="bk-det-label"></span>
          </p>
          <div class="bk-plate">
            <canvas class="bk-canvas" role="img" aria-label="SPHEREx frame. Click a dot to flag it${opts.viewer ? "; double-click to zoom in there" : ""}."></canvas>
            <div class="bk-progress" hidden><i></i></div>
            <div class="bk-msg" role="status" aria-live="polite" hidden>
              <p class="bk-msg-text"></p>
              <div class="bk-msg-actions"></div>
            </div>
          </div>
          <div class="bk-caption">
            <p class="bk-say"></p>
            <p class="bk-key"></p>
          </div>
        </div>

        <div class="bk-strip">
          <button class="btn btn-primary bk-play" type="button" aria-label="Play" aria-pressed="false">${icon.play}</button>
          <div class="bk-axis" role="slider" tabindex="0" aria-label="Time" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0">
            <div class="bk-track"></div>
            <div class="bk-tls"></div>
          </div>
        </div>

        <div class="bk-row">
          <div class="seg bk-clock" role="group" aria-label="Clock">
            <button type="button" data-clock="months" aria-pressed="false"
              title="One stack per visit. SPHEREx comes back every six months.">Months</button>
            <button type="button" data-clock="hours" aria-pressed="false"
              title="Single exposures, minutes to days apart">Hours</button>
          </div>
          <div class="seg seg-sm bk-fov" role="group" aria-label="Field of view"></div>
          <div class="bk-ramp" role="group" aria-label="Detector">
            <button class="bk-det det-all" type="button" data-det="all" aria-pressed="true"
              aria-label="All wavelengths, 0.75 to 5.0 µm"
              title="All six detectors of each moment combined in grey, each scaled by its own sky noise: a star faint at one wavelength still shows. Pick a detector to see it alone."><span>All</span></button>
            ${DETS.map(
              (d) => html`<button class="bk-det det-${d}" type="button" data-det="${d}" aria-pressed="false"
                aria-label="D${d}, ${DETECTORS[d].min} to ${DETECTORS[d].max} µm"
                title="D${d}: ${DETECTORS[d].min}–${DETECTORS[d].max} µm. Exposed at the same moment as D${SAME_MOMENT[d]}, so a change between them is colour, not motion."><span>D${d}</span></button>`,
            ).join("")}
          </div>
          <div class="bk-how">
            <div class="seg bk-modes" role="group" aria-label="View">
              <button type="button" data-mode="blink" aria-pressed="true">Blink</button>
              <button type="button" data-mode="difference" aria-pressed="false">Difference</button>
              <button type="button" data-mode="stack" aria-pressed="false">Stack</button>
            </div>
            <button class="btn btn-ghost bk-adjust-btn" type="button" aria-expanded="false" aria-controls="${uid}-adj">
              <span class="bk-adjust-text">Adjust</span>${icon.chevron}
            </button>
          </div>
        </div>

        <div class="bk-adjust" id="${uid}-adj" role="group" aria-label="Adjust the plate" hidden>
          <div class="bk-adj-row">
            <span class="bk-adj-k" id="${uid}-st">Stretch</span>
            <div class="seg seg-sm" role="group" aria-labelledby="${uid}-st">
              <button type="button" data-stretch="linear" aria-pressed="false">Linear</button>
              <button type="button" data-stretch="asinh" aria-pressed="true">Asinh</button>
            </div>
          </div>
          <div class="bk-adj-row">
            <label class="bk-adj-k" for="${uid}-contrast">Contrast</label>
            <input class="bk-contrast" id="${uid}-contrast" type="range" min="0" max="1" step="0.01" value="0.45" />
          </div>
          <div class="bk-adj-row">
            <span class="bk-adj-k" id="${uid}-pl">Plate</span>
            <div class="seg seg-sm" role="group" aria-labelledby="${uid}-pl">
              <button type="button" data-positive="false" aria-pressed="true">Negative</button>
              <button type="button" data-positive="true" aria-pressed="false">Positive</button>
            </div>
          </div>
          <div class="bk-adj-row">
            <span class="bk-adj-k">Level sky</span>
            <button class="bk-switch" type="button" data-opt="level" aria-pressed="true"
              aria-label="Level sky: subtract each frame's sky glow"><i></i></button>
            <span class="bk-adj-note">Removes each frame's sky glow</span>
          </div>
          <div class="bk-adj-row">
            <span class="bk-adj-k">Smooth</span>
            <button class="bk-switch" type="button" data-opt="smooth" aria-pressed="true"
              aria-label="Smooth differences to SPHEREx's resolution"><i></i></button>
            <span class="bk-adj-note">Blurs to SPHEREx's resolution before Difference</span>
          </div>
          <div class="bk-adj-row">
            <label class="bk-adj-k" for="${uid}-ref">Reference</label>
            <div class="bk-adj-pair">
              <select class="bk-select bk-ref" id="${uid}-ref"></select>
              <button class="btn bk-hold" type="button" aria-pressed="false"
                title="Hold (or hold C) to show the reference frame">Hold to compare</button>
            </div>
          </div>
          <div class="bk-adj-row bk-follow-row" hidden>
            <span class="bk-adj-k">Follow</span>
            <button class="bk-switch" type="button" data-opt="follow" aria-pressed="false"><i></i></button>
            <span class="bk-adj-note bk-follow-note"></span>
          </div>
          <div class="bk-adj-row">
            <span class="bk-adj-k" id="${uid}-mk">Marks</span>
            <div class="bk-marks" role="group" aria-labelledby="${uid}-mk">
              <button class="bk-mk" type="button" data-ov="measured" aria-pressed="${!opts.viewer}"
                title="What SkyShift measured in these photos: circles by class, with motion arrows"><i class="sw sw-measured"></i>Measured</button>
              <button class="bk-mk" type="button" data-ov="gaia" aria-pressed="true"><i class="sw sw-gaia"></i>Gaia</button>
              <button class="bk-mk" type="button" data-ov="known" aria-pressed="true"
                title="Known asteroids and comets bright enough for SPHEREx (V ${SPHEREX_V_LIMIT} or brighter), where their orbits put them"><i class="sw sw-known"></i>Known</button>
              <button class="bk-mk" type="button" data-ov="faint" aria-pressed="false"
                title="Also known asteroids fainter than V ${SPHEREX_V_LIMIT}, dashed: usually too faint for SPHEREx to show"><i class="sw sw-known sw-faint"></i>Faint</button>
              <button class="bk-mk" type="button" data-ov="target" aria-pressed="true"
                title="Red ticks: the field centre. Red circles: where the candidate should be in each frame"><i class="sw sw-target"></i>Target</button>
              <button class="bk-mk" type="button" data-ov="guides" aria-pressed="true"><i class="sw sw-guide"></i>Compass</button>
            </div>
          </div>
          <div class="bk-adj-row">
            <span class="bk-adj-k" id="${uid}-zm">Zoom</span>
            <div class="seg seg-sm" role="group" aria-labelledby="${uid}-zm">
              <button type="button" data-zoom="1" aria-pressed="true">1×</button>
              <button type="button" data-zoom="2" aria-pressed="false">2×</button>
              <button type="button" data-zoom="4" aria-pressed="false">4×</button>
            </div>
          </div>
          <div class="bk-adj-row">
            <span class="bk-adj-k" id="${uid}-sp">Speed</span>
            <div class="seg seg-sm" role="group" aria-labelledby="${uid}-sp">
              ${FPS.map((f) => `<button type="button" data-fps="${f}" aria-pressed="${f === 2}" aria-label="${f} frames a second">${f}/s</button>`).join("")}
            </div>
          </div>
          <p class="bk-adj-keys">Space plays, ← → step, hold C to compare.</p>
        </div>

        ${opts.viewer
          ? // The Viewer folds it to one line so the plate stays clear: opening it draws SkyShift's
            // circles on the plate too, folding it hides them (the switch inside sets them either way).
            html`<details class="bk-meas" hidden>
          <summary class="bk-meas-sum"><h2 class="bk-meas-title" id="${uid}-mt">What SkyShift measured</h2>
            <span class="bk-meas-count"></span></summary>
          <div class="bk-meas-head">`
          : html`<section class="bk-meas" aria-labelledby="${uid}-mt" hidden>
          <div class="bk-meas-head">
            <h2 class="bk-meas-title" id="${uid}-mt">What SkyShift measured</h2>`}
            <span class="bk-meas-stage" hidden></span>
            <button class="bk-meas-toggle" type="button" data-ov="measured" aria-pressed="${!opts.viewer}"
              title="Draw what SkyShift measured on the plate"><span class="bk-meas-knob" aria-hidden="true"><i></i></span>On the plate</button>
          </div>
          <p class="bk-meas-lead"></p>
          <p class="bk-meas-rest"></p>
          <ul class="bk-meas-key" aria-label="Marks"></ul>
          <p class="bk-meas-how"></p>
          <div class="bk-meas-items">
            <p class="bk-meas-lh" id="${uid}-ml"></p>
            <ul class="bk-meas-list" aria-labelledby="${uid}-ml"></ul>
            <button class="btn btn-ghost bk-meas-all" type="button" aria-expanded="false" hidden></button>
          </div>
          <p class="bk-meas-notes"></p>
          <p class="bk-meas-pro pro-only"></p>
        ${opts.viewer ? "</details>" : "</section>"}
      </div>
    </div>
  `;

  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const $$ = <T extends Element>(sel: string) => [...el.querySelectorAll<T>(sel)];
  const root = $<HTMLDivElement>(".bk");
  const col = $<HTMLDivElement>(".bk-col");
  const plate = $<HTMLDivElement>(".bk-plate");
  const canvas = $<HTMLCanvasElement>(".bk-canvas");
  const ctx = canvas.getContext("2d");
  const progress = $<HTMLDivElement>(".bk-progress");
  const msg = $<HTMLDivElement>(".bk-msg");
  const msgText = $<HTMLParagraphElement>(".bk-msg-text");
  const msgActions = $<HTMLDivElement>(".bk-msg-actions");
  const whenMain = $<HTMLElement>(".bk-when-main");
  const whenSub = $<HTMLElement>(".bk-when-sub");
  const detLabel = $<HTMLElement>(".bk-det-label");
  const say = $<HTMLParagraphElement>(".bk-say");
  const key = $<HTMLParagraphElement>(".bk-key");
  const playBtn = $<HTMLButtonElement>(".bk-play");
  const axisEl = $<HTMLDivElement>(".bk-axis");
  const strip = { track: $<HTMLDivElement>(".bk-track"), labels: $<HTMLDivElement>(".bk-tls") };
  const adjBtn = $<HTMLButtonElement>(".bk-adjust-btn");
  const adj = $<HTMLDivElement>(".bk-adjust");
  const refSel = $<HTMLSelectElement>("select.bk-ref");
  const contrastIn = $<HTMLInputElement>(".bk-contrast");
  const hold = $<HTMLButtonElement>(".bk-hold");
  const mp = {
    panel: $<HTMLElement>(".bk-meas"),
    /** The folded line's count (Viewer only). */
    count: el.querySelector<HTMLSpanElement>(".bk-meas-count"),
    stage: $<HTMLElement>(".bk-meas-stage"),
    lead: $<HTMLParagraphElement>(".bk-meas-lead"),
    rest: $<HTMLParagraphElement>(".bk-meas-rest"),
    key: $<HTMLUListElement>(".bk-meas-key"),
    how: $<HTMLParagraphElement>(".bk-meas-how"),
    items: $<HTMLDivElement>(".bk-meas-items"),
    lh: $<HTMLParagraphElement>(".bk-meas-lh"),
    list: $<HTMLUListElement>(".bk-meas-list"),
    all: $<HTMLButtonElement>(".bk-meas-all"),
    notes: $<HTMLParagraphElement>(".bk-meas-notes"),
    pro: $<HTMLParagraphElement>(".bk-meas-pro"),
  };
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
  let positive = false;
  let levelSky = true;
  let smoothOn = true;
  let follow = false;
  let mover: Mover | null = null;
  let shifts: { dx: number; dy: number }[] | null = null;
  /** All wavelengths (the default): every detector of one moment combined. Off: `view.det` alone. */
  let allWave = true;
  /** Practice and derived products can require a single detector without changing the user's raw-view preference. */
  let preferredAllWave = true;
  let singleDetector = false;
  /** The response as the app handed it (one or all detectors), its URL, and the combined moments. */
  let rawF: FramesResponse | null = null;
  let rawUrl = "";
  let waveGroups = new Map<string, WaveGroup>();
  /** Detectors really combined into each moment (a frame that failed to load drops out). */
  let waveLoaded = new Map<string, number>();
  let waveMembers = new Map<string, { epoch: Epoch; url: string }[]>();
  /** Live answers come one detector at a time: the others for this field, fetched in the background. */
  let liveDets: { key: string; got: Map<Det, { resp: FramesResponse; url: string }>; pending: boolean } = {
    key: "",
    got: new Map(),
    pending: false,
  };
  // On the Viewer, SkyShift's measured circles start off (the plate shows Gaia's rings) and opening
  // "What SkyShift measured" turns them on; other plates start with them on.
  const overlays = { measured: !opts.viewer, gaia: true, known: true, faint: false, target: true, guides: true };
  /** The candidate the red circles follow (BlinkHandle.track), its spots by frame id. */
  let trackM: TrackMarks | null = null;
  let trackBy = new Map<string, TrackSpot>();
  let trackGen = 0;
  /** The crosshair's widest opening around the red circles, for this plate and view. */
  let rgapMemo = { key: "", gap: 0 };
  // What SkyShift measured (contracts/api.md v3.3), prepared once per response and detector.
  let meas: Measured | null = null;
  let measAt = new Map<string, MeasuredDetection[]>();
  let epochDet = new Map<string, number>();
  let measItems: MeasuredItem[] = [];
  let measAll = false;
  /** How far sources reach on the shown plate (image pixels), by position: reset when the plate or its stretch changes. */
  let reachCache: { vals: Float32Array | null; floor: number; m: Map<string, number> } = { vals: null, floor: NaN, m: new Map() };
  /** The arrows of the last draw, by shift index: the list's focus ring follows them. */
  let shiftArrows = new Map<number, Arrow>();
  /** Suspect grades by track and by source (tracks and shifts carry none themselves). */
  let trackGrade = new Map<string, string>();
  let sourceGrade = new Map<number, string>();
  /** The list entry under the pointer or focus, and the one pinned by a click. */
  let focusItem: string | null = null;
  let pinned: string | null = null;
  /** Hover targets of the last draw, in screen pixels: the caption names what is under the pointer.
   *  `pri` 0 (what we measured) wins over 1 (a catalogue ring) where both sit under the pointer. */
  let hits: { X: number; Y: number; r: number; html: string; pri?: number }[] = [];
  let hoverXY: { X: number; Y: number } | null = null;
  let zoom = 1;
  let centre = { u: 0, v: 0 }; // view centre in image edge coordinates (pixel i spans [i, i+1])
  let playing = false;
  let resumeOnVisible = false;
  let fps = 2;
  let timer = 0;
  let comparing = false;
  let pick: SkyPos | null = null;
  let hover: { u: number; v: number } | null = null;
  let loadCtl = 0; // generation counter: a newer show() wins
  let busy: { done: number; total: number } | null = null;
  let appStatus: { kind: "loading" | "error"; message: string } | null = null;
  let axis: Axis | null = null;
  let shown: Float32Array | null = null;
  /** The burnt cores painted as ink over `shown` (null in Difference): with it, where the hatch shows. */
  let shownMask: Uint8Array | null = null;
  /** Hours clock: 1 where the plate shows a pixel filled from the visit's other photos (not in `shown`). */
  let shownFill: Uint8Array | null = null;
  let shownSigned = false;
  let shownLim: (Limits & { sigma?: number; n?: number }) | null = null;
  let firstShow = true;
  let pageActive = true;
  let destroyed = false;
  const leveled = new Map<number, Float32Array>();
  const smoothed = new Map<number, Float32Array>();
  const burnt = new Map<number, Uint8Array>();
  const filledFrames = new Map<number, Filled>();
  let stackVals: { key: string; vals: Float32Array; sky: number; sigma: number; burnt: Uint8Array } | null = null;
  const limitCache = new Map<string, Limits & { sigma?: number; n?: number }>();
  let lut: { key: string; data: Uint8ClampedArray } = { key: "", data: new Uint8ClampedArray(0) };
  let hatch: { key: string; pattern: CanvasPattern | null } = { key: "", pattern: null };
  let fillHatch: { key: string; pattern: CanvasPattern | null } = { key: "", pattern: null };
  let cssSize = 0;
  let dpr = 1;

  // ---- data helpers -------------------------------------------------------------------------
  const size = (): [number, number] => F?.size ?? [1, 1];
  const n = () => frames.length;
  const cur = (): Frame | null => frames[idx] ?? null;
  const shownIndex = () => (comparing ? ref : idx);
  const shownEpoch = (): Epoch | null => frames[shownIndex()]?.epoch ?? null;
  const centreOf = (e: Epoch | null): SkyPos => e?.center ?? F?.center ?? { ra: 0, dec: 0 };
  const unit = () => (view?.clock === "hours" ? "exposure" : "visit");
  const allAtOnce = () => mode === "stack" && !comparing;
  const followsObject = () => frames.some((f) => !!f.epoch.center);

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
  /** The frame as Difference uses it: levelled, and smoothed to the PSF when Smooth is on. */
  function diffBase(i: number): Float32Array {
    if (!smoothOn || !F) return level(i);
    let a = smoothed.get(i);
    if (!a) {
      const [nx, ny] = size();
      a = smooth(level(i), nx, ny, psfSigmaPx(PSF_FWHM_ARCSEC, F.pixel_arcsec));
      smoothed.set(i, a);
    }
    return a;
  }
  function burntOf(i: number): Uint8Array {
    let b = burnt.get(i);
    if (!b) {
      const [nx, ny] = size();
      const f = frames[i];
      b = burntCores(f.vals, nx, ny, f.sky, f.sigma);
      burnt.set(i, b);
    }
    return b;
  }
  /** The hours clock fills each photo's no-data part (past the detector's edge) from the visit's
   *  other photos, drawn paler and hatched. The months clock does not: its visits are six months apart. */
  const fillsHoles = () => view?.clock === "hours" && frames.length > 1;
  /** Photo i with its no-data pixels filled from the others (math.ts fillHoles), on photo i's level.
   *  Frames that follow a mover have their own centres: each other photo is lined up on the sky first. */
  function filledOf(i: number): Filled {
    let fl = filledFrames.get(i);
    if (!fl) {
      const [nx, ny] = size();
      const f = frames[i];
      const c0 = centreOf(f.epoch);
      const others: FillSource[] = [];
      frames.forEach((g, j) => {
        if (j === i || g.vals.length !== nx * ny) return;
        const cj = centreOf(g.epoch);
        let dx = 0;
        let dy = 0;
        if (F && (cj.ra !== c0.ra || cj.dec !== c0.dec)) {
          const p = skyToPixel(cj, F, c0);
          dx = p.x - (nx - 1) / 2;
          dy = p.y - (ny - 1) / 2;
        }
        others.push({ vals: g.vals, sky: g.sky, burnt: burntOf(j), dx, dy });
      });
      fl = fillHoles(level(i), levelSky ? 0 : f.sky, burntOf(i), others, nx, ny);
      filledFrames.set(i, fl);
    }
    return fl;
  }
  const following = () => follow && !!shifts && shifts.length === frames.length;
  /** Mean of every frame; shifted along the mover first when Follow is on (shift-and-add). */
  function stack(): NonNullable<typeof stackVals> {
    const k = `${levelSky}|${following()}`;
    if (stackVals?.key !== k) {
      const [nx, ny] = size();
      const list = frames.map((_, i) =>
        following() ? shiftBilinear(level(i), nx, ny, shifts![i].dx, shifts![i].dy) : level(i),
      );
      const vals = mean(list);
      const sky = median(vals);
      const sigma = robustSigma([vals], 40_000);
      stackVals = { key: k, vals, sky, sigma, burnt: burntCores(vals, nx, ny, sky, sigma) };
    }
    return stackVals;
  }
  /** The plain (not differenced) image behind what is shown: for marks that look at the plate. */
  function plainView() {
    if (allAtOnce()) {
      const s = stack();
      return { vals: s.vals, sky: s.sky, sigma: s.sigma, burnt: s.burnt, lim: limitsFor("stack") };
    }
    const i = mode === "difference" && !comparing ? idx : shownIndex();
    const f = frames[i];
    return { vals: level(i), sky: levelSky ? 0 : f.sky, sigma: f.sigma, burnt: burntOf(i), lim: limitsFor("blink") };
  }
  function resetCaches() {
    leveled.clear();
    smoothed.clear();
    filledFrames.clear();
    stackVals = null;
    limitCache.clear();
  }
  function limitsFor(kind: Mode): Limits & { sigma?: number; n?: number } {
    const k = `${kind}|${kind === "difference" ? `${ref}|${smoothOn}` : ""}|${kind === "stack" ? following() : ""}|${contrast}|${levelSky}`;
    let l = limitCache.get(k);
    if (!l) {
      if (kind === "difference") {
        const others = frames.map((_, i) => i).filter((i) => i !== ref);
        const diffs = (others.length ? others : [ref]).map((i) => subtract(diffBase(i), diffBase(ref)));
        l = sigmaLimits(diffs, contrast);
      } else if (kind === "stack") {
        l = robustLimits([stack().vals], contrast);
      } else {
        l = robustLimits(
          frames.map((_, i) => level(i)),
          contrast,
        );
      }
      limitCache.set(k, l);
    }
    return l;
  }

  // ---- painting ----------------------------------------------------------------------------
  function paintImage() {
    if (!F || !frames.length || !offCtx) return;
    const [nx, ny] = size();
    let values: Float32Array;
    let lim: Limits & { sigma?: number; n?: number };
    let mask: Uint8Array | null = null;
    let signed = false;
    // What is painted; `values` and `mask` stay the photo's own data (marks, hover, notes).
    let painted: Float32Array | null = null;
    let paintedMask: Uint8Array | null = null;
    let fill: Uint8Array | null = null;
    if (comparing || mode === "blink") {
      values = level(shownIndex());
      lim = limitsFor("blink");
      mask = burntOf(shownIndex());
      if (fillsHoles()) {
        const fl = filledOf(shownIndex());
        if (fl.count) {
          painted = fl.vals;
          paintedMask = fl.burnt;
          fill = fl.filled;
        }
      }
    } else if (mode === "difference") {
      values = subtract(diffBase(idx), diffBase(ref));
      lim = limitsFor("difference");
      signed = true;
    } else {
      const s = stack();
      values = s.vals;
      mask = s.burnt;
      lim = limitsFor("stack");
    }
    const k = `${signed}|${positive}`;
    if (lut.key !== k) lut = { key: k, data: buildLut(palette, signed, positive) };
    if (off.width !== nx || off.height !== ny) {
      off.width = nx;
      off.height = ny;
    }
    const img = offCtx.createImageData(nx, ny);
    paint(painted ?? values, img.data, lut.data, signed ? diffIndex(lim) : plainIndex(lim, stretchKind), paintedMask ?? mask);
    if (fill) fade(img.data, fill, palette.glass, FILL_FADE);
    offCtx.putImageData(img, 0, 0);
    shown = values;
    shownMask = mask;
    shownFill = fill;
    shownSigned = signed;
    shownLim = lim;
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
  type VP = ReturnType<typeof viewport>;
  const toScreen = (px: { x: number; y: number }, vp: VP) => ({
    X: vp.ox + (px.x + 0.5 - vp.u0) * vp.s,
    Y: vp.oy + (px.y + 0.5 - vp.v0) * vp.s,
  });
  const toImage = (X: number, Y: number, vp = viewport()) => ({ u: vp.u0 + (X - vp.ox) / vp.s, v: vp.v0 + (Y - vp.oy) / vp.s });

  const cssColor = (name: string, fb = "#18202e") => getComputedStyle(el).getPropertyValue(name).trim() || fb;

  /** Pencil colours, lifted toward white on a positive plate so they still read on ink. */
  function pen(name: string): string {
    const c = cssColor(name);
    if (!positive || shownSigned) return c;
    const [r, g, b] = lerp(parseHex(c, [207, 35, 56]), [255, 255, 255], 0.38);
    return `rgb(${r}, ${g}, ${b})`;
  }

  function draw() {
    hits = [];
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssSize, cssSize);
    if (!F || !frames.length) return;
    const vp = viewport();
    const hk = `${dpr}`;
    if (hatch.key !== hk) hatch = { key: hk, pattern: hatchPattern(ctx, dpr, cssColor("--sheet-2", "#e3e8e7"), cssColor("--hair-strong", "#a9b1b6")) };
    const plain = !shownSigned;
    ctx.fillStyle = hatch.pattern ?? cssColor("--sheet-2", "#e3e8e7");
    ctx.save();
    // The pattern is in device pixels; draw it untransformed so lines stay crisp.
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillRect(Math.round(vp.ox * dpr), Math.round(vp.oy * dpr), Math.round(vp.vw * vp.s * dpr), Math.round(vp.vh * vp.s * dpr));
    ctx.restore();
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(off, vp.u0, vp.v0, vp.vw, vp.vh, vp.ox, vp.oy, vp.vw * vp.s, vp.vh * vp.s);
    drawFillHatch(vp);
    drawMarks(vp);
    if (overlays.guides) drawGuides(vp, plain && positive);
    drawNoDataNote(vp);
  }

  /** Faint hatch lines over the part of the photo filled from the visit's other photos (shownFill). */
  function drawFillHatch(vp: VP) {
    if (!ctx || !shownFill) return;
    const [nx, ny] = size();
    if (shownFill.length !== nx * ny) return;
    const hk = `${dpr}`;
    if (fillHatch.key !== hk) fillHatch = { key: hk, pattern: hatchPattern(ctx, dpr, "rgba(0, 0, 0, 0)", cssColor("--hair-strong", "#a9b1b6")) };
    if (!fillHatch.pattern) return;
    const x0 = Math.max(0, Math.floor(vp.u0));
    const x1 = Math.min(nx, Math.ceil(vp.u0 + vp.vw));
    const y0 = Math.max(0, Math.floor(vp.v0));
    const y1 = Math.min(ny, Math.ceil(vp.v0 + vp.vh));
    const S = vp.s * dpr;
    ctx.save();
    // The pattern is in device pixels, like the no-data hatch: draw untransformed so lines stay crisp.
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.beginPath();
    let any = false;
    for (let y = y0; y < y1; y++) {
      const Y = (vp.oy + (y - vp.v0) * vp.s) * dpr;
      for (let x = x0; x < x1; ) {
        if (!shownFill[y * nx + x]) {
          x++;
          continue;
        }
        let e = x + 1;
        while (e < x1 && shownFill[y * nx + e]) e++;
        ctx.rect((vp.ox + (x - vp.u0) * vp.s) * dpr, Y, (e - x) * S, S);
        any = true;
        x = e;
      }
    }
    if (any) {
      ctx.clip();
      ctx.globalAlpha = 0.75;
      ctx.fillStyle = fillHatch.pattern;
      ctx.fillRect(Math.round(vp.ox * dpr), Math.round(vp.oy * dpr), Math.round(vp.vw * vp.s * dpr), Math.round(vp.vh * vp.s * dpr));
    }
    ctx.restore();
  }

  /** The plate's no-data area (raw 0: past this photo's edge) gets a small note, when it is not a
   *  sliver; so does the part filled from the visit's other photos (hours clock). */
  const NO_DATA_NOTE = "No data here: edge of this photo";
  const FILLED_NOTE = "Not in this photo: filled from its other photos";
  type Spot = ReturnType<typeof noDataSpot>;
  let noDataCache: { vals: Float32Array | null; fill: Uint8Array | null; key: string; empty: Spot; filled: Spot } = {
    vals: null,
    fill: null,
    key: "",
    empty: null,
    filled: null,
  };
  function drawNoDataNote(vp: VP) {
    if (!ctx || !shown || !F) return;
    const [nx, ny] = size();
    if (shown.length !== nx * ny) return;
    const key = `${vp.u0.toFixed(1)},${vp.v0.toFixed(1)},${vp.vw.toFixed(1)}`;
    if (noDataCache.vals !== shown || noDataCache.fill !== shownFill || noDataCache.key !== key) {
      let empty: Spot;
      let filled: Spot = null;
      if (shownFill && shownFill.length === nx * ny) {
        // noDataSpot counts NaN pixels whose mask is 0: mask out the filled part, then all but it.
        const fill = shownFill;
        const notEmpty = new Uint8Array(nx * ny);
        const notFilled = new Uint8Array(nx * ny);
        for (let k = 0; k < notEmpty.length; k++) {
          notEmpty[k] = shownMask?.[k] || fill[k] ? 1 : 0;
          notFilled[k] = fill[k] ? 0 : 1;
        }
        empty = noDataSpot(shown, notEmpty, nx, ny, vp);
        filled = noDataSpot(shown, notFilled, nx, ny, vp);
      } else empty = noDataSpot(shown, shownMask, nx, ny, vp);
      noDataCache = { vals: shown, fill: shownFill, key, empty, filled };
    }
    drawNote(NO_DATA_NOTE, noDataCache.empty, vp);
    drawNote(FILLED_NOTE, noDataCache.filled, vp);
  }
  function drawNote(text: string, spot: Spot, vp: VP) {
    if (!ctx || !spot || spot.frac < 0.03) return;
    const p = toScreen({ x: spot.x - 0.5, y: spot.y - 0.5 }, vp);
    ctx.save();
    ctx.font = `600 11px ${fontFamily}`;
    const w = ctx.measureText(text).width;
    const left = vp.ox + 6;
    const right = vp.ox + vp.vw * vp.s - 6;
    const x = Math.min(Math.max(p.X - w / 2, left), right - w);
    const y = Math.min(Math.max(p.Y + 4, vp.oy + 14), vp.oy + vp.vh * vp.s - 6);
    label(ctx, text, x, y, cssColor("--ink-2", "#4a5560"), cssColor("--sheet-2", "#e3e8e7"));
    ctx.restore();
  }

  function drawMarks(vp: VP) {
    if (!ctx || !F || !view) return;
    const e = allAtOnce() ? null : shownEpoch();
    const mjd = e?.mjd_mid ?? (frames[0].epoch.mjd_mid + frames[frames.length - 1].epoch.mjd_mid) / 2;
    const c = centreOf(e ?? cur()?.epoch ?? null);
    const inView = (p: { X: number; Y: number }, m = 16) =>
      p.X > vp.ox - m && p.X < vp.ox + vp.vw * vp.s + m && p.Y > vp.oy - m && p.Y < vp.oy + vp.vh * vp.s + m;
    const psf = (PSF_FWHM_ARCSEC / F.pixel_arcsec) * vp.s; // PSF FWHM in screen pixels
    const halo = positive && !shownSigned ? cssColor("--ink") : cssColor("--glass", "#edf0ef");

    ctx.save();
    ctx.beginPath();
    ctx.rect(vp.ox, vp.oy, vp.vw * vp.s, vp.vh * vp.s);
    ctx.clip();

    // What the plate shows under the marks, to size them and to skip rings around empty sky.
    const pv = plainView();
    const [nx, ny] = size();
    const fwhmPx = PSF_FWHM_ARCSEC / F.pixel_arcsec;
    const annulus = Math.max(3, Math.round(1.6 * fwhmPx));
    /** Does the plate show a dot here: peak (3 × 3) above the local sky around it by 6σ? */
    const standsOut = (x: number, y: number): boolean => {
      const ix = Math.round(x);
      const iy = Math.round(y);
      let peak = -Infinity;
      for (let dy = -1; dy <= 1; dy++)
        for (let dx = -1; dx <= 1; dx++) {
          const xx = ix + dx;
          const yy = iy + dy;
          if (xx < 0 || yy < 0 || xx >= nx || yy >= ny) continue;
          const k = yy * nx + xx;
          const v = pv.vals[k];
          if (Number.isNaN(v)) {
            if (pv.burnt[k]) return true;
          } else if (v > peak) peak = v;
        }
      const bg: number[] = [];
      for (let a = 0; a < 16; a++) {
        const xx = Math.round(ix + annulus * Math.cos((a * Math.PI) / 8));
        const yy = Math.round(iy + annulus * Math.sin((a * Math.PI) / 8));
        if (xx < 0 || yy < 0 || xx >= nx || yy >= ny) continue;
        const v = pv.vals[yy * nx + xx];
        if (!Number.isNaN(v)) bg.push(v);
      }
      if (bg.length < 6) return false;
      bg.sort((p, q) => p - q);
      return peak - bg[bg.length >> 1] > 6 * pv.sigma;
    };
    /** Radius (image pixels) of the dark blob at a pixel: how far the plate stays past mid-grey. */
    const blobRadius = (x: number, y: number): number => {
      const dark = (xx: number, yy: number) => {
        if (xx < 0 || yy < 0 || xx >= nx || yy >= ny) return false;
        const k = yy * nx + xx;
        const v = pv.vals[k];
        return Number.isNaN(v) ? pv.burnt[k] === 1 : stretch(v, pv.lim, stretchKind) > 0.5;
      };
      const x0 = Math.round(x);
      const y0 = Math.round(y);
      const rs = [
        [1, 0],
        [-1, 0],
        [0, 1],
        [0, -1],
      ].map(([dx, dy]) => {
        let r = 0;
        while (r < 24 && dark(x0 + dx * (r + 1), y0 + dy * (r + 1))) r++;
        return r;
      });
      rs.sort((a, b) => a - b);
      return dark(x0, y0) ? (rs[1] + rs[2]) / 2 : 0;
    };

    /** No ring or diamond over the hatched no-data area (past the detector's edge): nothing to mark there. */
    const hatched = (px: { x: number; y: number }): boolean =>
      !!shown && shown.length === nx * ny && noDataAt(shown, shownMask, nx, ny, px.x, px.y);

    // The target: the reticle opens wider than its dot. Gaia's own ring for it would crowd it.
    const targetPos = e?.center ?? (view.slug ? { ra: view.ra, dec: view.dec } : F.center);
    const tpx = skyToPixel(targetPos, F, c);
    const tp = toScreen(tpx, vp);
    const gap = Math.min(52, Math.max(8, psf * 1.15, (blobRadius(tpx.x, tpx.y) + 1) * vp.s + 4));

    // Sizes: how far each source reaches on this plate at this stretch (math.ts visibleExtent), so
    // each circle is as big as its source looks and each Gaia ring a little bigger (layout.ts).
    const visFloor = Math.max(pv.sky + 2 * pv.sigma, unstretch(VISIBLE_GREY, pv.lim, stretchKind));
    if (reachCache.vals !== pv.vals || reachCache.floor !== visFloor) reachCache = { vals: pv.vals, floor: visFloor, m: new Map() };
    const reachOpts = { floor: visFloor, sigma: pv.sigma, fwhm: fwhmPx, maxR: Math.min(60, Math.ceil(MAX_REACH_ARCSEC / F.pixel_arcsec)) };
    /** How far the source at an image position visibly reaches, in screen pixels. */
    const reach = (px: { x: number; y: number }): number => {
      const k = `${px.x.toFixed(2)},${px.y.toFixed(2)}`;
      let r = reachCache.m.get(k);
      if (r == null) {
        r = visibleExtent(pv.vals, pv.burnt, nx, ny, px.x, px.y, reachOpts);
        reachCache.m.set(k, r);
      }
      return r * vp.s;
    };
    /** A burnt (saturated) pixel within one FWHM of an image position. */
    const burntNear = (px: { x: number; y: number }): boolean => {
      const R = Math.max(1.5, fwhmPx);
      for (let yy = Math.floor(px.y - R); yy <= Math.ceil(px.y + R); yy++)
        for (let xx = Math.floor(px.x - R); xx <= Math.ceil(px.x + R); xx++)
          if (xx >= 0 && yy >= 0 && xx < nx && yy < ny && pv.burnt[yy * nx + xx] && Math.hypot(xx - px.x, yy - px.y) <= R) return true;
      return false;
    };

    // Single exposures reach about G 15.5, visit stacks a little deeper: those always get a ring.
    // Fainter Gaia stars get a quiet one only where the plate shows a dot (6σ over its local sky),
    // so rings never circle empty sky and no visible star goes unmarked. Not the target: the reticle
    // marks it, and Gaia's own ring would crowd it.
    const faintG = view.clock === "hours" ? 15.5 : 16.5;
    const gaiaIn = F.gaia
      .filter((s) => s.g != null && s.g <= 20.5)
      .map((s) => {
        const px = skyToPixel(gaiaAt(s, mjd), F!, c);
        return { s, px, p: toScreen(px, vp) };
      })
      .filter((o) => inView(o.p) && !hatched(o.px));
    const ringed = new Set(
      gaiaIn
        .filter((o) => !overlays.target || Math.hypot(o.p.X - tp.X, o.p.Y - tp.Y) > gap)
        .filter((o) => (o.s.g ?? 99) <= faintG || standsOut(o.px.x, o.px.y))
        .sort((a, b) => (a.s.g ?? 99) - (b.s.g ?? 99))
        .slice(0, Math.max(16, Math.round(MAX_RINGS * Math.min(1, (vp.vw * vp.s) ** 2 / 680 ** 2))))
        .map((o) => o.s.id),
    );
    const planStars: PlanStar[] = [];
    for (const o of gaiaIn) {
      const g = o.s.g ?? 99;
      const saturated = g < SATURATED_G || burntNear(o.px);
      // Stars with no ring matter only when bright: their glow can hold fragments.
      if (!ringed.has(o.s.id) && !saturated && g > 12) continue;
      planStars.push({ id: o.s.id, X: o.p.X, Y: o.p.Y, g: o.s.g, extent: reach(o.px), saturated, ringed: ringed.has(o.s.id) });
    }
    const measuredOn = !!meas && overlays.measured;
    const ds = measuredOn && e ? (measAt.get(e.id) ?? []) : [];
    const planDets: PlanDet[] = [];
    ds.forEach((d, i) => {
      const px = skyToPixel(d, F!, c);
      const p = toScreen(px, vp);
      if (!inView(p, 0) || hatched(px)) return;
      const x = extras(d);
      planDets.push({
        i,
        X: p.X,
        Y: p.Y,
        cls: d.class,
        extent: reach(px),
        gaiaId: d.gaia_id,
        reasons: d.reasons ?? [],
        parentGaiaId: x.parent_gaia_id,
        blendGaiaIds: x.blend_gaia_ids,
      });
    });
    const floor = Math.max(3.5, psf / 2);
    const plan = planMarks(planStars, planDets, { pxPerArcsec: vp.s / F.pixel_arcsec, floor, brightExtent: Math.max(10, 2 * psf), measured: measuredOn });
    const gaiaOf = new Map(gaiaIn.map((o) => [o.s.id, o.s]));

    if (overlays.gaia) {
      const blue = pen("--pencil-blue");
      for (const rg of plan.rings) {
        const s = gaiaOf.get(rg.id);
        const t = Math.min(1, Math.max(0, ((s?.g ?? faintG) - 9) / (faintG - 9)));
        ring(ctx, rg.X, rg.Y, rg.r);
        pencil(ctx, blue, 1.4 - 0.4 * t, 0.85 - 0.45 * t);
        // A blend: one ring, and a small dot where Gaia puts each of its stars.
        for (const b of rg.blend ?? []) {
          ctx.beginPath();
          ctx.arc(b.X, b.Y, 1.7, 0, Math.PI * 2);
          ctx.save();
          ctx.globalAlpha = 0.85;
          ctx.fillStyle = blue;
          ctx.fill();
          ctx.restore();
        }
        const gs = (rg.blend ?? [{ id: rg.id }]).map((b) => gaiaOf.get(b.id)).filter((x): x is NonNullable<typeof x> => !!x);
        const mags = listJoin(gs.map((x) => `G ${(x.g ?? NaN).toFixed(1)}`));
        hits.push({
          X: rg.X,
          Y: rg.Y,
          r: rg.r,
          pri: 1,
          html: rg.blend
            ? `<span class="bk-mtag" data-cls="star">${esc(blendWords(rg.blend.length))}</span> <span class="bk-val">${esc(mags)}: Gaia DR3 ${gs.map((x) => x.id).join(" and ")}</span>`
            : `<span class="bk-mtag" data-cls="star">Gaia DR3 ${esc(rg.id)}</span> <span class="bk-val">${esc(mags)}: where Gaia puts the star in this ${unit()}</span>`,
        });
      }
      // Fast stars: Gaia's predicted track through every epoch, dotted.
      if (!followsObject() && frames.length > 1) {
        for (const s of F.gaia) {
          if (Math.hypot(s.pmra ?? 0, s.pmdec ?? 0) <= FAST_STAR_MAS_YR) continue;
          const pts = frames.map((f) => toScreen(skyToPixel(gaiaAt(s, f.epoch.mjd_mid), F!, c), vp));
          if (!pts.some((q) => inView(q))) continue;
          ctx.beginPath();
          pts.forEach((q, i) => (i ? ctx.lineTo(q.X, q.Y) : ctx.moveTo(q.X, q.Y)));
          ctx.save();
          ctx.setLineDash([1, 4]);
          pencil(ctx, blue, 1.5, 0.7);
          ctx.restore();
        }
      }
    }

    if (overlays.known && e) {
      const violet = pen("--pencil-violet");
      const r = Math.max(6, Math.min(16, psf * 0.9));
      ctx.font = `600 11px ${fontFamily}`;
      // Only what SPHEREx can show (V ≤ 18, or a comet), unless Faint is on; only in view, not over
      // the no-data hatch, and not the target itself (a comet or asteroid the frames follow: the
      // reticle already marks it). Names when few diamonds are drawn.
      const list = F.known
        .filter((k) => (k.epoch_id === e.id || k.epoch_id === null) && (overlays.faint || seenBySpherex(k)))
        .map((k) => {
          const px = skyToPixel(k, F!, c);
          return { k, px, p: toScreen(px, vp) };
        })
        .filter((o) => inView(o.p, 0) && !hatched(o.px))
        .filter((o) => !overlays.target || Math.hypot(o.p.X - tp.X, o.p.Y - tp.Y) >= gap);
      const named = list.length <= 8;
      for (const { k, p } of list) {
        const faint = !seenBySpherex(k);
        diamond(ctx, p.X, p.Y, r);
        if (faint) {
          ctx.save();
          ctx.setLineDash([3, 2.5]);
          pencil(ctx, violet, 1.4, 0.7);
          ctx.restore();
        } else pencil(ctx, violet);
        if (named) label(ctx, faint && k.v != null ? `${k.name} V ${k.v.toFixed(1)}` : k.name, p.X + r + 5, p.Y + 4, violet, halo);
      }
    }

    drawMeasured(vp, e, c, inView, floor, halo, plan, ds, gaiaOf);

    // The candidate in every frame: a red circle, solid where measured, dashed where predicted.
    // Wider than its source and its measured circle, so it never hides the dot. Stack: every
    // frame's spot, faint (moved with the mover when following it). Not over the no-data hatch.
    // The crosshair opens past every frame's circle near the centre, and holds still while blinking:
    // it keeps the widest opening it needed on this plate and view (no pulse that reads as motion).
    const gapKey = `${trackGen}|${loadCtl}|${frames.length}|${mode}|${vp.s.toFixed(3)}|${vp.u0.toFixed(2)}|${vp.v0.toFixed(2)}`;
    let rgap = Math.max(gap, rgapMemo.key === gapKey ? rgapMemo.gap : 0);
    if (overlays.target && trackM) {
      const red = pen("--pencil-red");
      const len = Math.max(7, gap * 0.6);
      const circleAt = (s: TrackSpot, ep: Epoch, i: number) => {
        let px = skyToPixel(s, F!, centreOf(ep));
        if (i >= 0 && following()) px = { x: px.x - shifts![i].dx, y: px.y - shifts![i].dy };
        const p = toScreen(px, vp);
        return { px, p, r: Math.min(56, Math.max(8, psf * 1.3, Math.min(48, reach(px) + 3))) };
      };
      const clear = (p: { X: number; Y: number }, r: number) => {
        const d = Math.hypot(p.X - tp.X, p.Y - tp.Y);
        if (d - r < gap + len) rgap = Math.max(rgap, Math.min(64, d + r + 3));
      };
      frames.forEach((f, i) => {
        const s = spotFor(f.epoch);
        if (s) {
          const t = circleAt(s, f.epoch, i);
          if (inView(t.p, 0)) clear(t.p, t.r);
        }
      });
      const list = e ? [{ s: spotFor(e), i: -1, ep: e }] : frames.map((f, i) => ({ s: spotFor(f.epoch), i, ep: f.epoch }));
      for (const { s, i, ep } of list) {
        if (!s) continue;
        const { px, p, r: r0 } = circleAt(s, ep, i);
        if (!inView(p, 0) || hatched(px)) continue;
        let r = r0;
        for (const cc of plan.circles) if (Math.hypot(cc.X - p.X, cc.Y - p.Y) <= cc.r) r = Math.min(56, Math.max(r, cc.r + 5));
        clear(p, r);
        ring(ctx, p.X, p.Y, r);
        ctx.save();
        if (!s.measured) ctx.setLineDash(TRACK_DASH);
        pencil(ctx, halo, 3.5, e ? 0.45 : 0.25);
        pencil(ctx, red, 1.5, e ? 0.95 : 0.5);
        ctx.restore();
        if (e) hits.push({ X: p.X, Y: p.Y, r: r + 4, html: trackWords(s) });
      }
    }

    rgapMemo = { key: gapKey, gap: rgap };
    if (overlays.target) {
      reticle(ctx, tp.X, tp.Y, rgap, Math.max(7, rgap * 0.6));
      pencil(ctx, pen("--pencil-red"), 1.75);
    }

    if (pick) {
      const p = toScreen(skyToPixel(pick, F, c), vp);
      flagMark(ctx, p.X, p.Y, Math.max(7, Math.min(14, psf * 0.8)));
      pencil(ctx, pen("--pencil-red"), 1.75);
    }
    ctx.restore();
  }

  /**
   * What SkyShift measured, over the catalogue marks: the zone's edge when it cuts the field, motion
   * arrows to scale (hours: each mover's dots in time order; months: each shift worth drawing, from
   * its first-visit to its last-visit position), this epoch's circles as the plan sized them, one
   * dashed ring per saturated star without a detection, and the list entry in focus.
   */
  function drawMeasured(
    vp: VP,
    e: Epoch | null,
    c: SkyPos,
    inView: (p: { X: number; Y: number }, m?: number) => boolean,
    floor: number,
    halo: string,
    plan: Plan,
    ds: MeasuredDetection[],
    gaiaOf: Map<string, GaiaStar>,
  ) {
    const m = meas;
    shiftArrows = new Map();
    if (!ctx || !F || !view || !m || !overlays.measured) return;
    const f = F;
    const toS = (p: SkyPos) => toScreen(skyToPixel(p, f, c), vp);
    const stroke = (col: string, w: number, a = 0.9) => {
      pencil(ctx, halo, w + 2, 0.5);
      pencil(ctx, col, w, a);
    };
    /** A stretched arrow carries its factor ("×20") and a small open dot where the motion really ends. */
    const stretched = (a: Arrow, col: string) => {
      if (a.factor <= 1) return;
      const at = factorSpot(a.pts[0], a.pts[a.pts.length - 1]);
      ctx.font = `600 10.5px ${fontFamily}`;
      ctx.textAlign = "center";
      label(ctx, `×${a.factor}`, at.X, at.Y + 4, col, halo);
      ctx.textAlign = "start";
      ctx.beginPath();
      ctx.arc(a.end.X, a.end.Y, 2.2, 0, Math.PI * 2);
      stroke(col, 1.2);
    };
    const longer = (a: Arrow) => (a.factor > 1 ? ` The arrow is drawn ${a.factor} times longer than the motion.` : "");

    drawZoneEdge(m, vp, c, halo);

    if (m.clock === "hours") {
      for (const t of m.tracks) {
        const dots = trackDotsFor(t, epochDet, view.det);
        if (dots.length < 2) continue;
        const a = motionArrow(dots.map(toS));
        if (!a || !a.pts.some((q) => inView(q))) continue;
        const col = pen(greyed(t.class, trackGrade.get(t.id)) ? "--m-artifact" : classInfo(t.class).token);
        if (arrowPath(ctx, a.pts, 7)) stroke(col, 1.6);
        stretched(a, col);
        // A bead at each dot; this exposure's is larger, so blinking walks it along the arrow.
        dots.forEach((d, i) => {
          const now = d.epoch_id === e?.id;
          ctx.beginPath();
          ctx.arc(a.pts[i].X, a.pts[i].Y, now ? 3 : 1.75, 0, Math.PI * 2);
          ctx.save();
          ctx.globalAlpha = 0.9;
          ctx.fillStyle = col;
          ctx.fill();
          ctx.restore();
        });
        if (a.factor > 1) {
          const it = measItems.find((x) => x.track === t.id);
          const html = `<span class="bk-mtag" data-cls="${t.class}">${esc(it?.label ?? classInfo(t.class).name)}</span> <span class="bk-val">${esc(longer(a).trim())}</span>`;
          hits.push({ X: a.pts[a.pts.length - 1].X, Y: a.pts[a.pts.length - 1].Y, r: 8, html });
        }
      }
    } else {
      // From this detector's first visit to its last: the source's detections there when both
      // exist (the tip lands on the later one), else the fit moved to those visits.
      const G = gaiaById(f.gaia);
      const det = view.det;
      const mine = f.epochs.filter((x) => x.det === det).sort((p, q) => p.mjd_mid - q.mjd_mid);
      const first = mine[0];
      const last = mine[mine.length - 1];
      if (first && last && first !== last)
        m.shifts.forEach((s, i) => {
          if (!shiftVerdict(s, G).draw) return;
          const d0 = measAt.get(first.id)?.find((d) => d.source_id === s.source_id);
          const d1 = measAt.get(last.id)?.find((d) => d.source_id === s.source_id);
          const f0 = toS(shiftAt(s, first.mjd_mid));
          const f1 = toS(shiftAt(s, last.mjd_mid));
          const a = motionArrow(d0 && d1 ? [toS(d0), toS(d1)] : [f0, f1], { X: f1.X - f0.X, Y: f1.Y - f0.Y });
          if (!a || (!inView(a.pts[0]) && !inView(a.pts[a.pts.length - 1]))) return;
          shiftArrows.set(i, a);
          const grade = sourceGrade.get(s.source_id);
          const col = pen(greyed(s.class, grade) ? "--m-artifact" : classInfo(s.class).token);
          if (arrowPath(ctx, a.pts, 6)) stroke(col, 1.6);
          stretched(a, col);
          const html = `<span class="bk-mtag" data-cls="${s.class}">${esc(describeShift(s, grade))}</span> <span class="bk-val">${esc(shiftNumbers(s) + "." + longer(a))}</span>`;
          hits.push({ X: a.pts[0].X, Y: a.pts[0].Y, r: 8, html });
          hits.push({ X: a.pts[a.pts.length - 1].X, Y: a.pts[a.pts.length - 1].Y, r: 8, html });
        });
    }

    // This epoch's circles, as big as their sources look; pieces of a bright star stay off the plate.
    if (e) {
      ctx.font = `700 11px ${fontFamily}`;
      const order = [...plan.circles].sort((a, b) => DRAW_ORDER.indexOf(ds[a.i].class) - DRAW_ORDER.indexOf(ds[b.i].class));
      for (const cc of order) {
        const d = ds[cc.i];
        const col = pen(greyed(d.class, d.grade) ? "--m-artifact" : classInfo(d.class).token);
        measuredMark(ctx, d.class, cc.X, cc.Y, cc.r, col, halo);
        if (d.class === "suspect" && d.grade) label(ctx, d.grade, cc.X + cc.r * 0.75 + 3, cc.Y - cc.r * 0.75 - 2, col, halo);
        const blendTag = cc.blend && d.class === "star";
        const notes: string[] = [];
        if (cc.blend && !blendTag) notes.push(blendWords(cc.blend.length));
        const off = offsetWords(cc.offsetArcsec);
        if (off) notes.push(off);
        if (cc.bright) {
          const sat = (gaiaOf.get(cc.bright)?.g ?? 99) < SATURATED_G;
          notes.push(sat ? "a saturated star: the circle holds its whole blob" : "a bright star: the circle holds its whole glow");
          if (cc.fragments) notes.push(`${cc.fragments} ${cc.fragments === 1 ? "piece" : "pieces"} of its glow hidden`);
        }
        const tag = blendTag ? blendWords(cc.blend!.length) : describeDetection(d);
        hits.push({
          X: cc.X,
          Y: cc.Y,
          r: cc.r + 4,
          html: `<span class="bk-pos">${formatRaDec(d)}</span> <span class="bk-mtag" data-cls="${d.class}"${d.grade ? ` data-grade="${d.grade}"` : ""}>${esc(tag)}</span> <span class="bk-val">${esc([detectionNumbers(d), ...notes].join("; "))}</span>`,
        });
      }
    }

    // A saturated (or very bright) star with no detection of its own: one dashed ring around its
    // whole blob, never nothing and never its fragments.
    if (plan.big.length) {
      const col = pen("--m-star");
      ctx.font = `600 11px ${fontFamily}`;
      const named = plan.big.length <= 4;
      for (const b of plan.big) {
        bigStar(ctx, b.X, b.Y, b.r, col, halo);
        const wings = [...shiftArrows.keys()].some((i) => m.shifts[i].gaia_id === b.id);
        const s = gaiaOf.get(b.id);
        const text = b.saturated ? (wings ? "saturated: measured on the wings" : "saturated") : "";
        if (named && text) label(ctx, text, b.X + b.r * 0.71 + 4, b.Y + b.r * 0.71 + 12, col, halo);
        const what = b.saturated
          ? `Its core is burnt, so it has no single detection here${wings ? "; its motion is measured on its wings" : ""}.`
          : "Its glow is split into several detections.";
        const hidden = b.fragments ? ` ${b.fragments} ${b.fragments === 1 ? "piece" : "pieces"} of its glow hidden.` : "";
        hits.push({
          X: b.X,
          Y: b.Y,
          r: b.r + 4,
          html: `<span class="bk-mtag" data-cls="star">${b.saturated ? "Saturated star" : "Bright star"}</span> <span class="bk-val">Gaia DR3 ${esc(b.id)}${s?.g != null ? `, G ${s.g.toFixed(1)}` : ""}. ${esc(what + hidden)}</span>`,
        });
      }
    }

    // The list entry in focus (or pinned): an ink ring that follows it from photo to photo, sized on
    // the circle it holds; for a shift, at both ends of its arrow.
    const it = measItems.find((x) => x.key === (focusItem ?? pinned));
    if (it) {
      const pxPerArcsec = vp.s / f.pixel_arcsec;
      const around = (p: { X: number; Y: number }) => {
        let r = floor;
        for (const cc of plan.circles) if (Math.hypot(cc.X - p.X, cc.Y - p.Y) <= 6 * pxPerArcsec) r = Math.max(r, cc.r);
        for (const b of plan.big) if (Math.hypot(b.X - p.X, b.Y - p.Y) <= 6 * pxPerArcsec) r = Math.max(r, b.r);
        return r + 7;
      };
      const a = it.shift != null ? shiftArrows.get(it.shift) : undefined;
      const spots = a ? [a.pts[0], a.end] : [toS((e && it.at[e.id]) || it.pos)];
      spots.forEach((p, k) => {
        ring(ctx, p.X, p.Y, around(p));
        stroke(pen("--ink"), k ? 1.5 : 2, k ? 0.8 : 0.95);
      });
    }
  }

  /** The zone's box, dashed, when the field reaches past it: outside it nothing is linked. */
  function drawZoneEdge(m: Measured, vp: VP, c: SkyPos, halo: string) {
    if (!ctx || !F || !m.zone_box?.ra || !m.zone_box?.dec) return;
    const f = F;
    const [ra0, ra1raw] = m.zone_box.ra;
    const [dec0, dec1] = m.zone_box.dec;
    const ra1 = ra1raw < ra0 ? ra1raw + 360 : ra1raw; // a box across RA 0
    const inside = (p: SkyPos) => {
      const ra = p.ra < ra0 ? p.ra + 360 : p.ra;
      return p.dec >= dec0 && p.dec <= dec1 && ra >= ra0 && ra <= ra1;
    };
    const [nx, ny] = size();
    const corners = [
      [-0.5, -0.5],
      [nx - 0.5, -0.5],
      [-0.5, ny - 0.5],
      [nx - 0.5, ny - 0.5],
      [(nx - 1) / 2, -0.5],
      [(nx - 1) / 2, ny - 0.5],
      [-0.5, (ny - 1) / 2],
      [nx - 0.5, (ny - 1) / 2],
    ].map(([x, y]) => pixelToSky({ x, y }, f, c));
    if (corners.every(inside)) return;
    const N = 64;
    const sides: SkyPos[][] = [
      Array.from({ length: N + 1 }, (_, i) => ({ ra: ra0 + ((ra1 - ra0) * i) / N, dec: dec0 })),
      Array.from({ length: N + 1 }, (_, i) => ({ ra: ra0 + ((ra1 - ra0) * i) / N, dec: dec1 })),
      Array.from({ length: N + 1 }, (_, i) => ({ ra: ra0, dec: dec0 + ((dec1 - dec0) * i) / N })),
      Array.from({ length: N + 1 }, (_, i) => ({ ra: ra1, dec: dec0 + ((dec1 - dec0) * i) / N })),
    ];
    const W = vp.vw * vp.s;
    const mid = { X: vp.ox + W / 2, Y: vp.oy + (vp.vh * vp.s) / 2 };
    let best: { X: number; Y: number; d: number } | null = null;
    ctx.beginPath();
    for (const side of sides) {
      side.forEach((p, i) => {
        const q = toScreen(skyToPixel({ ra: ((p.ra % 360) + 360) % 360, dec: p.dec }, f, c), vp);
        if (i) ctx.lineTo(q.X, q.Y);
        else ctx.moveTo(q.X, q.Y);
        const inPlate = q.X > vp.ox + 40 && q.X < vp.ox + W - 80 && q.Y > vp.oy + 16 && q.Y < vp.oy + vp.vh * vp.s - 30;
        const d = Math.hypot(q.X - mid.X, q.Y - mid.Y);
        if (inPlate && (!best || d < best.d)) best = { X: q.X, Y: q.Y, d };
      });
    }
    ctx.save();
    ctx.setLineDash([6, 5]);
    pencil(ctx, halo, 3, 0.5);
    pencil(ctx, pen("--ink-2"), 1.25, 0.8);
    ctx.restore();
    const b = best as { X: number; Y: number } | null;
    if (b) {
      ctx.font = `600 11px ${fontFamily}`;
      label(ctx, `${m.zone} zone edge`, b.X + 6, b.Y - 6, pen("--ink-2"), halo);
    }
  }

  function drawGuides(vp: VP, onInk: boolean) {
    if (!ctx || !F) return;
    const W = cssSize;
    const ink = onInk ? cssColor("--glass", "#edf0ef") : cssColor("--ink");
    const halo = onInk ? cssColor("--ink") : cssColor("--glass", "#edf0ef");
    ctx.font = `600 11px ${fontFamily}`;
    const pad = Math.max(10, Math.round(W * 0.022));
    // Scale bar, bottom left.
    const { arcsec, label: text } = niceArcsec((W * 0.18 * F.pixel_arcsec) / vp.s);
    const len = (arcsec / F.pixel_arcsec) * vp.s;
    scaleBar(ctx, pad, W - pad, len);
    pencil(ctx, halo, 4, 0.6);
    pencil(ctx, ink, 1.25, 0.9);
    ctx.textAlign = "center";
    label(ctx, text, pad + len / 2, W - pad - 6, ink, halo);
    // Compass, bottom right: north up, east left.
    const L = 18;
    const cx = W - pad;
    const cy = W - pad;
    compass(ctx, cx, cy, L);
    pencil(ctx, halo, 4, 0.6);
    pencil(ctx, ink, 1.25, 0.9);
    label(ctx, "N", cx, cy - L - 5, ink, halo);
    ctx.textAlign = "right";
    label(ctx, "E", cx - L - 5, cy + 4, ink, halo);
    ctx.textAlign = "start";
  }

  // ---- words on the mat --------------------------------------------------------------------
  function renderLabel() {
    const e = shownEpoch();
    if (busy) {
      whenMain.textContent = "";
      whenSub.textContent = `Loading ${busy.done} of ${busy.total} frames`;
    } else if (appStatus?.kind === "loading") {
      whenMain.textContent = "";
      whenSub.textContent = appStatus.message;
    } else if (!e || !view) {
      whenMain.textContent = "";
      whenSub.textContent = "";
    } else {
      const hours = view.clock === "hours";
      const U = hours ? "exposure" : "visit";
      const when = (m: number) => plateDate(m, hours);
      if (allAtOnce()) {
        whenMain.textContent = `${plateDate(frames[0].epoch.mjd_mid)} to ${plateDate(frames[n() - 1].epoch.mjd_mid)}`;
        whenSub.textContent = following() && mover ? `mean of ${n()} ${U}s, following the mover` : `mean of ${n()} ${U}s`;
      } else if (mode === "difference" && !comparing) {
        whenMain.textContent = when(e.mjd_mid);
        whenSub.textContent = idx === ref ? `${U} ${idx + 1} is the reference` : `${U} ${idx + 1} minus ${U} ${ref + 1}`;
      } else {
        whenMain.textContent = when(e.mjd_mid);
        whenSub.textContent = `${comparing ? "reference, " : ""}${U} ${shownIndex() + 1} of ${n()}`;
      }
    }
    const det = view?.det;
    const g = e ? waveGroups.get(e.id) : undefined;
    if (det && g && view) {
      // All wavelengths: which detectors, their span, and how many really combined here.
      const dets = [...new Set([...waveGroups.values()].flatMap((x) => x.members.map((m) => m.det)))].sort();
      const here = allAtOnce() ? dets : g.members.map((m) => m.det);
      const name = view.clock === "hours" ? here.map((d) => `D${d}`).join(" and ") : "All wavelengths";
      const span = allAtOnce()
        ? [Math.min(...dets.map((d) => DETECTORS[d].min)), Math.max(...dets.map((d) => DETECTORS[d].max))]
        : [g.epoch.um_min, g.epoch.um_max];
      const note = allAtOnce() ? "" : detectorsNote(waveLoaded.get(g.epoch.id) ?? g.members.length, view.clock);
      detLabel.innerHTML = `<i class="bk-swatch"></i>${name} <span>${um(span[0])}–${um(span[1])} µm${note ? ` · ${note}` : ""}</span>`;
      detLabel.title = `${here.map((d) => `D${d}`).join(", ")} combined, each in units of its own sky noise. ${g.epoch.n_exposures} exposure${g.epoch.n_exposures === 1 ? "" : "s"}.`;
    } else if (det) {
      const lo = e && !allAtOnce() ? um(e.um_min) : um(DETECTORS[det].min);
      const hi = e && !allAtOnce() ? um(e.um_max) : um(DETECTORS[det].max);
      const band = lo === 'unknown' || hi === 'unknown' ? 'wavelength range not recorded' : `${lo}–${hi} µm`;
      detLabel.innerHTML = `<i class="bk-swatch"></i>D${det} <span>${band}</span>`;
      detLabel.title = e
        ? `${e.n_exposures} exposure${e.n_exposures === 1 ? "" : "s"}. ${pct(e.flag_frac)} of pixels flagged, ${pct(e.sat_frac)} saturated.`
        : "";
    } else detLabel.innerHTML = "";
  }

  function modeSentence(): string {
    const U = unit();
    const Us = `${U}s`;
    if (comparing) return `The reference ${U}. Let go to return.`;
    if (mode === "difference") {
      const base = ref === 0 ? "the first" : `${U} ${ref + 1}`;
      return `Each ${U} minus ${base}. Red: light arrived. Blue: light left.`;
    }
    if (mode === "stack") {
      if (following() && mover) return `Following ${moverName()}'s motion: it sharpens, other stars smear.`;
      return `Mean of all ${n()} ${Us}. Still stars sharpen; movers smear.`;
    }
    return view?.clock === "hours"
      ? "Each exposure in turn, minutes apart. A moving dot jumps."
      : "Each visit in turn, six months apart. A moving dot jumps.";
  }

  function renderCaption() {
    key.innerHTML = "";
    if (!frames.length || busy) {
      say.textContent = "";
      return;
    }
    // A list entry in focus, or a measured mark under the pointer: say what it is.
    const it = focusItem ? measItems.find((x) => x.key === focusItem) : null;
    if (it) {
      say.innerHTML = `<span class="bk-mtag" data-cls="${it.cls}"${it.grade ? ` data-grade="${it.grade}"` : ""}>${esc(it.label)}</span> <span class="bk-val">${esc(it.numbers)}</span>`;
      renderKey();
      return;
    }
    if (hover && hoverXY && hits.length) {
      let best: (typeof hits)[number] | null = null;
      let bd = Infinity;
      for (const h of hits) {
        const d = Math.hypot(h.X - hoverXY.X, h.Y - hoverXY.Y) + (h.pri ?? 0) * 1000;
        if (d - (h.pri ?? 0) * 1000 <= h.r && d < bd) {
          bd = d;
          best = h;
        }
      }
      if (best) {
        say.innerHTML = best.html;
        renderKey();
        return;
      }
    }
    if (hover && F && shown) {
      const [nx, ny] = size();
      const x = Math.floor(hover.u);
      const y = Math.floor(hover.v);
      if (x >= 0 && y >= 0 && x < nx && y < ny) {
        const sky = pixelToSky({ x: hover.u - 0.5, y: hover.v - 0.5 }, F, centreOf(allAtOnce() ? cur()?.epoch ?? null : shownEpoch()));
        const v = shown[y * nx + x];
        let val: string;
        if (Number.isNaN(v))
          val = burntAt(y * nx + x) ? "saturated" : shownFill?.[y * nx + x] ? "not in this photo (filled from its other photos)" : "no data";
        else if (shownSigned && shownLim?.sigma && shownLim.sigma > 0)
          val = `${v >= 0 ? "+" : "−"}${Math.abs(v / shownLim.sigma).toFixed(1)}σ`;
        // All wavelengths: the plate is already in units of sky noise, sky at 0.
        else if (waveGroups.size) val = `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}σ ${v >= 0 ? "above" : "below"} sky`;
        else val = `${v.toFixed(Math.abs(v) < 1 ? 3 : 2)} MJy/sr${levelSky ? " above sky" : ""}`;
        say.innerHTML = `<span class="bk-pos">${formatRaDec(sky)}</span> <span class="bk-val">${val}</span>`;
        renderKey();
        return;
      }
    }
    say.textContent = modeSentence();
    renderKey();
  }
  function burntAt(i: number): boolean {
    if (shownSigned) return false;
    if (allAtOnce()) return !!stackVals?.burnt[i];
    return !!burnt.get(shownIndex())?.[i];
  }
  function renderKey() {
    key.title = "";
    if (failed) {
      key.innerHTML = `<span class="bk-warn">${failed} frame${failed === 1 ? "" : "s"} failed to load</span>`;
      return;
    }
    if (mode === "difference" && !comparing && shownLim?.n) {
      key.innerHTML = `<span class="bk-keybar" aria-hidden="true"></span><span>±${shownLim.n}σ</span>`;
      key.title = `Colours run from ${shownLim.n} noise sigmas of light lost (blue) to ${shownLim.n} gained (red).`;
    } else if (mode === "blink" && !comparing) {
      key.innerHTML = `<span class="bk-hint">Click a dot to flag it${opts.viewer ? " · double-click to zoom in" : ""}</span>`;
    }
    // The red circles' key, when the candidate has a spot in these frames.
    if (overlays.target && trackM && frames.some((f) => spotFor(f.epoch))) {
      const k = document.createElement("span");
      k.className = "bk-trk";
      k.title = `Red circles: where ${trackM.label} should be in each ${unit()}. Solid: SkyShift measured it there. Dashed: predicted from its position and motion; look for a dot inside.`;
      k.innerHTML = `${TRACK_GLYPH(false)}<span>measured</span>${TRACK_GLYPH(true)}<span>predicted</span>`;
      key.append(k);
    }
  }

  /** Featured targets are centred, so the mover found at the centre is the target itself. */
  function moverName(): string {
    return view?.slug && view.name ? view.name : "the fast star";
  }

  // ---- UI sync -----------------------------------------------------------------------------
  function press(sel: string, attr: string, value: string) {
    // Buttons only: the player's root also carries data-mode, and aria-pressed is invalid there.
    $$<HTMLButtonElement>(`button${sel}`).forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute(attr) === value)));
  }

  function renderRow() {
    if (!view) return;
    press("[data-clock]", "data-clock", view.clock);
    const fovs = ALLOWED_FOV[view.clock];
    $(".bk-fov").innerHTML = fovs
      .map((f, i) => {
        if (!opts.viewer) {
          return html`<button type="button" data-fov="${f}" aria-pressed="${f === view!.fov}"
          aria-label="${f} arcminute field" title="${f} arcminute field">${f}′</button>`;
        }
        const word = i === 0 ? "Close-up" : "Wide";
        const about = `${word}: ${f} arcminutes, ${fieldVsMoon(f)}`;
        return html`<button type="button" data-fov="${f}" aria-pressed="${f === view!.fov}"
          aria-label="${about}" title="${about}">${word}</button>`;
      })
      .join("");
    const have = rawF ? waveDets() : [];
    // A static file holds every detector prepared; a live answer only the one asked for, so any other is a request away.
    const live = !!jsonUrl && new URL(jsonUrl, location.href).pathname.startsWith("/api/");
    $$<HTMLButtonElement>("[data-det]").forEach((b) => {
      if (b.dataset.det === "all") {
        b.setAttribute("aria-pressed", String(allWave));
        b.disabled = singleDetector;
        return;
      }
      const d = Number(b.dataset.det) as Det;
      b.setAttribute("aria-pressed", String(!allWave && d === view!.det));
      b.disabled = !!rawF && !live && !have.includes(d) && d !== view!.det;
    });
    root.style.setProperty("--det", allWave ? "var(--ink-2)" : `var(--d${view.det})`);
    markAvailable();
  }

  /** Featured targets: disable the clocks and fields that have no precomputed frames. */
  async function markAvailable() {
    const v = view;
    if (!v?.slug) return;
    const ix = await targetIndex(v.slug);
    if (!ix || view !== v) return;
    $$<HTMLButtonElement>("[data-clock]").forEach((b) => {
      const ok = ix.frames.some((f) => f.clock === b.dataset.clock);
      b.disabled = !ok && b.dataset.clock !== v.clock;
      b.dataset.tip ??= b.title;
      b.title = ok ? b.dataset.tip : `No ${b.dataset.clock} frames are prepared for ${ix.name}.`;
    });
    $$<HTMLButtonElement>("[data-fov]").forEach((b) => {
      const ok = ix.frames.some((f) => f.clock === v.clock && f.fov === Number(b.dataset.fov));
      b.disabled = !ok && Number(b.dataset.fov) !== v.fov;
    });
  }

  function renderControls() {
    press("[data-mode]", "data-mode", mode);
    press("[data-stretch]", "data-stretch", stretchKind);
    press("[data-positive]", "data-positive", String(positive));
    press("[data-zoom]", "data-zoom", String(zoom));
    press("[data-fps]", "data-fps", String(fps));
    $$<HTMLButtonElement>("[data-opt]").forEach((b) => {
      const o = b.dataset.opt;
      b.setAttribute("aria-pressed", String(o === "level" ? levelSky : o === "smooth" ? smoothOn : following()));
    });
    $$<HTMLButtonElement>("[data-ov]").forEach((b) =>
      b.setAttribute("aria-pressed", String(overlays[b.dataset.ov as keyof typeof overlays])),
    );
    const followRow = $<HTMLDivElement>(".bk-follow-row");
    followRow.hidden = !mover;
    if (mover) {
      const sw = $<HTMLButtonElement>("[data-opt='follow']");
      const rate = mover.rate >= 1 ? mover.rate.toFixed(1) : mover.rate.toFixed(2);
      $(".bk-follow-note").textContent = `${moverName()}, ${rate}″/yr (${mover.ref}). Turns on Stack.`;
      sw.setAttribute("aria-label", `Follow ${moverName()}: shift each frame along its motion before stacking`);
    }
    const stepOff = allAtOnce() || n() < 2 || !!busy;
    playBtn.disabled = stepOff;
    playBtn.innerHTML = playing ? icon.pause : icon.play;
    playBtn.setAttribute("aria-label", playing ? "Pause" : "Play");
    playBtn.setAttribute("aria-pressed", String(playing));
    hold.disabled = n() < 2;
    refSel.disabled = n() < 2;
    root.dataset.mode = mode;
    root.dataset.positive = String(positive);
    axisEl.setAttribute("aria-disabled", String(allAtOnce()));
  }

  function renderRefSelect() {
    refSel.innerHTML = frames
      .map(
        (f, i) =>
          `<option value="${i}">${
            view?.clock === "hours"
              ? `Exposure ${i + 1}, ${plateDate(f.epoch.mjd_mid, true).replace(/ \d{4},/, ",").replace(" UTC", "")}`
              : `Visit ${i + 1}, ${plateDate(f.epoch.mjd_mid)}`
          }</option>`,
      )
      .join("");
    refSel.value = String(ref);
  }

  function renderAxis() {
    strip.track.innerHTML = "";
    strip.labels.innerHTML = "";
    axis = null;
    if (!frames.length || !view) return renderAxisState();
    axis = drawStrip(
      strip,
      frames.map((f) => f.epoch),
      view.clock,
      axisEl.clientWidth || 600,
    );
    renderAxisState();
  }

  function renderAxisState() {
    if (!axis || !frames.length || !view) {
      // A slider must always say where it is: nothing to step through yet (or here).
      axisEl.setAttribute("aria-valuemin", "0");
      axisEl.setAttribute("aria-valuemax", "0");
      axisEl.setAttribute("aria-valuenow", "0");
      axisEl.setAttribute("aria-valuetext", "No frames");
      return;
    }
    updateStrip(strip, {
      axis,
      epochs: frames.map((f) => f.epoch),
      clock: view.clock,
      current: allAtOnce() ? null : shownIndex(),
      ref: (mode === "difference" || comparing) && n() > 1 ? ref : null,
      allLabel: `All ${n()} ${unit()}s`,
    });
    const e = shownEpoch();
    axisEl.setAttribute("aria-valuemin", "1");
    axisEl.setAttribute("aria-valuemax", String(n()));
    axisEl.setAttribute("aria-valuenow", String(idx + 1));
    axisEl.setAttribute("aria-valuetext", e ? `${unit()} ${idx + 1} of ${n()}, ${plateDate(e.mjd_mid, view.clock === "hours")}` : "");
  }

  function renderMsg() {
    const kind = busy ? "loading" : appStatus?.kind;
    const empty = !kind && !!F && !!view && !frames.length;
    // Progress: a calm line on the plate's top edge.
    progress.hidden = kind !== "loading";
    progress.classList.toggle("is-indeterminate", kind === "loading" && !busy);
    if (busy) progress.style.setProperty("--p", String(busy.total ? busy.done / busy.total : 0));
    plate.classList.toggle("is-dim", kind === "loading" && frames.length > 0);
    msg.hidden = !(kind === "error" || empty);
    msg.dataset.kind = kind ?? "empty";
    msgActions.innerHTML = "";
    msgText.textContent = "";
    if (kind === "error") {
      msgText.textContent = appStatus?.message ?? "Something went wrong. Try again, or pick a featured target.";
    } else if (empty && F && view) {
      const have = detectorsIn(F);
      if (!F.epochs.length) {
        msgText.textContent = `SPHEREx has no ${view.clock} frames here yet. Try the other clock or a featured target.`;
      } else if (failed) {
        msgText.textContent = `None of the ${failed} frames loaded. Check your connection, then try again.`;
        msgActions.innerHTML = `<button class="btn" type="button" data-retry>Try again</button>`;
      } else {
        msgText.textContent = `No D${view.det} frames on the ${view.clock} clock here. Pick a detector that has them.`;
        msgActions.innerHTML = have
          .map((d) => `<button class="btn det-${d} bk-msg-det" type="button" data-goto-det="${d}"><i class="bk-swatch"></i>D${d}</button>`)
          .join("");
      }
    }
    renderLabel();
  }

  // ---- what SkyShift measured: the panel under the controls --------------------------------
  /** Index the response's measured layer for this detector and pick the arrow exaggeration. */
  function prepareMeasured() {
    const st = measuredState(F);
    meas = st.kind === "measured" ? st.m : null;
    measAt = new Map();
    epochDet = new Map((F?.epochs ?? []).map((e) => [e.id, e.det]));
    measItems = [];
    if (!meas || !F || !view) return;
    trackGrade = new Map();
    sourceGrade = new Map();
    for (const d of meas.detections ?? []) {
      if (d.grade && d.track_id) trackGrade.set(d.track_id, d.grade);
      if (d.grade && d.source_id != null) sourceGrade.set(d.source_id, d.grade);
      const l = measAt.get(d.epoch_id);
      if (l) l.push(d);
      else measAt.set(d.epoch_id, [d]);
    }
    meas.tracks ??= [];
    meas.shifts ??= [];
    meas.detections ??= [];
    measItems = listItems(meas, epochDet, view.det, F.gaia);
  }

  /** Is there a saturated star to explain: Gaia lists one under G 9, or a photo of this detector has saturated pixels. */
  function saturatedHere(): boolean {
    if (!F || !view) return false;
    return F.gaia.some((g) => g.g != null && g.g < SATURATED_G) || F.epochs.some((x) => x.det === view!.det && x.sat_frac >= 0.001);
  }

  function renderMeasured() {
    const st = measuredState(F);
    mp.panel.hidden = st.kind === "absent";
    const on = st.kind === "measured";
    for (const e of [mp.rest, mp.key, mp.how, mp.items, mp.notes, mp.pro]) e.hidden = !on;
    mp.stage.hidden = !on;
    $<HTMLButtonElement>(".bk-meas-toggle").hidden = !on;
    if (mp.count) mp.count.textContent = "";
    if (st.kind === "absent") return;
    if (st.kind === "missing") {
      const r = reasonLine(st.reason);
      mp.lead.innerHTML = `${r.html}${r.retry ? ` <button class="btn btn-ghost bk-meas-retry" type="button" data-meas-retry>Try again</button>` : ""}`;
      mp.panel.dataset.state = st.reason;
      return;
    }
    const m = st.m;
    mp.panel.dataset.state = "measured";
    const s = summarize(m, F?.gaia);
    mp.lead.innerHTML = s.lead;
    mp.rest.textContent = s.rest;
    mp.rest.hidden = !s.rest;
    mp.stage.textContent = stageLabel(m.stage);
    mp.stage.dataset.stage = String(m.stage ?? "");
    mp.stage.hidden = !mp.stage.textContent;
    mp.key.innerHTML = classesPresent(m)
      .flatMap((c) => {
        const li = `<li title="${esc(CLASS_INFO[c].about)}">${glyph(c)}<span>${CLASS_INFO[c].name}</span></li>`;
        // Grade C suspects fail a check: greyed on the plate, so the legend says so.
        const hasC = c === "suspect" && m.detections.some((d) => d.class === "suspect" && d.grade === "C");
        return hasC
          ? [li, `<li title="A suspect that fails one of our checks (its reasons say which).">${glyph(c, "C")}<span>Suspect, grade C</span></li>`]
          : [li];
      })
      .join("") +
      (saturatedHere()
        ? `<li title="A star too bright for SPHEREx: its burnt core gives no single detection.">${SATURATED_GLYPH}<span>Saturated star</span></li>`
        : "");
    const arrows = m.clock === "hours" ? m.tracks.length > 0 : m.shifts.length > 0;
    mp.how.textContent =
      "Circles are what SkyShift measured in each photo, each as big as its source looks on the plate. " +
      "Blue rings are where Gaia predicts stars, a little bigger, so a match reads as a circle inside a ring; one ring with two dots is two Gaia stars SPHEREx sees as one. " +
      "Violet diamonds are where the Minor Planet Center predicts asteroids." +
      (saturatedHere() ? " A dashed ring is a saturated star: its burnt core gives no single detection, so its pieces aren't drawn." : "") +
      (arrows ? ` ${arrowNote(m.clock)}` : "");
    mp.notes.textContent = s.notes.join(" ");
    const c: Partial<Record<MeasuredClass, number>> = m.counts ?? {};
    mp.pro.textContent =
      `Detections per class, before the cap: ${CLASS_ORDER.map((k) => `${k} ${c[k] ?? 0}`).join(", ")}. ` +
      `${m.photos} photos read, ${m.photos_missing} without dots. Zone box RA ${m.zone_box?.ra?.join("–")}°, Dec ${m.zone_box?.dec?.join("–")}°.`;
    renderMeasuredList();
  }

  function renderMeasuredList() {
    const m = meas;
    if (!m || !view) return;
    const hours = m.clock === "hours";
    mp.items.hidden = !measItems.length;
    if (!measItems.length) return;
    if (mp.count) mp.count.textContent = `(${measItems.length})`;
    mp.lh.textContent = hours ? (waveGroups.size ? "Movers and artefacts on all detectors" : `Movers and artefacts on D${view.det}`) : "What moved between visits";
    const shown = measAll ? measItems : measItems.slice(0, LIST_SHOWN);
    mp.list.innerHTML = shown
      .map(
        (it) => html`<li><button class="bk-mi" type="button" data-item="${esc(it.key)}" aria-pressed="${it.key === pinned}"
          >${glyph(it.cls, it.grade)}<span class="bk-mi-t">${esc(it.label)}</span><span class="bk-mi-n">${esc(it.numbers)}</span></button></li>`,
      )
      .join("");
    const more = measItems.length - LIST_SHOWN;
    mp.all.hidden = more <= 0;
    mp.all.setAttribute("aria-expanded", String(measAll));
    mp.all.textContent = measAll ? "Show fewer" : `Show all ${measItems.length}`;
  }

  /** A list entry was clicked: pin its ring, and on the hours clock show a photo it is in. */
  function pickItem(key: string) {
    const it = measItems.find((x) => x.key === key);
    if (!it) return;
    pinned = pinned === key ? null : key;
    mp.list.querySelectorAll<HTMLButtonElement>(".bk-mi").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.item === pinned)));
    if (pinned && !overlays.measured) {
      overlays.measured = true;
      renderControls();
    }
    const here = cur()?.epoch.id;
    if (pinned && it.epochs.length && (!here || !it.epochs.includes(here))) {
      const i = frames.findIndex((f) => f.epoch.id === it.epochs[0]);
      if (i >= 0) {
        setPlaying(false);
        if (allAtOnce()) setMode("blink");
        goTo(i, false);
        return;
      }
    }
    draw();
  }

  function renderAll() {
    renderControls();
    renderLabel();
    renderCaption();
    renderAxisState();
  }

  // ---- actions -----------------------------------------------------------------------------
  /** Show epoch i. Stepping (skipRef) in Difference steps over the reference: it would be blank. */
  function goTo(i: number, skipRef = true) {
    if (!frames.length) return;
    const N = frames.length;
    let next = ((i % N) + N) % N;
    if (skipRef && mode === "difference" && N > 2 && next === ref) next = (((next + (i < idx ? -1 : 1)) % N) + N) % N;
    const changed = next !== idx;
    idx = next;
    paintImage();
    renderAll();
    if (changed) emitEpoch();
  }
  function emitEpoch() {
    const shownE = cur()?.epoch;
    if (!shownE) return;
    const e = memberEpoch(shownE);
    const members = waveMembers.get(shownE.id) ?? [{ epoch: e, url: jsonUrl }];
    opts.onEpoch?.({
      id: e.id, mjd_mid: shownE.mjd_mid, det: e.det,
      presentation: {
        mode: waveGroups.has(shownE.id) ? "all-wavelengths" : "single-detector",
        detectors: [...new Set(members.map(m => m.epoch.det))].sort() as Det[],
        units: waveGroups.has(shownE.id) ? "sky-noise" : "MJy/sr",
        members: members.map(m => ({id:m.epoch.id,det:m.epoch.det,mjd_mid:m.epoch.mjd_mid,data_url:new URL(m.epoch.data_url,m.url).href})),
      },
    });
  }
  /** The red circle's spot in a frame: its own, or (all wavelengths) a member detector's, measured first. */
  function spotFor(e: Epoch): TrackSpot | null {
    const own = trackBy.get(e.id);
    if (own) return own;
    const found = (waveGroups.get(e.id)?.members ?? []).flatMap((m) => trackBy.get(m.id) ?? []);
    return found.find((s) => s.measured) ?? found[0] ?? null;
  }
  /** The caption for a red circle under the pointer. */
  function trackWords(s: TrackSpot): string {
    const what = trackM?.label ?? "the find";
    const U = unit();
    const text = s.measured
      ? `${what[0].toUpperCase()}${what.slice(1)}, measured in this ${U}.`
      : `Where ${what} should be in this ${U}: predicted, nothing measured here.`;
    return `<span class="bk-mtag" data-cls="track">${s.measured ? "Red circle" : "Dashed red circle"}</span> <span class="bk-val">${esc(text)}</span>`;
  }
  function setTrack(marks: TrackMarks | null) {
    trackGen++;
    trackM = marks?.spots.length ? marks : null;
    trackBy = new Map((trackM?.spots ?? []).map((s) => [s.epoch_id, s]));
  }
  /** The real epoch behind a combined one: the chosen detector's, else the first. */
  function memberEpoch(e: Epoch): Epoch {
    const g = waveGroups.get(e.id);
    const members = waveMembers.get(e.id)?.map(m => m.epoch) ?? g?.members;
    return members?.find((m) => m.det === view?.det) ?? members?.[0] ?? e;
  }
  function setPlaying(on: boolean) {
    if (destroyed) return;
    const canPlay = on && pageActive && frames.length > 1 && !allAtOnce() && !busy;
    resumeOnVisible = canPlay && document.hidden;
    playing = canPlay && !document.hidden;
    clearTimeout(timer);
    if (playing) {
      const tick = () => {
        if (destroyed) return;
        if (document.hidden || !pageActive) { setPlaying(playing); return; }
        goTo(idx + 1);
        timer = window.setTimeout(tick, 1000 / fps);
      };
      timer = window.setTimeout(tick, 1000 / fps);
    }
    renderControls();
  }
  function setMode(m: Mode) {
    if (m === mode) return;
    mode = m;
    if (mode === "stack") setPlaying(false);
    if (mode === "difference" && idx === ref && frames.length > 1) idx = ref === frames.length - 1 ? 0 : frames.length - 1;
    if (mode !== "stack" && follow) follow = false;
    paintImage();
    renderAll();
  }
  function setCompare(on: boolean) {
    if (on === comparing || frames.length < 2) return;
    comparing = on;
    hold.setAttribute("aria-pressed", String(on));
    paintImage();
    renderAll();
  }
  function setZoom(z: number) {
    const [nx, ny] = size();
    if (z !== zoom) {
      if (pick && F) {
        const p = skyToPixel(pick, F, centreOf(cur()?.epoch ?? null));
        centre = { u: p.x + 0.5, v: p.y + 0.5 };
      } else centre = { u: nx / 2, v: ny / 2 };
    }
    zoom = z;
    if (z === 1) centre = { u: nx / 2, v: ny / 2 };
    canvas.classList.toggle("is-zoomed", z > 1);
    renderControls();
    draw();
  }

  // ---- Adjust: popover beside the plate on desktop, inline sheet on a phone ----------------
  function setAdjust(open: boolean) {
    adj.hidden = !open;
    adjBtn.setAttribute("aria-expanded", String(open));
    if (open) placeAdjust();
  }
  function placeAdjust() {
    if (adj.hidden) return;
    adj.classList.remove("is-right", "is-up", "is-sheet");
    const narrow = root.clientWidth < 600;
    if (narrow) {
      adj.classList.add("is-sheet");
      return;
    }
    const r = col.getBoundingClientRect();
    const room = document.documentElement.clientWidth - r.right - 16;
    if (room >= 360) {
      adj.classList.add("is-right");
      adj.style.bottom = "";
    } else {
      // No room beside the plate: open upward from the controls row, over the plate's corner.
      adj.classList.add("is-up");
      const row = $<HTMLDivElement>(".bk-row").getBoundingClientRect();
      adj.style.bottom = `${Math.round(r.bottom - row.top + 8)}px`;
    }
  }

  // ---- all wavelengths ---------------------------------------------------------------------
  const waveKey = (v: View) => `${v.ra.toFixed(5)},${v.dec.toFixed(5)},${v.clock},${v.fov}`;
  /** The responses for this field: the app's, and the other detectors fetched live. */
  function waveSources(v: View, resp: FramesResponse, url: string) {
    const extra = liveDets.key === waveKey(v) ? [...liveDets.got.values()].filter((x) => x.resp !== resp && sameGrid(x.resp, resp)) : [];
    return [{ resp, url }, ...extra];
  }
  /** Frames can only be summed pixel by pixel on one grid: same size, sampling and centre. */
  function sameGrid(a: FramesResponse, b: FramesResponse): boolean {
    if (a.clock !== b.clock || a.size[0] !== b.size[0] || a.size[1] !== b.size[1]) return false;
    if (Math.abs(a.pixel_arcsec - b.pixel_arcsec) > 1e-6) return false;
    const dra = (a.center.ra - b.center.ra) * Math.cos((b.center.dec * Math.PI) / 180);
    return Math.hypot(dra, a.center.dec - b.center.dec) * 3600 < 0.25 * b.pixel_arcsec;
  }
  /** Detectors this field has, in every response we hold. */
  function waveDets(): Det[] {
    if (!rawF || !view) return [];
    return [...new Set(waveSources(view, rawF, rawUrl).flatMap((x) => detectorsIn(x.resp)))].sort() as Det[];
  }
  /**
   * The response as the combined plate sees it: one epoch per moment (all detectors), known
   * objects and the measured layer moved onto those moments. null when only one detector is here.
   */
  function waveOf(v: View, resp: FramesResponse, url: string): { F: FramesResponse; groups: WaveGroup[]; urlOf: Map<string, string> } | null {
    const src = waveSources(v, resp, url);
    const urlOf = new Map<string, string>();
    const epochs: Epoch[] = [];
    for (const x of src)
      for (const e of x.resp.epochs)
        if (!urlOf.has(e.id)) {
          urlOf.set(e.id, x.url);
          epochs.push(e);
        }
    if (new Set(epochs.map((e) => e.det)).size < 2) return null;
    const groups = groupEpochs(epochs, v.clock, v.det);
    const groupOf = new Map(groups.flatMap((g) => g.members.map((m) => [m.id, g.epoch.id] as const)));
    // Only answers with a frame on the plate count: a live detector whose exposures are all at
    // other times (hours) adds nothing, and its dots and photos would be counted for nothing.
    const used = src.filter((x) => x.resp === resp || x.resp.epochs.some((e) => urlOf.get(e.id) === x.url && groupOf.has(e.id)));
    const seen = new Set<string>();
    const known = used
      .flatMap((x) => x.resp.known)
      .map((k) => ({ ...k, epoch_id: k.epoch_id == null ? null : (groupOf.get(k.epoch_id) ?? "") }))
      .filter((k) => k.epoch_id !== "" && !seen.has(`${k.epoch_id}|${k.name}`) && !!seen.add(`${k.epoch_id}|${k.name}`));
    const merged = mergeMeasured(used.map((x) => x.resp.measured));
    const measured = merged ? remapMeasured(merged, groups) : resp.measured;
    return { F: { ...resp, epochs: groups.map((g) => g.epoch), known, measured }, groups, urlOf };
  }
  /**
   * Static first: the frames we have (a static file, or one live detector) show at once. The
   * detectors they lack come from /api/frames in the background, in parallel, and are combined as
   * they arrive; one that fails or comes on another grid just stays out. Months: all six. Hours: the
   * partners exposed at the same moment. Not for frames that follow a mover: the live answer
   * stays on the sky, so it can't be summed with them.
   */
  function fetchOtherDets(v: View, resp: FramesResponse) {
    const key = waveKey(v);
    if (liveDets.key === key) return;
    liveDets = { key, got: new Map(), pending: true };
    if (resp.epochs.some((e) => !!e.center)) return;
    const have = new Set(detectorsIn(resp));
    if (!have.size) return;
    const want =
      v.clock === "months" ? DETS.filter((d) => !have.has(d)) : [...new Set([...have].map((d) => SAME_MOMENT[d] as Det))].filter((d) => !have.has(d));
    if (!want.length) return;
    void Promise.allSettled(
      want.map(async (d) => {
        const r = await loadFrames({ ra: resp.center.ra, dec: resp.center.dec, clock: v.clock, fov: v.fov, det: d, slug: null });
        if (!destroyed && liveDets.key === key && r.frames.clock === v.clock) liveDets.got.set(d, { resp: r.frames, url: r.jsonUrl });
      }),
    ).then(() => {
      if (destroyed || liveDets.key !== key) return;
      liveDets.pending = false;
      if (allWave && view && rawF && waveKey(view) === key && liveDets.got.size) void load(view, rawF, rawUrl);
    });
  }

  // ---- loading -----------------------------------------------------------------------------
  function setSingleDetector(locked: boolean): boolean {
    const before = allWave;
    singleDetector = locked;
    allWave = !locked && preferredAllWave;
    // A raw response fetched earlier must not contribute to a derived product on the same sky grid.
    if (locked) liveDets = { key: "", got: new Map(), pending: false };
    return before !== allWave;
  }

  async function load(next: View, resp: FramesResponse, url: string): Promise<void> {
    if (destroyed) return;
    const gen = ++loadCtl;
    const wasPlaying = playing || resumeOnVisible || firstShow;
    setPlaying(false);
    const prev = view;
    const prevMjd = cur()?.epoch.mjd_mid;
    const prevRefMjd = frames[ref]?.epoch.mjd_mid;
    const sameClock = prev?.clock === next.clock;
    const moved = !prev || Math.abs(prev.ra - next.ra) > 1e-6 || Math.abs(prev.dec - next.dec) > 1e-6;
    if (moved) {
      pick = null;
      follow = false;
    }
    view = next;
    rawF = resp;
    rawUrl = url;
    const wave = allWave ? waveOf(next, resp, url) : null;
    F = wave?.F ?? resp;
    waveGroups = new Map((wave?.groups ?? []).map((g) => [g.epoch.id, g]));
    const loadedCounts = new Map<string, number>();
    const loadedMembers = new Map<string, { epoch: Epoch; url: string }[]>();
    waveLoaded = loadedCounts;
    waveMembers = loadedMembers;
    jsonUrl = url;
    if (allWave) fetchOtherDets(next, resp);
    appStatus = null;
    comparing = false;
    root.dataset.ready = "false";
    renderRow();
    if (moved || prev?.clock !== next.clock || prev?.det !== next.det) {
      pinned = null;
      measAll = false;
    }
    focusItem = null;
    prepareMeasured();
    renderMeasured();
    const eps = wave ? wave.groups.map((g) => g.epoch) : epochsFor(resp, next.det);
    if (!eps.length) {
      frames = [];
      failed = 0;
      busy = null;
      mover = null;
      shifts = null;
      burnt.clear();
      resetCaches();
      renderMsg();
      renderAll();
      renderAxis();
      draw();
      root.dataset.ready = "true";
      return;
    }
    busy = { done: 0, total: eps.length };
    renderMsg();
    renderControls();
    const [gx, gy] = resp.size;
    /** One moment of all wavelengths: each detector's frame in units of its own noise, summed. */
    const combined = async (g: WaveGroup): Promise<Float32Array> => {
      const got = await Promise.allSettled(
        g.members.map(async (m): Promise<WavePart & { epoch: Epoch; url: string }> => {
          const memberUrl = wave!.urlOf.get(m.id) ?? url;
          const v = toMJySr(await cachedFrame(m, memberUrl), m);
          if (v.length !== gx * gy) throw new Error(`frame ${m.id} has the wrong size`);
          const sky = median(v);
          const sigma = robustSigma([v], 40_000);
          return { vals: v, burnt: burntCores(v, gx, gy, sky, sigma), sky, sigma, epoch:m, url:memberUrl };
        }),
      );
      const parts = got.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
      if (!parts.length) throw new Error(`no frame of ${g.epoch.id} loaded`);
      loadedCounts.set(g.epoch.id, parts.length);
      loadedMembers.set(g.epoch.id, parts.map(p => ({epoch:p.epoch,url:p.url})));
      return combineFrames(parts);
    };
    const results = await pool(
      eps,
      6,
      async (e) => {
        const g = waveGroups.get(e.id);
        return g ? combined(g) : toMJySr(await cachedFrame(e, url), e);
      },
      () => {
        if (gen !== loadCtl || !busy) return;
        busy.done++;
        progress.style.setProperty("--p", String(busy.done / busy.total));
        renderLabel();
      },
      () => !destroyed && gen === loadCtl,
    );
    if (gen !== loadCtl) return;
    const [nx, ny] = resp.size;
    const loaded: Frame[] = [];
    failed = 0;
    results.forEach((r, i) => {
      if (r instanceof Error || r.length !== nx * ny) failed++;
      else loaded.push({ epoch: eps[i], vals: r, sky: median(r), sigma: robustSigma([r], 40_000) });
    });
    frames = loaded;
    burnt.clear();
    resetCaches();
    busy = null;
    const near = (m: number | undefined) =>
      m == null || !sameClock ? 0 : nearestIndex(frames.map((f) => f.epoch.mjd_mid), m);
    idx = near(prevMjd);
    ref = near(prevRefMjd);
    if (mode === "difference" && idx === ref && frames.length > 1) idx = frames.length - 1;
    if (moved || prev?.fov !== next.fov) {
      zoom = 1;
      centre = { u: nx / 2, v: ny / 2 };
      canvas.classList.remove("is-zoomed");
    }
    findFollow();
    renderMsg();
    renderRefSelect();
    renderAxis();
    if (frames.length) {
      paintImage();
      renderAll();
      emitEpoch();
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      if (pageActive && wasPlaying && !reduce && !allAtOnce()) setPlaying(true);
      firstShow = false;
    } else {
      renderAll();
      draw();
    }
    root.dataset.ready = "true";
  }

  /** Offer "Follow the mover" when a fast mover sits at the centre and drifts visibly. */
  function findFollow() {
    mover = null;
    shifts = null;
    if (!F || !view || frames.length < 2 || followsObject()) {
      follow = false;
      return;
    }
    const mjds = frames.map((f) => f.epoch.mjd_mid);
    const mid = (mjds[0] + mjds[mjds.length - 1]) / 2;
    const m = findMover(F, mid, view.slug, { ra: view.ra, dec: view.dec });
    if (!m) {
      follow = false;
      return;
    }
    const s = followShifts(m.star, mjds, F, F.center, mid);
    if (driftPx(s) < 0.5) {
      follow = false;
      return;
    }
    mover = m;
    shifts = s;
  }

  // ---- events ------------------------------------------------------------------------------
  root.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button");
    if (!b || b.disabled || !view) return;
    const d = b.dataset;
    if (d.clock) {
      if (d.clock !== view.clock) {
        const clock = d.clock as Clock;
        const fov = DEFAULT_FOV[clock];
        const v = view;
        // A featured target's other clock may lack this detector (WISE 0855 opens on D6; its hours
        // frames are D2 and D5): switch to the nearest one it has instead of an empty plate.
        if (!v.slug) opts.onViewChange({ clock, fov });
        else
          void targetIndex(v.slug).then((ix) => {
            if (view !== v) return;
            const dets = ix?.frames.find((f) => f.clock === clock && f.fov === fov)?.dets ?? [];
            const det = dets.length && !dets.includes(v.det)
              ? dets.reduce((a, b) => (Math.abs(b - v.det) < Math.abs(a - v.det) ? b : a))
              : null;
            opts.onViewChange(det ? { clock, fov, det } : { clock, fov });
          });
      }
    } else if (d.fov) {
      if (Number(d.fov) !== view.fov) opts.onViewChange({ fov: Number(d.fov) });
    } else if (d.det) {
      // "All" combines every detector; a detector button shows that one alone, as a filter.
      const all = d.det === "all";
      if (all && singleDetector) return;
      if (!singleDetector) preferredAllWave = all;
      if (all !== allWave) {
        allWave = all;
        if (rawF && (all || Number(d.det) === view.det)) void load(view, rawF, rawUrl);
      }
      if (!all && Number(d.det) !== view.det) opts.onViewChange({ det: Number(d.det) as Det });
      else renderRow();
    } else if (d.gotoDet) {
      allWave = false;
      if (!singleDetector) preferredAllWave = false;
      opts.onViewChange({ det: Number(d.gotoDet) as Det });
    }
    else if (d.retry !== undefined && (rawF ?? F)) void load(view, (rawF ?? F)!, jsonUrl);
    else if (d.mode) setMode(d.mode as Mode);
    else if (d.stretch) {
      stretchKind = d.stretch as StretchKind;
      paintImage();
      renderAll();
    } else if (d.positive) {
      positive = d.positive === "true";
      paintImage();
      renderAll();
    } else if (d.opt === "level") {
      levelSky = !levelSky;
      resetCaches();
      paintImage();
      renderAll();
    } else if (d.opt === "smooth") {
      smoothOn = !smoothOn;
      smoothed.clear();
      limitCache.clear();
      paintImage();
      renderAll();
    } else if (d.opt === "follow") {
      follow = !following() && !!mover;
      if (follow && mode !== "stack") {
        mode = "stack";
        setPlaying(false);
      }
      paintImage();
      renderAll();
    } else if (d.ov) {
      const k = d.ov as keyof typeof overlays;
      overlays[k] = !overlays[k];
      renderControls();
      draw();
      if (k === "target") renderCaption();
    } else if (d.zoom) setZoom(Number(d.zoom));
    else if (d.fps) {
      fps = Number(d.fps);
      renderControls();
      if (playing) setPlaying(true);
    } else if (d.item) pickItem(d.item);
    else if (b === mp.all) {
      measAll = !measAll;
      renderMeasuredList();
    } else if (d.measRetry !== undefined) opts.onViewChange({});
    else if (b === playBtn) setPlaying(!playing);
    else if (b === adjBtn) setAdjust(adj.hidden);
  });

  // The list: pointer or focus on an entry rings it on the plate and names it under the plate.
  const itemOf = (t: EventTarget | null) => (t instanceof Element ? t.closest<HTMLButtonElement>(".bk-mi") : null);
  const focusOn = (key: string | null) => {
    if (key === focusItem) return;
    focusItem = key;
    draw();
    renderCaption();
  };
  // Opening "What SkyShift measured" draws its circles on the plate; folding it clears the plate again.
  if (mp.panel instanceof HTMLDetailsElement) {
    const fold = mp.panel;
    fold.addEventListener("toggle", () => {
      if (overlays.measured === fold.open) return;
      overlays.measured = fold.open;
      renderControls();
      draw();
    });
  }
  mp.list.addEventListener("pointerover", (ev) => focusOn(itemOf(ev.target)?.dataset.item ?? null));
  mp.list.addEventListener("pointerleave", () => focusOn(document.activeElement && itemOf(document.activeElement) ? itemOf(document.activeElement)!.dataset.item ?? null : null));
  mp.list.addEventListener("focusin", (ev) => focusOn(itemOf(ev.target)?.dataset.item ?? null));
  mp.list.addEventListener("focusout", (ev) => {
    if (!itemOf(ev.relatedTarget)) focusOn(null);
  });

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
    if (mode === "difference" && idx === ref && frames.length > 1) idx = ref === frames.length - 1 ? 0 : frames.length - 1;
    paintImage();
    renderAll();
  });
  contrastIn.addEventListener("input", () => {
    contrast = Number(contrastIn.value);
    paintImage();
    renderCaption();
  });

  // Close Adjust on Escape or a click elsewhere.
  const onDocDown = (ev: PointerEvent) => {
    if (adj.hidden) return;
    const t = ev.target as Node;
    if (adj.contains(t) || adjBtn.contains(t)) return;
    setAdjust(false);
  };
  document.addEventListener("pointerdown", onDocDown);

  // Time strip: click or drag to scrub.
  let scrubbing = false;
  const epochAt = (ev: PointerEvent): number | null => {
    if (!axis || !frames.length) return null;
    const r = strip.track.getBoundingClientRect();
    const p = (ev.clientX - r.left) / r.width;
    return nearestIndex(frames.map((f) => axis!.x(f.epoch.mjd_mid)), p);
  };
  const scrub = (ev: PointerEvent) => {
    const i = epochAt(ev);
    if (i !== null && i !== idx) goTo(i, false);
  };
  /** A faint mark under the pointer shows which epoch a click would pick. */
  const ghost = (ev: PointerEvent | null) => {
    const g = strip.track.querySelector<HTMLElement>(".bk-ghost");
    if (!g) return;
    const i = ev && ev.pointerType === "mouse" && !allAtOnce() ? epochAt(ev) : null;
    g.hidden = i === null || i === idx;
    if (i !== null && axis) g.style.left = `${(axis.x(frames[i].epoch.mjd_mid) * 100).toFixed(3)}%`;
  };
  axisEl.addEventListener("pointerdown", (ev) => {
    if (allAtOnce() || !frames.length) return;
    scrubbing = true;
    axisEl.setPointerCapture?.(ev.pointerId);
    setPlaying(false);
    scrub(ev);
    ghost(null);
  });
  axisEl.addEventListener("pointermove", (ev) => (scrubbing ? scrub(ev) : ghost(ev)));
  axisEl.addEventListener("pointerleave", () => ghost(null));
  for (const t of ["pointerup", "pointercancel"]) axisEl.addEventListener(t, () => (scrubbing = false));
  axisEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Home" || ev.key === "End") {
      ev.preventDefault();
      setPlaying(false);
      goTo(ev.key === "Home" ? 0 : frames.length - 1, false);
    }
  });

  // Plate: hover reads a pixel, click flags a dot, double-click (or double-tap) zooms in on that
  // spot (1× → 2× → 4× → back to 1×), drag pans when zoomed.
  let down: { X: number; Y: number; u: number; v: number; moved: boolean } | null = null;
  let lastTap: { t: number; X: number; Y: number } | null = null;
  const DOUBLE_MS = 350;
  const DOUBLE_PX = 24;
  function zoomAt(u: number, v: number) {
    const next = zoom >= 4 ? 1 : zoom * 2;
    zoom = next;
    if (next === 1) {
      const [nx, ny] = size();
      centre = { u: nx / 2, v: ny / 2 };
    } else {
      centre = { u, v };
      const vp = viewport(); // keeps the view inside the frame near the edges
      centre = { u: vp.u0 + vp.vw / 2, v: vp.v0 + vp.vh / 2 };
    }
    canvas.classList.toggle("is-zoomed", next > 1);
    renderControls();
    draw();
  }
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
        draw();
      }
    }
    hover = ev.pointerType === "mouse" ? toImage(X, Y) : null;
    hoverXY = hover ? { X, Y } : null;
    renderCaption();
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
    // Viewer: a second click close in time and place zooms there (the first one picked the spot).
    const now = performance.now();
    if (opts.viewer && lastTap && now - lastTap.t < DOUBLE_MS && Math.hypot(X - lastTap.X, Y - lastTap.Y) < DOUBLE_PX) {
      lastTap = null;
      zoomAt(u, v);
      return;
    }
    lastTap = { t: now, X, Y };
    const shownE = cur()?.epoch;
    if (!shownE) return;
    const sky = pixelToSky({ x: u - 0.5, y: v - 0.5 }, F, centreOf(shownE));
    pick = sky;
    draw();
    // A combined moment is not a SPHEREx epoch: the vote names one of the photos in it.
    const e = memberEpoch(shownE);
    opts.onPick({ ra: sky.ra, dec: sky.dec, mjd: e.mjd_mid, clock: view.clock, det: e.det, epoch_id: e.id });
  });
  canvas.addEventListener("pointerleave", () => {
    hover = null;
    hoverXY = null;
    renderCaption();
  });

  // Keyboard: space plays, arrows step, hold C compares, Escape closes Adjust. Ignored while typing.
  const typing = (t: EventTarget | null) =>
    t instanceof HTMLElement && !!t.closest("input, textarea, select, [contenteditable='true']");
  const visible = () => el.isConnected && el.getClientRects().length > 0;
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape" && !adj.hidden) {
      setAdjust(false);
      adjBtn.focus();
      return;
    }
    if (!visible() || typing(ev.target) || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.key === " ") {
      if (ev.target instanceof HTMLElement && ev.target.closest("button")) return;
      ev.preventDefault();
      setPlaying(!playing);
    } else if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
      if (!frames.length || allAtOnce()) return;
      if (ev.target instanceof HTMLElement && ev.target.closest(".seg, .bk-ramp, .bk-marks")) return;
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
  const onVisibility = () => setPlaying(playing || resumeOnVisible);
  document.addEventListener("visibilitychange", onVisibility);

  // Size the canvas to the plate, at device resolution; redraw the strip at its new width.
  let stripWidth = 0;
  const ro = new ResizeObserver(() => {
    const w = plate.clientWidth;
    if (w) {
      const r = Math.min(3, window.devicePixelRatio || 1);
      if (w !== cssSize || r !== dpr || canvas.width !== Math.round(w * r)) {
        cssSize = w;
        dpr = r;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(w * dpr);
        draw();
      }
    }
    if (axis && axisEl.clientWidth !== stripWidth) {
      stripWidth = axisEl.clientWidth;
      renderAxis();
    }
    placeAdjust();
  });
  ro.observe(plate);
  ro.observe(axisEl);
  ro.observe(root);

  // Fonts and tokens may arrive after mount.
  document.fonts?.ready.then(() => {
    if (destroyed) return;
    fontFamily = getComputedStyle(el).fontFamily || fontFamily;
    palette = readPalette(el);
    lut.key = "";
    hatch.key = "";
    paintImage();
    renderAxis();
  });

  renderControls();

  return {
    async show(next, resp, url, options) {
      // Red circles stay with the frames they were made for: none of these epochs, no circles.
      if (trackM && !resp.epochs.some((e) => trackBy.has(e.id))) setTrack(null);
      setSingleDetector(options?.singleDetector ?? false);
      await load(next, resp, url);
    },
    singleDetector(locked) {
      if (setSingleDetector(locked) && view && rawF) void load(view, rawF, rawUrl);
      else renderRow();
    },
    status(kind, message) {
      appStatus =
        kind === "idle" ? null : { kind, message: message ?? (kind === "loading" ? "Loading frames…" : "Something went wrong.") };
      if (kind === "error") {
        loadCtl++;
        busy = null;
        setPlaying(false);
        frames = [];
        F = null;
        prepareMeasured();
        renderMeasured();
        renderAxis();
        draw();
        renderCaption();
        root.dataset.ready = "true";
      } else if (kind === "loading") { loadCtl++; setPlaying(false); root.dataset.ready = "false"; }
      renderMsg();
    },
    highlight(pos) { pick=pos; draw(); },
    track(marks) {
      setTrack(marks);
      draw();
      renderCaption();
    },
    gaia(stars) { if (F) { F.gaia = stars; draw(); } },
    active(value) { pageActive = value; if (!value) setPlaying(false); },
    display(value) { setMode(value); },
    step() { setPlaying(false); goTo(idx + 1); },
    canCompare() { return frames.length >= 2 && !busy; },
    destroy() {
      destroyed = true;
      playing = resumeOnVisible = false;
      loadCtl++;
      liveDets = { key: "", got: new Map(), pending: false };
      clearTimeout(timer);
      ro.disconnect();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("keyup", onKeyUp);
      document.removeEventListener("pointerdown", onDocDown);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      el.innerHTML = "";
    },
  };
};
