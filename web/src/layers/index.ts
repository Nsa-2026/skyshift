// Plate layers on the Blink page: Observed, Sky model, Residual, DR1 difference (layers.ts).
// A quiet row under the player's controls switches what the plate shows; the player itself (P4,
// web/src/blink) is not edited. This module mounts the player and hands the page a BlinkHandle
// that shows the chosen layer for every view the page loads:
// - The page still loads the observations (they feed the card, the sky view, the filters and the
//   marks). On a derived layer the handle swaps in that layer's frames before the player sees them.
// - The layer stays while the person changes target, detector or clock, as long as the new view
//   has it. Where it doesn't (any spot but the five featured stars, Hours, 15′), the plate goes
//   back to Observed and one line says why. A layer that fails to load does the same.
// - A newer view always wins: the page's "loading" status cancels a layer still loading, so a
//   picture never shows under another target's name.
// - A colour filter (web/src/colour) covers the plate: picking a layer removes the filter, so the
//   choice is seen.
// Idea and data: PR #214 (science workspace) by 春。 and Codex.
import "./layers.css";
import { loadFrames as loadScienceFrames, type Target } from "../api/science-client";
import type { FramesResponse } from "../api/types";
import type { BlinkHandle, BlinkOptions, MountBlink, View } from "../app/modules";
import { LAYERS, availability, isLayer, layerInfo, marksLine, qualityLine, withMarks, type Layer } from "./layers";

const html = String.raw;
let mounts = 0;
/** A layer still loading after this long shows the player's loading state. */
const SLOW_MS = 200;

/** What the science client needs to find a field's prepared products. */
function targetOf(v: View): Target {
  return { slug: v.slug ?? "", name: v.name ?? "", ra: v.ra, dec: v.dec, default_det: v.det, n_frames: 0, n_visits: 0, clock: v.clock, fov: v.fov };
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** A derived layer lives in the URL beside the view (?t=barnard&layer=resid), so it can be shared. */
const LAYER_PARAM = "layer";
function layerFromUrl(): Layer | null {
  const v = new URLSearchParams(location.search).get(LAYER_PARAM) ?? undefined;
  return isLayer(v) ? v : null;
}
/** Write the layer into the Blink page's URL (replace, not push: the view already made the entry). */
function layerToUrl(layer: Layer) {
  if (location.hash && !location.hash.startsWith("#/explore")) return;
  const p = new URLSearchParams(location.search);
  const want = layer === "raw" ? null : layer;
  if (p.get(LAYER_PARAM) === want) return;
  if (want) p.set(LAYER_PARAM, want);
  else p.delete(LAYER_PARAM);
  const qs = p.toString();
  history.replaceState(history.state, "", `${location.pathname}${qs ? `?${qs}` : ""}${location.hash}`);
}

export interface PlateLayersHandle extends BlinkHandle {
  /** The layer the plate shows (or is loading). */
  layer(): Layer;
}

export function mountPlateLayers(el: HTMLElement, mount: MountBlink, opts: BlinkOptions): PlateLayersHandle {
  const uid = `pl${++mounts}`;
  /** The shown epoch (the player's own, for the chosen detector), for the quality line. */
  let epochId: string | null = null;
  const inner = mount(el, {
    ...opts,
    onEpoch(e) {
      epochId = e.id;
      renderQuality();
      opts.onEpoch?.(e);
    },
  });

  const bar = document.createElement("div");
  bar.className = "pl";
  bar.hidden = true;
  bar.innerHTML = html`
    <div class="pl-row">
      <span class="pl-k" id="${uid}-k">Layer</span>
      <div class="seg pl-seg" role="group" aria-labelledby="${uid}-k">
        ${LAYERS.map(
          (l) => html`<button type="button" data-plate-layer="${l.id}" aria-pressed="${l.id === "raw"}"
            aria-describedby="${uid}-say">${l.label}</button>`,
        ).join("")}
      </div>
    </div>
    <p class="pl-say" id="${uid}-say" aria-live="polite"></p>
    <p class="pl-quality"></p>
    <p class="pl-pro pro-only"></p>
  `;
  // Under the player's controls (and its Adjust panel), above "What SkyShift measured".
  const after = el.querySelector(".bk-adjust") ?? el.querySelector(".bk-row");
  if (after) after.insertAdjacentElement("afterend", bar);
  else el.appendChild(bar);

  const say = bar.querySelector<HTMLParagraphElement>(".pl-say")!;
  const quality = bar.querySelector<HTMLParagraphElement>(".pl-quality")!;
  const pro = bar.querySelector<HTMLParagraphElement>(".pl-pro")!;
  const buttons = [...bar.querySelectorAll<HTMLButtonElement>("[data-plate-layer]")];

  let layer: Layer = layerFromUrl() ?? "raw";
  /** The page's last view and its observations, as handed to show(). null while the page loads a new one. */
  let last: { view: View; frames: FramesResponse; url: string } | null = null;
  /** The view the row describes (kept while the next one loads). */
  let shownView: View | null = null;
  /** What the player shows now: its frames and where they came from. */
  let shown: { layer: Layer; frames: FramesResponse | null; url: string; dropped: boolean } = {
    layer: "raw",
    frames: null,
    url: "",
    dropped: false,
  };
  /** A line that wins over the layer's own sentence: why the plate went back to Observed, or what's needed. */
  let note: { text: string; fix: { clock: "months"; fov: number } | null; want: Layer | null } | null = null;
  let gen = 0;
  let ctl: AbortController | null = null;
  let slowTimer: ReturnType<typeof setTimeout> | undefined;

  function cancel() {
    gen++;
    ctl?.abort();
    ctl = null;
    clearTimeout(slowTimer);
  }

  function render() {
    bar.hidden = !shownView;
    if (shownView) layerToUrl(layer);
    const av = shownView ? availability(shownView) : null;
    for (const b of buttons) {
      const id = b.dataset.plateLayer as Layer;
      const off = id !== "raw" && !!av && !av.ok;
      b.setAttribute("aria-pressed", String(id === layer));
      // aria-disabled, not disabled: a dimmed option still answers a click with the reason.
      b.setAttribute("aria-disabled", String(off));
      b.title = off && av && !av.ok ? av.why : "";
    }
    if (note) {
      say.dataset.kind = "note";
      say.innerHTML = "";
      say.append(note.text);
      if (note.fix) {
        const go = document.createElement("button");
        go.type = "button";
        go.className = "btn btn-ghost pl-fix";
        go.dataset.plateFix = "";
        const at = `${note.fix.fov}′ on Months`;
        go.textContent = note.want ? `Show the ${layerInfo(note.want).name} at ${at}` : `Switch to ${at}`;
        say.append(" ", go);
      }
    } else if (layer !== "raw") {
      say.dataset.kind = "layer";
      say.textContent = layerInfo(layer).says;
    } else {
      say.dataset.kind = "";
      say.textContent = "";
    }
    say.hidden = !say.textContent;
    renderQuality();
    renderPro();
  }

  /** Small print under the sentence: the shown frame's quality (Observed), or a derived layer's marks and limits. */
  function renderQuality() {
    const e = epochId && shown.frames ? shown.frames.epochs.find((x) => x.id === epochId) : null;
    const marks = marksLine(shown.layer, shown.dropped);
    quality.textContent = shown.frames ? `${marks} ${qualityLine(e, shown.layer)}`.trim() : "";
    quality.hidden = !quality.textContent;
  }

  /** Details on: where a derived layer's frames come from, for professionals. */
  function renderPro() {
    if (shown.layer === "raw" || !shown.frames || !shown.url) {
      pro.textContent = "";
      pro.hidden = true;
      return;
    }
    const path = new URL(shown.url, location.href);
    const [nx, ny] = shown.frames.size;
    pro.textContent =
      `${cap(layerInfo(shown.layer).name)} from the science workspace (contracts v3.8), ${path.pathname}${path.search}: ` +
      `${nx} × ${ny} pixels of ${shown.frames.pixel_arcsec}″, MJy/sr, raw 0 outside the analysis footprint. Not part of the scored pipeline.`;
    pro.hidden = false;
  }

  /** Show the chosen layer of the page's last view. */
  async function apply(): Promise<void> {
    if (!last) return;
    const { view, frames, url } = last;
    const av = availability(view);
    if (layer !== "raw" && !av.ok) {
      note = { text: `${av.why} The plate shows the observations.`, fix: av.fix, want: av.fix ? layer : null };
      layer = "raw";
    }
    if (layer === "raw") {
      shown = { layer: "raw", frames, url, dropped: false };
      render();
      return inner.show(view, frames, url);
    }
    const want = layer;
    const my = ++gen;
    ctl?.abort();
    ctl = new AbortController();
    const signal = ctl.signal;
    render();
    // The prepared files usually arrive at once: say "loading" only when they don't. (The player's
    // loading state also pauses it, and a blink that keeps playing across a layer switch reads better.)
    clearTimeout(slowTimer);
    slowTimer = setTimeout(() => my === gen && inner.status("loading", `Loading the ${layerInfo(want).name}…`), SLOW_MS);
    try {
      const { data, jsonUrl } = await loadScienceFrames(targetOf(view), view.det, view.clock, signal, want, view.fov);
      clearTimeout(slowTimer);
      if (my !== gen) return;
      if (!data.epochs.some((e) => e.det === view.det)) throw new Error(`No D${view.det} frames in the ${want} layer`);
      const marked = withMarks(data as unknown as FramesResponse, frames);
      shown = { layer: want, frames: marked.frames, url: jsonUrl, dropped: marked.dropped };
      render();
      await inner.show(view, marked.frames, jsonUrl);
    } catch {
      clearTimeout(slowTimer);
      if (my !== gen || signal.aborted) return;
      layer = "raw";
      note = { text: `The ${layerInfo(want).name} didn't load, so the plate shows the observations. Try it again in a minute.`, fix: null, want: null };
      shown = { layer: "raw", frames, url, dropped: false };
      render();
      await inner.show(view, frames, url);
    }
  }

  /** A filter (web/src/colour) covers the plate: take it off so the chosen layer is seen. */
  function removeFilter(keepFocus: HTMLElement) {
    const off = el.querySelector<HTMLButtonElement>(".flt-on .flt-off");
    if (!off) return;
    off.click();
    keepFocus.focus({ preventScroll: true });
  }

  bar.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button");
    if (!b) return;
    if (b.dataset.plateFix !== undefined) {
      const fix = note?.fix;
      const want = note?.want ?? null;
      if (!fix) return;
      note = null;
      if (want) layer = want;
      render();
      opts.onViewChange(fix);
      return;
    }
    const id = b.dataset.plateLayer;
    if (!isLayer(id)) return;
    const av = shownView ? availability(shownView) : null;
    if (id !== "raw" && av && !av.ok) {
      // Dimmed: say why and what to do, nothing loads.
      note = { text: av.why, fix: av.fix, want: av.fix ? id : null };
      render();
      return;
    }
    removeFilter(b);
    note = null;
    if (id === layer && shown.layer === id) return render();
    layer = id;
    cancel();
    render();
    void apply();
  });

  return {
    layer: () => layer,
    async show(view, frames, url) {
      cancel();
      note = null;
      last = { view, frames, url };
      shownView = view;
      await apply();
    },
    status(kind, message) {
      // The page is loading (or failed to load) a new view: whatever this module was loading is stale.
      if (kind !== "idle") {
        cancel();
        last = null;
        epochId = null;
        if (kind === "error") shown = { layer, frames: null, url: "", dropped: false };
        renderQuality();
      }
      inner.status(kind, message);
    },
    highlight: inner.highlight?.bind(inner),
    gaia: inner.gaia?.bind(inner),
    active: inner.active?.bind(inner),
    display: inner.display?.bind(inner),
    step: inner.step?.bind(inner),
    canCompare: inner.canCompare?.bind(inner),
    destroy() {
      cancel();
      bar.remove();
      inner.destroy();
    },
  };
}
