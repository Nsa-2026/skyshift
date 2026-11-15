// Filters on the main image (layers A1, A2 and A3). An "Add filters" button above the plate opens a
// menu; the chosen filter replaces the picture on the plate, same field and orientation (north up,
// east left), and a key under the plate says what each colour means. Nothing downloads until a
// filter is chosen. Algorithms and their checks: rgb.ts, engine.ts, docs/colour-layers.md.
//
// The blink's own DOM (P4) is not edited: this module adds its button, layer and key around the
// plate from outside, and the plate shows through again when the filter is removed.
import "./filters.css";
import type { FramesResponse, SkyPos } from "../api/types";
import { formatMjd, formatRaDec, gaiaAt, pixelToSky, skyToPixel } from "../app/astro";
import type { View } from "../app/modules";
import { targetBySlug } from "../app/state";
import { CUBE_UM } from "../sky/layers";
import { blinkFieldArcmin } from "../sky/overlay";
import { FEATURES, PRESETS, frameText, type Preset, type PresetId } from "./bands";
import { BLANK_FRAMES, type Fetcher, type TileInfo } from "./cube";
import {
  GROUPS,
  TARGET_MATCH_PX,
  applyContext,
  bandRows,
  compositionSvg,
  describeBlock,
  GLOW_FILL,
  glowAt,
  glowRange,
  lookalikes,
  mjy,
  nearestSource,
  stateWord,
  summarise,
  tally,
  BANDS,
  type Marked,
  type Window as PixelWindow,
} from "./compview";
import { FieldReader, type ColourPicture, type CompositionPicture, type WavelengthPicture } from "./engine";
import { KEYS, KEY_SETS, keySet, swatchColour } from "./legend";
import { loadVisits, visitOf, type VisitSet } from "./visit";
import { compose, greyImage, type Channel } from "./rgb";
import { SATURATED_READOUT, saturationSnapshotText, whyUnreliable, type TargetCheck } from "./why";

const html = String.raw;
type Mode = "none" | "diff" | PresetId | "wave" | "comp";
const POINT_HINT = "Point at the picture to see how much light is there in each colour.";
const COMP_HINT = "Point at any star to read it. Click it for the numbers.";
/** Key swatches drawn as on the picture: a dashed ring (hint), a thin ring (nothing found), a cross (can't tell). */
const swatch = (inner: string) =>
  `<svg class="sw sw-svg" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="7" fill="#10141b" />${inner}</svg>`;
const DASHED_SWATCH = swatch(`<circle cx="7" cy="7" r="4.2" fill="none" stroke="#e6e9ee" stroke-width="1.6" stroke-dasharray="3.3 3.3" />`);
const THIN_SWATCH = swatch(`<circle cx="7" cy="7" r="4.2" fill="none" stroke="#cfd6e0" stroke-width="1" />`);
const CROSS_SWATCH = swatch(`<path d="M4.5,4.5l5,5M9.5,4.5l-5,5" fill="none" stroke="#cfd6e0" stroke-width="1.3" />`);
/** Default wavelength: the 3.3 µm methane band, where the coldest brown dwarfs go dark. */
const DEFAULT_FRAME = 62;

export interface FiltersHandle {
  goto(view: View, frames: FramesResponse | null): void;
  /** The blink moved to another epoch: colour pictures follow it (one per visit). */
  epoch(e: { id: string; mjd_mid: number }): void;
  destroy(): void;
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]!);
}

/** Plain words for a signal-to-noise ratio. */
export function level(snr: number): string {
  if (!Number.isFinite(snr)) return "no data";
  if (snr < 3) return "none";
  if (snr < 10) return "faint";
  if (snr < 100) return "clear";
  return "bright";
}

/** Step over the blank dichroic frames in the direction of travel. */
export function skipBlank(next: number, prev: number): number {
  if (!BLANK_FRAMES.has(next)) return next;
  return next > prev ? Math.max(...BLANK_FRAMES) + 1 : Math.min(...BLANK_FRAMES) - 1;
}

/**
 * Share of the usual exposures kept in the 3 × 3 pixels at (mid, mid): per pixel and frame, NHITS
 * over the median of the 7 × 7 box around it (capped at 1), averaged.
 */
export function keptShare(hits: Int32Array[], n: number, mid: number): number {
  let sum = 0;
  let count = 0;
  const box: number[] = [];
  for (const h of hits) {
    for (let dj = -1; dj <= 1; dj++) {
      for (let di = -1; di <= 1; di++) {
        const j = mid + dj;
        const i = mid + di;
        box.length = 0;
        for (let y = Math.max(0, j - 3); y <= Math.min(n - 1, j + 3); y++) {
          for (let x = Math.max(0, i - 3); x <= Math.min(n - 1, i + 3); x++) box.push(h[y * n + x]);
        }
        box.sort((a, b) => a - b);
        const m = box[box.length >> 1];
        if (m > 0) {
          sum += Math.min(1, h[j * n + i] / m);
          count++;
        }
      }
    }
  }
  return count ? sum / count : 1;
}

/**
 * Pixels in the 3 × 3 core with no data while at least 12 of the 16 pixels around them have data:
 * a hole where the map left overfilled readings out.
 */
export function coreHoles(values: Float32Array, n: number, mid: number): number {
  let ring = 0;
  let holes = 0;
  for (let dj = -2; dj <= 2; dj++) {
    for (let di = -2; di <= 2; di++) {
      const v = values[(mid + dj) * n + mid + di];
      const inCore = Math.abs(dj) <= 1 && Math.abs(di) <= 1;
      if (inCore && !Number.isFinite(v)) holes++;
      if (!inCore && Number.isFinite(v)) ring++;
    }
  }
  return ring >= 12 ? holes : 0;
}

/**
 * Do the short and long windows peak in different pixels of the 3 × 3 core? Each peak must be ≥ 5σ
 * in its own window, and at the other window's peak the short light must be at most half its own
 * peak, or the long light at most half its own: one object puts both peaks in the same pixel.
 */
export function blendPeaks(short: Channel, long: Channel, n: number, mid: number): boolean {
  const core: number[] = [];
  for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) core.push((mid + dj) * n + mid + di);
  const peak = (c: Channel) => core.reduce((a, k) => (c.values[k] > c.values[a] ? k : a), core[0]);
  const ps = peak(short);
  const pl = peak(long);
  if (ps === pl) return false;
  const s0 = short.values[ps];
  const l0 = long.values[pl];
  if (!(s0 >= 5 * short.stats.sigma && l0 >= 5 * long.stats.sigma)) return false;
  return short.values[pl] <= 0.5 * s0 || long.values[ps] <= 0.5 * l0;
}

/**
 * Field of a filter picture, arcmin. The map's pixels are 12.9″, so the blink's 5′ field would be
 * only 23 pixels across and look like a huge zoom. Show three times the blink's field, at least 15′
 * (70 pixels, stars as dots) and at most 60′; the blink's own field is drawn as a dashed square.
 */
export function filterField(plateArcmin: number): number {
  return Math.min(60, Math.max(15, 3 * plateArcmin));
}

/**
 * Field the composition filter measures and shows, arcmin: always 30′. Wide enough to hold the
 * ordinary stars that set each band's zero point (at least 8 are needed; 30′ fields had 10–170),
 * and no wider, because the download grows with the area (30′ is 7 MB, about half a minute).
 * A plate wider than this gets its middle 30′.
 */
export const COMPOSITION_FIELD = 30;

/** The cube frame whose wavelength is closest to `um`. */
function nearestFrame(um: number): number {
  let best = 0;
  CUBE_UM.forEach((v, i) => {
    if (Math.abs(v - um) < Math.abs(CUBE_UM[best] - um)) best = i;
  });
  return best;
}

export function mountFilters(blinkEl: HTMLElement, fetcher: Fetcher = (u, i) => fetch(u, i)): FiltersHandle {
  const col = blinkEl.querySelector<HTMLElement>(".bk-col");
  const mat = blinkEl.querySelector<HTMLElement>(".bk-mat");
  const plate = blinkEl.querySelector<HTMLElement>(".bk-plate");
  const root = blinkEl.querySelector<HTMLElement>(".bk");
  if (!col || !mat || !plate || !root) return { goto() {}, epoch() {}, destroy() {} };

  // ---- the button and menu, just above the plate ------------------------------------------
  const bar = document.createElement("div");
  bar.className = "flt-bar";
  bar.innerHTML = html`
    <button class="btn flt-add" type="button" aria-expanded="false" aria-controls="flt-menu">
      <span class="flt-add-icon" aria-hidden="true">＋</span><span class="flt-add-text">Add filters</span>
    </button>
    <span class="flt-active" hidden>
      <span class="flt-active-name" data-active></span>
      <button class="flt-off" type="button">Remove filter</button>
    </span>
    <div class="flt-menu" id="flt-menu" role="group" aria-label="Filters" hidden>
      <p class="flt-h">Show the picture as</p>
      <div class="flt-opts">
        <button type="button" data-filter="none" aria-pressed="true"><b>No filter</b>
          <span>The normal view, where you watch things move.</span></button>
        <button type="button" data-filter="diff" aria-pressed="false"><b>Difference</b>
          <span>Each date minus the first one (or the one picked under Adjust). Stars that stay put vanish; red is light that arrived, blue is
            light that left, so something that moved shows as a blue and red pair. Changes too small to tell
            from noise stay blank.</span></button>
        <button type="button" data-filter="cold" aria-pressed="false"><b>Temperature</b>
          <span>Colours show how hot or cold things are. Ordinary stars look blue; very cold objects, like
            brown dwarfs, look pink or red. Changes with the date, like the normal view.</span></button>
        <button type="button" data-filter="nir" aria-pressed="false"><b>Star colours</b>
          <span>Tells kinds of stars apart, from hot blue stars to cool orange ones, and shows stars
            reddened by dust. Changes with the date, like the normal view.</span></button>
        <button type="button" data-filter="wave" aria-pressed="false"><b>Single colour</b>
          <span>See the sky in just one of SPHEREx's 102 colours of infrared light. Slide to find where gases
            like water or methane block the light.</span></button>
        <button type="button" data-filter="comp" aria-pressed="false"><b>Molecules</b>
          <span>A ring marks each star whose light shows methane, ices, water vapour or carbon monoxide. A
            green shade marks sky where dust glows. Takes about half a minute.</span></button>
      </div>
      <div class="flt-wave" hidden>
        <input class="flt-frame" type="range" min="0" max="${CUBE_UM.length - 1}" step="1" value="${DEFAULT_FRAME}"
          aria-label="Colour of light: slide through SPHEREx's 102 colours" />
        <div class="flt-ticks" aria-hidden="true">
          ${FEATURES.map(
            (f) => html`<span class="flt-tick" style="left:${(100 * nearestFrame(f.um)) / (CUBE_UM.length - 1)}%"
              title="${f.um} micrometres, ${f.name}: ${f.what}"></span>`,
          ).join("")}
          <span class="flt-tick flt-tick-gap" style="left:${(100 * 50.5) / (CUBE_UM.length - 1)}%"
            title="2.40–2.45 micrometres: no data"></span>
        </div>
        <p class="flt-wave-text" aria-live="polite"></p>
      </div>
      <div class="flt-row">
        <button class="flt-switch" type="button" data-sat aria-pressed="true">Mark stars too bright to measure</button>
      </div>
      <p class="flt-src">These pictures come from NASA's SPHEREx map of the whole sky. It is blurrier than
        the normal view and combines about a year of photos into one, so it shows a wider area around the
        target: the dashed square is the normal view's area.</p>
    </div>
  `;
  col.insertBefore(bar, mat);

  // ---- the picture, on the plate ----------------------------------------------------------
  const layer = document.createElement("div");
  layer.className = "flt-layer";
  layer.hidden = true;
  layer.innerHTML = html`
    <canvas class="flt-canvas" aria-label="Filtered picture of the plate's field"></canvas>
    <svg class="flt-overlay" aria-hidden="true"></svg>
    <span class="flt-compass" aria-hidden="true">N ↑ E ←</span>
    <div class="flt-note" role="status" hidden></div>
  `;
  plate.appendChild(layer);

  // ---- picture sliders, right under the plate: Brightness (filter pictures) and Contrast ----
  // Contrast is the blink's own slider (P4), moved here with its listener: it sets the plate's
  // stretch in Blink and Stack and the colour range in Difference. Only the one that changes what
  // is on screen is shown: Brightness over a filter picture, Contrast otherwise.
  const tools = document.createElement("div");
  tools.className = "flt-tools";
  tools.innerHTML = html`
    <p class="flt-still" role="status" hidden><span data-still-text></span>
      <button class="btn btn-ghost flt-still-go" type="button" data-still-go></button></p>
    <label class="flt-range" data-brightness><span>Brightness</span>
      <input type="range" min="-1" max="1.3" step="0.05" value="0" data-knee
        aria-label="Brightness: show fainter things more or less" /></label>
    <label class="flt-range" data-contrast><span>Contrast</span></label>
  `;
  mat.insertAdjacentElement("afterend", tools);

  // The blink's Difference button moves into the menu: hide it there and drive the blink by it.
  const blinkMode = (m: string) => root.querySelector<HTMLButtonElement>(`.bk-modes [data-mode="${m}"]`);
  const diffBtn = blinkMode("difference");
  const contrastIn = root.querySelector<HTMLInputElement>(".bk-contrast");
  const contrastHome = contrastIn?.closest<HTMLElement>(".bk-adj-row") ?? null;
  const contrastLabel = tools.querySelector<HTMLLabelElement>("[data-contrast]")!;
  const brightnessLabel = tools.querySelector<HTMLLabelElement>("[data-brightness]")!;
  const stillEl = tools.querySelector<HTMLParagraphElement>(".flt-still")!;
  const stillText = stillEl.querySelector<HTMLSpanElement>("[data-still-text]")!;
  const stillGo = stillEl.querySelector<HTMLButtonElement>("[data-still-go]")!;
  if (diffBtn) diffBtn.hidden = true;
  if (contrastIn) {
    contrastIn.setAttribute("aria-label", "Contrast: how strongly the plate shows faint light and small changes");
    contrastLabel.append(contrastIn);
    if (contrastHome) contrastHome.hidden = true;
  } else contrastLabel.hidden = true;

  // ---- the key, under the slider ----------------------------------------------------------
  const key = document.createElement("div");
  key.className = "flt-key";
  key.hidden = true;
  tools.insertAdjacentElement("afterend", key);

  const q = <T extends Element>(scope: Element, sel: string) => scope.querySelector(sel) as T;
  const addBtn = q<HTMLButtonElement>(bar, ".flt-add");
  const addIcon = q<HTMLSpanElement>(bar, ".flt-add-icon");
  const addText = q<HTMLSpanElement>(bar, ".flt-add-text");
  const menu = q<HTMLDivElement>(bar, ".flt-menu");
  const active = q<HTMLSpanElement>(bar, ".flt-active");
  const activeName = q<HTMLSpanElement>(bar, "[data-active]");
  const waveBox = q<HTMLDivElement>(bar, ".flt-wave");
  const frameIn = q<HTMLInputElement>(bar, ".flt-frame");
  const waveText = q<HTMLParagraphElement>(bar, ".flt-wave-text");
  const kneeIn = q<HTMLInputElement>(tools, "[data-knee]");
  const satBtn = q<HTMLButtonElement>(bar, "[data-sat]");
  const canvas = q<HTMLCanvasElement>(layer, ".flt-canvas");
  const overlay = q<SVGSVGElement>(layer, ".flt-overlay");
  const note = q<HTMLDivElement>(layer, ".flt-note");

  let view: View | null = null;
  let frames: FramesResponse | null = null;
  let mode: Mode = "none";
  let frame = DEFAULT_FRAME;
  let showSat = true;
  let reader: FieldReader | null = null;
  type Shown =
    | { kind: "colour"; preset: Preset; pic: ColourPicture; check: TargetCheck }
    | { kind: "wave"; pic: WavelengthPicture; check: TargetCheck }
    | { kind: "comp"; pic: CompositionPicture; check: TargetCheck; marked: Marked[]; win: PixelWindow; max: number }
    | { kind: "visits"; preset: Preset; set: VisitSet; check: TargetCheck; sat: { mask: Uint8Array; n: number } | null };
  let compReader: FieldReader | null = null;
  // What the composition picture draws besides what was found: the unconfirmed hints, and
  // everything else that was checked. Both are switches in the key.
  const compShow = { hints: true, all: false };
  let picked = -1;
  let picture: Shown | null = null;
  /** The visit the blink shows (Months clock), so colour pictures follow it. */
  let currentVisit: number | null = null;
  /** The blink's own date for that epoch, so the key and the blink's label agree. */
  let currentMjd: number | null = null;
  /** Why a colour filter shows one still picture instead of one per date, or null. */
  let stillReason: string | null = null;
  let satReader: FieldReader | null = null;
  let ctl: AbortController | null = null;
  let readout = POINT_HINT;
  const infos = new Map<number, Promise<TileInfo>>();

  const centre = (): SkyPos => frames?.center ?? (view ? { ra: view.ra, dec: view.dec } : { ra: 0, dec: 0 });
  const plateField = (): number => (view ? blinkFieldArcmin(view, frames) : 5);
  const field = (): number => filterField(plateField());

  function say(text: string | null, kind: "info" | "fail" = "info") {
    note.hidden = text == null;
    note.dataset.kind = kind;
    note.textContent = text ?? "";
  }

  function openMenu(open: boolean) {
    menu.hidden = !open;
    addBtn.setAttribute("aria-expanded", String(open));
  }

  /** A filter picture covers the plate (every filter but Difference, which is the blink's own). */
  function colourOn(): boolean {
    return mode !== "none" && mode !== "diff";
  }

  function modeName(): string {
    if (mode === "diff") return "difference";
    if (mode === "wave") return `one colour of light, ${CUBE_UM[frame].toFixed(2)} micrometres`;
    if (mode === "comp") return "what things are made of";
    return PRESETS.find((p) => p.id === mode)?.label ?? "";
  }

  function press() {
    bar.querySelectorAll<HTMLButtonElement>("[data-filter]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === mode)));
    waveBox.hidden = mode !== "wave";
    satBtn.hidden = mode === "wave" || mode === "comp" || mode === "diff";
    const on = mode !== "none";
    const pic = colourOn();
    layer.hidden = !pic;
    key.hidden = !pic;
    brightnessLabel.hidden = !pic;
    contrastLabel.hidden = pic || !contrastIn;
    active.hidden = !on;
    updateStill();
    addIcon.textContent = on ? "✎" : "＋";
    addText.textContent = on ? "Change filter" : "Add filters";
    activeName.textContent = on ? `Filter: ${modeName()}` : "";
    frameIn.value = String(frame);
    waveText.textContent = frameText(frame);
    frameIn.setAttribute("aria-valuetext", frameText(frame));
  }

  /**
   * Dim the blink's date and time strip only when the picture doesn't follow them, and say so under
   * the picture, so Play never looks broken. It never changes the blink's view for the user.
   */
  function updateStill() {
    const moving = picture?.kind === "visits";
    root!.classList.toggle("flt-on", colourOn() && !moving);
    const still = colourOn() && picture != null && !moving;
    stillEl.hidden = !still;
    if (!still) return;
    const why =
      picture!.kind === "colour" && stillReason
        ? stillReason
        : "it combines about a year of SPHEREx's map of the whole sky into one picture";
    stillText.textContent = `This picture doesn't change with the date, so Play and the dates are off: ${why}.`;
    stillGo.textContent = "Remove the filter";
  }

  function ensureReader(): FieldReader {
    const c = centre();
    const fov = field();
    if (!reader || reader.center.ra !== c.ra || reader.center.dec !== c.dec || reader.fovArcmin !== fov) {
      reader = new FieldReader(c, fov, (x, y, n, s) => pixelToSky({ x, y }, { pixel_arcsec: s, size: [n, n] }, c), fetcher, infos);
      picture = null;
    }
    return reader;
  }

  /** The composition's own field around the same centre (COMPOSITION_FIELD). */
  function ensureCompReader(): FieldReader {
    const c = centre();
    const fov = COMPOSITION_FIELD;
    if (!compReader || compReader.center.ra !== c.ra || compReader.center.dec !== c.dec || compReader.fovArcmin !== fov) {
      compReader = new FieldReader(c, fov, (x, y, n, s) => pixelToSky({ x, y }, { pixel_arcsec: s, size: [n, n] }, c), fetcher, infos);
    }
    return compReader;
  }

  /** Gaia stars, known asteroids and the plate's target, as pixels of the composition's field. */
  function compContext(pic: CompositionPicture) {
    const { n, pixelArcsec } = pic.plan;
    const grid = { pixel_arcsec: pixelArcsec, size: [n, n] as [number, number] };
    const c = centre();
    const mjdOf = (d: string | null) => (d && Number.isFinite(Date.parse(d)) ? Date.parse(d) / 86400000 + 40587 : NaN);
    const span = [mjdOf(pic.dates.beg), mjdOf(pic.dates.end)].filter(Number.isFinite);
    const epochs = frames?.epochs.map((e) => e.mjd_mid) ?? [];
    const mid = span.length === 2 ? (span[0] + span[1]) / 2 : epochs.length ? (epochs[0] + epochs[epochs.length - 1]) / 2 : NaN;
    const gaia = (frames?.gaia ?? []).map((g) => ({ ...skyToPixel(Number.isFinite(mid) ? gaiaAt(g, mid) : g, grid, c), g: g.g }));
    const known = (frames?.known ?? []).map((k) => ({ ...skyToPixel(k, grid, c), v: k.v, name: k.name }));
    const kind = targetBySlug(view?.slug)?.kind ?? "";
    const is = /variable/i.test(kind)
      ? ("variable" as const)
      : /asteroid|comet/i.test(kind) || frames?.epochs.some((e) => e.center)
        ? ("moving" as const)
        : null;
    const target = is ? { x: (n - 1) / 2, y: (n - 1) / 2, is, name: view?.name ?? "the target" } : null;
    return { gaia, known, target };
  }

  /** What the data says at the plate's centre (within 2 pixels), plus what we know of the target. */
  function check(
    n: number,
    saturated: Uint8Array | null,
    byWindow: { um: number; hits: Int32Array[] }[],
    dates: TargetCheck["dates"],
    extra: Pick<TargetCheck, "holes" | "blend"> = {},
  ): TargetCheck {
    const mid = Math.round((n - 1) / 2);
    let sat = false;
    if (saturated) {
      for (let dj = -2; dj <= 2; dj++) for (let di = -2; di <= 2; di++) if (saturated[(mid + dj) * n + mid + di]) sat = true;
    }
    const kind = targetBySlug(view?.slug)?.kind ?? null;
    return {
      name: view?.name ?? null,
      kind,
      saturated: sat,
      kept: byWindow.map((w) => ({ um: w.um, share: keptShare(w.hits, n, mid) })),
      moving: !!frames?.epochs.some((e) => e.center) || /asteroid|comet/i.test(kind ?? ""),
      dates,
      ...extra,
      blend: sat ? null : (extra.blend ?? null),
    };
  }

  async function render() {
    ctl?.abort();
    if (!colourOn() || !view) return;
    ctl = new AbortController();
    const signal = ctl.signal;
    const progress = (done: number, total: number) => {
      if (!signal.aborted && total) say(`Reading from NASA's SPHEREx map: ${done} of ${total}…`);
    };
    say("Reading from NASA's SPHEREx map…");
    try {
      if (mode === "comp") {
        const pic = await ensureCompReader().composition(signal, progress, async () => {
          say("Measuring every star in the picture…");
          await new Promise((done) => setTimeout(done, 30)); // let the note paint before the page is busy
        });
        if (signal.aborted) return;
        const n = pic.plan.n;
        const finite = Array.from(pic.result.detection).filter(Number.isFinite).sort((a, b) => a - b);
        const max = Math.max(finite[Math.floor(0.995 * (finite.length - 1))] ?? 5, 5);
        picked = -1;
        const marked = applyContext(pic.result.sources, compContext(pic));
        picture = { kind: "comp", pic, check: check(n, null, [], pic.dates), marked, win: { x0: 0, y0: 0, x1: n, y1: n }, max };
      } else if (mode === "wave") {
        const pic = await ensureReader().wavelength(frame, signal, progress);
        if (signal.aborted) return;
        const mid = Math.round((pic.plan.n - 1) / 2);
        picture = { kind: "wave", pic, check: check(pic.plan.n, null, [], pic.dates, { holes: coreHoles(pic.values, pic.plan.n, mid) }) };
      } else if (view.clock === "months" && (await tryVisits(signal))) {
        // one colour picture per date (tryVisits set picture)
      } else {
        if (view.clock !== "months") stillReason = "colours that change with the date need the Months view";
        const preset = PRESETS.find((p) => p.id === mode)!;
        const pic = await ensureReader().colour(preset, signal, progress);
        if (signal.aborted) return;
        const n = pic.plan.n;
        const mid = Math.round((n - 1) / 2);
        const blend = blendPeaks(pic.b, pic.r, n, mid)
          ? { shortUm: (preset.blue.lo + preset.blue.hi) / 2, longUm: (preset.red.lo + preset.red.hi) / 2 }
          : null;
        picture = { kind: "colour", preset, pic, check: check(n, pic.saturated, pic.byWindow, pic.dates, { blend }) };
      }
      say(null);
      draw();
    } catch (e) {
      if (signal.aborted) return;
      picture = null;
      const why = e instanceof Error ? e.message : String(e);
      say(`NASA IRSA's SPHEREx map didn't answer (${why}). Remove the filter to see the blink.`, "fail");
      renderKey();
    }
  }

  /**
   * Colour pictures that follow the blink: one per visit from the blink's own snapshots (visit.ts).
   * The saturated-star marks come from the all-sky map over the plate's field: a star too bright for
   * SPHEREx is too bright on every date. Returns false (with stillReason) when this view can't.
   */
  async function tryVisits(signal: AbortSignal): Promise<boolean> {
    const preset = PRESETS.find((p) => p.id === mode)!;
    stillReason = null;
    try {
      say("Reading the snapshots for every date…");
      const set = await loadVisits(view!, preset.id, signal, (done, total) => {
        if (!signal.aborted && total) say(`Reading the snapshots for every date: ${done} of ${total}…`);
      });
      if (signal.aborted) return true;
      let sat: { mask: Uint8Array; n: number } | null = null;
      let check0: TargetCheck = check(1, null, [], { beg: null, end: null });
      try {
        const c = centre();
        const fov = plateField();
        if (!satReader || satReader.center.ra !== c.ra || satReader.center.dec !== c.dec || satReader.fovArcmin !== fov) {
          satReader = new FieldReader(c, fov, (x, y, n, s2) => pixelToSky({ x, y }, { pixel_arcsec: s2, size: [n, n] }, c), fetcher, infos);
        }
        say("Checking for stars too bright to measure…");
        const cube = await satReader.colour(PRESETS[0], signal, () => {});
        if (signal.aborted) return true;
        sat = { mask: cube.saturated, n: cube.plan.n };
        check0 = check(cube.plan.n, cube.saturated, cube.byWindow, cube.dates);
      } catch (e) {
        if (signal.aborted) return true;
        // Without the all-sky map the dates still show; only the saturation marks are missing.
      }
      picture = { kind: "visits", preset, set, check: { ...check0, blend: null }, sat };
      if (currentVisit == null || !set.visits.has(currentVisit)) currentVisit = [...set.visits.keys()][0] ?? null;
      return true;
    } catch (e) {
      if (signal.aborted) return true;
      stillReason = e instanceof Error ? e.message : String(e);
      return false;
    }
  }

  const knee = () => 10 ** Number(kneeIn.value);

  function drawVisit() {
    if (picture?.kind !== "visits") return;
    const [nx, ny] = picture.set.size;
    canvas.width = nx;
    canvas.height = ny;
    const ctx = canvas.getContext("2d");
    const v = currentVisit != null ? picture.set.visits.get(currentVisit) : undefined;
    const names = ["red", "green", "blue"];
    const lacking = v ? v.channels.map((c, i) => (c ? null : names[i])).filter((x): x is string => !!x) : names;
    if (!v || lacking.length) {
      ctx?.clearRect(0, 0, nx, ny);
      say(`No colour picture for this date: SPHEREx has no ${lacking.join(" or ")} snapshot from it. Step to another date.`);
    } else {
      say(null);
      const [r, g, b] = v.channels as Channel[];
      ctx?.putImageData(new ImageData(compose(r, g, b, knee()).rgba, nx, ny), 0, 0);
    }
    updateStill();
    drawOverlay();
    renderKey();
  }

  function draw() {
    if (!picture) return;
    if (picture.kind === "visits") return drawVisit();
    updateStill();
    const n = picture.pic.plan.n;
    const rgba =
      picture.kind === "colour"
        ? compose(picture.pic.r, picture.pic.g, picture.pic.b, knee()).rgba
        : picture.kind === "comp"
          ? greyImage(picture.pic.result.detection, { sigma: 1, max: picture.max }, knee())
          : greyImage(picture.pic.values, picture.pic.scale, knee());
    canvas.width = n;
    canvas.height = n;
    canvas.getContext("2d")?.putImageData(new ImageData(rgba, n, n), 0, 0);
    drawOverlay();
    renderKey();
  }

  function drawOverlay() {
    if (!picture) return;
    if (picture.kind === "visits") {
      const [nx] = picture.set.size;
      overlay.setAttribute("viewBox", `0 0 ${nx} ${nx}`);
      const parts: string[] = [];
      if (showSat && picture.sat) {
        const { mask, n: nc } = picture.sat;
        const f = nx / nc;
        let d = "";
        for (let k = 0; k < mask.length; k++) if (mask[k]) d += `M${(k % nc) * f},${Math.floor(k / nc) * f}h${f}v${f}h${-f}z`;
        if (d) parts.push(`<path class="ov-sat" d="${d}" />`);
      }
      const mid = nx / 2;
      const t = Math.max(2, nx / 40);
      const gap = t * 0.8;
      parts.push(
        `<path class="ov-target" d="M${mid - gap - t},${mid}h${t}M${mid + gap},${mid}h${t}M${mid},${mid - gap - t}v${t}M${mid},${mid + gap}v${t}" />`,
      );
      overlay.innerHTML = parts.join("");
      return;
    }
    const n = picture.pic.plan.n;
    overlay.setAttribute("viewBox", `0 0 ${n} ${n}`);
    const parts: string[] = [];
    if (picture.kind === "comp") {
      const scale = overlay.getBoundingClientRect().width / n;
      parts.push(compositionSvg(picture.marked, picture.pic.result.blocks, picture.win, { ...compShow, selected: picked, scale }));
    }
    if (showSat && picture.kind === "colour") {
      let d = "";
      const s = picture.pic.saturated;
      for (let k = 0; k < s.length; k++) if (s[k]) d += `M${k % n},${Math.floor(k / n)}h1v1h-1z`;
      if (d) parts.push(`<path class="ov-sat" d="${d}" />`);
    }
    const mid = n / 2;
    const side = (plateField() * 60) / picture.pic.plan.pixelArcsec;
    // The composition picture keeps to its own marks: the cross is enough to find the centre.
    if (side < n - 1 && picture.kind !== "comp") {
      parts.push(`<rect class="ov-box" fill="none" x="${mid - side / 2}" y="${mid - side / 2}" width="${side}" height="${side}" />`);
    }
    const t = Math.max(1.2, n / 40);
    const gap = t * 0.6;
    parts.push(
      `<path class="ov-target" d="M${mid - gap - t},${mid}h${t}M${mid + gap},${mid}h${t}M${mid},${mid - gap - t}v${t}M${mid},${mid + gap}v${t}" />`,
    );
    overlay.innerHTML = parts.join("");
  }

  function renderKey() {
    const p = picture;
    const reasons = p ? whyUnreliable(p.check) : [];
    const who = esc(p?.check.name ?? "the object at the centre");
    const date = (d: string | null) => (d ? d.slice(0, 10) : "?");
    let title = "";
    let entries = "";
    if (mode === "comp") {
      key.innerHTML = compKey();
      return;
    }
    if (mode === "wave") {
      title = esc(frameText(frame));
      entries = html`<li><i class="sw sw-ramp"></i><b>White to black</b><span>bright to faint in this colour of
        light. If a star fades as you move the slider, it really is fainter there: a star that goes dark near
        3.3 micrometres may contain methane gas.</span></li>
        <li><i class="sw sw-none"></i><b>Striped</b><span>no data here</span></li>
        <li><i class="sw sw-box"></i><b>Dashed square</b><span>the area of the normal view</span></li>`;
    } else if (p?.kind === "visits") {
      const v = currentVisit != null ? p.set.visits.get(currentVisit) : undefined;
      const when = v ? formatMjd(currentMjd ?? v.mjd) : "";
      title = `${esc(p.preset.label)}${when ? `, ${when}` : ""}: red, green and blue show three colours of infrared light, invisible to our eyes`;
      entries = KEY_SETS[keySet(true, p.preset.id)]
        .map((e) => {
          const sw =
            e.swatch === "colour" && e.rgb
              ? `<i class="sw" style="background:${swatchColour(e.rgb)}"></i>`
              : `<i class="sw sw-${e.swatch}"></i>`;
          return `<li>${sw}<b>${esc(e.label)}</b><span>${esc(e.examples)}</span></li>`;
        })
        .join("");
      const um = (r: { min: number; max: number } | null) => (r ? `${r.min.toFixed(2)}–${r.max.toFixed(2)}` : "none");
      const lacking = v ? ["red", "green", "blue"].filter((_, i) => !v.ranges[i]) : [];
      const dateNote = !v
        ? ""
        : lacking.length
          ? `<p class="flt-date-note">SPHEREx took no ${lacking.join(" or ")} snapshot on this date, so there is no colour
              picture for it. Step to another date.</p>`
          : `<p class="flt-date-note">On this date SPHEREx saw red light at ${um(v.ranges[0])}, green at ${um(v.ranges[1])} and
              blue at ${um(v.ranges[2])} micrometres. These change a little from date to date, so a small change in colour
              between dates can come from that. A change in position is real.</p>`;
      key.innerHTML = html`
        <p class="flt-key-title">${title}</p>
        <ul class="flt-key-list">${entries}</ul>
        ${dateNote}
        <p class="flt-readout" aria-live="polite">${esc(readout)}</p>
        ${reasons.length
          ? html`<section class="flt-why" role="note">
              <h3>Why the colour of ${who} can't be trusted</h3>
              ${reasons
                .filter((x) => x.key !== "variable" && x.key !== "moving")
                .map((x) => `<p><strong>${esc(x.title)}.</strong> ${esc(x.key === "saturated" ? saturationSnapshotText(p.check.name ?? "The object in the middle") : x.text)}</p>`)
                .join("")}
            </section>`
          : ""}
        <details class="flt-how">
          <summary>How this picture is made</summary>
          <p>Each date's picture is made from the same SPHEREx snapshots as the normal view: one taken with the
            camera that sees ${p.preset.id === "cold" ? "4.4–5.0" : "1.6–2.4"} micrometres for red,
            ${p.preset.id === "cold" ? "1.6–2.4" : "1.1–1.6"} for green and ${p.preset.id === "cold" ? "1.1–1.6" : "0.75–1.1"} for blue.
            Step through the dates to see what moves and how its colour looks each time. A spot only gets a colour when
            it is clearly brighter than the background; otherwise it stays grey. Stars marked as too bright come from
            NASA's map of the whole sky. The method and the tests behind it are in docs/colour-layers.md.</p>
        </details>
      `;
      return;
    } else if (colourOn()) {
      const preset = PRESETS.find((x) => x.id === mode)!;
      title = `${esc(preset.label)}: red, green and blue show three colours of infrared light, invisible to our eyes (${esc(preset.red.plain)}, ${esc(preset.green.plain)} and ${esc(preset.blue.plain)})`;
      if (stillReason) title += `. This picture doesn't change with the date (${esc(stillReason)}): it combines about a year of photos`;
      entries = KEYS[preset.id]
        .map((e) => {
          const sw =
            e.swatch === "colour" && e.rgb
              ? `<i class="sw" style="background:${swatchColour(e.rgb)}"></i>`
              : `<i class="sw sw-${e.swatch}"></i>`;
          return `<li>${sw}<b>${esc(e.label)}</b><span>${esc(e.examples)}</span></li>`;
        })
        .join("");
      entries += `<li><i class="sw sw-box"></i><b>Dashed square</b><span>the area of the normal view</span></li>`;
    }
    key.innerHTML = html`
      <p class="flt-key-title">${title}</p>
      <ul class="flt-key-list">${entries}</ul>
      <p class="flt-readout" aria-live="polite">${esc(readout)}</p>
      ${reasons.length
        ? html`<section class="flt-why" role="note">
            <h3>${p?.kind === "wave" ? `Why this picture of ${who} can't be trusted` : `Why the colour of ${who} can't be trusted`}</h3>
            ${reasons.map((x) => `<p><strong>${esc(x.title)}.</strong> ${esc(x.text)}</p>`).join("")}
          </section>`
        : ""}
      <details class="flt-how">
        <summary>How this picture is made</summary>
        <p>It comes from NASA's SPHEREx map of the whole sky, downloaded live from NASA's archive. SPHEREx
          photographs the sky in 102 colours of infrared light, which our eyes can't see. This map is blurrier
          than the normal view, so objects close together can merge. Its colours were taken on different days
          between ${date(p && "pic" in p ? p.pic.dates.beg : null)} and ${date(p && "pic" in p ? p.pic.dates.end : null)}, so the colours of
          things that move or change brightness can be wrong. A spot only gets a colour when it is clearly
          brighter than the background; otherwise it stays grey. The method and the tests behind it are in
          docs/colour-layers.md.</p>
      </details>
    `;
  }

  /** The key of the composition filter: what is in this picture, the centre, the picked source. */
  function compKey(): string {
    const p = picture?.kind === "comp" ? picture : null;
    const date = (d: string | null) => (d ? d.slice(0, 10) : "?");
    const t = p ? tally(p.marked, p.pic.result.blocks, p.win) : null;
    const colourOf = (id: keyof typeof BANDS) => GROUPS.find((g) => g.id === BANDS[id].group)!.colour;
    // One entry per molecule that is in this picture, and nothing for the ones that aren't.
    let entries = "";
    if (t) {
      for (const g of GROUPS) {
        if (g.id === "pah") {
          const r = p ? glowRange(p.pic.result.blocks, p.win) : null;
          const measured = t.glow.found + t.glow.possible + t.glow.none;
          if (t.glow.found && r) {
            const share = Math.round((100 * t.glow.found) / Math.max(1, measured));
            entries += `<li><i class="sw sw-glow" style="--c:${GLOW_FILL}"></i><b>${esc(g.label)}</b><span>${share >= 95 ? "all over the picture" : `over ${share}% of the picture`} · stronger green is brighter (${mjy(r.lo)} to ${mjy(r.hi)} MJy/sr)</span></li>`;
          }
        } else if (t.groups[g.id].found) {
          const k = t.groups[g.id].found;
          entries += `<li><i class="sw sw-ring" style="--c:${g.colour}"></i><b>${esc(g.label)}</b><span>${k} ${k === 1 ? "star" : "stars"} · ${esc(g.what)}</span></li>`;
        }
      }
      const hints = t.possible + t.glow.possible;
      if (compShow.hints && hints) entries += `<li>${DASHED_SWATCH}<b>Dashed ring${t.glow.possible ? ", faintest green" : ""}</b><span>a hint, not confirmed</span></li>`;
      if (compShow.all) {
        entries += `<li>${THIN_SWATCH}<b>Thin ring</b><span>${t.none} checked, nothing found</span></li>
          <li>${CROSS_SWATCH}<b>Cross</b><span>${t.cant} we can't tell (too faint, too bright or blended)</span></li>`;
      }
    }
    const nothing = t && !t.found && !t.glow.found;
    const uncal = p ? GROUPS.flatMap((g) => g.bands).filter((id) => !p.pic.result.cal[id].ok) : [];
    // The source at the centre: the plate's target.
    let centreLine = "";
    if (p) {
      const mid = p.pic.plan.n / 2;
      const c = nearestSource(p.marked, mid, mid, TARGET_MATCH_PX);
      const who = p.check.name ? `${p.check.name} (centre)` : "At the centre";
      centreLine = `<p class="cp-line"><b>${esc(who)}.</b> ${esc(c >= 0 ? summarise(p.marked[c]) : "No star stands out here in this map, so there is nothing to measure.")}</p>`;
    }
    const src = picked >= 0 && p ? p.marked[picked] : null;
    let detail = "";
    if (src && p) {
      const { n, pixelArcsec } = p.pic.plan;
      const pos = pixelToSky({ x: src.x, y: src.y }, { pixel_arcsec: pixelArcsec, size: [n, n] }, centre());
      const all = bandRows(src);
      const shown = all.filter((r) => r.state === "found" || r.state === "possible");
      const bands = shown
        .map((r) => {
          const why = r.state === "possible" ? `Not confirmed: ${r.reasons.join("; ")}.` : r.note ? `${r.note[0].toUpperCase()}${r.note.slice(1)}.` : "";
          return `<li>${r.state === "possible" ? DASHED_SWATCH : `<i class="sw sw-ring" style="--c:${colourOf(r.id)}"></i>`}
            <span><b>${esc(r.name)}</b> at ${esc(r.um)}: ${esc(r.value || "not measurable on its own")}</span>${why ? `<small>${esc(why)}</small>` : ""}</li>`;
        })
        .join("");
      const rows = all
        .map(
          (r) => `<tr class="cp-row-${r.state}"><th scope="row">${esc(r.name)} <span>${esc(r.um)}</span></th>
            <td>${esc(stateWord(r.state))}</td><td>${esc(r.value || "—")}</td>
            <td>${esc(r.reasons.join("; ") || r.note)}</td></tr>`,
        )
        .join("");
      const others = lookalikes(src);
      const alike = others.length ? `<p><strong>What else could look like this.</strong> ${others.map(esc).join(" ")}</p>` : "";
      const left = src.dropped.exposures + src.dropped.glitches;
      const link = new URLSearchParams({
        ra: pos.ra.toFixed(5),
        dec: pos.dec.toFixed(5),
        clock: view?.clock ?? "months",
        fov: String(view?.fov ?? 5),
        det: String(view?.det ?? 2),
      });
      detail = html`<section class="cp-detail">
        <h3>${esc(summarise(src))}</h3>
        <p>The star you picked, at ${esc(formatRaDec(pos))}.
          ${src.gaiaG != null ? `Gaia has a star here (brightness G = ${src.gaiaG.toFixed(1)}).` : ""}</p>
        ${bands ? `<ul class="cp-bands">${bands}</ul>
          <p>Each number is the share of the star's light that is missing at that colour (for dust: extra), give
            or take its error, and how many times the noise it is.</p>` : ""}
        ${alike}
        <p><a href="?${link}#/explore">Open the normal view on this spot</a> to check it in NASA's sharper
          photos and see its spectrum.</p>
        <details class="cp-all">
          <summary>All nine checks on this star</summary>
          ${left ? `<p>${left} of its 83 colours of light ${left === 1 ? "was" : "were"} left out (lost photos or one-off glitches).</p>` : ""}
          <div class="cp-table"><table>
            <thead><tr><th scope="col">Band</th><th scope="col">Result</th><th scope="col">How deep</th><th scope="col">Notes</th></tr></thead>
            <tbody>${rows}</tbody></table></div>
        </details>
      </section>`;
    }
    const who = p?.check.name ?? "The target";
    const kind = p?.check.kind ?? "";
    const warn = !p
      ? ""
      : /variable/i.test(kind)
        ? `${who} is a star whose brightness changes. Each colour of light was photographed on a different day, so a change in brightness can look like a molecule. Anything found on it is shown as a hint only.`
        : p.check.moving
          ? `${who} moves across the sky, so it is in this map on some days only. The rings describe the sky behind it.`
          : "";
    const hintCount = t ? t.possible + t.glow.possible : 0;
    return html`
      <p class="flt-key-title">${nothing ? "No molecules found in this picture" : "Molecules found in this picture"}</p>
      ${entries ? `<ul class="cp-legend">${entries}</ul>` : ""}
      ${nothing && t
        ? `<p class="cp-line">We checked ${t.sources} stars for methane, ices, water vapour, carbon monoxide and glowing dust. None shows clearly. That is the usual result: this map can only see them on cold brown dwarfs and in star-forming clouds.</p>`
        : ""}
      ${centreLine}
      ${t && compShow.all
        ? `<p class="cp-line">Glowing dust was checked on ${Math.round(100 * t.glowChecked)}% of this picture; bright stars take up the rest.</p>`
        : ""}
      ${uncal.length
        ? `<p class="cp-line">Not checked here, because the picture has too few ordinary stars to compare with: ${[...new Set(uncal.map((id) => `${BANDS[id].name} ${BANDS[id].um}`))].map(esc).join(", ")}.</p>`
        : ""}
      <p class="flt-readout" aria-live="polite"${readout ? "" : " hidden"}>${esc(readout)}</p>
      ${detail}
      ${warn ? `<section class="flt-why" role="note"><p>${esc(warn)}</p></section>` : ""}
      ${t
        ? `<div class="cp-more">
            <button class="flt-switch" type="button" data-cp="hints" aria-pressed="${compShow.hints}">Show hints (${hintCount})</button>
            <button class="flt-switch" type="button" data-cp="all" aria-pressed="${compShow.all}">Show the other stars checked (${t.none + t.cant})</button>
          </div>`
        : ""}
      <details class="flt-how">
        <summary>How this is measured, and how far to trust it</summary>
        <p>It comes from NASA's SPHEREx map of the whole sky, downloaded live from NASA's archive: 83 colours
          of infrared light, from 1.1 to 5.0 micrometres. A molecule blocks (or, for dust, adds) light at its
          own colours. For each star we add up its light in every colour and compare the molecule's colour
          with the colours either side of it, with the same formula and limits as our search pipeline. The
          ordinary stars in the picture, which have none of these molecules, set what "nothing there" looks
          like and how big the errors are. Colours with lost photos or one-off glitches are left out.</p>
        <p>A solid ring means the sign is clearly measured, seen in more than one colour of light, and passed
          the look-alike checks. A dashed ring is a hint: a sign is measured, but one check failed. "Can't
          tell" means the star is too faint, too bright or mixed with a neighbour; it doesn't mean nothing is
          there.</p>
        <p>This is a first look, not a discovery. The map combines photos taken between
          ${date(p?.pic.dates.beg ?? null)} and ${date(p?.pic.dates.end ?? null)}, so something that moves or
          changes brightness can look like a molecule, and we can only warn about that for our named targets.
          The picture is ${COMPOSITION_FIELD}′ wide and blurrier than the normal view; the cross marks the
          normal view's centre. A ring is a reason to look at the star's spectrum. The method and its tests
          on real data are in docs/colour-layers.md.</p>
      </details>
    `;
  }

  function onPointComp(ev: PointerEvent) {
    if (picture?.kind !== "comp") return;
    const rect = canvas.getBoundingClientRect();
    const n = picture.pic.plan.n;
    const x = ((ev.clientX - rect.left) / rect.width) * n;
    const y = ((ev.clientY - rect.top) / rect.height) * n;
    if (!(x >= 0 && y >= 0 && x < n && y < n)) return;
    const k = nearestSource(picture.marked, x, y);
    const glow = k < 0 ? glowAt(picture.pic.result.blocks, x, y) : null;
    const clicked = ev.type === "pointerdown" && k !== picked;
    if (clicked) picked = k;
    // The picked star is already described in full below, so pointing at it adds nothing.
    readout = k >= 0 ? (k === picked ? "" : `${summarise(picture.marked[k])} Click for the numbers.`) : glow ? describeBlock(glow, picture.pic.plan.pixelArcsec) : COMP_HINT;
    if (clicked) {
      drawOverlay();
      renderKey();
      return;
    }
    const el = key.querySelector<HTMLElement>(".flt-readout");
    if (el) {
      el.textContent = readout;
      el.hidden = !readout;
    }
  }

  function onPointVisit(ev: PointerEvent) {
    if (picture?.kind !== "visits") return;
    const rect = canvas.getBoundingClientRect();
    const [nx] = picture.set.size;
    const i = Math.floor(((ev.clientX - rect.left) / rect.width) * nx);
    const j = Math.floor(((ev.clientY - rect.top) / rect.height) * nx);
    if (i < 0 || j < 0 || i >= nx || j >= nx) return;
    const k = j * nx + i;
    const v = currentVisit != null ? picture.set.visits.get(currentVisit) : undefined;
    if (!v) return;
    const part = (name: string, c: Channel | null) => `${name} ${c ? level(c.values[k] / c.stats.sigma) : "no data"}`;
    let sat = false;
    if (picture.sat) {
      const f = picture.sat.n / nx;
      sat = !!picture.sat.mask[Math.floor(j * f) * picture.sat.n + Math.floor(i * f)];
    }
    readout = `Here: ${part("red light", v.channels[0])}, ${part("green", v.channels[1])}, ${part("blue", v.channels[2])}${sat ? `. ${SATURATED_READOUT}` : ""}.`;
    canvas.title = formatRaDec(pixelToSky({ x: i, y: j }, { pixel_arcsec: picture.set.pixelArcsec, size: picture.set.size }, centre()));
    const el = key.querySelector(".flt-readout");
    if (el) el.textContent = readout;
  }

  function onPoint(ev: PointerEvent) {
    if (!picture) return;
    if (picture.kind === "comp") return onPointComp(ev);
    if (picture.kind === "visits") return onPointVisit(ev);
    const rect = canvas.getBoundingClientRect();
    const { n, pixelArcsec } = picture.pic.plan;
    const i = Math.floor(((ev.clientX - rect.left) / rect.width) * n);
    const j = Math.floor(((ev.clientY - rect.top) / rect.height) * n);
    if (i < 0 || j < 0 || i >= n || j >= n) return;
    const k = j * n + i;
    const pos = formatRaDec(pixelToSky({ x: i, y: j }, { pixel_arcsec: pixelArcsec, size: [n, n] }, centre()));
    const part = (name: string, c: Channel) => `${name} ${level(c.values[k] / c.stats.sigma)}`;
    if (picture.kind === "colour") {
      const { r, g, b, saturated } = picture.pic;
      readout = `Here: ${part("red light", r)}, ${part("green", g)}, ${part("blue", b)}${saturated[k] ? `. ${SATURATED_READOUT}` : ""}.`;
    } else {
      const v = picture.pic.values[k];
      readout = Number.isFinite(v) ? `Here: ${level(v / picture.pic.scale.sigma)} in this colour of light.` : "Here: no data.";
    }
    canvas.title = `${pos}`;
    const el = key.querySelector(".flt-readout");
    if (el) el.textContent = readout;
  }

  // ---- events ------------------------------------------------------------------------------
  addBtn.addEventListener("click", () => openMenu(menu.hidden));
  /** Difference is the blink's own view: entering it presses the blink's (hidden) button, leaving it presses Blink. */
  function syncBlink() {
    const inDiff = diffBtn?.getAttribute("aria-pressed") === "true";
    if (mode === "diff" && !inDiff) diffBtn?.click();
    else if (mode !== "diff" && inDiff) blinkMode("blink")?.click();
  }
  function setFilter(next: Mode) {
    if (next === mode) return;
    mode = next;
    picture = null;
    picked = -1;
    readout = mode === "comp" ? COMP_HINT : POINT_HINT;
    press();
    syncBlink();
    if (!colourOn()) ctl?.abort();
    else void render();
  }
  q<HTMLButtonElement>(bar, ".flt-off").addEventListener("click", () => setFilter("none"));
  stillGo.addEventListener("click", () => setFilter("none"));
  bar.querySelectorAll<HTMLButtonElement>("[data-filter]").forEach((b) =>
    b.addEventListener("click", () => {
      setFilter(b.dataset.filter as Mode);
      if (mode !== "wave") openMenu(false);
    }),
  );
  // The other way round: Blink or Stack pressed on the blink while Difference is the filter.
  const diffWatch = diffBtn
    ? new MutationObserver(() => {
        const inDiff = diffBtn.getAttribute("aria-pressed") === "true";
        if (mode === "diff" && !inDiff) setFilter("none");
      })
    : null;
  if (diffBtn) diffWatch!.observe(diffBtn, { attributes: true, attributeFilter: ["aria-pressed"] });
  frameIn.addEventListener("input", () => {
    frame = skipBlank(Number(frameIn.value), frame);
    press();
    void render();
  });
  kneeIn.addEventListener("input", () => draw());
  q<HTMLButtonElement>(bar, "[data-sat]").addEventListener("click", (ev) => {
    showSat = !showSat;
    (ev.currentTarget as HTMLButtonElement).setAttribute("aria-pressed", String(showSat));
    drawOverlay();
  });
  // The composition key's switches are redrawn with the key, so listen on the key itself.
  key.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("[data-cp]");
    if (!b) return;
    const k = b.dataset.cp as keyof typeof compShow;
    compShow[k] = !compShow[k];
    drawOverlay();
    renderKey();
  });
  const onDoc = (ev: MouseEvent) => {
    if (!menu.hidden && !bar.contains(ev.target as Node)) openMenu(false);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape" && !menu.hidden) {
      openMenu(false);
      addBtn.focus();
    }
  };
  document.addEventListener("click", onDoc);
  document.addEventListener("keydown", onKey);
  layer.addEventListener("pointermove", onPoint);
  layer.addEventListener("pointerdown", onPoint);

  press();

  return {
    goto(v, f) {
      view = v;
      frames = f;
      if (colourOn()) void render();
    },
    epoch(e) {
      const v = visitOf(e.id);
      currentMjd = e.mjd_mid;
      if (v == null || v === currentVisit) return;
      currentVisit = v;
      if (picture?.kind === "visits") drawVisit();
    },
    destroy() {
      ctl?.abort();
      document.removeEventListener("click", onDoc);
      document.removeEventListener("keydown", onKey);
      diffWatch?.disconnect();
      if (diffBtn) diffBtn.hidden = false;
      if (contrastIn && contrastHome) {
        contrastIn.disabled = false;
        contrastHome.append(contrastIn);
        contrastHome.hidden = false;
      }
      bar.remove();
      layer.remove();
      tools.remove();
      key.remove();
      root.classList.remove("flt-on");
    },
  };
}
