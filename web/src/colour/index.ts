// Filters on the main image (layers A1 and A2). An "Add filters" button above the plate opens a
// menu; the chosen filter replaces the picture on the plate, same field and orientation (north up,
// east left), and a key under the plate says what each colour means. Nothing downloads until a
// filter is chosen. Algorithms and their checks: rgb.ts, engine.ts, docs/colour-layers.md.
//
// The blink's own DOM (P4) is not edited: this module adds its button, layer and key around the
// plate from outside, and the plate shows through again when the filter is removed.
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
import { KEYS, swatchColour } from "./legend";
import { compose, greyImage, type Channel } from "./rgb";
import { SATURATED_READOUT, whyUnreliable, type TargetCheck } from "./why";

const html = String.raw;
type Mode = "none" | PresetId | "wave";
/** Default wavelength: the 3.3 µm methane band, where the coldest brown dwarfs go dark. */
const DEFAULT_FRAME = 62;

export interface FiltersHandle {
  goto(view: View, frames: FramesResponse | null): void;
  destroy(): void;
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]!);
}

function fmt(v: number): string {
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 1 ? v.toFixed(2) : v.toPrecision(2);
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
  if (!col || !mat || !plate || !root) return { goto() {}, destroy() {} };

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
      <p class="flt-h">Show the plate as</p>
      <div class="flt-opts">
        <button type="button" data-mode="none" aria-pressed="true"><b>No filter</b>
          <span>The blink, as it was</span></button>
        <button type="button" data-mode="cold" aria-pressed="false"><b>Cold bodies</b>
          <span>4.6, 2.2 and 1.2 µm as red, green and blue. Cold brown dwarfs stand out red or magenta.</span></button>
        <button type="button" data-mode="nir" aria-pressed="false"><b>2MASS-like</b>
          <span>2.2, 1.65 and 1.2 µm, the bands of the 2MASS survey, for comparison.</span></button>
        <button type="button" data-mode="wave" aria-pressed="false"><b>Single wavelength</b>
          <span>Any of SPHEREx's 102 wavelengths: see where molecules absorb.</span></button>
      </div>
      <div class="flt-wave" hidden>
        <input class="flt-frame" type="range" min="0" max="${CUBE_UM.length - 1}" step="1" value="${DEFAULT_FRAME}"
          aria-label="Wavelength, all 102 SPHEREx frames" />
        <div class="flt-ticks" aria-hidden="true">
          ${FEATURES.map(
            (f) => html`<span class="flt-tick" style="left:${(100 * nearestFrame(f.um)) / (CUBE_UM.length - 1)}%"
              title="${f.um} µm ${f.name}: ${f.what}"></span>`,
          ).join("")}
          <span class="flt-tick flt-tick-gap" style="left:${(100 * 50.5) / (CUBE_UM.length - 1)}%"
            title="2.40–2.45 µm: no data (dichroic)"></span>
        </div>
        <p class="flt-wave-text" aria-live="polite"></p>
      </div>
      <div class="flt-row">
        <button class="flt-switch" type="button" data-sat aria-pressed="true">Mark saturated stars</button>
        <label class="flt-range"><span>Stretch</span>
          <input type="range" min="-1" max="1.3" step="0.05" value="0" data-knee
            aria-label="Stretch: where brightness turns from linear to compressed" /></label>
      </div>
      <p class="flt-src">Filters use NASA's SPHEREx all-sky map (DR1): 12.9″ pixels, coarser than the blink,
        with all dates merged into one picture.</p>
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
    | { kind: "wave"; pic: WavelengthPicture; check: TargetCheck };
  let picture: Shown | null = null;
  let ctl: AbortController | null = null;
  let readout = "Point at the picture to read its values.";
  const infos = new Map<number, Promise<TileInfo>>();

  const centre = (): SkyPos => frames?.center ?? (view ? { ra: view.ra, dec: view.dec } : { ra: 0, dec: 0 });
  const field = (): number => (view ? blinkFieldArcmin(view, frames) : 5);

  function say(text: string | null, kind: "info" | "fail" = "info") {
    note.hidden = text == null;
    note.dataset.kind = kind;
    note.textContent = text ?? "";
  }

  function openMenu(open: boolean) {
    menu.hidden = !open;
    addBtn.setAttribute("aria-expanded", String(open));
  }

  function modeName(): string {
    if (mode === "wave") return `${CUBE_UM[frame].toFixed(2)} µm`;
    return PRESETS.find((p) => p.id === mode)?.label ?? "";
  }

  function press() {
    bar.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    waveBox.hidden = mode !== "wave";
    satBtn.hidden = mode === "wave";
    const on = mode !== "none";
    layer.hidden = !on;
    key.hidden = !on;
    active.hidden = !on;
    root!.classList.toggle("flt-on", on);
    addIcon.textContent = on ? "✎" : "＋";
    addText.textContent = on ? "Change filter" : "Add filters";
    activeName.textContent = on ? `Filter: ${modeName()}` : "";
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
    if (mode === "none" || !view) return;
    ctl = new AbortController();
    const signal = ctl.signal;
    const r = ensureReader();
    const progress = (done: number, total: number) => {
      if (!signal.aborted && total) say(`Reading from NASA's SPHEREx map: ${done} of ${total}…`);
    };
    say("Reading from NASA's SPHEREx map…");
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
    const t = Math.max(1.2, n / 30);
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
    if (mode === "wave") {
      title = esc(frameText(frame));
      entries = html`<li><i class="sw sw-ramp"></i><b>White to black</b><span>bright to faint at this wavelength.
        The brightness scale is fixed for this field (set at 2.0–2.3 µm), so a star that gets fainter as you move
        the slider really is fainter there: a dark spot at 3.3 µm can be methane.</span></li>
        <li><i class="sw sw-none"></i><b>Hatched</b><span>NASA's map has no data there</span></li>`;
    } else if (mode !== "none") {
      const preset = PRESETS.find((x) => x.id === mode)!;
      title = `${esc(preset.label)}: red ${esc(preset.red.name)}, green ${esc(preset.green.name)}, blue ${esc(preset.blue.name)}`;
      entries = KEYS[mode]
        .map((e) => {
          const sw =
            e.swatch === "colour" && e.rgb
              ? `<i class="sw" style="background:${swatchColour(e.rgb)}"></i>`
              : `<i class="sw sw-${e.swatch}"></i>`;
          return `<li>${sw}<b>${esc(e.label)}</b><span>${esc(e.examples)}</span></li>`;
        })
        .join("");
    }
    key.innerHTML = html`
      <p class="flt-key-title">${title}</p>
      <ul class="flt-key-list">${entries}</ul>
      <p class="flt-readout" aria-live="polite">${esc(readout)}</p>
      ${reasons.length
        ? html`<section class="flt-why" role="note">
            <h3>${p?.kind === "wave" ? `Why this picture of ${who} isn't reliable here` : `Why the colour of ${who} isn't reliable here`}</h3>
            ${reasons.map((x) => `<p><strong>${esc(x.title)}.</strong> ${esc(x.text)}</p>`).join("")}
          </section>`
        : ""}
      <details class="flt-how">
        <summary>How this picture is made</summary>
        <p>From NASA's SPHEREx all-sky map (DR1), read live from NASA IRSA. Pixels are
          ${p ? p.pic.plan.pixelArcsec.toFixed(1) : "13"}″, coarser than the blink, so close stars blend. Each
          wavelength was observed on a different date between ${date(p?.pic.dates.beg ?? null)} and
          ${date(p?.pic.dates.end ?? null)}, so the colours of things that move or change brightness are not
          reliable. Each colour is the median of the map's frames in its window, which removes single-frame
          glitches, and a pixel gets colour only when one window is clearly detected. The picture shows the
          whole field: the blink's zoom doesn't apply. The method and the checks behind it are in
          docs/colour-layers.md.</p>
      </details>
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
    const part = (name: string, c: Channel) =>
      Number.isFinite(c.values[k]) ? `${name} ${fmt(c.values[k])} MJy/sr (${(c.values[k] / c.stats.sigma).toFixed(1)}σ)` : `${name} no data`;
    if (picture.kind === "colour") {
      const { r, g, b, saturated } = picture.pic;
      readout = `${pos} · ${part("R", r)} · ${part("G", g)} · ${part("B", b)}${saturated[k] ? ` · ${SATURATED_READOUT}` : ""}`;
    } else {
      const v = picture.pic.values[k];
      readout = Number.isFinite(v) ? `${pos} · ${fmt(v)} MJy/sr` : `${pos} · no data`;
    }
    const el = key.querySelector(".flt-readout");
    if (el) el.textContent = readout;
  }

  // ---- events ------------------------------------------------------------------------------
  addBtn.addEventListener("click", () => openMenu(menu.hidden));
  q<HTMLButtonElement>(bar, ".flt-off").addEventListener("click", () => {
    mode = "none";
    ctl?.abort();
    press();
  });
  bar.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach((b) =>
    b.addEventListener("click", () => {
      const next = b.dataset.mode as Mode;
      if (next !== mode) {
        mode = next;
        readout = "Point at the picture to read its values.";
        press();
        if (mode === "none") ctl?.abort();
        else void render();
      }
      if (mode !== "wave") openMenu(false);
    }),
  );
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
      if (mode !== "none") void render();
    },
    destroy() {
      ctl?.abort();
      document.removeEventListener("click", onDoc);
      document.removeEventListener("keydown", onKey);
      bar.remove();
      layer.remove();
      key.remove();
      root.classList.remove("flt-on");
    },
  };
}
