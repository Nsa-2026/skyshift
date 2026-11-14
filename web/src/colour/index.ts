// Filters on the main image (layers A1 and A2). An "Add filters" button above the plate opens a
// menu; the chosen filter replaces the picture on the plate, same field and orientation (north up,
// east left), and a key under the plate says what each colour means. Nothing downloads until a
// filter is chosen. Algorithms and their checks: rgb.ts, engine.ts, docs/colour-layers.md.
//
// The blink's own DOM (P4) is not edited: this module adds its button, layer and key around the
// plate from outside, and the plate shows through again when the filter is removed. While a
// filter is on the blink is paused with its own Play button, and played again when it's removed.
//
// The pictures come live from IRSA, which fails and stalls under load: every read has a timeout
// (cube.ts), the plate always says what is happening, and a failure says so in plain words with
// "Try again" and "Back to the blink" (failure.ts). Never a blank plate or an endless wait.
import "./filters.css";
import type { FramesResponse, SkyPos } from "../api/types";
import { formatRaDec, pixelToSky } from "../app/astro";
import type { View } from "../app/modules";
import { targetBySlug } from "../app/state";
import { CUBE_UM } from "../sky/layers";
import { blinkFieldArcmin } from "../sky/overlay";
import { FEATURES, PRESETS, frameText, type Preset, type PresetId } from "./bands";
import { BLANK_FRAMES, type Fetcher, type TileInfo } from "./cube";
import { FieldReader, type ColourPicture, type WavelengthPicture } from "./engine";
import { describeFailure } from "./failure";
import { KEYS, swatchColour } from "./legend";
import { compose, greyImage, type Channel } from "./rgb";
import { SATURATED_READOUT, whyUnreliable, type TargetCheck } from "./why";

const html = String.raw;
type Mode = "none" | PresetId | "wave";
/** Default wavelength: the 3.3 µm methane band, where the coldest brown dwarfs go dark. */
const DEFAULT_FRAME = 62;
/** Without progress for this long, the loading note says IRSA is slow (the read goes on). */
const SLOW_MS = 12_000;
/** The wavelength slider reads its frame once it rests this long, not at every step of a drag. */
const SLIDE_MS = 200;
let mounts = 0;

export interface FiltersHandle {
  /** The blink's field is ready (frames), or failed to load (null): draw the filter there. */
  goto(view: View, frames: FramesResponse | null): void;
  /** The blink is loading `view`. A filter on another spot is cleared, never shown as this one. */
  loading(view: View): void;
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
 * (70 pixels, stars as dots) and at most 30′; the blink's own field is drawn as a dashed square.
 * At most 30′ because the read grows with the area: a 60′ field (the 20′ asteroid and comet plates)
 * took 64 s in Chromium on 14 Nov; a 60′ plate shows its middle 30′.
 */
export const MAX_FILTER_ARCMIN = 30;
export function filterField(plateArcmin: number): number {
  return Math.min(MAX_FILTER_ARCMIN, Math.max(15, 3 * plateArcmin));
}

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
  if (!col || !mat || !plate || !root) return { goto() {}, loading() {}, destroy() {} };
  const uid = `flt${++mounts}`;

  // ---- the button and menu, just above the plate ------------------------------------------
  const bar = document.createElement("div");
  bar.className = "flt-bar";
  bar.innerHTML = html`
    <button class="btn flt-add" type="button" aria-expanded="false" aria-controls="${uid}-menu">
      <span class="flt-add-icon" aria-hidden="true">＋</span><span class="flt-add-text">Add filters</span>
    </button>
    <span class="flt-active" hidden>
      <span class="flt-active-name" data-active></span>
      <button class="flt-off" type="button">Remove filter</button>
    </span>
    <div class="flt-menu" id="${uid}-menu" role="group" aria-label="Filters" hidden>
      <p class="flt-h">Show the picture as</p>
      <div class="flt-opts">
        <button type="button" data-flt-mode="none" aria-pressed="true"><b>No filter</b>
          <span>The normal view, where you watch things move.</span></button>
        <button type="button" data-flt-mode="cold" aria-pressed="false"><b>Find cold objects</b>
          <span>Colours show how warm or cold things are. Ordinary stars look blue; cold objects such as brown
            dwarfs look pink or red. Dust and distant galaxies can look red too, so a red dot is something to
            check, not a discovery.</span></button>
        <button type="button" data-flt-mode="nir" aria-pressed="false"><b>Compare with 2MASS</b>
          <span>The same colours as 2MASS, an older and well-known map of the sky, so you can compare the two.</span></button>
        <button type="button" data-flt-mode="wave" aria-pressed="false"><b>One colour of light</b>
          <span>See the sky in just one of SPHEREx's 102 colours of infrared light. Slide to find where gases
            like water or methane block the light.</span></button>
      </div>
      <div class="flt-row">
        <button class="flt-switch" type="button" data-flt-sat aria-pressed="true">Mark stars too bright to measure</button>
        <label class="flt-range"><span>Brightness</span>
          <input type="range" min="-1" max="1.3" step="0.05" value="0" data-knee
            aria-label="Brightness: show fainter things more or less" /></label>
      </div>
      <p class="flt-src">These pictures come live from NASA's SPHEREx map of the whole sky, so they need
        NASA's archive to answer. The map is blurrier than the normal view and combines about a year of
        photos into one, so it shows a wider area around the target: the dashed square is the normal
        view's area.</p>
    </div>
    <!-- The wavelength slider sits above the plate, not in the menu: the picture must stay in view
         while it slides. -->
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
      <p class="flt-wave-text"></p>
    </div>
  `;
  col.insertBefore(bar, mat);
  col.classList.add("has-flt");

  // ---- the picture, on the plate ----------------------------------------------------------
  const layer = document.createElement("div");
  layer.className = "flt-layer";
  layer.hidden = true;
  layer.innerHTML = html`
    <canvas class="flt-canvas" role="img" aria-label="Filtered picture of the plate's field"></canvas>
    <svg class="flt-overlay" aria-hidden="true"></svg>
    <span class="flt-compass" aria-hidden="true">N ↑ E ←</span>
    <div class="flt-note" role="status" hidden>
      <p class="flt-note-title"></p>
      <p class="flt-note-text"></p>
      <div class="flt-note-actions"></div>
    </div>
  `;
  plate.appendChild(layer);

  // ---- the key, under the plate -----------------------------------------------------------
  const key = document.createElement("div");
  key.className = "flt-key";
  key.hidden = true;
  mat.insertAdjacentElement("afterend", key);

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
  const kneeIn = q<HTMLInputElement>(bar, "[data-knee]");
  const satBtn = q<HTMLButtonElement>(bar, "[data-flt-sat]");
  const canvas = q<HTMLCanvasElement>(layer, ".flt-canvas");
  const overlay = q<SVGSVGElement>(layer, ".flt-overlay");
  const note = q<HTMLDivElement>(layer, ".flt-note");
  const noteTitle = q<HTMLParagraphElement>(note, ".flt-note-title");
  const noteText = q<HTMLParagraphElement>(note, ".flt-note-text");
  const noteActions = q<HTMLDivElement>(note, ".flt-note-actions");
  const playBtn = root.querySelector<HTMLButtonElement>(".bk-play");

  let view: View | null = null;
  let frames: FramesResponse | null = null;
  let mode: Mode = "none";
  let frame = DEFAULT_FRAME;
  let showSat = true;
  let reader: FieldReader | null = null;
  type Shown =
    | { kind: "colour"; preset: Preset; pic: ColourPicture; check: TargetCheck }
    | { kind: "wave"; pic: WavelengthPicture; check: TargetCheck };
  let picture: Shown | null = null;
  let ctl: AbortController | null = null;
  let readout = "Point at the picture to see how much light is there in each colour.";
  const infos = new Map<number, Promise<TileInfo>>();
  /** The blink was playing when the filter went on: play it again when the filter goes off. */
  let pausedBlink = false;
  let slowTimer: ReturnType<typeof setTimeout> | undefined;
  let slideTimer: ReturnType<typeof setTimeout> | undefined;

  const centre = (): SkyPos => frames?.center ?? (view ? { ra: view.ra, dec: view.dec } : { ra: 0, dec: 0 });
  const plateField = (): number => (view ? blinkFieldArcmin(view, frames) : 5);
  const field = (): number => filterField(plateField());

  /**
   * The note over the plate: a read in progress (with a way back to the blink), or a failure in
   * plain words with Try again and Back to the blink. null hides it.
   */
  type Note = { kind: "info"; text: string; slow?: boolean } | { kind: "fail"; title: string; text: string };
  function say(n: Note | null) {
    // Focus never falls to the page when the note's buttons go away.
    const focused = note.contains(document.activeElement);
    note.hidden = n == null;
    if (!n) {
      if (focused) addBtn.focus();
      return;
    }
    note.dataset.kind = n.kind;
    noteTitle.textContent = n.kind === "fail" ? n.title : "";
    noteTitle.hidden = n.kind !== "fail";
    noteText.textContent =
      n.kind === "fail" ? n.text : n.slow ? `${n.text} NASA's archive is slow right now; still trying.` : n.text;
    const actions = n.kind === "fail"
      ? html`<button class="btn btn-primary" type="button" data-flt-retry>Try again</button>
          <button class="btn" type="button" data-flt-back>Back to the blink</button>`
      : html`<button class="btn btn-ghost" type="button" data-flt-back>Back to the blink</button>`;
    if (noteActions.dataset.kind !== n.kind) {
      noteActions.innerHTML = actions;
      noteActions.dataset.kind = n.kind;
      if (focused) noteActions.querySelector<HTMLButtonElement>("button")?.focus();
    }
  }

  function reading(text: string) {
    say({ kind: "info", text });
    clearTimeout(slowTimer);
    slowTimer = setTimeout(() => say({ kind: "info", text, slow: true }), SLOW_MS);
  }

  function openMenu(open: boolean, focusButton = false) {
    menu.hidden = !open;
    addBtn.setAttribute("aria-expanded", String(open));
    if (open) placeMenu();
    if (!open && focusButton) addBtn.focus();
  }

  /** Beside the plate when there is room (like the blink's Adjust), else dropped over the plate. */
  function placeMenu() {
    if (menu.hidden) return;
    const room = document.documentElement.clientWidth - col!.getBoundingClientRect().right - 16;
    menu.classList.toggle("is-right", root!.clientWidth >= 600 && room >= 436);
  }

  /** Pause the blink under a filter, with its own Play button; play it again afterwards. */
  function holdBlink(on: boolean) {
    if (!playBtn) return;
    const playing = playBtn.getAttribute("aria-pressed") === "true";
    if (on && playing) {
      playBtn.click();
      pausedBlink = true;
    } else if (!on && pausedBlink) {
      if (!playing) playBtn.click();
      pausedBlink = false;
    }
  }

  /** Clear the picture, so a stale one is never shown under a new label or after a failure. */
  function clearPicture() {
    picture = null;
    canvas.width = 0;
    canvas.height = 0;
    overlay.innerHTML = "";
  }

  /** Back to the blink: the filter off, the read stopped, focus on the button. */
  function removeFilter() {
    mode = "none";
    ctl?.abort();
    clearTimeout(slowTimer);
    clearTimeout(slideTimer);
    say(null);
    press();
    openMenu(false);
    addBtn.focus();
  }

  function modeName(): string {
    if (mode === "wave") return `One colour of light, ${CUBE_UM[frame].toFixed(2)} micrometres`;
    return PRESETS.find((p) => p.id === mode)?.label ?? "";
  }

  function press() {
    bar.querySelectorAll<HTMLButtonElement>("[data-flt-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.fltMode === mode)));
    waveBox.hidden = mode !== "wave";
    satBtn.hidden = mode === "wave";
    const on = mode !== "none";
    layer.hidden = !on;
    key.hidden = !on;
    active.hidden = !on;
    root!.classList.toggle("flt-on", on);
    holdBlink(on);
    addIcon.textContent = on ? "✎" : "＋";
    addText.textContent = on ? "Change filter" : "Add filters";
    activeName.textContent = on ? `Filter: ${modeName()}` : "";
    canvas.setAttribute("aria-label", on ? `The target's field with the filter ${modeName()}, from NASA's SPHEREx map` : "Filtered picture");
    frameIn.value = String(frame);
    waveText.textContent = frameText(frame);
    frameIn.setAttribute("aria-valuetext", frameText(frame));
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
    clearTimeout(slideTimer);
    if (mode === "none" || !view) return;
    ctl = new AbortController();
    const signal = ctl.signal;
    const r = ensureReader();
    const progress = (done: number, total: number) => {
      if (!signal.aborted && total) reading(`Reading from NASA's SPHEREx map: ${done} of ${total}…`);
    };
    reading("Reading from NASA's SPHEREx map…");
    key.hidden = false;
    try {
      if (mode === "wave") {
        const pic = await r.wavelength(frame, signal, progress);
        if (signal.aborted) return;
        const mid = Math.round((pic.plan.n - 1) / 2);
        picture = { kind: "wave", pic, check: check(pic.plan.n, null, [], pic.dates, { holes: coreHoles(pic.values, pic.plan.n, mid) }) };
      } else {
        const preset = PRESETS.find((p) => p.id === mode)!;
        const pic = await r.colour(preset, signal, progress);
        if (signal.aborted) return;
        const n = pic.plan.n;
        const mid = Math.round((n - 1) / 2);
        const blend = blendPeaks(pic.b, pic.r, n, mid)
          ? { shortUm: (preset.blue.lo + preset.blue.hi) / 2, longUm: (preset.red.lo + preset.red.hi) / 2 }
          : null;
        picture = { kind: "colour", preset, pic, check: check(n, pic.saturated, pic.byWindow, pic.dates, { blend }) };
      }
      clearTimeout(slowTimer);
      say(null);
      draw();
    } catch (e) {
      if (signal.aborted) return;
      // Stop the rest of this read: IRSA is struggling, don't keep asking.
      ctl?.abort();
      clearTimeout(slowTimer);
      clearPicture();
      // The key describes a picture; with none, the note on the plate says it all.
      key.hidden = true;
      say({ kind: "fail", ...describeFailure(e) });
    }
  }

  const knee = () => 10 ** Number(kneeIn.value);

  function draw() {
    if (!picture) return;
    const n = picture.pic.plan.n;
    const rgba =
      picture.kind === "colour"
        ? compose(picture.pic.r, picture.pic.g, picture.pic.b, knee()).rgba
        : greyImage(picture.pic.values, picture.pic.scale, knee());
    canvas.width = n;
    canvas.height = n;
    canvas.getContext("2d")?.putImageData(new ImageData(rgba, n, n), 0, 0);
    drawOverlay();
    renderKey();
  }

  function drawOverlay() {
    if (!picture) return;
    const n = picture.pic.plan.n;
    overlay.setAttribute("viewBox", `0 0 ${n} ${n}`);
    const parts: string[] = [];
    if (showSat && picture.kind === "colour") {
      let d = "";
      const s = picture.pic.saturated;
      for (let k = 0; k < s.length; k++) if (s[k]) d += `M${k % n},${Math.floor(k / n)}h1v1h-1z`;
      if (d) parts.push(`<path class="ov-sat" d="${d}" />`);
    }
    const mid = n / 2;
    const side = (plateField() * 60) / picture.pic.plan.pixelArcsec;
    if (side < n - 1) {
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
    const beg = p?.pic.dates.beg?.slice(0, 10);
    const end = p?.pic.dates.end?.slice(0, 10);
    const when = beg && end ? `between ${beg} and ${end}` : "over about a year";
    // The dashed square is drawn only when the normal view fits inside the picture (drawOverlay).
    const box =
      plateField() < field() * 0.98
        ? html`<li><i class="sw sw-box"></i><b>Dashed square</b><span>the area of the normal view</span></li>`
        : html`<li><i class="sw sw-wide"></i><b>Middle part</b><span>the normal view is wider: this picture
            shows its middle ${field()} arcminutes</span></li>`;
    let title = "";
    let entries = "";
    let lead = "";
    if (mode === "wave") {
      title = esc(frameText(frame));
      entries = html`<li><i class="sw sw-ramp"></i><b>White to black</b><span>bright to faint in this colour of
        light. If a star fades as you move the slider, it really is fainter there: a star that goes dark near
        3.3 micrometres may contain methane gas.</span></li>
        <li><i class="sw sw-none"></i><b>Striped</b><span>no data here</span></li>
        ${box}`;
    } else if (mode !== "none") {
      const preset = PRESETS.find((x) => x.id === mode)!;
      title = `${esc(preset.label)}: red, green and blue show three colours of infrared light, invisible to our eyes (${esc(preset.red.plain)}, ${esc(preset.green.plain)} and ${esc(preset.blue.plain)})`;
      entries = KEYS[mode]
        .map((e) => {
          const sw =
            e.swatch === "colour" && e.rgb
              ? `<i class="sw" style="background:${swatchColour(e.rgb)}"></i>`
              : `<i class="sw sw-${e.swatch}"></i>`;
          return `<li>${sw}<b>${esc(e.label)}</b><span>${esc(e.examples)}</span></li>`;
        })
        .join("");
      entries += box;
      if (mode === "cold") {
        lead = html`<p class="flt-key-lead">Pink or red only says the light gets stronger towards 4.6 micrometres.
          Cold brown dwarfs do that, but so do dust and distant galaxies: a red dot is something to check, not a
          discovery.</p>`;
      }
    }
    key.innerHTML = html`
      <p class="flt-key-title">${title}</p>
      ${lead}
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
          ${when}, so the colours of things that move or change brightness can be wrong. A spot only gets a
          colour when it is clearly brighter than the background; otherwise it stays grey. The method and the
          tests behind it are in docs/colour-layers.md.</p>
      </details>
      <p class="flt-key-note">SkyShift's marks (what it measured, Gaia stars, known asteroids) belong to the
        normal view, so they are hidden while a filter is on.</p>
    `;
  }

  function onPoint(ev: PointerEvent) {
    if (!picture) return;
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
  // The blink handles clicks on any button inside its root by data attribute (data-mode, data-retry,
  // …). The filters' clicks are their own: they used to switch the blink into mode "cold" or "none".
  // Attributes are prefixed data-flt-, and the button clicks stay here.
  for (const part of [bar, noteActions]) part.addEventListener("click", (ev) => ev.stopPropagation());
  addBtn.addEventListener("click", () => openMenu(menu.hidden));
  q<HTMLButtonElement>(bar, ".flt-off").addEventListener("click", removeFilter);
  noteActions.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest("button");
    if (b?.hasAttribute("data-flt-retry")) void render();
    else if (b?.hasAttribute("data-flt-back")) removeFilter();
  });
  bar.querySelectorAll<HTMLButtonElement>("[data-flt-mode]").forEach((b) =>
    b.addEventListener("click", () => {
      const next = b.dataset.fltMode as Mode;
      if (next === "none") {
        removeFilter();
        return;
      }
      if (next !== mode) {
        mode = next;
        readout = "Point at the picture to see how much light is there in each colour.";
        // A picture of another filter is never shown under this one's name, even while loading.
        if (picture && (picture.kind === "wave" ? mode !== "wave" : picture.preset.id !== mode)) clearPicture();
        press();
        renderKey();
        void render();
      }
      // The menu closes; "One colour of light" hands the keyboard to its slider above the plate.
      openMenu(false, mode !== "wave");
      if (mode === "wave") frameIn.focus();
    }),
  );
  frameIn.addEventListener("input", () => {
    frame = skipBlank(Number(frameIn.value), frame);
    press();
    // Read once the slider rests, not at every step of a drag (each step was a cancelled read).
    clearTimeout(slideTimer);
    slideTimer = setTimeout(() => void render(), SLIDE_MS);
  });
  kneeIn.addEventListener("input", () => draw());
  q<HTMLButtonElement>(bar, "[data-flt-sat]").addEventListener("click", (ev) => {
    showSat = !showSat;
    (ev.currentTarget as HTMLButtonElement).setAttribute("aria-pressed", String(showSat));
    drawOverlay();
  });
  const onDoc = (ev: MouseEvent) => {
    if (!menu.hidden && !bar.contains(ev.target as Node)) openMenu(false);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape" && !menu.hidden) openMenu(false, true);
  };
  document.addEventListener("click", onDoc);
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", placeMenu);
  // The blink starts playing by itself when it first loads: keep it paused under a filter.
  const playWatch = playBtn ? new MutationObserver(() => mode !== "none" && holdBlink(true)) : null;
  if (playBtn) playWatch?.observe(playBtn, { attributes: true, attributeFilter: ["aria-pressed"] });
  canvas.addEventListener("pointermove", onPoint);
  canvas.addEventListener("pointerdown", onPoint);

  press();

  return {
    goto(v, f) {
      view = v;
      frames = f;
      if (mode !== "none") void render();
    },
    loading(v) {
      const moved = !view || v.ra !== view.ra || v.dec !== view.dec;
      view = v;
      if (!moved) return;
      frames = null;
      ctl?.abort();
      clearTimeout(slowTimer);
      clearTimeout(slideTimer);
      clearPicture();
      if (mode !== "none") {
        say({ kind: "info", text: "Waiting for the new spot to load…" });
        renderKey();
      }
    },
    destroy() {
      ctl?.abort();
      clearTimeout(slowTimer);
      clearTimeout(slideTimer);
      playWatch?.disconnect();
      holdBlink(false);
      document.removeEventListener("click", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", placeMenu);
      bar.remove();
      layer.remove();
      key.remove();
      root.classList.remove("flt-on");
      col.classList.remove("has-flt");
    },
  };
}
