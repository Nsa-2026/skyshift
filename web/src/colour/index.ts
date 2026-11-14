// Colour view (layer A2): the plate's spot as a false-colour picture from three SPHEREx wavelength
// windows, read straight from NASA IRSA's all-sky cube. Algorithm and its checks: rgb.ts, and
// docs/colour-layers.md. Closed by default: nothing downloads until the person opens it.
import "./colour.css";
import type { FramesResponse, SkyPos } from "../api/types";
import { formatRaDec, pixelToSky } from "../app/astro";
import { targetBySlug } from "../app/state";
import type { View } from "../app/modules";
import { blinkFieldArcmin } from "../sky/overlay";
import { PRESETS, framesIn, type Preset } from "./bands";
import { planField, pool, readTileInfo, sampleInt, samplePlane, type FieldPlan, type Fetcher, type TileInfo } from "./cube";
import {
  FULL_COLOUR_SIGMA,
  GREY_BELOW_SIGMA,
  SATURATED_MIN_PLANES,
  compose,
  deficitPlanes,
  medianPlanes,
  saturatedMask,
  toChannel,
  type Channel,
} from "./rgb";
import { SATURATED_READOUT, whyUnreliable, type TargetCheck } from "./why";

const html = String.raw;
const FIELDS = [15, 30, 60] as const;
type Field = (typeof FIELDS)[number];
/** Requests in flight at once. */
const PARALLEL = 8;

export interface ColourHandle {
  goto(view: View, frames: FramesResponse | null): void;
  destroy(): void;
}

interface Loaded {
  key: string;
  plan: FieldPlan;
  center: SkyPos;
  r: Channel;
  g: Channel;
  b: Channel;
  saturated: Uint8Array;
  dates: { beg: string | null; end: string | null };
  /** What we measured at the centre, for "why the colour isn't reliable". */
  check: TargetCheck;
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

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]!);
}

function defaultField(view: View): Field {
  return view.fov >= 60 ? 60 : view.fov >= 20 ? 30 : 15;
}

function fmt(v: number): string {
  if (!Number.isFinite(v)) return "no data";
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 1 ? v.toFixed(2) : v.toPrecision(2);
}

export function mountColour(el: HTMLElement, fetcher: Fetcher = (u, i) => fetch(u, i)): ColourHandle {
  el.classList.add("colour-panel");
  el.innerHTML = html`
    <header class="colour-head">
      <h2>Colour from SPHEREx's 102 wavelengths</h2>
      <p class="colour-explain">Three wavelength windows of NASA's SPHEREx all-sky cube shown as red, green and
        blue. White means equally bright in all three; the hue is the slope of the object's spectrum.</p>
    </header>
    <button class="btn colour-toggle" type="button" aria-expanded="false" aria-controls="colour-body">
      <span class="colour-closed">Show the colour view</span><span class="colour-open">Hide the colour view</span>
    </button>
    <div class="colour-body" id="colour-body" hidden>
      <div class="colour-controls" role="group" aria-label="Colour view settings">
        <div class="seg" role="group" aria-label="Wavelength set">
          ${PRESETS.map(
            (p, i) => html`<button type="button" data-preset="${p.id}" aria-pressed="${i === 0}"
              title="Red ${p.red.name}, green ${p.green.name}, blue ${p.blue.name}">${p.label}</button>`,
          ).join("")}
        </div>
        <div class="seg" role="group" aria-label="Field size">
          ${FIELDS.map((f) => html`<button type="button" data-field="${f}" aria-pressed="false">${f}′</button>`).join("")}
        </div>
        <label class="colour-range"><span>Stretch</span>
          <input type="range" min="-1" max="1.3" step="0.05" value="0" data-knee
            aria-label="Stretch: where brightness turns from linear to compressed" /></label>
        <button class="colour-switch" type="button" data-sat aria-pressed="true">Saturated marks</button>
        <button class="colour-switch" type="button" data-box aria-pressed="true">Plate field</button>
      </div>
      <div class="colour-mat">
        <div class="colour-stage" data-stage>
          <canvas class="colour-canvas" data-canvas aria-label="False-colour picture of the field"></canvas>
          <svg class="colour-overlay" data-overlay aria-hidden="true"></svg>
          <span class="colour-compass" aria-hidden="true">N ↑ E ←</span>
          <div class="colour-note" data-note role="status" hidden></div>
        </div>
      </div>
      <p class="colour-readout" data-readout aria-live="polite">Point at the picture to read the three values.</p>
      <div class="colour-legend" data-legend></div>
    </div>
  `;

  const q = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const toggle = q<HTMLButtonElement>(".colour-toggle");
  const body = q<HTMLDivElement>(".colour-body");
  const stage = q<HTMLDivElement>("[data-stage]");
  const canvas = q<HTMLCanvasElement>("[data-canvas]");
  const overlay = q<SVGSVGElement>("[data-overlay]");
  const note = q<HTMLDivElement>("[data-note]");
  const readout = q<HTMLParagraphElement>("[data-readout]");
  const legend = q<HTMLDivElement>("[data-legend]");
  const kneeIn = q<HTMLInputElement>("[data-knee]");

  let view: View | null = null;
  let frames: FramesResponse | null = null;
  let preset: Preset = PRESETS[0];
  let field: Field | null = null; // null: follow the plate
  let showSat = true;
  let showBox = true;
  let open = false;
  let loaded: Loaded | null = null;
  let ctl: AbortController | null = null;
  let destroyed = false;

  // Planes already read for the current field, by cube frame (presets share windows).
  let planeKey = "";
  // Only finished reads are reused: an aborted or failed read is dropped and read again.
  const imageCache = new Map<number, Float32Array>();
  const hitsCache = new Map<number, Int32Array>();
  const infoCache = new Map<number, Promise<TileInfo>>();

  const currentField = (): Field => field ?? (view ? defaultField(view) : 15);
  const centre = (): SkyPos => frames?.center ?? (view ? { ra: view.ra, dec: view.dec } : { ra: 0, dec: 0 });

  function say(text: string | null, kind: "info" | "fail" = "info") {
    note.hidden = text == null;
    note.dataset.kind = kind;
    note.textContent = text ?? "";
  }

  function pressAll() {
    el.querySelectorAll<HTMLButtonElement>("[data-preset]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.preset === preset.id)),
    );
    el.querySelectorAll<HTMLButtonElement>("[data-field]").forEach((b) =>
      b.setAttribute("aria-pressed", String(Number(b.dataset.field) === currentField())),
    );
  }

  async function load() {
    if (!open || !view || destroyed) return;
    const c = centre();
    const fov = currentField();
    const key = `${c.ra.toFixed(6)}|${c.dec.toFixed(6)}|${fov}|${preset.id}`;
    if (loaded?.key === key) return;
    ctl?.abort();
    ctl = new AbortController();
    const signal = ctl.signal;
    const fieldKey = `${c.ra.toFixed(6)}|${c.dec.toFixed(6)}|${fov}`;
    if (fieldKey !== planeKey) {
      planeKey = fieldKey;
      imageCache.clear();
      hitsCache.clear();
    }
    const windows = [preset.red, preset.green, preset.blue];
    const wanted = windows.map(framesIn);
    const all = [...new Set(wanted.flat())];
    let done = 0;
    const total = 2 * all.length;
    say(`Reading ${all.length} wavelength planes from NASA IRSA…`);
    try {
      const info = (t: number) => {
        if (!infoCache.has(t)) {
          const p = readTileInfo(t, fetcher, signal);
          p.catch(() => infoCache.delete(t));
          infoCache.set(t, p);
        }
        return infoCache.get(t)!;
      };
      const plan = await planField(
        fov,
        (x, y, n, s) => pixelToSky({ x, y }, { pixel_arcsec: s, size: [n, n] }, c),
        info,
      );
      if (signal.aborted) return;
      const tick = () => {
        done++;
        if (!signal.aborted) say(`Reading wavelength planes from NASA IRSA: ${done} of ${total}…`);
      };
      const jobs: (() => Promise<unknown>)[] = [];
      for (const f of all) {
        if (!imageCache.has(f)) {
          jobs.push(async () => {
            const v = await samplePlane(plan, f, fetcher, signal);
            if (!signal.aborted && planeKey === fieldKey) imageCache.set(f, v);
            tick();
          });
        } else done++;
        if (!hitsCache.has(f)) {
          jobs.push(async () => {
            const v = await sampleInt(plan, "NHITS", f, fetcher, signal);
            if (!signal.aborted && planeKey === fieldKey) hitsCache.set(f, v);
            tick();
          });
        } else done++;
      }
      await pool(jobs, PARALLEL);
      if (signal.aborted) return;
      const chans = wanted.map((fs) => toChannel(medianPlanes(fs.map((f) => imageCache.get(f)!))));
      const hits = all.map((f) => hitsCache.get(f)!);
      const [r, g, b] = chans;
      const sig = Math.sqrt(r.stats.sigma ** 2 + g.stats.sigma ** 2 + b.stats.sigma ** 2) / 3;
      const deficits = deficitPlanes(hits, plan.n);
      const saturated = saturatedMask(deficits, r, g, b, sig, plan.n);
      const infos = plan.tiles.map((t) => t.info);
      const begs = infos.map((i) => i.dateBeg).filter((d): d is string => !!d).sort();
      const ends = infos.map((i) => i.dateEnd).filter((d): d is string => !!d).sort();
      const dates = { beg: begs[0] ?? null, end: ends.at(-1) ?? null };
      // Hits per colour window, shortest wavelength first (blue, green, red).
      const byWindow = [wanted[2], wanted[1], wanted[0]].map((fs, i) => ({
        um: [preset.blue, preset.green, preset.red].map((w) => (w.lo + w.hi) / 2)[i],
        hits: fs.map((f) => hitsCache.get(f)!),
      }));
      loaded = { key, plan, center: c, r, g, b, saturated, dates, check: centreCheck(plan.n, saturated, byWindow, dates) };
      say(null);
      draw();
    } catch (e) {
      if (signal.aborted) return;
      imageCache.clear();
      hitsCache.clear();
      loaded = null;
      const why = e instanceof Error ? e.message : String(e);
      say(`NASA IRSA's SPHEREx cube didn't answer (${why}). The plate above works without it.`, "fail");
    }
  }

  /** What the data says at the plate's centre (within 2 pixels), plus what we know of the target. */
  function centreCheck(
    n: number,
    saturated: Uint8Array,
    byWindow: { um: number; hits: Int32Array[] }[],
    dates: TargetCheck["dates"],
  ): TargetCheck {
    const mid = Math.round((n - 1) / 2);
    let sat = false;
    for (let dj = -2; dj <= 2; dj++) {
      for (let di = -2; di <= 2; di++) if (saturated[(mid + dj) * n + mid + di]) sat = true;
    }
    const kind = targetBySlug(view?.slug)?.kind ?? null;
    return {
      name: view?.name ?? null,
      kind,
      saturated: sat,
      kept: byWindow.map((w) => ({ um: w.um, share: keptShare(w.hits, n, mid) })),
      moving: !!frames?.epochs.some((e) => e.center) || /asteroid|comet/i.test(kind ?? ""),
      dates,
    };
  }

  function knee(): number {
    return 10 ** Number(kneeIn.value);
  }

  function draw() {
    if (!loaded) return;
    const { plan, r, g, b } = loaded;
    const n = plan.n;
    const comp = compose(r, g, b, knee());
    canvas.width = n;
    canvas.height = n;
    const ctx = canvas.getContext("2d");
    if (ctx) ctx.putImageData(new ImageData(comp.rgba, n, n), 0, 0);
    drawOverlay();
    renderLegend();
  }

  function drawOverlay() {
    if (!loaded || !view) return;
    const { plan, saturated } = loaded;
    const n = plan.n;
    overlay.setAttribute("viewBox", `0 0 ${n} ${n}`);
    const parts: string[] = [];
    const mid = n / 2;
    if (showSat) {
      let d = "";
      for (let k = 0; k < saturated.length; k++) {
        if (saturated[k]) d += `M${k % n},${Math.floor(k / n)}h1v1h-1z`;
      }
      if (d) parts.push(`<path class="ov-sat" d="${d}" />`);
    }
    if (showBox) {
      const side = (blinkFieldArcmin(view, frames) * 60) / plan.pixelArcsec;
      if (side < n) parts.push(`<rect class="ov-box" x="${mid - side / 2}" y="${mid - side / 2}" width="${side}" height="${side}" />`);
    }
    const t = Math.max(1.2, n / 40);
    const gap = t * 0.6;
    parts.push(
      `<path class="ov-target" d="M${mid - gap - t},${mid}h${t}M${mid + gap},${mid}h${t}M${mid},${mid - gap - t}v${t}M${mid},${mid + gap}v${t}" />`,
    );
    // 1′ scale bar, bottom right.
    const bar = 60 / plan.pixelArcsec;
    parts.push(`<path class="ov-scale" d="M${n - 1.5 - bar},${n - 1.5}h${bar}" /><text class="ov-scale-t" x="${n - 1.5 - bar / 2}" y="${n - 2.6}">1′</text>`);
    overlay.innerHTML = parts.join("");
  }

  function renderLegend() {
    if (!loaded) return;
    const p = preset;
    const reasons = whyUnreliable(loaded.check);
    const who = esc(loaded.check.name ?? "the object at the centre");
    const satCount = loaded.saturated.reduce((a, v) => a + v, 0);
    const date = (d: string | null) => (d ? d.slice(0, 10) : "?");
    legend.innerHTML = html`
      ${reasons.length
        ? html`<section class="colour-why" role="note">
            <h3>Why the colour of ${who} isn't reliable here</h3>
            ${reasons.map((x) => `<p><strong>${x.title}.</strong> ${esc(x.text)}</p>`).join("")}
          </section>`
        : ""}
      <ul class="colour-key">
        <li><i class="sw sw-r"></i>Red: ${p.red.name}</li>
        <li><i class="sw sw-g"></i>Green: ${p.green.name}</li>
        <li><i class="sw sw-b"></i>Blue: ${p.blue.name}</li>
      </ul>
      <ul class="colour-reads">
        ${p.reads.map((s) => `<li>${s}</li>`).join("")}
        <li><i class="sw sw-grey"></i>Grey: no band is bright enough to give a colour (the brightest of the three is below ${GREY_BELOW_SIGMA}σ in its own band; full colour from ${FULL_COLOUR_SIGMA}σ).</li>
        <li><i class="sw sw-none"></i>Hatched: the cube has no data there.</li>
        <li><i class="sw sw-sat"></i>Red squares: too bright. SPHEREx's pixels overfilled (saturated) and NASA's map
          left those readings out, so the colour there is not reliable. We spot it from the exposures missing in
          ≥ ${SATURATED_MIN_PLANES} wavelength frames${satCount ? "" : " (none in this field)"}.</li>
      </ul>
      <p class="colour-caveat">Each wavelength was seen on a different date between ${date(loaded.dates.beg)} and
        ${date(loaded.dates.end)}, so the colours of things that move or change brightness are not reliable.
        Pixels are ${loaded.plan.pixelArcsec.toFixed(1)}″, twice the plate's: close pairs blend.</p>
    `;
  }

  function onPoint(ev: PointerEvent) {
    if (!loaded) return;
    const rect = canvas.getBoundingClientRect();
    const n = loaded.plan.n;
    const i = Math.floor(((ev.clientX - rect.left) / rect.width) * n);
    const j = Math.floor(((ev.clientY - rect.top) / rect.height) * n);
    if (i < 0 || j < 0 || i >= n || j >= n) return;
    const k = j * n + i;
    const { r, g, b, saturated, plan, center } = loaded;
    const part = (name: string, c: Channel) =>
      Number.isFinite(c.values[k])
        ? `${name} ${fmt(c.values[k])} MJy/sr (${(c.values[k] / c.stats.sigma).toFixed(1)}σ)`
        : `${name} no data`;
    const pos = pixelToSky({ x: i, y: j }, { pixel_arcsec: plan.pixelArcsec, size: [n, n] }, center);
    readout.textContent =
      `${formatRaDec(pos)} · ${part("R", r)} · ${part("G", g)} · ${part("B", b)}` +
      (saturated[k] ? ` · ${SATURATED_READOUT}` : "");
  }

  // ---- events --------------------------------------------------------------------------
  toggle.addEventListener("click", () => {
    open = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.classList.toggle("is-open", open);
    body.hidden = !open;
    if (open) void load();
  });
  el.querySelectorAll<HTMLButtonElement>("[data-preset]").forEach((b) =>
    b.addEventListener("click", () => {
      const next = PRESETS.find((p) => p.id === b.dataset.preset);
      if (!next || next === preset) return;
      preset = next;
      pressAll();
      void load();
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-field]").forEach((b) =>
    b.addEventListener("click", () => {
      field = Number(b.dataset.field) as Field;
      pressAll();
      void load();
    }),
  );
  kneeIn.addEventListener("input", () => draw());
  q<HTMLButtonElement>("[data-sat]").addEventListener("click", (ev) => {
    showSat = !showSat;
    (ev.currentTarget as HTMLButtonElement).setAttribute("aria-pressed", String(showSat));
    drawOverlay();
  });
  q<HTMLButtonElement>("[data-box]").addEventListener("click", (ev) => {
    showBox = !showBox;
    (ev.currentTarget as HTMLButtonElement).setAttribute("aria-pressed", String(showBox));
    drawOverlay();
  });
  stage.addEventListener("pointermove", onPoint);
  stage.addEventListener("pointerdown", onPoint);

  pressAll();

  return {
    goto(v, f) {
      const moved = !view || v.ra !== view.ra || v.dec !== view.dec;
      view = v;
      frames = f;
      if (moved) field = null;
      pressAll();
      void load();
    },
    destroy() {
      destroyed = true;
      ctl?.abort();
      el.classList.remove("colour-panel");
      el.innerHTML = "";
    },
  };
}
