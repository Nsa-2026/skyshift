// The globe: Aladin Lite 3.8.2 with IRSA HiPS and a selectable plate-negative display.
// Heat cells and zone boxes are graphic-overlay polygons; counts, zone names, signals and
// featured targets are catalogs with custom canvas shapes (pencil marks). Hit-testing is ours,
// on screen positions, so a click means the same thing in the SVG fallback.
import type { Candidate, SkyPos } from "../api/types";
import type { Target } from "../app/state";
import type { AladinCatalog, AladinClick, AladinHiPS, AladinInstance, AladinOverlay, AladinStatic } from "../sky/aladin";
import { ALLWISE, SPHEREX, TWO_MASS, cubeFrameForDet, type SkyLayer } from "../sky/layers";
import { ang2pixNest } from "./healpix";
import { cellOutline, isNested, rampAlpha, rampIndex, rampSteps, zoneLabelAnchor, zoneOutline, zoneSideAnchor, type Cell } from "./heat";
import { countText, isGradeC, oneLine, zoneName } from "./kinds";
import type { BackgroundId, Layers, MapCallbacks, MapHandle, MapModel, MapView } from "./map";
import { arrowLengthPx, drawLabel, drawSignal, drawTarget, nearestHit, paToScreen, type Ink, type Vec } from "./marks";
import { paintSearch, type SearchInk } from "./searchdraw";
import { offsetPa, sepDeg } from "./sphere";
import { LABEL_MIN_PX, WHOLE_FOV, cellPx, heatOrder, levelFor, showsHeat, showsSignals, type Level } from "./zoom";

/** The parts of the 3.8.2 API this page uses beyond src/sky/aladin.d.ts. */
interface Al extends AladinInstance {
  setProjection(p: string): void;
  setFrame(f: string): void;
  setCooGrid?(o: Record<string, unknown>): void;
  setBackgroundColor?(c: string): void;
  animateToRaDec?(ra: number, dec: number, seconds: number, done?: () => void): void;
  zoomToFoV?(fov: number, seconds: number, done?: () => void): void;
  increaseZoom?(): void;
  decreaseZoom?(): void;
  stopAnimation?(): void;
}

interface Hips extends AladinHiPS {
  setColormap?(name: string | null, o?: Record<string, unknown>): void;
  setCuts?(lo: number, hi: number): void;
}

interface Src {
  x: number;
  y: number;
  ra: number;
  dec: number;
  data: Record<string, unknown>;
}

type ShapeFn = (s: Src, ctx: CanvasRenderingContext2D, vp: { fov: [number, number]; width: number; height: number }) => void;

const LAYERS: Record<BackgroundId, SkyLayer> = { spherex: SPHEREX, "2mass": TWO_MASS, allwise: ALLWISE };
/** SPHEREx cube frame for the background: the middle of D3 (about 2 µm, like 2MASS K). */
export const SPHEREX_FRAME = cubeFrameForDet(3);
/** The negative sits on the white mat at this opacity: a soft plate, so the pencil reads first. */
const NEGATIVE_OPACITY = 0.62;
/**
 * Colour map for the SPHEREx cube when the plate is positive. One of the four Aladin 3.8.2
 * ships ("grayscale", "native", "magma", "redtemperature"); magma over the others because its
 * dark end is purple-black, so the red pencil marks keep their contrast off the galactic plane,
 * where the hunt zone sits. "redtemperature" is the warmer NASA look and swaps in here.
 */
const CUBE_COLOUR_MAP = "magma";
/**
 * Stretch for that colour map. Linear on the cube's own 8-bit tiles: the sky at SPHEREX_FRAME
 * (about 2 µm) is bright everywhere, because the zodiacal light is in the picture and NASA does
 * not subtract it, so asinh lifts the faint end until the whole sky reads white.
 */
const CUBE_STRETCH = "linear";
/** Radius of a provisional suspect's dashed grey ring, px (a candidate's mark is solid pencil). */
const PROV_R = 6;

function token(name: string, fallback: string): string {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

export function readInk(): Ink {
  return {
    red: token("--pencil-red", "#cf2338"),
    violet: token("--pencil-violet", "#7a3fc4"),
    ink: token("--ink", "#18202e"),
    ink2: token("--ink-2", "#4f5867"),
    glass: token("--glass", "#edf0ef"),
    sheet: token("--sheet", "#f8faf9"),
  };
}

export function rgba(hex: string, a: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a.toFixed(3)})`;
}

/**
 * Width for Aladin's div: the whole map, except on the whole-sky ellipse at its widest field,
 * where a map wider than 2:1 is narrowed to 2:1 (less a small margin) so the poles show.
 */
export function fitWidth(mol: boolean, fovDeg: number, w: number, h: number, margin = 10, narrowed = false): number {
  // A little hysteresis: Aladin may keep its scale when the div changes width, which changes the field.
  if (!mol || fovDeg < (narrowed ? 340 : 359) || !(h > 0) || !(w > 0)) return w;
  const want = 2 * (h - 2 * margin);
  return want > 0 && want < w ? want : w;
}

/**
 * Where the projection's edge sits on screen, or null when the sky fills the whole map.
 * Aladin's fov spans the map's width: the globe (SIN) is a disk 180° across at fov 180;
 * the whole sky (Mollweide) a 2:1 ellipse 360° across at fov 360.
 */
export function limbEllipse(mol: boolean, fovDeg: number, w: number, h: number): { cx: number; cy: number; rx: number; ry: number } | null {
  if (!(fovDeg > 0) || !(w > 0) || !(h > 0)) return null;
  const pxPerDeg = w / fovDeg;
  const rx = (mol ? 180 : 90) * pxPerDeg;
  const ry = 90 * pxPerDeg;
  const cx = w / 2;
  const cy = h / 2;
  // Every corner inside the ellipse: nothing to cover.
  if ((cx / rx) ** 2 + (cy / ry) ** 2 <= 1) return null;
  return { cx, cy, rx, ry };
}

export interface AladinMapInit {
  view: MapView;
  gal: boolean;
  mol: boolean;
  layers: Layers;
  reducedMotion: boolean;
}

export function createAladinMap(host: HTMLElement, A: AladinStatic, init: AladinMapInit, cb: MapCallbacks): MapHandle {
  const ink = readInk();
  const mapEl = document.createElement("div");
  mapEl.className = "atl-aladin";
  host.appendChild(mapEl);

  // ---- backgrounds: IRSA HiPS only ---------------------------------------------------------
  // Every layer is made here, never by Aladin from a URL: Aladin caches layers by id, and one
  // it made itself would ignore the colour map set on ours.
  const hips: Partial<Record<BackgroundId, Hips>> = {};
  let layers: Layers = init.layers;

  function setSlice(h: Hips) {
    try {
      h.setSliceNumber(SPHEREX_FRAME);
    } catch {
      /* not a cube yet: the success callback sets it */
    }
  }

  /**
   * Negative: grey scale reversed, so the dark sky becomes glass and stars become ink.
   * Positive: 2MASS and AllWISE are colour JPEGs, so "native" is their own real colour. The
   * SPHEREx cube is one 12-bit channel of 102, which "native" draws blown out and white: colour
   * has to come from a colour map over an asinh stretch, the way IRSA and Aladin show the cube.
   * That is a colour map on one wavelength, not an RGB composite of three, and the label under
   * the map says which wavelength, so nobody reads it as the sky's own colour.
   */
  function style(h: Hips, id: BackgroundId) {
    try {
      if (layers.negative) h.setColormap?.("grayscale", { reversed: true, stretch: "linear" });
      else if (id === "spherex") h.setColormap?.(CUBE_COLOUR_MAP, { reversed: false, stretch: CUBE_STRETCH });
      else h.setColormap?.("native", { reversed: false });
      h.setOpacity(layers.negative ? NEGATIVE_OPACITY : 1);
    } catch {
      /* keep the layer's own look */
    }
  }

  function layerFor(id: BackgroundId): Hips {
    const have = hips[id];
    if (have) return have;
    const l = LAYERS[id];
    const made = A.HiPS(l.url, {
      name: l.label,
      imgFormat: l.format,
      // The cube opens on its middle frame, 50, a blank dichroic channel (404s): show one
      // inside a detector instead, once Aladin has read the cube's properties.
      successCallback: (layer) => {
        if (id === "spherex") setSlice(layer as Hips);
        if (id === layers.background) style(layer as Hips, id);
      },
    }) as Hips;
    hips[id] = made;
    if (id === "spherex") setSlice(made);
    style(made, id);
    return made;
  }

  function applyBackground() {
    try {
      // The cube's PNG tiles carry an alpha channel, so whatever is behind them shows through:
      // on the white sheet even a colour map washes out to pink. The sky goes to ink for the
      // positive, which is also what the marks already assume (see text() and the shapes below).
      al.setBackgroundColor?.(layers.negative ? ink.sheet : ink.ink);
      const h = layerFor(layers.background);
      al.setBaseImageLayer(h);
      style(h, layers.background);
    } catch {
      /* the map keeps its current background */
    }
  }

  const al = A.aladin(mapEl, {
    survey: layerFor(layers.background), // never a CDS id: the default P/DSS2/color is broken
    target: `${init.view.ra} ${init.view.dec}`,
    fov: init.view.fov,
    cooFrame: init.gal ? "galactic" : "ICRS",
    projection: init.mol ? "MOL" : "SIN",
    backgroundColor: ink.sheet,
    showSimbadPointerControl: false,
    showLayersControl: false,
    showFullscreenControl: false,
    showProjectionControl: false,
    showCooGridControl: false,
    showSettingsControl: false,
    showShareControl: false,
    showGotoControl: false,
    showFrame: false,
    showZoomControl: false,
    showContextMenu: false,
    showStatusBar: false,
    showReticle: false,
    showCooLocation: false,
    showFov: false,
    samp: false,
    log: false, // Aladin's usage logger lives on CDS
  }) as Al;
  // Dev only: lets the screenshot scripts look inside the map.
  if (import.meta.env.DEV) (mapEl as unknown as { __al?: unknown }).__al = al;

  function applyGrid(gal: boolean) {
    try {
      al.setCooGrid?.({
        enabled: true,
        color: gal ? ink.violet : ink.ink2,
        opacity: 0.14,
        thickness: 1,
        showLabels: false,
        labelSize: 11,
      });
    } catch {
      /* no grid */
    }
  }

  // ---- overlays -------------------------------------------------------------------------
  const quiet = (color: string) => ({ color, hoverColor: color, selectionColor: color });
  const heatOv = A.graphicOverlay({ name: "Objects to check per cell", ...quiet(ink.red), lineWidth: 1 }) as AladinOverlay;
  const zoneOv = A.graphicOverlay({ name: "Zones", ...quiet(ink.ink), lineWidth: 1.25 }) as AladinOverlay;

  let model: MapModel | null = null;
  let level: Level = levelFor(init.view.fov);
  let order = heatOrder(init.view.fov, host.clientWidth || 800);
  let hoverId: string | null = null;
  let destroyed = false;

  /** Ink on glass on the negative; glass on ink on the positive (dark) sky. */
  const text = () => (layers.negative ? { color: ink.ink, halo: ink.glass } : { color: ink.glass, halo: ink.ink });

  const labelShape: ShapeFn = (s, ctx) => {
    const d = s.data;
    if (d.kind === "zone") {
      // On the whole sky the zone is a small blot: the name sits beside it (east, screen left).
      if (d.side) drawLabel(ctx, ink, s.x - 6, s.y, String(d.text), { ...text(), align: "right", weight: 650, size: 13 });
      else drawLabel(ctx, ink, s.x + 2, s.y + 13, String(d.text), { ...text(), align: "left", weight: 650, size: 13 });
    } else {
      drawLabel(ctx, ink, s.x, s.y, String(d.text), { ...text(), weight: 700, size: 12.5 });
    }
  };

  /** North and east on screen at a position: a small step each way through the projection. */
  function axes(pos: SkyPos, x: number, y: number, stepDeg: number): { north: Vec; east: Vec } | null {
    const n = al.world2pix(pos.ra, Math.min(89.999, pos.dec + stepDeg));
    const e = al.world2pix(offsetPa(pos, 90, stepDeg).ra, offsetPa(pos, 90, stepDeg).dec);
    if (!n || !e) return null;
    return { north: { x: n[0] - x, y: n[1] - y }, east: { x: e[0] - x, y: e[1] - y } };
  }

  const signalShape: ShapeFn = (s, ctx, vp) => {
    const c = s.data.c as Candidate | undefined;
    if (!c) return;
    const fov = vp?.fov?.[0] ?? curFov();
    const width = vp?.width ?? host.clientWidth;
    let dir: Vec | null = null;
    if (c.pa_deg != null && Number.isFinite(c.pa_deg)) {
      const ax = axes(c, s.x, s.y, Math.max(1e-4, fov / 40));
      if (ax) dir = paToScreen(c.pa_deg, ax.north, ax.east);
    }
    const pxPerArcsec = width / (fov * 3600);
    const selected = model?.selectedId === c.id;
    drawSignal(ctx, ink, c.kind, s.x, s.y, dir, arrowLengthPx(c.pm_mas_yr, pxPerArcsec), { selected, hovered: hoverId === c.id, muted: isGradeC(c) });
    if (level === "detail") {
      drawLabel(ctx, ink, s.x + 12, s.y + 14, oneLine(c), { color: ink.ink2, align: "left", weight: 550, size: 11.5 });
    }
  };

  const targetShape: ShapeFn = (s, ctx) => {
    const t = s.data.t as Target | undefined;
    if (!t) return;
    // Near the right edge the name goes on the left, so the plate's edge doesn't cut it.
    const width = mapEl.clientWidth || host.clientWidth;
    drawTarget(ctx, ink, s.x, s.y, level === "whole" && host.clientWidth < 640 ? null : t.short, s.x > width * 0.78 ? "left" : "right");
  };

  /** A provisional suspect: a small dashed grey ring, never a pencil mark (it is not a candidate). */
  const provisionalShape: ShapeFn = (s, ctx) => {
    ctx.save();
    ctx.beginPath();
    ctx.arc(s.x, s.y, PROV_R, 0, Math.PI * 2);
    // A faint halo so the ring reads over the stars, then the dashed ring itself.
    ctx.globalAlpha = 0.6;
    ctx.lineWidth = 3.5;
    ctx.strokeStyle = layers.negative ? ink.glass : "#000";
    ctx.stroke();
    ctx.globalAlpha = 0.85;
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = layers.negative ? ink.ink2 : ink.glass;
    ctx.stroke();
    ctx.restore();
  };

  const labelCat = A.catalog({ name: "Labels", shape: labelShape, sourceSize: 8 }) as AladinCatalog;
  const provisionalCat = A.catalog({ name: "Provisional suspects", shape: provisionalShape, sourceSize: 10 }) as AladinCatalog;
  const signalCat = A.catalog({ name: "Signals", shape: signalShape, sourceSize: 20 }) as AladinCatalog;
  const targetCat = A.catalog({ name: "Featured targets", shape: targetShape, sourceSize: 18 }) as AladinCatalog;

  al.addOverlay(heatOv);
  al.addOverlay(zoneOv);
  // Labels last, so a name is never crossed out by a mark beside it.
  al.addCatalog(provisionalCat);
  al.addCatalog(targetCat);
  al.addCatalog(signalCat);
  al.addCatalog(labelCat);
  applyBackground();
  applyGrid(init.gal);

  function stopFlight() {
    try { al.stopAnimation?.(); } catch { /* Older viewers may not expose cancellation. */ }
  }

  function curFov(): number {
    try {
      return al.getFov()[0];
    } catch {
      return WHOLE_FOV;
    }
  }

  function view(): MapView {
    try {
      const [ra, dec] = al.getRaDec();
      return { ra, dec, fov: curFov() };
    } catch {
      return init.view;
    }
  }

  // ---- drawing --------------------------------------------------------------------------
  function drawHeat() {
    heatOv.removeAll();
    labelCat.removeAll();
    if (!model) return;
    const w = host.clientWidth || 800;
    const fov = curFov();
    if (showsHeat(level) && model.layers.heat) {
      const cells = model.cellsAt(order);
      const steps = rampSteps(cells.reduce((m, c) => Math.max(m, c.count), 0));
      const step = order <= 3 ? 4 : 2;
      // Counts are written in the cells from the middle range in; the whole sky names the zones.
      const labelled = level !== "whole" && cellPx(order, fov, w) >= LABEL_MIN_PX;
      const labels: unknown[] = [];
      for (const cell of cells) {
        const a = rampAlpha(rampIndex(cell.count, steps), steps.length);
        heatOv.add(
          A.polygon(cellOutline(cell, step), {
            fill: true,
            fillColor: rgba(ink.red, a),
            color: rgba(ink.red, Math.min(1, a + 0.2)),
            hoverColor: rgba(ink.red, Math.min(1, a + 0.2)),
            selectionColor: rgba(ink.red, Math.min(1, a + 0.2)),
            lineWidth: 1,
            opacity: 1,
          }),
        );
        if (labelled) labels.push(A.source(cell.center.ra, cell.center.dec, { kind: "count", text: countText(cell.count), alpha: a }));
      }
      if (labels.length) labelCat.addSources(labels);
    }
    drawZones();
  }

  function drawZones() {
    zoneOv.removeAll();
    if (!model || !model.layers.zones) return;
    const labels: unknown[] = [];
    for (const z of model.zones) {
      // Close in, the zone's edge is a long straight line across the signals: keep it faint there.
      const edge = rgba(ink.ink, showsSignals(level) ? 0.3 : 0.7);
      zoneOv.add(A.polygon(zoneOutline(z, 0.5), { ...quiet(edge), lineWidth: 1.25, opacity: 1 }));
      // On the whole sky a zone inside a busier one (the pilot in the hunt) would print on top of it.
      if (level !== "detail" && !(level === "whole" && isNested(z, model.zones))) {
        const side = level === "whole";
        const at = side ? zoneSideAnchor(z) : zoneLabelAnchor(z);
        const name = side ? `${zoneName(z.zone)}, ${new Intl.NumberFormat("en-GB").format(z.count)}` : zoneName(z.zone);
        labels.push(A.source(at.ra, at.dec, { kind: "zone", text: name, side }));
      }
    }
    if (labels.length) labelCat.addSources(labels);
  }

  function drawSignals() {
    signalCat.removeAll();
    if (!model || !model.layers.signals || !showsSignals(level)) return;
    signalCat.addSources(model.candidates.map((c) => A.source(c.ra, c.dec, { c, id: c.id })));
  }

  function drawProvisional() {
    provisionalCat.removeAll();
    const p = model?.provisional;
    if (!p || !model?.layers.provisional || !showsSignals(level)) return;
    provisionalCat.addSources(p.suspects.map((x) => A.source(x.ra, x.dec, { id: x.id })));
  }

  function drawTargets() {
    targetCat.removeAll();
    if (!model) return;
    targetCat.addSources(model.targets.map((t) => A.source(t.ra, t.dec, { t, id: t.slug })));
  }

  /** Ask Aladin to repaint the catalogs (selection and hover live in the shape functions). */
  function repaint() {
    for (const c of [signalCat, labelCat, targetCat]) {
      try {
        (c as unknown as { reportChange?(): void }).reportChange?.();
      } catch {
        /* next frame will show it */
      }
    }
  }

  // ---- tonight's search: a canvas between the survey and Aladin's overlays ----------------------
  const searchInk: SearchInk = {
    ink: ink.ink,
    red: ink.red,
    blue: token("--pencil-blue", "#2556c8"),
    grey: token("--ink-3", "#78818f"),
    warn: token("--warn", "#b4232f"),
  };
  const searchCanvas = document.createElement("canvas");
  searchCanvas.className = "atl-searchcanvas";
  searchCanvas.setAttribute("aria-hidden", "true");
  // Just above the survey image and below the canvas Aladin draws its overlays and marks on.
  const imageCanvas = mapEl.querySelector(".aladin-imageCanvas");
  if (imageCanvas?.parentElement) imageCanvas.after(searchCanvas);
  else mapEl.appendChild(searchCanvas);

  function drawSearch() {
    const w = mapEl.clientWidth;
    const h = mapEl.clientHeight;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = Math.round(w * dpr);
    const H = Math.round(h * dpr);
    if (searchCanvas.width !== W || searchCanvas.height !== H) {
      searchCanvas.width = W;
      searchCanvas.height = H;
    }
    const ctx = searchCanvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const layer = model?.search;
    if (!layer || !model?.layers.search || !showsHeat(level) || !(w > 0)) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    paintSearch(ctx, layer, (ra, dec) => rawScreen({ ra, dec }), searchInk, { w, h });
  }

  // ---- view changes: at most once per frame ------------------------------------------------
  // ---- the plate's edge: Aladin paints outside the projection near-black; the mat covers it --
  const svgNS = "http://www.w3.org/2000/svg";
  const limb = document.createElementNS(svgNS, "svg");
  limb.setAttribute("class", "atl-limb");
  limb.setAttribute("aria-hidden", "true");
  const limbMat = document.createElementNS(svgNS, "path");
  limbMat.setAttribute("class", "atl-limb-mat");
  limbMat.setAttribute("fill-rule", "evenodd");
  const limbEdge = document.createElementNS(svgNS, "ellipse");
  limbEdge.setAttribute("class", "atl-limb-edge");
  limb.append(limbMat, limbEdge);
  host.appendChild(limb);
  let mol = init.mol;

  /** Horizontal inset of Aladin's div inside the stage (see fitWidth). */
  let inset = 0;

  /**
   * Aladin can't zoom out past 360° on the whole-sky map, so on a map wider than 2:1 the
   * ellipse would lose its poles. At the whole-sky field only, narrow Aladin's div to 2:1.
   */
  function fit(fov: number) {
    const W = host.clientWidth;
    const H = host.clientHeight;
    const w = fitWidth(mol, fov, W, H, 10, inset > 0);
    const next = Math.round((W - w) / 2);
    if (next !== inset) {
      inset = next;
      mapEl.style.marginLeft = inset ? `${inset}px` : "";
      mapEl.style.width = inset ? `${W - 2 * inset}px` : "";
    }
  }

  function drawLimb(fov: number) {
    fit(fov);
    const W = host.clientWidth;
    const h = host.clientHeight;
    const e = limbEllipse(mol, fov, W - 2 * inset, h);
    if (!e) {
      mapEl.style.clipPath = "";
      limb.style.display = "none";
      return;
    }
    // Leave the area outside the sky projection open to the shared page backdrop.
    mapEl.style.clipPath = `ellipse(${e.rx}px ${e.ry}px at ${e.cx}px ${e.cy}px)`;
    e.cx += inset;
    const w = W;
    limb.style.display = "";
    limb.setAttribute("viewBox", `0 0 ${w} ${h}`);
    const { cx, cy, rx, ry } = e;
    limbMat.setAttribute(
      "d",
      `M0 0H${w}V${h}H0Z M${cx - rx} ${cy}A${rx} ${ry} 0 1 0 ${cx + rx} ${cy}A${rx} ${ry} 0 1 0 ${cx - rx} ${cy}Z`,
    );
    limbEdge.setAttribute("cx", String(cx));
    limbEdge.setAttribute("cy", String(cy));
    limbEdge.setAttribute("rx", String(rx));
    limbEdge.setAttribute("ry", String(ry));
  }

  let raf = 0;
  function onMove() {
    if (raf || destroyed) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (destroyed) return;
      const v = view();
      drawLimb(v.fov);
      const nextLevel = levelFor(v.fov);
      const nextOrder = heatOrder(v.fov, host.clientWidth || 800);
      const wasLabelled = cellPx(order, lastFov, host.clientWidth || 800) >= LABEL_MIN_PX;
      const isLabelled = cellPx(nextOrder, v.fov, host.clientWidth || 800) >= LABEL_MIN_PX;
      lastFov = v.fov;
      if (nextLevel !== level || nextOrder !== order || wasLabelled !== isLabelled) {
        const signalsChanged = showsSignals(nextLevel) !== showsSignals(level) || (nextLevel === "detail") !== (level === "detail");
        level = nextLevel;
        order = nextOrder;
        drawHeat();
        if (signalsChanged) {
          drawSignals();
          drawProvisional();
        }
        drawTargets();
      }
      drawSearch();
      cb.onView(v);
    });
  }
  let lastFov = init.view.fov;
  al.on("positionChanged", onMove);
  al.on("zoomChanged", onMove);
  drawLimb(init.view.fov);
  let ro: ResizeObserver | null = null;
  if (typeof ResizeObserver === "function") {
    ro = new ResizeObserver(() => onMove());
    ro.observe(host);
  }

  // ---- hit-testing (in Aladin's div; callbacks get stage coordinates) ------------------------
  function rawScreen(pos: SkyPos): { x: number; y: number } | null {
    try {
      const p = al.world2pix(pos.ra, pos.dec);
      if (!p || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) return null;
      return { x: p[0], y: p[1] };
    } catch {
      return null;
    }
  }

  function screenOf(pos: SkyPos): { x: number; y: number } | null {
    const p = rawScreen(pos);
    return p ? { x: p.x + inset, y: p.y } : null;
  }

  function near<T extends SkyPos>(items: readonly T[], id: (t: T) => string, x: number, y: number, radius: number): T | null {
    const v = view();
    const reach = Math.min(180, v.fov * 0.9 + 1);
    const pts: { id: string; x: number; y: number; item: T }[] = [];
    for (const it of items) {
      if (sepDeg(it, v) > reach) continue;
      const p = rawScreen(it);
      if (p) pts.push({ id: id(it), x: p.x, y: p.y, item: it });
    }
    return nearestHit(pts, x, y, radius)?.item ?? null;
  }

  function signalAt(x: number, y: number): Candidate | null {
    if (!model || !model.layers.signals || !showsSignals(level)) return null;
    return near(model.candidates, (c) => c.id, x, y, 14);
  }

  function targetAt(x: number, y: number): Target | null {
    if (!model) return null;
    return near(model.targets, (t) => t.slug, x, y, 12);
  }

  function cellAt(pos: SkyPos): Cell | null {
    if (!model || !model.layers.heat || !showsHeat(level)) return null;
    const pix = ang2pixNest(order, pos.ra, pos.dec);
    return model.cellsAt(order).find((c) => c.pix === pix) ?? null;
  }

  al.on("click", (e: AladinClick) => {
    if (destroyed || !e || e.isDragging || !Number.isFinite(e.ra) || !Number.isFinite(e.dec)) return;
    const s = signalAt(e.x, e.y);
    if (s) return cb.onSignal(s);
    const t = targetAt(e.x, e.y);
    if (t) return cb.onTarget(t);
    const cell = cellAt(e);
    if (cell) return cb.onCell(cell);
    cb.onEmpty({ ra: e.ra, dec: e.dec }, e.x + inset, e.y);
  });

  let hoverRaf = 0;
  let lastMouse: { x: number; y: number } | null = null;
  const onMouseMove = (ev: MouseEvent) => {
    const r = mapEl.getBoundingClientRect();
    lastMouse = { x: ev.clientX - r.left, y: ev.clientY - r.top };
    if (hoverRaf) return;
    hoverRaf = requestAnimationFrame(() => {
      hoverRaf = 0;
      if (!lastMouse || destroyed) return;
      const s = signalAt(lastMouse.x, lastMouse.y);
      const t = s ? null : targetAt(lastMouse.x, lastMouse.y);
      const id = s?.id ?? null;
      mapEl.classList.toggle("is-pointing", !!(s || t));
      if (id !== hoverId) {
        hoverId = id;
        repaint();
      }
      cb.onHover(s, lastMouse.x + inset, lastMouse.y);
    });
  };
  const onMouseLeave = () => {
    lastMouse = null;
    if (hoverId) {
      hoverId = null;
      repaint();
    }
    mapEl.classList.remove("is-pointing");
    cb.onHover(null, 0, 0);
  };
  mapEl.addEventListener("mousemove", onMouseMove);
  mapEl.addEventListener("mouseleave", onMouseLeave);

  return {
    kind: "aladin",
    update(m) {
      const prev = model;
      model = m;
      const layersChanged =
        !prev || prev.layers.background !== m.layers.background || prev.layers.negative !== m.layers.negative;
      layers = m.layers;
      if (layersChanged) {
        applyBackground();
        repaint();
      }
      if (!prev || prev.candidates !== m.candidates || prev.layers.heat !== m.layers.heat || prev.layers.zones !== m.layers.zones || prev.zones !== m.zones) {
        drawHeat();
      }
      if (!prev || prev.candidates !== m.candidates || prev.layers.signals !== m.layers.signals) drawSignals();
      if (!prev || prev.targets !== m.targets) drawTargets();
      if (!prev || prev.provisional !== m.provisional || prev.layers.provisional !== m.layers.provisional) drawProvisional();
      if (!prev || prev.search !== m.search || prev.layers.search !== m.layers.search) drawSearch();
      if (prev && prev.selectedId !== m.selectedId) repaint();
    },
    view,
    flyTo(to, animate) {
      stopFlight();
      const fov = to.fov;
      try {
        if (animate && al.animateToRaDec && al.zoomToFoV) {
          al.animateToRaDec(to.ra, to.dec, 1.1);
          if (fov != null) al.zoomToFoV(fov, 1.1);
        } else {
          al.gotoRaDec(to.ra, to.dec);
          if (fov != null) al.setFoV(fov);
        }
      } catch {
        /* the view stays */
      }
      onMove();
    },
    setProjection(m) {
      mol = m;
      try {
        al.setProjection(m ? "MOL" : "SIN");
      } catch {
        /* keep the projection */
      }
      onMove();
    },
    setFrame(gal) {
      try {
        al.setFrame(gal ? "galactic" : "ICRS");
      } catch {
        /* keep the frame */
      }
      applyGrid(gal);
      onMove();
    },
    zoomBy(f) {
      stopFlight();
      try {
        if (f > 1 && al.decreaseZoom) al.decreaseZoom();
        else if (f < 1 && al.increaseZoom) al.increaseZoom();
        else al.setFoV(curFov() * f);
      } catch {
        /* ignore */
      }
      onMove();
    },
    panBy(dx, dy) {
      stopFlight();
      const v = view();
      const w = mapEl.clientWidth || host.clientWidth || 1;
      const h = mapEl.clientHeight || host.clientHeight || 1;
      // Move the centre to the sky under the screen point (cx + dx·w, cy + dy·h).
      try {
        const al2 = al as unknown as { pix2world?(x: number, y: number): [number, number] | undefined };
        const p = al2.pix2world?.(w / 2 + dx * w, h / 2 + dy * h);
        if (p && Number.isFinite(p[0])) al.gotoRaDec(p[0], p[1]);
        else al.gotoRaDec(v.ra, v.dec);
      } catch {
        /* ignore */
      }
      onMove();
    },
    screenOf,
    destroy() {
      destroyed = true;
      if (raf) cancelAnimationFrame(raf);
      if (hoverRaf) cancelAnimationFrame(hoverRaf);
      mapEl.removeEventListener("mousemove", onMouseMove);
      mapEl.removeEventListener("mouseleave", onMouseLeave);
      ro?.disconnect();
      limb.remove();
      searchCanvas.remove();
      mapEl.remove();
    },
  };
}
