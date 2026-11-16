// The blink player (P4). Draws SPHEREx frames on a plate and blinks them on the months clock (visit
// stacks) or the hours clock (single exposures): by default all wavelengths at once (allwave.ts:
// every detector of one moment combined in grey), or one detector picked on the ramp.
// Seam: src/app/modules.ts. Design: web/DESIGN.md ("Lightbox"). The plate is the hero, the time
// strip sits right under it, one row of primary controls follows, and everything secondary lives
// behind "Adjust".
import "./blink.css";
import { ALLOWED_FOV, ApiFailure, DEFAULT_FOV, detectorsIn, epochsFor, loadFrameData, loadFrames, loadTargetIndex, toMJySr } from "../api/client";
import {
  absolute,
  gridWhy,
  joinPage,
  newStream,
  nextPage,
  PAGE_RETRY_MS,
  PAGES_AT_ONCE,
  PAGES_AT_ONCE_PAIRED,
  pageUrl,
  pagesText,
  photosOf,
  sameGrid,
  streamDone,
  visitSource,
  type PageStream,
} from "./pages";
import { detKey, KIND_ORDER, KIND_WORDS, onePhotoChecksAsync, onePhotoCounts, onePhotoSay, type OnePhoto, type OnePhotoKind, type OPFrame } from "./onephoto";
import { combineFrames, detectorsNote, groupEpochs, mergeMeasured, remapMeasured, type WaveGroup, type WavePart } from "./allwave";
import type { Clock, Det, Epoch, FramesResponse, GaiaStar, Measured, MeasuredClass, MeasuredDetection, SkyPos, TargetIndex } from "../api/types";
import { DETECTORS, SAME_MOMENT, formatRaDec, gaiaAt, pixelToSky, skyToPixel } from "../app/astro";
import type { BlinkHandle, BlinkOptions, MountBlink, TrackMarks, TrackSpot, View } from "../app/modules";
import { nearestIndex, type Axis } from "./axis";
import { SPHEREX_V_LIMIT, seenBySpherex } from "./known";
import { besideArrow, LabelSpace, type Disc } from "./labels";
import { circleRadius, OWN_ARCSEC, planMarks, sourceGaia, type Plan, type PlanDet, type PlanStar } from "./layout";
import { arrowBarbs, arrowPath, bigStar, compass, diamond, flagMark, hatchPattern, label, measuredMark, onePhotoBadge, pencil, reticle, ring, scaleBar } from "./marks";
import { engineNow, loadEngine, nearEcliptic, planetsWithin, type PlanetSpot } from "./planets";
import { getsRing, glowRadius, localSky, peakNear, ringLimitG, standsOutAt, type SeenStar } from "./rings";
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
  plainBlendWords,
  plainShiftWords,
  viewerShows,
  withoutArtefacts,
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
  moreLine,
  motionArrow,
  offsetWords,
  reasonLine,
  shiftAt,
  shiftNumbers,
  shiftVerdict,
  summarize,
  trackDotsFor,
  visitPath,
  wobbles,
  wobbleWords,
  type Arrow,
  type MeasuredItem,
  type TrackExtras,
} from "./measured";
import { driftPx, findMover, followShifts, type Mover } from "./motion";
import { buildLut, diffIndex, fade, lerp, paint, parseHex, plainIndex, readPalette, type Palette } from "./paint";
import { drawStrip, plateDate, updateStrip, visitSpan } from "./strip";
import { getDetails, onDetails } from "../prefs/details";

type Mode = "blink" | "difference" | "stack";
const DETS: Det[] = [1, 2, 3, 4, 5, 6];
const FPS = [1, 2, 4, 8];
const PSF_FWHM_ARCSEC = 7.53;
const FAST_STAR_MAS_YR = 300;
/** At most this many Gaia rings on a 680 px plate (fewer on a smaller one): the brightest win. */
const MAX_RINGS = 80;
/** Past that cap, a Gaia star whose dot shows within this many PSF FWHM of a ringed star still gets its ring. */
const PAIR_FWHM = 2;
/** The ring limit is measured on at most this many of a plate's photos (rings.ts). */
const CAL_FRAMES = 12;
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

/** Another detector's live answer for the field on show (all wavelengths). */
interface LivePart {
  resp: FramesResponse;
  /** Its page 0's URL (page k is the same request with `page`). */
  url: string;
}
/** One detector's pages of the visit on show (pages.ts); `part`: joined to a LivePart, not the plate's
 *  own answer. `first`: the exposures of its page 0 (an API without pages answers it again). `joined`:
 *  the pages it has put in its answer (a page may still be asked again: it was partial). */
type Stream = PageStream & { part: boolean; first: string; joined: Set<number> };

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

/** A frame's pixels in MJy/sr, kept with its raw array (the one-photo checks read every photo often). */
const mjyCache = new WeakMap<Uint16Array, Float32Array>();
function mjy(epoch: Epoch, raw: Uint16Array): Float32Array {
  let v = mjyCache.get(raw);
  if (!v) {
    v = toMJySr(raw, epoch);
    mjyCache.set(raw, v);
  }
  return v;
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
  other: `<circle cx="9" cy="9" r="5" stroke-width="1.3"/>`,
  known: `<circle cx="9" cy="9" r="3.6"/><circle cx="9" cy="9" r="6.6"/>`,
  mover: `<circle cx="9" cy="9" r="4.2"/><path d="M1 9h2.6M14.4 9H17"/>`,
  suspect: `<circle cx="9" cy="9" r="5" stroke-width="2.4"/>`,
  artifact: `<circle cx="9" cy="9" r="5"/><path d="M5.5 12.5l7-7"/>`,
  stack: `<circle cx="9" cy="9" r="5" stroke-dasharray="3 2.2" stroke-width="1.3"/>`,
};
/** The Viewer's legend in plain words, where they differ from the To check plate's (CLASS_INFO). */
const VIEWER_NAME: Partial<Record<MeasuredClass, string>> = {
  other: "Source Gaia doesn't list",
  stack: "Seen only when photos are added up",
};
const glyph = (c: MeasuredClass, grade?: string | null) =>
  `<svg class="bk-mg" data-cls="${c}"${grade ? ` data-grade="${grade}"` : ""} viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">${GLYPH_PATH[c] ?? GLYPH_PATH.other}</svg>`;
/** A saturated star with no detection of its own: the plate's long-dashed ring (marks.ts `bigStar`). */
// Only the dashed ring means "saturated": no dot in it (a filled dot read as the blue blend dots).
const SATURATED_GLYPH = `<svg class="bk-mg" data-cls="star" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="9" cy="9" r="7" stroke-dasharray="4 2.4"/></svg>`;
/** Advanced: the small blue dots inside a ring, one per Gaia star of a blend. */
const BLEND_DOTS_GLYPH = `<svg class="bk-mg" data-cls="star" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.2"><circle cx="9" cy="9" r="7"/><circle cx="6.6" cy="8" r="1.4" fill="currentColor" stroke="none"/><circle cx="11.4" cy="10.2" r="1.4" fill="currentColor" stroke="none"/></svg>`;
/** Advanced: a bright star's glow, a faint dotted circle. */
const GLOW_GLYPH = `<svg class="bk-mg" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" opacity="0.7"><circle cx="9" cy="9" r="7" stroke-dasharray="0.1 2.8"/></svg>`;
/** The dotted edge of a bright star's glow (Advanced): dash pattern and opacity. */
const GLOW_DASH = [0.1, 4];
const GLOW_ALPHA = 0.55;
/** Draw order on the plate: the plainest first, so suspects end on top. */
const DRAW_ORDER: MeasuredClass[] = [...CLASS_ORDER].reverse();
/** Rows shown before "Show all". */
const LIST_SHOWN = 6;
/** The red circle where a candidate is only predicted (BlinkHandle.track): dashed. */
const TRACK_DASH = [4, 3.5];
const TRACK_GLYPH = (dashed: boolean) =>
  `<svg class="bk-mg" data-cls="track" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="9" cy="9" r="6"${dashed ? ` stroke-dasharray="3 2.6"` : ""}/></svg>`;
/** A circle with the small "1" of a dot circled in one photo only (marks.ts onePhotoBadge). */
const ONE_GLYPH = `<svg class="bk-mg bk-one-g" viewBox="0 0 18 18" aria-hidden="true"><circle cx="10.5" cy="10.5" r="5.5" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="5" cy="5" r="4.4" fill="var(--glass, #edf0ef)" stroke="currentColor" stroke-width="1.1"/><text x="5" y="5.3" font-size="6.5" font-weight="700" text-anchor="middle" dominant-baseline="middle" fill="currentColor">1</text></svg>`;
const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/** A word waiting for its spot on the plate (labels.ts): beside the mark `at`, lowest `prio` placed first. */
interface Word {
  text: string;
  at: Disc;
  color: string;
  font: string;
  prio: number;
  /** Directions to try (radians, screen y down); default lower right first, then round the mark. */
  angles?: number[];
  /** Space between the mark and the word, px. */
  gap?: number;
  /** A suspect's grade whose circle the candidate's red circle replaced: it moves beside that circle. */
  onTrack?: boolean;
}
/** Names read best to the right of their mark, as before; then round it. */
const RIGHT_FIRST = [0, 45, -45, 180, 135, -135, 90, -90].map((d) => (d * Math.PI) / 180);
/** A suspect's grade: upper right of its circle, as before; then round it. */
const GRADE_FIRST = [-45, 45, -135, 135, 0, 180, -90, 90].map((d) => (d * Math.PI) / 180);

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
        <p class="bk-pages" hidden>
          <span class="bk-pages-n" aria-live="polite"
            title="SkyShift loads every photo of this visit from SPHEREx by itself, a few at a time (each takes up to half a minute), and adds them to the blink as they arrive."></span>
        </p>

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
          <p class="bk-meas-stacknote" hidden>The Stack view adds every visit together, so it draws no circles: each one belongs to one visit. Switch to Blink or Difference to see them.</p>
          <p class="bk-meas-rest pro-only"></p>
          <ul class="bk-meas-key" aria-label="Marks"></ul>
          <p class="bk-meas-short plain-only"></p>
          <div class="bk-meas-one" hidden>
            <p class="bk-meas-one-h"></p>
            <ul class="bk-meas-one-list"></ul>
            <p class="bk-meas-one-note"></p>
          </div>
          <p class="bk-meas-how pro-only"></p>
          <div class="bk-meas-items">
            <p class="bk-meas-lh" id="${uid}-ml"></p>
            <ul class="bk-meas-list" aria-labelledby="${uid}-ml"></ul>
            <button class="btn btn-ghost bk-meas-all" type="button" aria-expanded="false" hidden></button>
            <button class="bk-meas-more" type="button" aria-expanded="false" aria-controls="${uid}-mx" hidden></button>
            <ul class="bk-meas-list bk-meas-extra" id="${uid}-mx" hidden></ul>
          </div>
          <p class="bk-meas-notes pro-only"></p>
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
  const whenEl = $<HTMLElement>(".bk-when");
  const whenMain = $<HTMLElement>(".bk-when-main");
  const whenSub = $<HTMLElement>(".bk-when-sub");
  const detLabel = $<HTMLElement>(".bk-det-label");
  const say = $<HTMLParagraphElement>(".bk-say");
  const key = $<HTMLParagraphElement>(".bk-key");
  const playBtn = $<HTMLButtonElement>(".bk-play");
  const axisEl = $<HTMLDivElement>(".bk-axis");
  const strip = { track: $<HTMLDivElement>(".bk-track"), labels: $<HTMLDivElement>(".bk-tls") };
  const pagesEl = $<HTMLParagraphElement>(".bk-pages");
  const pagesN = $<HTMLSpanElement>(".bk-pages-n");
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
    /** Stack view: why the plate has no circles (each belongs to one visit). */
    stackNote: $<HTMLParagraphElement>(".bk-meas-stacknote"),
    rest: $<HTMLParagraphElement>(".bk-meas-rest"),
    key: $<HTMLUListElement>(".bk-meas-key"),
    how: $<HTMLParagraphElement>(".bk-meas-how"),
    items: $<HTMLDivElement>(".bk-meas-items"),
    lh: $<HTMLParagraphElement>(".bk-meas-lh"),
    list: $<HTMLUListElement>(".bk-meas-list"),
    all: $<HTMLButtonElement>(".bk-meas-all"),
    /** The quiet line the entries the lead doesn't count fold under, and their list. */
    more: $<HTMLButtonElement>(".bk-meas-more"),
    extra: $<HTMLUListElement>(".bk-meas-extra"),
    notes: $<HTMLParagraphElement>(".bk-meas-notes"),
    pro: $<HTMLParagraphElement>(".bk-meas-pro"),
    short: $<HTMLParagraphElement>(".bk-meas-short"),
    /** Dots circled in one photo only: what the other photos say (onephoto.ts). */
    one: $<HTMLDivElement>(".bk-meas-one"),
    oneH: $<HTMLParagraphElement>(".bk-meas-one-h"),
    oneList: $<HTMLUListElement>(".bk-meas-one-list"),
    oneNote: $<HTMLParagraphElement>(".bk-meas-one-note"),
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
  /** Live answers come one detector at a time: the others for this field, fetched in the background.
   *  Hours: each partner also gets the pages the shown detector has (`pages` merged into `resp`). */
  let liveDets: { key: string; got: Map<Det, LivePart>; pending: boolean } = {
    key: "",
    got: new Map(),
    pending: false,
  };
  /**
   * The whole visit on show (hours: contracts/api.md v3.23 `page`). `merged` is the answer show() was
   * handed with every page joined since (all its detectors); the plate shows `filter(merged)`. One
   * stream per detector asks the live pages. `gen` changes when the plate goes elsewhere, and a page
   * of an older generation is dropped when it lands.
   */
  const pg = {
    gen: 0, on: false, key: "",
    /** The URL of the answer show() was handed (the same plate again keeps its visit), and the one its
     *  relative data_url resolve against (a live answer's, once the plate went live on another grid). */
    src: "", base: "",
    merged: null as FramesResponse | null,
    filter: null as ((f: FramesResponse) => FramesResponse) | null,
    streams: [] as Stream[],
    /** Why the plate shows live photos instead of the saved ones (another grid), else "". */
    switched: "",
    /** Pages joined that the plate doesn't show yet. */
    dirty: false,
    /** Photos of the shown detector on the plate (joined and loaded into the blink), for the line. */
    shown: 0,
  };
  /** The last full load (not a quiet one): pages wait for it, so they never cut it short. */
  let pendingLoad: Promise<void> = Promise.resolve();
  // On the Viewer, SkyShift's measured circles start off (the plate shows Gaia's rings) and opening
  // "What SkyShift measured" turns them on; other plates start with them on.
  const overlays = { measured: !opts.viewer, gaia: true, known: true, faint: false, target: true, guides: true };
  // The Viewer and the To check plates leave artefacts (camera glitches) out entirely.
  const noArtefacts = !!opts.viewer || !!opts.hideArtefacts;
  /** The response's measured layer, without artefacts where this plate leaves them out. */
  const measuredHere = () => {
    const st = measuredState(F);
    return noArtefacts && st.kind === "measured" ? { ...st, m: withoutArtefacts(st.m) } : st;
  };
  /** The candidate the red circles follow (BlinkHandle.track), its spots by frame id. */
  let trackM: TrackMarks | null = null;
  /** The candidate on the plate even when no frame has a spot for it: the panel names it (summarize `candidate`). */
  let trackFor: (TrackMarks & TrackExtras) | null = null;
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
  /** The entries the lead doesn't count, unfolded. */
  let measMore = false;
  /** How far sources reach on the shown plate (image pixels), by position: reset when the plate or its stretch changes. */
  let reachCache: { vals: Float32Array | null; floor: number; m: Map<string, number> } = { vals: null, floor: NaN, m: new Map() };
  /** How far each bright star's glow reaches on the plate shown (rings.ts glowRadius), image pixels. */
  let glowCache: { vals: Float32Array | null; m: Map<string, number> } = { vals: null, m: new Map() };
  /** What the last plate drew that the legend explains (redrawn when it changes). */
  let plateKey = { dots: false, glow: false };
  /** Hours: what the other photos of its detector say about a dot circled in one photo only
   *  (onephoto.ts), by detKey; the moments measured (this generation) and measured at least once.
   *  Measured again when more photos of the visit come (kept meanwhile); cleared for a new plate. */
  let opDone = new Map<string, OnePhoto>();
  let opJobs = new Set<string>();
  let opChecked = new Set<string>();
  let opGen = 0;
  /** The plate's ring limit and the Gaia stars its detections matched (rings.ts), for these frames and this measured layer. */
  let ringCal: { frames: Frame[]; meas: Measured | null; limit: number | null; matched: Set<string> } | null = null;
  /** Planets near the field, by time and centre (planets.ts); filled once the ephemeris has loaded. */
  const planetCache = new Map<string, PlanetSpot[]>();
  let planetWait = false;
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
  /** Exposures used only to fill the edge (not blinked): the rest of the visit, when the plate
   *  shows a few exposures of one session that all share one edge (an object to check). */
  let fillSrc: { epoch: Epoch; url: string }[] = [];
  let fillOnly: (Frame & { burnt: Uint8Array })[] = [];
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
  const fillsHoles = () => view?.clock === "hours" && frames.length + fillOnly.length > 1;
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
      for (const g of fillOnly) {
        if (g.vals.length !== nx * ny) continue;
        const cj = centreOf(g.epoch);
        let dx = 0;
        let dy = 0;
        if (F && (cj.ra !== c0.ra || cj.dec !== c0.dec)) {
          const p = skyToPixel(cj, F, c0);
          dx = p.x - (nx - 1) / 2;
          dy = p.y - (ny - 1) / 2;
        }
        others.push({ vals: g.vals, sky: g.sky, burnt: g.burnt, dx, dy });
      }
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
  type NoteBox = { x: number; y: number; w: number };
  /** Vertical step between two notes that would overlap (CSS px; the label is 11 px text). */
  const NOTE_LINE = 20;
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
    const first = drawNote(NO_DATA_NOTE, noDataCache.empty, vp);
    drawNote(FILLED_NOTE, noDataCache.filled, vp, first);
  }
  /** Draw a note near `spot`; it moves a line down (or up) rather than cover the note `avoid`. */
  function drawNote(text: string, spot: Spot, vp: VP, avoid: NoteBox | null = null): NoteBox | null {
    if (!ctx || !spot || spot.frac < 0.03) return null;
    const p = toScreen({ x: spot.x - 0.5, y: spot.y - 0.5 }, vp);
    ctx.save();
    ctx.font = `600 11px ${fontFamily}`;
    const w = ctx.measureText(text).width;
    const left = vp.ox + 6;
    const right = vp.ox + vp.vw * vp.s - 6;
    const top = vp.oy + 14;
    const bottom = vp.oy + vp.vh * vp.s - 6;
    const x = Math.min(Math.max(p.X - w / 2, left), right - w);
    let y = Math.min(Math.max(p.Y + 4, top), bottom);
    if (avoid && Math.abs(y - avoid.y) < NOTE_LINE && x < avoid.x + avoid.w && avoid.x < x + w) {
      y = avoid.y + NOTE_LINE <= bottom ? avoid.y + NOTE_LINE : avoid.y - NOTE_LINE;
    }
    label(ctx, text, x, y, cssColor("--ink-2", "#4a5560"), cssColor("--sheet-2", "#e3e8e7"));
    ctx.restore();
    return { x, y, w };
  }

  /**
   * How deep this plate goes (rings.ts `ringLimitG`), from its own pixels: for each Gaia star, the
   * photos with data at its place and the photos that show a dot there (6σ over its local sky). Over
   * the photos blinked here (up to CAL_FRAMES of them, spread out), so the rings hold still from
   * photo to photo. Also the Gaia stars a detection matched in any of them: SkyShift sees those on
   * their own (layout.ts plans one ring per blob with it).
   */
  function ringCalibration(): { limit: number | null; matched: Set<string> } {
    if (ringCal && ringCal.frames === frames && ringCal.meas === meas) return ringCal;
    const matched = new Set<string>();
    const here = new Set(frames.map((f) => f.epoch.id));
    for (const d of meas?.detections ?? []) if (d.gaia_id && here.has(d.epoch_id)) matched.add(d.gaia_id);
    let limit: number | null = null;
    if (F && frames.length) {
      const f0 = F;
      const [nx, ny] = size();
      const annulus = Math.max(3, Math.round(1.6 * (PSF_FWHM_ARCSEC / f0.pixel_arcsec)));
      const step = Math.max(1, Math.ceil(frames.length / CAL_FRAMES));
      const use = frames.map((f, i) => ({ f, i })).filter(({ i }) => i % step === 0);
      const seen: SeenStar[] = [];
      for (const s of f0.gaia) {
        if (s.g == null) continue;
        let covered = 0;
        let hits = 0;
        for (const { f, i } of use) {
          if (f.vals.length !== nx * ny) continue;
          const px = skyToPixel(gaiaAt(s, f.epoch.mjd_mid), f0, centreOf(f.epoch));
          if (px.x < 0 || px.y < 0 || px.x > nx - 1 || px.y > ny - 1) continue;
          const b = burntOf(i);
          if (noDataAt(f.vals, b, nx, ny, px.x, px.y)) continue;
          covered++;
          if (standsOutAt(f.vals, b, nx, ny, px.x, px.y, annulus, f.sigma)) hits++;
        }
        if (covered) seen.push({ g: s.g, frames: covered, hits });
      }
      limit = ringLimitG(seen);
    }
    ringCal = { frames, meas, limit, matched };
    return ringCal;
  }

  /**
   * Planets in or near the field at this time (planets.ts). The ephemeris loads only for a plate
   * near the ecliptic, the first time one draws; the plate draws again once it has.
   */
  function planetsAt(mjd: number, c: SkyPos): PlanetSpot[] {
    if (!F) return [];
    const [nx, ny] = size();
    // Half the plate's diagonal, plus a margin for a big disc just past the edge.
    const radius = (Math.hypot(nx, ny) / 2) * (F.pixel_arcsec / 3600) + 0.05;
    if (!nearEcliptic(c, radius)) return [];
    const A = engineNow();
    if (!A) {
      if (!planetWait) {
        planetWait = true;
        loadEngine().then(
          () => {
            planetWait = false;
            if (!destroyed) draw();
          },
          () => {}, // no ephemeris (offline): no planet names, and no retry on every draw
        );
      }
      return [];
    }
    const k = `${mjd.toFixed(4)}|${c.ra.toFixed(5)}|${c.dec.toFixed(5)}|${radius.toFixed(3)}`;
    let list = planetCache.get(k);
    if (!list) {
      list = planetsWithin(A, c, radius, mjd);
      planetCache.set(k, list);
      if (planetCache.size > 400) planetCache.delete(planetCache.keys().next().value as string);
    }
    return list;
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
    /** Does the plate show a dot here: peak (3 × 3) above the local sky around it by 6σ? (rings.ts) */
    const standsOut = (x: number, y: number): boolean => standsOutAt(pv.vals, pv.burnt, nx, ny, x, y, annulus, pv.sigma);
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
    /**
     * The same, for a source on a bright star's glow: how far it reaches over its local sky (rings.ts
     * localSky, the glow included), by the same margin as `reach` over the plate's sky. Over the plate's
     * sky alone a G 15 star on Barnard's glow read the whole glow as its own, and got a ring 20″ wide.
     */
    const reachOverGlow = (px: { x: number; y: number }): number => {
      const k = `g${px.x.toFixed(2)},${px.y.toFixed(2)}`;
      let r = reachCache.m.get(k);
      if (r == null) {
        const bg = localSky(pv.vals, nx, ny, px.x, px.y, annulus);
        const floor = Number.isNaN(bg) ? visFloor : Math.max(visFloor, bg + (visFloor - pv.sky));
        r = visibleExtent(pv.vals, pv.burnt, nx, ny, px.x, px.y, { ...reachOpts, floor });
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

    // Which Gaia stars get a ring: every star SkyShift measured in this photo (a single-photo
    // detection matched to it, or under a source the server didn't match: layout.ts sourceGaia), and
    // the others only when brighter than this plate's own limit (rings.ts: the G at which about half
    // the Gaia stars here show a dot), so faint Gaia stars SPHEREx can't see get no ring on blank sky.
    // With too few stars to measure that limit, the old rule: single exposures reach about G 15.5,
    // visit stacks a little deeper. Either way a Gaia star where the plate shows a dot (6σ over its
    // local sky) keeps its ring, so no visible star goes unmarked. Inside a bright star's glow only a
    // star that stands out there, or one SkyShift measured, does. Not the target: the reticle marks
    // it, and Gaia's own ring would crowd it.
    //
    // All of this is worked out from this photo's detections whatever the measured layer shows: the
    // same Gaia stars get the same rings with the layer on or off. (With it, it used to ring stars
    // only stack-only sources matched, which the plain plate never shows: a ring appeared when the
    // layer was turned on.)
    const faintG = view.clock === "hours" ? 15.5 : 16.5;
    const cal = ringCalibration();
    const pxPerArcsec = vp.s / F.pixel_arcsec;
    const floor = Math.max(3.5, psf / 2);
    const brightExtent = Math.max(10, 2 * psf);
    const advanced = getDetails();
    const gaiaIn = F.gaia
      .filter((s) => s.g != null && s.g <= 20.5)
      .map((s) => {
        const px = skyToPixel(gaiaAt(s, mjd), F!, c);
        return { s, px, p: toScreen(px, vp), sat: (s.g ?? 99) < SATURATED_G || burntNear(px) };
      })
      .filter((o) => inView(o.p) && !hatched(o.px));
    // Bright stars and how far their glow reaches on this plate (rings.ts glowRadius), screen pixels.
    if (glowCache.vals !== pv.vals) glowCache = { vals: pv.vals, m: new Map() };
    const maxGlow = Math.ceil(MAX_REACH_ARCSEC / F.pixel_arcsec);
    // Brightest first; a fainter star inside a brighter one's glow (a G 17 star on Barnard's burnt
    // core reads saturated) has none of its own.
    const glows: { id: string; X: number; Y: number; g: number; glow: number; r: number }[] = [];
    for (const o of gaiaIn
      .filter((q) => q.sat || (q.s.g ?? 99) <= 12)
      .filter((q) => q.sat || reach(q.px) >= brightExtent)
      .sort((a, b) => (a.s.g ?? 99) - (b.s.g ?? 99))) {
      if (glows.some((z) => Math.hypot(o.p.X - z.X, o.p.Y - z.Y) <= z.r)) continue;
      let r = glowCache.m.get(o.s.id);
      if (r == null) {
        r = glowRadius(pv.vals, pv.burnt, nx, ny, o.px.x, o.px.y, pv.sky, pv.sigma, maxGlow);
        glowCache.m.set(o.s.id, r);
      }
      glows.push({ id: o.s.id, X: o.p.X, Y: o.p.Y, g: o.s.g ?? 99, glow: r * vp.s, r: Math.max(r * vp.s, circleRadius(reach(o.px), floor)) });
    }
    const glowOf = new Map(glows.map((z) => [z.id, z.glow]));
    /** The bright star whose glow holds this point (not the star itself), if any. */
    const inGlow = (p: { X: number; Y: number }, id?: string) =>
      glows.some((z) => z.id !== id && Math.hypot(p.X - z.X, p.Y - z.Y) <= z.r && Math.hypot(p.X - z.X, p.Y - z.Y) > OWN_ARCSEC * pxPerArcsec);
    const measuredOn = !!meas && overlays.measured;
    // This photo's detections, whatever the measured layer shows. The Viewer circles every real
    // source the plate shows; not camera glitches or grade C suspects, and stack-only sources only
    // with Advanced on (viewerShows).
    const all = e ? (measAt.get(e.id) ?? []) : [];
    const ds = all.filter((d) => !opts.viewer || viewerShows(d.class, d.grade, advanced));
    const allAt = all.map((d) => {
      const px = skyToPixel(d, F!, c);
      return { d, px, p: toScreen(px, vp) };
    });
    const claimed = new Set(all.filter((d) => d.class !== "stack" && d.class !== "artifact" && d.gaia_id).map((d) => d.gaia_id!));
    const foundHere = new Set(claimed);
    const starPts = gaiaIn.map((o) => ({ id: o.s.id, X: o.p.X, Y: o.p.Y, g: o.s.g }));
    for (const { d, p } of allAt)
      for (const id of sourceGaia({ X: p.X, Y: p.Y, cls: d.class, gaiaId: d.gaia_id, blendGaiaIds: extras(d).blend_gaia_ids }, starPts, claimed, pxPerArcsec, inGlow(p)))
        foundHere.add(id);
    const halfFwhm = 0.5 * fwhmPx;
    /** Does the plate show this star's dot: over its local sky; in a glow, a peak of its own there. */
    const shows = (o: (typeof gaiaIn)[number]) =>
      inGlow(o.p, o.s.id) ? peakNear(pv.vals, pv.burnt, nx, ny, o.px.x, o.px.y, halfFwhm, annulus, pv.sigma) : standsOut(o.px.x, o.px.y);
    const ringable = gaiaIn
      .filter((o) => !overlays.target || Math.hypot(o.p.X - tp.X, o.p.Y - tp.Y) > gap)
      .filter((o) => getsRing(o.s.g ?? 99, foundHere.has(o.s.id), cal.limit, faintG, () => shows(o), inGlow(o.p, o.s.id)));
    // Stars found in this photo always keep their ring; the others, the brightest first, up to the
    // cap. (Found stars sharing the cap pushed the brightest unmatched one out: Barnard's Star itself.)
    const capped = ringable
      .filter((o) => !foundHere.has(o.s.id))
      .sort((a, b) => (a.s.g ?? 99) - (b.s.g ?? 99))
      .slice(0, Math.max(16, Math.round(MAX_RINGS * Math.min(1, (vp.vw * vp.s) ** 2 / 680 ** 2))));
    const ringed = new Set([...ringable.filter((o) => foundHere.has(o.s.id)).map((o) => o.s.id), ...capped.map((o) => o.s.id)]);
    // Past the cap, a star whose dot shows right beside a ringed one or a single-photo detection
    // (within PAIR_FWHM) keeps its ring: a bare dot next to a marked star reads as one SkyShift
    // missed. (Crowded hours plates: hundreds of such pairs on Barnard's.) Stack-only sources don't
    // count: the plain Viewer doesn't draw them, and the rings are the same with Advanced on or off.
    const pairPx = PAIR_FWHM * fwhmPx * vp.s;
    const marked = [...gaiaIn.filter((o) => ringed.has(o.s.id)).map((o) => o.p), ...allAt.filter((a) => a.d.class !== "stack" && a.d.class !== "artifact").map((a) => a.p)];
    for (const o of ringable)
      if (!ringed.has(o.s.id) && marked.some((m) => Math.hypot(m.X - o.p.X, m.Y - o.p.Y) <= pairPx) && shows(o)) ringed.add(o.s.id);
    const planStars: PlanStar[] = [];
    for (const o of gaiaIn) {
      const g = o.s.g ?? 99;
      // Stars with no ring matter only when bright: their glow can hold fragments.
      if (!ringed.has(o.s.id) && !o.sat && g > 12) continue;
      planStars.push({
        id: o.s.id,
        X: o.p.X,
        Y: o.p.Y,
        g: o.s.g,
        extent: !o.sat && inGlow(o.p, o.s.id) ? reachOverGlow(o.px) : reach(o.px),
        saturated: o.sat,
        ringed: ringed.has(o.s.id),
        detected: cal.matched.has(o.s.id),
        peak: ringed.has(o.s.id) && peakNear(pv.vals, pv.burnt, nx, ny, o.px.x, o.px.y, halfFwhm, annulus, pv.sigma),
        glow: glowOf.get(o.s.id),
        found: foundHere.has(o.s.id),
      });
    }
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
        extent: inGlow(p) ? reachOverGlow(px) : reach(px),
        gaiaId: d.gaia_id,
        reasons: d.reasons ?? [],
        snr: d.snr,
        parentGaiaId: x.parent_gaia_id,
        blendGaiaIds: x.blend_gaia_ids,
      });
    });
    // The plain Viewer leaves out sources a bright star's glow may have made; Advanced (and the To
    // check plate) draws them greyed, with the glow's edge.
    const plan = planMarks(planStars, planDets, { pxPerArcsec, floor, brightExtent, measured: measuredOn, hideGlow: !!opts.viewer && !advanced });
    const gaiaOf = new Map(gaiaIn.map((o) => [o.s.id, o.s]));
    // The plate's small words go last, each where it covers no other word or arrow (labels.ts):
    // marks register here as they are drawn, words wait in `words` until then.
    const space = new LabelSpace({ x: vp.ox, y: vp.oy, w: vp.vw * vp.s, h: vp.vh * vp.s });
    const words: Word[] = [];
    {
      // The scale bar and the compass (drawGuides) keep their corners.
      const W = cssSize;
      const pad = Math.max(10, Math.round(W * 0.022));
      if (overlays.guides) {
        space.addBox({ x: pad - 4, y: W - pad - 20, w: W * 0.18 + 30, h: 26 });
        space.addBox({ x: W - pad - 34, y: W - pad - 36, w: 38, h: 42 });
      }
    }
    for (const cc of plan.circles) space.addDisc(cc);
    for (const b of plan.big) space.addDisc(b);
    if (overlays.gaia) for (const rg of plan.rings) space.addDisc(rg);

    // A blend's ring holds a small blue dot where Gaia puts each of its stars: in Advanced (and on the
    // To check plate) only. On the plain plate they read as stars of their own, or as "saturated".
    const blendDots = !opts.viewer || advanced;
    const drew = { dots: false, glow: false };
    if (overlays.gaia) {
      const blue = pen("--pencil-blue");
      const ringFaint = Math.max(10, cal.limit ?? faintG);
      for (const rg of plan.rings) {
        const s = gaiaOf.get(rg.id);
        const t = Math.min(1, Math.max(0, ((s?.g ?? ringFaint) - 9) / (ringFaint - 9)));
        ring(ctx, rg.X, rg.Y, rg.r);
        pencil(ctx, blue, 1.4 - 0.4 * t, 0.85 - 0.45 * t);
        if (blendDots && rg.blend?.length) drew.dots = true;
        for (const b of blendDots ? (rg.blend ?? []) : []) {
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
        space.addDisc({ X: p.X, Y: p.Y, r });
        if (named)
          words.push({
            text: faint && k.v != null ? `${k.name} V ${k.v.toFixed(1)}` : k.name,
            at: { X: p.X, Y: p.Y, r },
            color: violet,
            font: `600 11px ${fontFamily}`,
            angles: RIGHT_FIRST,
            gap: 5,
            prio: 4,
          });
      }
    }

    // A planet in the field (planets.ts): its name beside its disc, quiet, and a note on hover.
    for (const pl of planetsAt(mjd, c)) {
      const px = skyToPixel(pl, F, c);
      const p = toScreen(px, vp);
      if (!inView(p, 0)) continue;
      const r = Math.max(floor, reach(px));
      words.push({ text: pl.name, at: { X: p.X, Y: p.Y, r }, color: pen("--ink-2"), font: `600 11px ${fontFamily}`, gap: 5, prio: 2 });
      hits.push({
        X: p.X,
        Y: p.Y,
        r: r + 4,
        pri: 1,
        html: `<span class="bk-mtag" data-cls="planet">${pl.name}</span> <span class="bk-val">A planet: its moons show up as movers.</span>`,
      });
    }

    // The candidate's red circle this frame, if any: a suspect circle on the same spot is not drawn
    // again (two red rings read as one thick smudge); its grade goes beside the red circle.
    const trackNow = overlays.target && trackM && e ? spotFor(e) : null;
    const trackNowAt = trackNow && e ? toScreen(skyToPixel(trackNow, F, centreOf(e)), vp) : null;

    // A bright star's name in a hover: the target's own name (the star under the reticle), else its G.
    const targetStar = view.slug && view.name ? gaiaIn.find((o) => Math.hypot(o.p.X - tp.X, o.p.Y - tp.Y) <= gap && o.sat) : undefined;
    const starName = (id: string) => {
      if (targetStar?.s.id === id) return view!.name!;
      const g = gaiaOf.get(id)?.g;
      return g != null ? `the G ${g.toFixed(1)} star` : "a bright star";
    };
    drawMeasured(vp, e, c, inView, floor, halo, plan, ds, gaiaOf, space, words, trackNowAt, advanced, starName);
    // The legend explains the blue dots and the glow's edge when the plate draws them.
    drew.glow = advanced && overlays.measured && plan.glows.length > 0;
    if (meas && (drew.dots !== plateKey.dots || drew.glow !== plateKey.glow)) {
      plateKey = drew;
      renderMeasuredKey(meas);
    }

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
        space.addDisc({ X: p.X, Y: p.Y, r });
        if (e) {
          hits.push({ X: p.X, Y: p.Y, r: r + 4, html: trackWords(s) });
          // A grade whose circle this red one replaced goes beside the red one.
          for (const w of words) if (w.onTrack) w.at = { X: p.X, Y: p.Y, r: Math.max(w.at.r, r) };
        }
      }
    }

    rgapMemo = { key: gapKey, gap: rgap };
    if (overlays.target) {
      const len = Math.max(7, rgap * 0.6);
      reticle(ctx, tp.X, tp.Y, rgap, len);
      pencil(ctx, pen("--pencil-red"), 1.75);
      for (const [dx, dy] of [
        [1, 0],
        [-1, 0],
        [0, 1],
        [0, -1],
      ])
        space.addPath([
          { X: tp.X + dx * rgap, Y: tp.Y + dy * rgap },
          { X: tp.X + dx * (rgap + len), Y: tp.Y + dy * (rgap + len) },
        ]);
    }
    placeWords(space, words, halo);

    if (pick) {
      const p = toScreen(skyToPixel(pick, F, c), vp);
      flagMark(ctx, p.X, p.Y, Math.max(7, Math.min(14, psf * 0.8)));
      pencil(ctx, pen("--pencil-red"), 1.75);
    }
    ctx.restore();
  }

  /** Draw the plate's words, most important first, each at the first free spot beside its mark (labels.ts). */
  function placeWords(space: LabelSpace, words: Word[], halo: string) {
    if (!ctx) return;
    ctx.save();
    ctx.textAlign = "start";
    for (const w of [...words].sort((a, b) => a.prio - b.prio)) {
      ctx.font = w.font;
      const m = ctx.measureText(w.text);
      // The box holds the text and its 1.5 px halo; ascent and descent as measured where the canvas can.
      const asc = m.actualBoundingBoxAscent || 8;
      const desc = m.actualBoundingBoxDescent || 2;
      const b = space.place(w.at, m.width + 3, asc + desc + 3, w.gap ?? 3, w.angles);
      if (b) label(ctx, w.text, b.x + 1.5, b.y + 1.5 + asc, w.color, halo);
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
    space: LabelSpace,
    words: Word[],
    trackAt: { X: number; Y: number } | null,
    advanced: boolean,
    starName: (id: string) => string,
  ) {
    const m = meas;
    shiftArrows = new Map();
    if (!ctx || !F || !view || !m || !overlays.measured) return;
    // Advanced: the edge of each bright star's glow that holds a greyed source, faint and dotted.
    if (advanced)
      for (const gl of plan.glows) {
        ring(ctx, gl.X, gl.Y, gl.r);
        ctx.save();
        ctx.setLineDash(GLOW_DASH);
        pencil(ctx, pen("--ink-2"), 1.4, GLOW_ALPHA);
        ctx.restore();
      }
    const f = F;
    const toS = (p: SkyPos) => toScreen(skyToPixel(p, f, c), vp);
    const stroke = (col: string, w: number, a = 0.9) => {
      pencil(ctx, halo, w + 2, 0.5);
      pencil(ctx, col, w, a);
    };
    /** An arrow drawn: words keep off its shaft and its head. */
    const keepOff = (a: Arrow, head: number) => {
      space.addPath(a.pts);
      for (const [tip, barb] of arrowBarbs(a.pts, head) ?? []) space.addPath([tip, barb]);
    };
    /** A stretched arrow carries its factor ("×20"), beside its middle and clear of other words, and a small open dot where the motion really ends. */
    const stretched = (a: Arrow, col: string) => {
      if (a.factor <= 1) return;
      const p0 = a.pts[0];
      const p1 = a.pts[a.pts.length - 1];
      words.push({
        text: `×${a.factor}`,
        at: { X: (p0.X + p1.X) / 2, Y: (p0.Y + p1.Y) / 2, r: 0 },
        angles: besideArrow(p0, p1),
        gap: 4,
        color: col,
        font: `600 10.5px ${fontFamily}`,
        prio: 1,
      });
      ctx.beginPath();
      ctx.arc(a.end.X, a.end.Y, 2.2, 0, Math.PI * 2);
      stroke(col, 1.2);
      space.addDisc({ X: a.end.X, Y: a.end.Y, r: 2.2 });
    };
    const longer = (a: Arrow) => (a.factor > 1 ? ` The arrow is drawn ${a.factor} times longer than the motion.` : "");

    drawZoneEdge(m, vp, c, halo, space);

    if (m.clock === "hours") {
      for (const t of m.tracks) {
        const dots = trackDotsFor(t, epochDet, view.det);
        if (dots.length < 2) continue;
        if (opts.viewer && greyed(t.class, trackGrade.get(t.id))) continue;
        const a = motionArrow(dots.map(toS));
        if (!a || !a.pts.some((q) => inView(q))) continue;
        const col = pen(greyed(t.class, trackGrade.get(t.id)) ? "--m-artifact" : classInfo(t.class).token);
        if (arrowPath(ctx, a.pts, 7)) stroke(col, 1.6);
        keepOff(a, 7);
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
          if (opts.viewer && greyed(s.class, sourceGrade.get(s.source_id))) return;
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
          keepOff(a, 6);
          stretched(a, col);
          const said = opts.viewer ? plainShiftWords(s) : shiftNumbers(s) + ".";
          const html = `<span class="bk-mtag" data-cls="${s.class}">${esc(describeShift(s, grade))}</span> <span class="bk-val">${esc(said + longer(a))}</span>`;
          hits.push({ X: a.pts[0].X, Y: a.pts[0].Y, r: 8, html });
          hits.push({ X: a.pts[a.pts.length - 1].X, Y: a.pts[a.pts.length - 1].Y, r: 8, html });
        });
      drawVisitPaths(m, det, toS, vp, inView, stroke, space, words);
    }

    // This epoch's circles, as big as their sources look; pieces of a bright star stay off the plate.
    if (e) {
      if (m.clock === "hours" && plan.circles.length && !opJobs.has(e.id)) void checkOnePhoto(e);
      if (m.clock === "hours") checkAllOnePhoto();
      const pxPerArcsec = vp.s / f.pixel_arcsec;
      const order = [...plan.circles].sort((a, b) => DRAW_ORDER.indexOf(ds[a.i].class) - DRAW_ORDER.indexOf(ds[b.i].class));
      for (const cc of order) {
        // A bright star's own measurement (layout.ts: the nearest dot within 6″ of where Gaia puts it)
        // is that Gaia star, whatever the server's match: Barnard's Star moves too fast for it, and
        // read "Source Gaia doesn't list" now that the Viewer circles those.
        const d0 = ds[cc.i];
        // A source the server didn't match that sits on Gaia stars (layout.ts sourceGaia) is drawn and
        // named as those stars: a "Gaia doesn't list" circle with Gaia's ring around it said both.
        // Display only. A stack-only one keeps its dashed circle.
        const d = (cc.bright || cc.asGaia) && d0.class === "other" ? { ...d0, class: "star" as const } : cc.bright && d0.class === "stack" ? { ...d0, class: "star" as const } : d0;
        // In a bright star's glow, with nothing in Gaia to back it: greyed (Advanced; the plain Viewer leaves it out).
        const col = pen(greyed(d.class, d.grade) || cc.glow ? "--m-artifact" : classInfo(d.class).token);
        // The candidate's own red circle already marks this suspect: one red ring, not two.
        const onTrack =
          d.class === "suspect" && !!trackAt && Math.hypot(cc.X - trackAt.X, cc.Y - trackAt.Y) <= Math.max(cc.r, 6 * pxPerArcsec);
        if (!onTrack) measuredMark(ctx, d.class, cc.X, cc.Y, cc.r, col, halo);
        // Hours: circled in this photo only (onephoto.ts): a small "1" on the circle, and the other
        // photos' answer in plain words under the plate.
        const op = m.clock === "hours" ? opDone.get(detKey(d0)) : undefined;
        const lone = !!op && !op.track;
        if (lone && !onTrack) space.addDisc(onePhotoBadge(ctx, cc.X, cc.Y, cc.r, col, halo, fontFamily));
        // Its grade, upper right first as before, clear of the arrow and the arrow's "×20".
        if (d.class === "suspect" && d.grade)
          words.push({ text: d.grade, at: { X: cc.X, Y: cc.Y, r: cc.r }, color: col, font: `700 11px ${fontFamily}`, gap: 2, angles: GRADE_FIRST, prio: 0, onTrack });
        const blendTag = cc.blend && d.class === "star";
        const notes: string[] = [];
        if (cc.glow) notes.push(`in ${starName(cc.glow)}'s glow: may not be real`);
        if (cc.blend && !blendTag) notes.push(opts.viewer ? plainBlendWords(cc.blend.length) : blendWords(cc.blend.length));
        if (cc.asGaia && cc.star)
          notes.push(`Gaia DR3 ${cc.star}${gaiaOf.get(cc.star)?.g != null ? `, G ${gaiaOf.get(cc.star)!.g!.toFixed(1)}` : ""}${opts.viewer ? "" : " (not matched by the pipeline)"}`);
        const off = offsetWords(cc.offsetArcsec);
        if (off) notes.push(off);
        if (cc.bright) {
          const sat = (gaiaOf.get(cc.bright)?.g ?? 99) < SATURATED_G;
          notes.push(sat ? "a saturated star: the circle holds its whole blob" : "a bright star: the circle holds its whole glow");
          if (cc.fragments) notes.push(`${cc.fragments} ${cc.fragments === 1 ? "piece" : "pieces"} of its glow hidden`);
        }
        // Other measurements of the same object (closer than half a PSF width), not drawn.
        if (cc.also?.length) {
          const n = cc.also.length;
          const kinds = listJoin([...new Set(cc.also.map((k) => classInfo(ds[k].class).name.toLowerCase()))]);
          notes.push(`${n === 1 ? "another measurement" : `${n} more measurements`} of it here (${kinds}), not drawn`);
        }
        const tag = blendTag ? (opts.viewer ? plainBlendWords(cc.blend!.length) : blendWords(cc.blend!.length)) : describeDetection(d);
        // The Viewer leaves out signal-to-noise and µJy: the notes say what matters.
        const vals = opts.viewer ? notes : [detectionNumbers(d), ...notes];
        hits.push({
          X: cc.X,
          Y: cc.Y,
          r: cc.r + 4,
          html:
            `<span class="bk-pos">${formatRaDec(d)}</span> <span class="bk-mtag" data-cls="${d.class}"${d.grade ? ` data-grade="${d.grade}"` : ""}>${esc(tag)}</span> <span class="bk-val">${esc(vals.join("; "))}</span>` +
            (lone ? ` <span class="bk-one">${ONE_GLYPH}<span>${esc(onePhotoSay(op!, pagesLoading()))}</span></span>` : ""),
        });
      }
    }

    // A saturated star: one dashed ring around its whole blob, always, labelled "saturated" (its own
    // detection, from its wings, is drawn as this ring, not as a circle of its own). A very bright
    // star with no detection of its own: the same ring, never nothing and never its fragments.
    if (plan.big.length) {
      const col = pen("--m-star");
      const named = plan.big.length <= 4;
      for (const b of plan.big) {
        bigStar(ctx, b.X, b.Y, b.r, col, halo);
        const wings = [...shiftArrows.keys()].some((i) => m.shifts[i].gaia_id === b.id);
        const s = gaiaOf.get(b.id);
        const text = b.saturated ? (wings ? "saturated: measured on the wings" : "saturated") : "";
        // Lower right of its ring as before, else the first spot clear of other words and arrows.
        if (named && text) words.push({ text, at: { X: b.X, Y: b.Y, r: b.r }, color: col, font: `600 11px ${fontFamily}`, gap: 3, prio: 3 });
        const own = b.det != null ? ds[b.det] : undefined;
        const what = b.saturated
          ? own
            ? `Its core is burnt: SkyShift measures it on its wings${opts.viewer ? "" : ` (${detectionNumbers(own)})`}.`
            : `Its core is burnt, so it has no single detection here${wings ? "; its motion is measured on its wings" : ""}.`
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

  /**
   * Hours: for the dots of moment `e` that no other photo of their detector shows, measure their
   * place in those other photos (onephoto.ts, the rule of core's pixelcheck.one_photo) and say on the
   * circle what it is. Each detector on its own frames (all wavelengths: each member photo against
   * its own detector's), from the frames already loaded; the circles' words follow when it is done.
   */
  function checkOnePhoto(e: Epoch): Promise<void> {
    opJobs.add(e.id);
    const gen = opGen;
    const v = view;
    if (!v || !rawF || v.clock !== "hours") return Promise.resolve();
    // Against every photo of the visit loaded so far (pages.ts), not only those on show: more
    // photos, a surer hint.
    const whole = pg.on && !!pg.merged && pg.key === pagesKey(v);
    const resp = whole ? pg.merged! : rawF;
    const url = whole ? pg.base : rawUrl;
    const sources = waveGroups.size ? waveSources(v, resp, url) : [{ resp, url }];
    const members = waveGroups.get(e.id)?.members ?? [e];
    return (async () => {
      const found = new Map<string, OnePhoto>();
      for (const mem of members) {
        const src = sources.find((x) => x.resp.epochs.some((q) => q.id === mem.id));
        if (!src?.resp.measured?.detections?.length) continue;
        const eps = src.resp.epochs.filter((q) => q.det === mem.det);
        const got = await Promise.allSettled(eps.map(async (q) => ({ q, vals: mjy(q, await cachedFrame(q, src.url)) })));
        const frs: OPFrame[] = got.flatMap((r) =>
          r.status === "fulfilled" ? [{ id: r.value.q.id, det: r.value.q.det, mjd: r.value.q.mjd_mid, vals: r.value.vals, centre: r.value.q.center ?? src.resp.center }] : [],
        );
        const own = frs.find((x) => x.id === mem.id);
        if (!own) continue;
        const ids = new Set(eps.map((q) => q.id));
        const dets = src.resp.measured.detections.filter((d) => ids.has(d.epoch_id));
        // A photo at a time between frames of the blink; given up once the frames change.
        const pause = async () => {
          await new Promise((ok) => setTimeout(ok, 0));
          if (gen !== opGen || destroyed) throw new Error("frames changed");
        };
        for (const [k, val] of await onePhotoChecksAsync(src.resp, frs, own, dets, pause)) found.set(k, val);
      }
      if (gen !== opGen || destroyed) return;
      for (const [k, val] of found) opDone.set(k, val);
      opChecked.add(e.id);
      if (found.size) {
        draw();
        renderCaption();
      }
      renderOnePanel();
    })().catch(() => {
      /* the circles keep their usual words */
    });
  }
  /** Hours, with SkyShift's circles on: every photo's hints, one photo after another in the
   *  background, so the panel counts the whole visit and each "1" is there when its photo shows. */
  let opQueue = -1;
  function checkAllOnePhoto() {
    if (opQueue === opGen || !overlays.measured || view?.clock !== "hours" || !meas) return;
    const gen = (opQueue = opGen);
    void (async () => {
      let done = false;
      try {
        for (const f of [...frames]) {
          if (gen !== opGen || destroyed || !overlays.measured) return;
          if (!opJobs.has(f.epoch.id)) await checkOnePhoto(f.epoch);
        }
        done = true;
      } finally {
        if (!done && opQueue === gen) opQueue = -1;
      }
    })();
  }
  /** The panel's part on dots circled in one photo only: how many of each kind, and what each means. */
  let oneShown = false;
  function renderOnePanel() {
    const counts = meas?.clock === "hours" && overlays.measured ? onePhotoCounts(opDone.values()) : new Map<OnePhotoKind, number>();
    const total = [...counts.values()].reduce((a, b) => a + b, 0);
    if (!!total !== oneShown) {
      // the legend and the plain line name the "1" only while the plate has one
      oneShown = !!total;
      renderMeasured();
      return;
    }
    mp.one.hidden = !total;
    if (!total) return;
    // Photos of the shown detector (All wavelengths: a moment the partner alone has is left out of
    // the count, as from the line under the strip).
    const mine = frames.filter((f) => !view || (waveGroups.get(f.epoch.id)?.members ?? [f.epoch]).some((m) => m.det === view!.det));
    const n = mine.length;
    const checked = mine.filter((f) => opChecked.has(f.epoch.id)).length;
    mp.oneH.textContent =
      `Circled in one photo only: ${total} (a small “1” on the circle)${checked < n ? `, in the ${checked} of ${n} photos checked so far` : ""}. ` +
      "At each one's spot, SkyShift measured the light in the visit's other photos:";
    mp.oneList.innerHTML = KIND_ORDER.filter((k) => counts.get(k))
      .map((k) => `<li data-kind="${k}">${esc(KIND_WORDS[k](counts.get(k)!))}</li>`)
      .join("");
    const loaded = view ? photosOf(pg.on && pg.merged ? pg.merged : rawF, view.det) : n;
    mp.oneNote.textContent =
      `Point at a circle marked “1” for its own numbers. A hint from the ${loaded} photos of this visit loaded in your browser` +
      `${pagesLoading() ? " (more are loading)" : ""}; NASA's own cosmic-ray marks aren't available here.`;
  }

  /**
   * contracts v3.21: a shift's measured position in each visit of this detector, joined in time
   * order, each with its 1σ error circle (measured.ts visitPath): for the list entry in focus, and
   * with Advanced on for every shift. A steady motion reads as beads on a line; a wobble (dashed)
   * goes out and comes back. Magnified about the visits' mean when small, with its factor ("×10").
   */
  function drawVisitPaths(
    m: Measured,
    det: number,
    toS: (p: SkyPos) => { X: number; Y: number },
    vp: VP,
    inView: (p: { X: number; Y: number }, m?: number) => boolean,
    stroke: (col: string, w: number, a?: number) => void,
    space: LabelSpace,
    words: Word[],
  ) {
    if (!ctx || !F) return;
    const inFocus = measItems.find((x) => x.key === (focusItem ?? pinned))?.shift;
    const pxPerArcsec = vp.s / F.pixel_arcsec;
    m.shifts.forEach((s, i) => {
      if (i !== inFocus && !getDetails()) return;
      const grade = sourceGrade.get(s.source_id);
      if (opts.viewer && greyed(s.class, grade)) return;
      // the all-wavelengths plate: the detector with the most visits (positions compare within one only)
      // a shift with an arrow: the arrow's scale, so one motion is drawn at one scale
      const p = visitPath(s, waveGroups.size ? null : det, toS, pxPerArcsec, shiftArrows.get(i)?.factor);
      if (!p || !p.pts.some((q) => inView(q))) return;
      const col = pen(greyed(s.class, grade) ? "--m-artifact" : classInfo(s.class).token);
      ctx.save();
      if (wobbles(s)) ctx.setLineDash([3, 3]);
      ctx.beginPath();
      p.pts.forEach((q, k) => (k ? ctx.lineTo(q.X, q.Y) : ctx.moveTo(q.X, q.Y)));
      stroke(col, 1.4, 0.95);
      ctx.restore();
      p.pts.forEach((q, k) => {
        ring(ctx, q.X, q.Y, p.r[k]);
        pencil(ctx, col, 0.8, 0.4);
        ctx.beginPath();
        ctx.arc(q.X, q.Y, 2.2, 0, Math.PI * 2);
        ctx.save();
        ctx.fillStyle = col;
        ctx.fill();
        ctx.restore();
        // The one in focus numbers its visits, so out and back reads in time order.
        if (i === inFocus)
          words.push({ text: String(k + 1), at: { X: q.X, Y: q.Y, r: 2.2 }, color: col, font: `600 9.5px ${fontFamily}`, gap: 2, prio: 2 });
      });
      space.addPath(p.pts);
      const last = p.pts[p.pts.length - 1];
      if (p.factor > 1)
        words.push({ text: `×${p.factor}`, at: { X: last.X, Y: last.Y, r: p.r[p.r.length - 1] }, color: col, font: `600 10.5px ${fontFamily}`, gap: 3, prio: 1 });
      const what = `${wobbles(s) ? wobbleWords(s) : "Its position in each visit, joined in time order."} D${p.det}, ${p.pts.length} visits.`;
      const scale = p.factor > 1 ? ` Drawn ${p.factor} times larger, about the visits' mean.` : "";
      const html = `<span class="bk-mtag" data-cls="${s.class}">${esc(describeShift(s, grade))}</span> <span class="bk-val">${esc(what + scale)}</span>`;
      for (const q of p.pts) hits.push({ X: q.X, Y: q.Y, r: 6, html });
    });
  }

  /** The zone's box, dashed, when the field reaches past it: outside it nothing is linked. */
  function drawZoneEdge(m: Measured, vp: VP, c: SkyPos, halo: string, space: LabelSpace) {
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
      const text = `${m.zone} zone edge`;
      label(ctx, text, b.X + 6, b.Y - 6, pen("--ink-2"), halo);
      space.addBox({ x: b.X + 4, y: b.Y - 16, w: ctx.measureText(text).width + 4, h: 14 });
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
      // Hours: the photo's own date and time. Months: the visit's weeks, since its plate is one stack
      // of photos taken over them (dated by its middle it read as another day than an hours photo).
      const when = (x: Epoch) => (hours ? plateDate(x.mjd_mid, true) : visitSpan(x.mjd_start, x.mjd_end));
      const photos = memberEpoch(e).n_exposures;
      whenEl.title = hours
        ? "One photo (113 s), dated at its middle, in UTC."
        : `One median stack of ${photos} photo${photos === 1 ? "" : "s"} of this visit, taken over these weeks.`;
      if (allAtOnce()) {
        whenMain.textContent = `${plateDate(frames[0].epoch.mjd_mid)} to ${plateDate(frames[n() - 1].epoch.mjd_mid)}`;
        whenSub.textContent = following() && mover ? `mean of ${n()} ${U}s, following the mover` : `mean of ${n()} ${U}s`;
      } else if (mode === "difference" && !comparing) {
        whenMain.textContent = when(e);
        whenSub.textContent = idx === ref ? `${U} ${idx + 1} is the reference` : `${U} ${idx + 1} minus ${U} ${ref + 1}`;
      } else {
        whenMain.textContent = when(e);
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
      // The Viewer says what its blue circles are, without opening anything.
      if (opts.viewer && overlays.gaia && F?.gaia.length) {
        key.insertAdjacentHTML(
          "afterbegin",
          `<span class="bk-gaia-key" title="Gaia is a European space telescope that has mapped the positions and motions of nearly two billion stars."><i class="bk-gaia-ring" aria-hidden="true"></i>Blue circles: stars Gaia already knows</span>`,
        );
      }
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
    mp.stackNote.hidden = !(allAtOnce() && mp.panel.dataset.state === "measured");
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
              : `Visit ${i + 1}, ${visitSpan(f.epoch.mjd_start, f.epoch.mjd_end)}`
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
    axisEl.setAttribute("aria-valuetext", e ? `${unit()} ${idx + 1} of ${n()}, ${view.clock === "hours" ? plateDate(e.mjd_mid, true) : visitSpan(e.mjd_start, e.mjd_end)}` : "");
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
    const st = measuredHere();
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
    measItems = listItems(meas, epochDet, view.det, F.gaia, !!opts.viewer);
  }

  /** Is there a saturated star to explain: Gaia lists one under G 9, or a photo of this detector has saturated pixels. */
  function saturatedHere(): boolean {
    if (!F || !view) return false;
    return F.gaia.some((g) => g.g != null && g.g < SATURATED_G) || F.epochs.some((x) => x.det === view!.det && x.sat_frac >= 0.001);
  }

  /**
   * The legend. The Viewer's names only what its plate draws: the blue dots of a blend and the edge
   * of a bright star's glow only in Advanced, and only when the plate shows one (`plateKey`, set as
   * the plate draws). Returns the classes listed.
   */
  function renderMeasuredKey(m: Measured): MeasuredClass[] {
    const present = classesPresent(m).filter((c) => !opts.viewer || viewerShows(c, null, getDetails()));
    mp.key.innerHTML =
      present
        .flatMap((c) => {
          const name = (opts.viewer ? VIEWER_NAME[c] : undefined) ?? CLASS_INFO[c].name;
          const li = `<li title="${esc(CLASS_INFO[c].about)}">${glyph(c)}<span>${esc(name)}</span></li>`;
          // Grade C suspects fail a check: greyed on the plate, so the legend says so.
          const hasC = c === "suspect" && m.detections.some((d) => d.class === "suspect" && d.grade === "C");
          return hasC && !opts.viewer
            ? [li, `<li title="A suspect that fails one of our checks (its reasons say which).">${glyph(c, "C")}<span>Suspect, grade C</span></li>`]
            : [li];
        })
        .join("") +
      (saturatedHere()
        ? `<li title="A star too bright for SPHEREx: its burnt core gives no single detection. Only this dashed ring means saturated.">${SATURATED_GLYPH}<span>Saturated star</span></li>`
        : "") +
      (plateKey.dots
        ? `<li title="Where Gaia puts each star of a blend: two or more Gaia stars SPHEREx sees as one.">${BLEND_DOTS_GLYPH}<span>Blue dots: each Gaia star when one circle holds several</span></li>`
        : "") +
      (plateKey.glow
        ? `<li title="Where a bright star's light stays over 3 times the sky's noise. Its glow makes sources of its own: greyed circles inside may not be real.">${GLOW_GLYPH}<span>Dotted circle: a bright star's glow; grey circles inside may not be real</span></li>`
        : "") +
      (oneShown && m.clock === "hours"
        ? `<li title="SkyShift circled it in this photo and in no other photo of the visit. It measured the light at that spot in the other photos: point at the circle to read what they say.">${ONE_GLYPH}<span>A small “1”: circled in this photo only (point at it: what the other photos show there)</span></li>`
        : "");
    return present;
  }

  function renderMeasured() {
    const st = measuredHere();
    mp.panel.hidden = st.kind === "absent";
    mp.stackNote.hidden = true;
    const on = st.kind === "measured";
    for (const e of [mp.rest, mp.key, mp.how, mp.short, mp.items, mp.notes, mp.pro]) e.hidden = !on;
    if (!on) mp.one.hidden = true;

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
    mp.stackNote.hidden = !allAtOnce();
    // The lead counts what the list shows: movers on this detector, and the candidate when it isn't among them.
    const spots = trackFor?.spots ?? [];
    const s = summarize(m, F?.gaia, {
      artefacts: !noArtefacts,
      epochDet,
      det: view?.det,
      candidate: trackFor && {
        label: trackFor.label,
        measured: spots.some((x) => x.measured),
        marked: spots.length > 0,
        gapDays: trackFor.find_gap_days,
      },
    });
    mp.lead.innerHTML = s.lead;
    mp.rest.textContent = s.rest;
    mp.rest.hidden = !s.rest;
    // The Viewer leaves out the pipeline stage ("Final", "Provisional"): it means nothing to visitors.
    mp.stage.textContent = opts.viewer ? "" : stageLabel(m.stage);
    mp.stage.dataset.stage = String(m.stage ?? "");
    mp.stage.hidden = !mp.stage.textContent;
    const present = renderMeasuredKey(m);
    // Months: only a shift with an arrow (what the lead counts) puts arrows on the plate.
    const G = gaiaById(F?.gaia);
    const arrows = m.clock === "hours" ? m.tracks.length > 0 : m.shifts.some((x) => shiftVerdict(x, G).draw);
    mp.how.textContent =
      "Circles are what SkyShift measured in each photo, each as big as its source looks on the plate. " +
      "Blue rings are where Gaia predicts stars, a little bigger, so a match reads as a circle inside a ring; one ring with two dots is two Gaia stars SPHEREx sees as one. " +
      "Violet diamonds are where the Minor Planet Center predicts asteroids." +
      (saturatedHere() ? " A dashed ring is a saturated star: its core is burnt, so its pieces aren't drawn and it is measured, if at all, on its wings." : "") +
      (arrows ? ` ${arrowNote(m.clock)}` : "");
    // Plain view: one line for the marks; the full explanation, breakdown and notes are Advanced. (This
    // line is plain-only, where stack-only sources aren't drawn: no dashed circles to explain.)
    mp.short.textContent = opts.viewer
      ? "Blue circles: stars Gaia already knows, where Gaia says they are on this date. A circle inside one is where " +
        "SkyShift found that star in this photo." +
        (present.includes("other") ? " Grey circles: sources Gaia doesn't list." : "") +
        " Violet diamonds: known asteroids." +
        (arrows ? (m.clock === "hours" ? " Arrows: how something moved between photos." : " Arrows: how far something moved between visits.") : "") +
        (oneShown && m.clock === "hours" ? " A small “1”: circled in this photo only; what the other photos show there is below." : "") +
        " Camera glitches are hidden."
      : "Circles: what we measured. Blue rings: Gaia stars. Violet diamonds: known asteroids." + (arrows ? " Arrows: motion." : "") +
        (oneShown && m.clock === "hours" ? " “1”: circled in this photo only." : "");
    mp.notes.textContent = s.notes.join(" ");
    const c: Partial<Record<MeasuredClass, number>> = m.counts ?? {};
    mp.pro.textContent =
      `Detections per class, before the cap: ${CLASS_ORDER.filter((k) => !noArtefacts || k !== "artifact")
        .map((k) => `${k} ${c[k] ?? 0}`)
        .join(", ")}. ` +
      `${m.photos} photos read, ${m.photos_missing} without dots. Zone box RA ${m.zone_box?.ra?.join("–")}°, Dec ${m.zone_box?.dec?.join("–")}°.`;
    // The Viewer keeps only the summary, the legend, the plain line and the list, even under Advanced
    // (emptied, not just hidden: research mode shows .pro-only with !important). The long breakdown
    // (σ, artefact counts, zone names) stays on the To check plate.
    if (opts.viewer)
      for (const e of [mp.rest, mp.how, mp.notes, mp.pro]) {
        e.textContent = "";
        e.hidden = true;
      }
    renderMeasuredList();
    renderOnePanel();
  }

  function renderMeasuredList() {
    const m = meas;
    if (!m || !view) return;
    const hours = m.clock === "hours";
    mp.items.hidden = !measItems.length;
    if (!measItems.length) return;
    // The list shows what the lead counts; the rest fold under one quiet line.
    const counted = measItems.filter((it) => it.counted);
    const extra = measItems.filter((it) => !it.counted);
    if (mp.count) mp.count.textContent = counted.length ? `(${counted.length})` : "";
    mp.lh.hidden = !counted.length;
    mp.lh.textContent = opts.viewer
      ? hours ? "What moved between photos" : "What moved between visits"
      : hours
        ? `Movers${noArtefacts ? "" : " and artefacts"} on ${waveGroups.size ? "all detectors" : `D${view.det}`}`
        : "What moved between visits";
    const row = (it: MeasuredItem) => html`<li><button class="bk-mi" type="button" data-item="${esc(it.key)}" aria-pressed="${it.key === pinned}"
          >${glyph(it.cls, it.grade)}<span class="bk-mi-t">${esc(it.label)}</span><span class="bk-mi-n pro-only">${esc(it.numbers)}</span></button></li>`;
    const shown = measAll ? counted : counted.slice(0, LIST_SHOWN);
    mp.list.hidden = !counted.length;
    mp.list.innerHTML = shown.map(row).join("");
    const more = counted.length - LIST_SHOWN;
    mp.all.hidden = more <= 0;
    mp.all.setAttribute("aria-expanded", String(measAll));
    mp.all.textContent = measAll ? "Show fewer" : `Show all ${counted.length}`;
    // A pinned entry among the folded ones keeps them open.
    const open = measMore || extra.some((it) => it.key === pinned);
    mp.more.hidden = !extra.length;
    mp.more.setAttribute("aria-expanded", String(open));
    mp.more.textContent = moreLine(measItems, m.clock);
    mp.extra.hidden = !open || !extra.length;
    mp.extra.innerHTML = open ? extra.map(row).join("") : "";
  }

  /** A list entry was clicked: pin its ring, and on the hours clock show a photo it is in. */
  function pickItem(key: string) {
    const it = measItems.find((x) => x.key === key);
    if (!it) return;
    pinned = pinned === key ? null : key;
    mp.items.querySelectorAll<HTMLButtonElement>(".bk-mi").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.item === pinned)));
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
    trackFor = marks;
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
    const have = new Set(detectorsIn(resp));
    const want =
      v.clock === "months" ? DETS.filter((d) => !have.has(d)) : [...new Set([...have].map((d) => SAME_MOMENT[d] as Det))].filter((d) => !have.has(d));
    if (resp.epochs.some((e) => !!e.center) || !have.size || !want.length) {
      liveDets.pending = false;
      return;
    }
    // Hours: the visit the plate shows (an object's find, a saved file's own photos), so the partner's
    // photos are of the same moments; a plain live answer asks as before.
    const src = v.clock === "hours" ? visitSource(resp, rawUrl, v.fov, v.det, pg.on ? mainMjd() : null) : null;
    const at = src ? Number(new URL(src.url0).searchParams.get("mjd")) : NaN;
    void Promise.allSettled(
      want.map(async (d) => {
        const r = await loadFrames({ ra: resp.center.ra, dec: resp.center.dec, clock: v.clock, fov: v.fov, det: d, slug: null, ...(at > 0 ? { mjd: at } : {}) });
        if (!destroyed && liveDets.key === key && r.frames.clock === v.clock) liveDets.got.set(d, { resp: r.frames, url: r.jsonUrl });
      }),
    ).then(async () => {
      if (destroyed || liveDets.key !== key) return;
      liveDets.pending = false;
      // The visit's pages waited for the partner: now both go on together.
      if (pg.on && view && waveKey(view) === key) pump(pg.gen);
      // Joined without stopping the blink when the plate already shows this field.
      if (allWave && view && rawF && waveKey(view) === key && liveDets.got.size) void load(view, rawF, rawUrl, frames.length ? "force" : false);
    });
  }

  // ---- the whole visit (hours: contracts/api.md v3.23 `page`) ---------------------------------
  const pagesKey = (v: View) => `${waveKey(v)},${v.det}`;
  /** Where a stream's pages are joined: the plate's own answer, or (part) a detector fetched live beside it. */
  const holderOf = (s: Stream): FramesResponse | null => (s.part ? (liveDets.got.get(s.det)?.resp ?? null) : pg.merged);
  const haveOf = (s: Stream) => photosOf(holderOf(s), s.det);
  const mainOf = () => (view ? pg.streams.find((s) => !s.part && s.det === view!.det) : undefined);
  /** Photos of the shown detector joined so far: the whole visit's, not only those on show. */
  const pagePhotos = () => (view ? photosOf(pg.merged, view.det) : 0);
  /** More photos of the visit are on their way to the plate (the one-photo hints may still change):
   *  the shown detector's pages, or photos joined but not in the blink yet. The detector beside it
   *  checking its own pages doesn't count once every photo of this one is on the plate. */
  const pagesLoading = () => {
    const main = pg.on ? mainOf() : undefined;
    return !!main && (!streamDone(main, haveOf(main)) || pg.shown < pagePhotos());
  };
  /** What of the visit the plate shows (show()'s `filter`: an object to check shows the day of its find). */
  const shownOf = (f: FramesResponse) => (pg.filter ? pg.filter(f) : f);
  function renderPages() {
    const main = view && pg.on && view.clock === "hours" && pg.key === pagesKey(view) ? mainOf() : undefined;
    pagesEl.hidden = !main;
    if (!main) return;
    pagesN.textContent = pagesText(pg.shown, main.total, pagesLoading(), main.failed) + (pg.switched ? `. ${pg.switched}` : "");
  }
  /** A new plate: forget its visit (an older page is dropped when it lands). */
  function pagesReset() {
    pg.gen++;
    Object.assign(pg, { on: false, key: "", src: "", base: "", merged: null, filter: null, streams: [], switched: "", dirty: false, shown: 0 });
    renderPages();
  }
  /** Load the rest of the visit behind the plate just shown (resp from url). `mjd`: a time inside
   *  the visit the live API should show (an object's find), else one of the plate's own photos. */
  function pagesStart(v: View, resp: FramesResponse, url: string, mjd: number | null): void {
    const src = visitSource(resp, url, v.fov, v.det, mjd);
    if (!src) return;
    pg.gen++;
    const main: Stream = { ...newStream(v.det, src), part: false, first: src.start === 1 ? idsOf(resp, v.det) : "", joined: new Set() };
    Object.assign(pg, { on: true, key: pagesKey(v), src: url, base: url, merged: resp, streams: [main], switched: "", dirty: false, shown: photosOf(resp, v.det) });
    renderPages();
    pump(pg.gen);
  }
  /** The same plate shown again (a layer, a panel, "Show all"): what is loaded stays, and loading goes on. */
  function pagesRestart() {
    pg.gen++;
    for (const s of pg.streams) s.asking = new Set();
    renderPages();
    pump(pg.gen);
  }
  const idsOf = (r: FramesResponse, det: Det) => r.epochs.filter((e) => e.det === det).map((e) => e.id).sort().join(",");
  /** The time the main stream asks the live API at (its URL's `mjd`), so a partner shows the same visit. */
  function mainMjd(): number | null {
    const m = mainOf();
    const t = m ? Number(new URL(m.url0).searchParams.get("mjd")) : NaN;
    return t > 0 ? t : null;
  }
  /** All wavelengths on the hours clock: the detector exposed at the same moment gets the same pages,
   *  from the plate's own answer (a static file of both) or from its live answer beside the plate. */
  function ensurePartner() {
    if (!view || !pg.on || !allWave || view.clock !== "hours") return;
    const d = SAME_MOMENT[view.det] as Det;
    if (pg.streams.some((s) => s.det === d) || !pg.merged) return;
    const inMain = pg.merged.epochs.some((e) => e.det === d);
    const part = !inMain && liveDets.key === waveKey(view) ? liveDets.got.get(d) : undefined;
    if (!inMain && !(part && sameGrid(part.resp, pg.merged))) return;
    const src = inMain ? visitSource(pg.merged, pg.base, view.fov, d, mainMjd()) : visitSource(part!.resp, part!.url, view.fov, d, mainMjd());
    if (src) pg.streams.push({ ...newStream(d, src), part: !inMain, first: !inMain && src.start === 1 ? idsOf(part!.resp, d) : "", joined: new Set() });
  }
  /** Ask the next pages, a few page numbers at a time; every stream asks the same numbers together. */
  function pump(gen: number) {
    if (gen !== pg.gen || !pg.on || !view || destroyed) return;
    // All wavelengths: the detector beside the plate is on its way (fetchOtherDets); its pages and
    // these go together once it is here, so no moment shows one detector alone.
    if (allWave && liveDets.key === waveKey(view) && liveDets.pending) return renderPages();
    ensurePartner();
    const cap = pg.streams.length > 1 ? PAGES_AT_ONCE_PAIRED : PAGES_AT_ONCE;
    for (;;) {
      const asking = new Set(pg.streams.flatMap((s) => [...s.asking]));
      if (asking.size >= cap) break;
      const want = pg.streams.map((s) => nextPage(s, haveOf(s)));
      const k = want.reduce<number | null>((lo, n) => (n != null && (lo == null || n < lo) ? n : lo), null);
      if (k == null) break;
      pg.streams.forEach((s, i) => {
        if (want[i] === k) void askPage(s, k, gen);
      });
    }
    renderPages();
  }
  /** A page answer and whether it is partial (cutouts that came too late: X-SkyShift-Partial). */
  async function getPage(url: string): Promise<{ page: FramesResponse; partial: boolean }> {
    const res = await fetch(url);
    if (!res.ok) {
      let code = "internal";
      try {
        code = (await res.json())?.error?.code ?? code;
      } catch {
        /* not JSON */
      }
      throw new ApiFailure(`${res.status} for ${url}`, res.status, code);
    }
    const page = (await res.json()) as FramesResponse;
    if ((page as { synthetic?: unknown }).synthetic === true) throw new ApiFailure("synthetic", 502, "upstream");
    return { page, partial: !!res.headers.get("X-SkyShift-Partial") };
  }
  const later = () => new Promise((ok) => setTimeout(ok, PAGE_RETRY_MS));
  /** Ask page k of stream s; a failed or partial answer is asked once more, a few seconds later. */
  async function askPage(s: Stream, k: number, gen: number) {
    s.asking.add(k);
    const url = pageUrl(s.url0, k);
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        let got: { page: FramesResponse; partial: boolean };
        try {
          got = await getPage(url);
        } catch (e) {
          if (gen !== pg.gen) return;
          if (e instanceof ApiFailure && (e.status === 404 || e.status === 400)) {
            // Past the visit's last page (or nothing the API can page here): nothing more to ask.
            s.last = Math.min(s.last ?? k - 1, k - 1);
            s.got.add(k);
            return;
          }
          if (attempt === 0) {
            await later();
            if (gen !== pg.gen) return;
            continue;
          }
          s.failed++;
          s.got.add(k);
          return;
        }
        if (gen !== pg.gen) return;
        const { page, partial } = got;
        if (page.visit_exposures != null) s.total = page.visit_exposures;
        if (page.more !== true) s.last = Math.min(s.last ?? k, k);
        // An API without pages answers page 0 again: there is nothing more to ask.
        const ids = idsOf(page, s.det);
        if (k === 0) s.first = ids;
        else if (ids && ids === s.first) {
          s.last = Math.min(s.last ?? k - 1, k - 1);
          s.got.add(k);
          return;
        }
        join(s, page, url);
        s.joined.add(k);
        if (pg.dirty && !pairPending()) schedule();
        if (partial && attempt === 0) {
          await later();
          if (gen !== pg.gen) return;
          continue;
        }
        s.got.add(k);
        return;
      }
    } finally {
      if (gen === pg.gen) {
        s.asking.delete(k);
        if (pg.dirty && !pairPending()) schedule();
        pump(gen);
      }
    }
  }
  /** A page number one detector has joined while the other's same page is still on its way: its
   *  moments would show one detector alone (a change of colour that blinks like motion). */
  function pairPending(): boolean {
    return pg.streams.length > 1 && pg.streams.some((s) => [...s.asking].some((k) => !s.joined.has(k) && pg.streams.some((o) => o !== s && o.joined.has(k))));
  }
  function join(s: Stream, page: FramesResponse, url: string) {
    if (s.part) {
      const p = liveDets.got.get(s.det);
      const j = p ? joinPage(p.resp, page, url) : null;
      if (p && j?.added) {
        p.resp = j.resp;
        pg.dirty = true;
      }
      return;
    }
    if (!pg.merged) return;
    const j = joinPage(pg.merged, page, url);
    if (j) {
      if (j.added) {
        pg.merged = j.resp;
        pg.dirty = true;
      }
      return;
    }
    // The live photos come on another grid than the saved ones: they can't be blinked together. The
    // plate goes live for this visit (the whole visit is there, the saved photos among them), keeping
    // its photo and playing on; the detector beside it starts again on the live grid.
    if (s.start !== 0 || pg.switched || !view || s.det !== view.det) return;
    pg.switched = `Showing SkyShift's live photos: ${gridWhy(pg.merged, page)}`;
    pg.merged = absolute(page, url);
    pg.base = url;
    for (const o of pg.streams)
      if (o !== s && !o.part) {
        o.got = new Set();
        o.joined = new Set();
        o.first = "";
      }
    pg.dirty = true;
  }
  /** Put what the pages brought on the plate: one quiet load at a time, the blink playing on. */
  let applyGen = -1;
  let applyAgain = false;
  function schedule() {
    const gen = pg.gen;
    if (applyGen === gen) {
      applyAgain = true;
      return;
    }
    applyGen = gen;
    void (async () => {
      try {
        do {
          applyAgain = false;
          await pendingLoad;
          if (gen !== pg.gen || !view || !pg.merged || pagesKey(view) !== pg.key) return;
          if (pairPending()) return; // the page's partner lands soon and asks again
          pg.dirty = false;
          const shown = shownOf(pg.merged);
          const photos = photosOf(pg.merged, view.det);
          rawF = shown; // a load started meanwhile (another detector arriving, All switched) takes it too
          opts.onFrames?.(pg.merged);
          await load(view, shown, pg.base, "force");
          if (gen === pg.gen) pg.shown = photos;
          renderPages();
        } while (applyAgain && gen === pg.gen);
      } finally {
        if (applyGen === gen) applyGen = -1;
      }
    })();
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

  /** Show resp. quiet (more photos of the same view, pages.ts): the blink keeps playing and keeps its
   *  place while the new frames download, then takes them all, in time order, at once, when there are
   *  more of them ("force": always, e.g. another detector joined the same moments). */
  function load(next: View, resp: FramesResponse, url: string, quiet: boolean | "force" = false): Promise<void> {
    const p = loadNow(next, resp, url, !!quiet, quiet === "force");
    if (!quiet) pendingLoad = p.catch(() => {});
    return p;
  }
  async function loadNow(next: View, resp: FramesResponse, url: string, quiet: boolean, force = false): Promise<void> {
    if (destroyed) return;
    const gen = ++loadCtl;
    const wasPlaying = playing || resumeOnVisible || firstShow;
    if (!quiet) setPlaying(false);
    const prev = view;
    let prevMjd = cur()?.epoch.mjd_mid;
    let prevRefMjd = frames[ref]?.epoch.mjd_mid;
    const sameClock = prev?.clock === next.clock;
    const moved = !prev || Math.abs(prev.ra - next.ra) > 1e-6 || Math.abs(prev.dec - next.dec) > 1e-6;
    if (moved && !quiet) {
      pick = null;
      follow = false;
    }
    const wave = allWave ? waveOf(next, resp, url) : null;
    const groups = new Map((wave?.groups ?? []).map((g) => [g.epoch.id, g]));
    const loadedCounts = new Map<string, number>();
    const loadedMembers = new Map<string, { epoch: Epoch; url: string }[]>();
    /** The plate takes the response: at once, or (quiet) when its frames are in. */
    const take = () => {
      view = next;
      rawF = resp;
      rawUrl = url;
      F = wave?.F ?? resp;
      waveGroups = groups;
      waveLoaded = loadedCounts;
      waveMembers = loadedMembers;
      jsonUrl = url;
      opGen++;
      if (!quiet) {
        opDone = new Map();
        opChecked = new Set();
      }
      opJobs = new Set();
      prepareMeasured();
    };
    const eps = wave ? wave.groups.map((g) => g.epoch) : epochsFor(resp, next.det);
    if (quiet && !eps.length) return;
    if (!quiet) {
      take();
      if (allWave) fetchOtherDets(next, resp);
      appStatus = null;
      comparing = false;
      root.dataset.ready = "false";
      renderRow();
      if (moved || prev?.clock !== next.clock || prev?.det !== next.det) {
        pinned = null;
        measAll = false;
        measMore = false;
      }
      focusItem = null;
      renderMeasured();
    }
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
    if (!quiet) {
      busy = { done: 0, total: eps.length };
      renderMsg();
      renderControls();
    }
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
        const g = groups.get(e.id);
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
    let missed = 0;
    results.forEach((r, i) => {
      if (r instanceof Error || r.length !== nx * ny) missed++;
      else loaded.push({ epoch: eps[i], vals: r, sky: median(r), sigma: robustSigma([r], 40_000) });
    });
    const shownId = cur()?.epoch.id;
    if (quiet) {
      // Nothing more to show changes nothing; else the blink goes on from the frame on show now.
      if (!loaded.length || (!force && loaded.length <= frames.length)) return;
      prevMjd = cur()?.epoch.mjd_mid;
      prevRefMjd = frames[ref]?.epoch.mjd_mid;
      take();
      renderRow();
      renderMeasured();
    } else fillOnly = [];
    failed = missed;
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
      if (!quiet) {
        emitEpoch();
        const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        if (pageActive && wasPlaying && !reduce && !allAtOnce()) setPlaying(true);
      } else if (cur()?.epoch.id !== shownId) emitEpoch(); // the same moment, renumbered (all wavelengths)
      firstShow = false;
    } else {
      renderAll();
      draw();
    }
    root.dataset.ready = "true";
    if (fillSrc.length && next.clock === "hours") void loadFill(gen, nx, ny);
  }

  /** Most fill exposures loaded beside the plate's own, spread over the visit (other pointings). */
  const FILL_EXTRA = 8;
  /** Load a spread of the visit's other exposures, used only to fill the plate's edge, then repaint. */
  async function loadFill(gen: number, nx: number, ny: number): Promise<void> {
    const seen = new Set(frames.map((f) => f.epoch.id));
    const rest = fillSrc
      .filter((s) => s.epoch.det === view?.det && !seen.has(s.epoch.id) && (seen.add(s.epoch.id), true))
      .sort((a, b) => a.epoch.mjd_mid - b.epoch.mjd_mid);
    const n = Math.min(FILL_EXTRA, rest.length);
    if (!n) return;
    const pick = n === rest.length ? rest : Array.from({ length: n }, (_, k) => rest[Math.round((k * (rest.length - 1)) / Math.max(1, n - 1))]);
    const got = await pool(pick, 4, async (s) => toMJySr(await cachedFrame(s.epoch, s.url), s.epoch), () => {}, () => !destroyed && gen === loadCtl);
    if (destroyed || gen !== loadCtl) return;
    const extra: typeof fillOnly = [];
    // All wavelengths: the plate is in units of each detector's noise (combineFrames); so is the fill.
    const inNoise = waveGroups.size > 0;
    got.forEach((r, i) => {
      if (!r || r instanceof Error || r.length !== nx * ny) return;
      const sky = median(r);
      const sigma = robustSigma([r], 40_000);
      const b = burntCores(r, nx, ny, sky, sigma);
      const vals = inNoise ? combineFrames([{ vals: r, burnt: b, sky, sigma }]) : r;
      extra.push({ epoch: pick[i].epoch, vals, sky: inNoise ? 0 : sky, sigma: inNoise ? 1 : sigma, burnt: b });
    });
    if (!extra.length) return;
    fillOnly = extra;
    filledFrames.clear();
    if (frames.length) paintImage();
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
    } else if (b === mp.more) {
      measMore = mp.more.getAttribute("aria-expanded") !== "true";
      if (!measMore && pinned && measItems.some((it) => it.key === pinned && !it.counted)) pinned = null;
      renderMeasuredList();
      draw();
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
  // On the container, so the folded entries (mp.extra) behave like the rest.
  mp.items.addEventListener("pointerover", (ev) => focusOn(itemOf(ev.target)?.dataset.item ?? null));
  mp.items.addEventListener("pointerleave", () => focusOn(document.activeElement && itemOf(document.activeElement) ? itemOf(document.activeElement)!.dataset.item ?? null : null));
  mp.items.addEventListener("focusin", (ev) => focusOn(itemOf(ev.target)?.dataset.item ?? null));
  mp.items.addEventListener("focusout", (ev) => {
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
  // The Viewer draws stack-only sources (and names them in its legend) only with Advanced on.
  const offDetails = opts.viewer
    ? onDetails(() => {
        renderMeasured();
        draw();
      })
    : () => {};

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
      fillSrc = [];
      // Hours (contracts/api.md v3.23 `page`): what the plate was handed at once, the rest of its
      // visit behind it.
      const paged = !!options?.pages && next.clock === "hours";
      const filter = options?.filter ?? null;
      if (paged && pg.on && pg.key === pagesKey(next) && pg.src === url && pg.merged) {
        // The same plate again (a layer, a panel, an object's "Show all"): the visit loaded so far stays.
        pg.filter = filter;
        if (pg.merged !== resp) opts.onFrames?.(pg.merged);
        const shown = shownOf(pg.merged);
        await load(next, shown, pg.base);
        if (!destroyed && rawF === shown) pagesRestart();
        return;
      }
      pagesReset();
      const gen = pg.gen;
      pg.filter = filter;
      const shown = shownOf(resp);
      await load(next, shown, url);
      if (paged && gen === pg.gen && !destroyed && rawF === shown) pagesStart(next, resp, url, options?.visitMjd ?? null);
    },
    needsFill() {
      // A no-data part (past the detector's edge, not a burnt core) on more than 0.5% of the photo,
      // after what its other photos and the fill exposures already fill.
      const f = frames[idx];
      if (!f || view?.clock !== "hours") return false;
      const fl = fillsHoles() ? filledOf(idx) : null;
      const vals = fl?.vals ?? f.vals;
      const b = fl?.burnt ?? burntOf(idx);
      let n = 0;
      for (let i = 0; i < vals.length; i++) if (Number.isNaN(vals[i]) && !b[i]) n++;
      return n > vals.length * 0.005;
    },
    async fill(sources) {
      fillSrc = sources;
      if (!view || !rawF || view.clock !== "hours" || !frames.length) return;
      const [nx, ny] = rawF.size;
      await loadFill(loadCtl, nx, ny);
    },
    singleDetector(locked) {
      if (setSingleDetector(locked) && view && rawF) void load(view, rawF, rawUrl);
      else renderRow();
    },
    preferAllWave(on) {
      const before = preferredAllWave;
      preferredAllWave = on;
      return before;
    },
    status(kind, message) {
      appStatus =
        kind === "idle" ? null : { kind, message: message ?? (kind === "loading" ? "Loading frames…" : "Something went wrong.") };
      // A plate elsewhere is coming (or failed): pages of this one are dropped when they land.
      if (kind !== "idle") pg.gen++;
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
      pg.gen++;
      liveDets = { key: "", got: new Map(), pending: false };
      clearTimeout(timer);
      ro.disconnect();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("keyup", onKeyUp);
      document.removeEventListener("pointerdown", onDocDown);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      offDetails();
      el.innerHTML = "";
    },
  };
};
