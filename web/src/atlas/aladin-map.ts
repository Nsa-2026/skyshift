// The globe: Aladin Lite 3.8.2 with IRSA HiPS and a selectable plate-negative display.
// Searched zones use a canvas with seam-safe paths (one faint fill per union of boxes);
// heat cells (off unless Adjust turns them on) are polygons too. Objects to check and featured
// targets are catalogs with custom canvas shapes (pencil marks). Zone badges and target names
// are drawn on a canvas of our own over the map, placed so they never cover a zone, a mark or each
// other. Hit-testing is ours, on screen positions, so a click means the same thing in the SVG fallback.
import type { Candidate, SkyPos } from "../api/types";
import type { Target } from "../app/state";
import type { AladinCatalog, AladinClick, AladinHiPS, AladinInstance, AladinOverlay, AladinStatic } from "../sky/aladin";
import { ALLWISE, SPHEREX, TWO_MASS, cubeFrameForDet, type SkyLayer } from "../sky/layers";
import { ang2pixNest } from "./healpix";
import { cellOutline, rampAlpha, rampIndex, rampSteps, type Cell } from "./heat";
import { countText, isGradeC, oneLine } from "./kinds";
import { NAME, STAR, aroundPoint, boundsOf, boxHolds, estimateSize, placeLabels, type Box, type LabelSpec, type Placed, type Size } from "./labels";
import type { BackgroundId, Layers, MapCallbacks, MapHandle, MapModel, MapView } from "./map";
import { arrowLengthPx, drawLabel, drawSignal, drawSignalDot, drawTarget, nearestHit, paToScreen, signalInk, type Ink, type Vec } from "./marks";
import { paintSearch, type SearchInk } from "./searchdraw";
import { offsetPa, sepDeg } from "./sphere";
import { bandAt, CONE_DEG, overheadAt, overheadTrack, sunAt, type BandCell, type NowModel } from "./spherex-now";
import { LABEL_MIN_PX, WHOLE_FOV, cellPx, heatOrder, levelFor, showsDots, showsHeat, type Level } from "./zoom";
import { areaAt, densifyRing, type ZoneArea } from "./zones";
import { BRIGHT_STARS } from "./stars";
import { zoneEdges, zoneFills } from './zone-paths';

/** The parts of the 3.8.2 API this page uses beyond src/sky/aladin.d.ts. */
interface Al extends AladinInstance {
  setProjection(p: string): void;
  setFrame(f: string): void;
  setCooGrid?(o: Record<string, unknown>): void;
  animateToRaDec?(ra: number, dec: number, seconds: number, done?: () => void): void;
  zoomToFoV?(fov: number, seconds: number, done?: () => void): void;
  increaseZoom?(): void;
  decreaseZoom?(): void;
  /** 1 at the projection's widest field (360° whole sky, 180° globe); more when zoomed out past it. */
  getZoomFactor?(): number;
  setFoVRange?(min: number | null, max: number | null): void;
  pix2world?(x: number, y: number): [number, number] | undefined;
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
/** Radius of a provisional suspect's dashed grey ring, px (a candidate's mark is solid pencil). */
const PROV_R = 6;
/** Room kept above and below the whole sky, px. */
const FIT_MARGIN = 8;

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

/** The projection's widest field: the whole sky (Mollweide) is 360° across, the globe (SIN) 180°. */
export const projectionMax = (mol: boolean): number => (mol ? 360 : 180);

/**
 * Aladin's field for the whole sky as large as the map holds it: the whole-sky ellipse (2:1) or the
 * globe's disk, as wide as the map unless that makes it taller than the map (less a margin).
 * Aladin takes a field past the projection's widest (getFov() then still says 360 or 180).
 * `inset` is what floats over the map's top (the site header): the sky, centred, keeps clear of it
 * at the top and the bottom alike, unless that would halve it.
 */
export function fitFov(mol: boolean, w: number, h: number, margin = FIT_MARGIN, inset = 0): number {
  const max = projectionMax(mol);
  if (!(w > 0) || !(h > 2 * margin)) return max;
  const clear = h - 2 * (margin + Math.max(0, inset));
  const room = clear >= h / 2 ? clear : h - 2 * margin;
  return Math.max(max, (180 * w) / room);
}

/**
 * Where the projection's edge sits on screen, or null when the sky fills the whole map.
 * Aladin's fov spans the map's width: the globe (SIN) is a disk 180° across at fov 180;
 * the whole sky (Mollweide) a 2:1 ellipse 360° across at fov 360. `fovDeg` is the true field,
 * past 360° (or 180°) when zoomed out further.
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

/**
 * Opacity of a zone's one fill and one edge at each level: a small clear patch far out, a faint
 * wash in the middle range, only a faint edge close in (where the dots matter). Red stays for the
 * objects to check; a zone is drawn in the sky's own pale ink (ink on the negative).
 */
export function zoneStyle(level: Level): { fill: number; edge: number } {
  if (level === "whole") return { fill: 0.2, edge: 0.9 };
  if (level === "mid") return { fill: 0.06, edge: 0.6 };
  return { fill: 0, edge: 0.3 };
}

/** The zones' pale ink on the dark sky (the opening's line colour). */
export const ZONE_LIGHT = "#dbe8f4";

export interface AladinMapInit {
  view: MapView;
  gal: boolean;
  mol: boolean;
  layers: Layers;
  reducedMotion: boolean;
}

/** What a placed label opens when clicked. */
type LabelHit = Placed & ({ kind: "zone"; area: ZoneArea } | { kind: "target"; target: Target });

export function createAladinMap(host: HTMLElement, A: AladinStatic, init: AladinMapInit, cb: MapCallbacks): MapHandle {
  const ink = readInk();
  const mapEl = document.createElement("div");
  mapEl.className = "atl-aladin";
  host.appendChild(mapEl);
  let mol = init.mol;

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

  /** Negative: grey scale reversed, so the dark sky becomes glass and stars become ink. */
  function style(h: Hips) {
    try {
      if (layers.negative) h.setColormap?.("grayscale", { reversed: true, stretch: "linear" });
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
        if (id === layers.background) style(layer as Hips);
      },
    }) as Hips;
    hips[id] = made;
    if (id === "spherex") setSlice(made);
    style(made);
    return made;
  }

  function applyBackground() {
    try {
      const h = layerFor(layers.background);
      al.setBaseImageLayer(h);
      style(h);
    } catch {
      /* the map keeps its current background */
    }
  }

  const fitNow = () => fitFov(mol, host.clientWidth, host.clientHeight, FIT_MARGIN, cb.insetTop?.() ?? 0);
  const wantsWhole = (fov: number | undefined) => fov != null && fov >= projectionMax(mol) * 0.999;

  const al = A.aladin(mapEl, {
    survey: layerFor(layers.background), // never a CDS id: the default P/DSS2/color is broken
    target: `${init.view.ra} ${init.view.dec}`,
    fov: wantsWhole(init.view.fov) ? fitNow() : init.view.fov,
    cooFrame: init.gal ? "galactic" : "ICRS",
    projection: init.mol ? "MOL" : "SIN",
    // The night sky's own dark (the site backdrop, cosmic.css): a frame of Aladin's background
    // drawn before the clip catches up must not flash a pale rim around the sky.
    backgroundColor: "#05090f",
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

  let model: MapModel | null = null;
  let level: Level = levelFor(init.view.fov);
  let order = heatOrder(init.view.fov, host.clientWidth || 800);
  let hoverId: string | null = null;
  let destroyed = false;

  /** Ink on glass on the negative; glass on ink on the positive (dark) sky. */
  const text = () => (layers.negative ? { color: ink.ink, halo: ink.glass } : { color: ink.glass, halo: ink.ink });
  /** Names and target marks: light on a soft shadow on the dark sky, ink on a soft glow on the negative. */
  const soft = () => (layers.negative ? { color: ink.ink, shadow: rgba(ink.glass, 0.95) } : { color: "#f5f7f9", shadow: "rgba(0, 0, 0, 0.9)" });
  const zoneInk = () => (layers.negative ? ink.ink : ZONE_LIGHT);

  /** Counts in heat cells (only with the heat map on). */
  const labelShape: ShapeFn = (s, ctx) => {
    drawLabel(ctx, ink, s.x, s.y, String(s.data.text), { ...text(), weight: 700, size: 12.5 });
  };

  /** North and east on screen at a position: a small step each way through the projection. */
  function axes(pos: SkyPos, x: number, y: number, stepDeg: number): { north: Vec; east: Vec } | null {
    const n = al.world2pix(pos.ra, Math.min(89.999, pos.dec + stepDeg));
    const e = al.world2pix(offsetPa(pos, 90, stepDeg).ra, offsetPa(pos, 90, stepDeg).dec);
    if (!n || !e) return null;
    return { north: { x: n[0] - x, y: n[1] - y }, east: { x: e[0] - x, y: e[1] - y } };
  }

  /** A compact hollow mark, with contrast adapted to the survey or plate negative. */
  function drawDot(ctx: CanvasRenderingContext2D, c: Candidate, x: number, y: number) {
    drawSignalDot(ctx, signalInk(ink, layers.negative), c.kind, x, y, {
      selected: model?.selectedId === c.id, hovered: hoverId === c.id, muted: isGradeC(c),
    });
  }

  const signalShape: ShapeFn = (s, ctx, vp) => {
    const c = s.data.c as Candidate | undefined;
    if (!c) return;
    if (level === "mid") return drawDot(ctx, c, s.x, s.y);
    const fov = vp?.fov?.[0] ?? curFov();
    const width = vp?.width ?? host.clientWidth;
    let dir: Vec | null = null;
    if (c.pa_deg != null && Number.isFinite(c.pa_deg)) {
      const ax = axes(c, s.x, s.y, Math.max(1e-4, fov / 40));
      if (ax) dir = paToScreen(c.pa_deg, ax.north, ax.east);
    }
    const pxPerArcsec = width / (fov * 3600);
    const selected = model?.selectedId === c.id;
    drawSignal(ctx, signalInk(ink, layers.negative), c.kind, s.x, s.y, dir, arrowLengthPx(c.pm_mas_yr, pxPerArcsec), { selected, hovered: hoverId === c.id, muted: isGradeC(c) });
    if (level === "detail") {
      drawLabel(ctx, ink, s.x + 12, s.y + 14, oneLine(c), { ...text(), align: "left", weight: 550, size: 11.5 });
    }
  };

  /** A featured target's mark; its name is placed with the other labels. */
  const targetShape: ShapeFn = (s, ctx) => {
    if (s.data.t) drawTarget(ctx, ink, s.x, s.y, null, "right", soft());
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

  /**
   * The projection's scale as a field: the projection's widest field times Aladin's zoom factor.
   * Past the whole sky this is the true field (getFov() stops at 360° or 180°); closer in it is the
   * projection plane's width, which sets where the projection's edge falls on screen.
   */
  function scaleFov(): number {
    try {
      const z = al.getZoomFactor?.();
      if (z != null && Number.isFinite(z) && z > 0) return projectionMax(mol) * z;
    } catch {
      /* fall back on getFov */
    }
    return curFov();
  }

  function view(): MapView {
    try {
      const [ra, dec] = al.getRaDec();
      return { ra, dec, fov: Math.min(projectionMax(mol), curFov()) };
    } catch {
      return init.view;
    }
  }

  // ---- drawing --------------------------------------------------------------------------
  function drawHeat() {
    heatOv.removeAll();
    labelCat.removeAll();
    if (!model || !showsHeat(level) || !model.layers.heat) return;
    const w = host.clientWidth || 800;
    const cells = model.cellsAt(order);
    const steps = rampSteps(cells.reduce((m, c) => Math.max(m, c.count), 0));
    const step = order <= 3 ? 4 : 2;
    // Counts are written in the cells in the middle range.
    const labelled = level !== "whole" && cellPx(order, curFov(), w) >= LABEL_MIN_PX;
    const labels: unknown[] = [];
    for (const cell of cells) {
      const a = rampAlpha(rampIndex(cell.count, steps), steps.length);
      heatOv.add(
        A.polygon(cellOutline(cell, step), {
          fill: true,
          fillColor: rgba(ink.red, a),
          ...quiet(rgba(ink.red, Math.min(1, a + 0.2))),
          lineWidth: 1,
          opacity: 1,
        }),
      );
      if (labelled) labels.push(A.source(cell.center.ra, cell.center.dec, { kind: "count", text: countText(cell.count) }));
    }
    if (labels.length) labelCat.addSources(labels);
  }

  /** Each area once: its union outline and one faint fill. */
  function drawZones() {
    const ctx = prepare(zoneCanvas);
    if (!ctx || !model || !model.layers.zones) return;
    const { fill, edge } = zoneStyle(level);
    const maxJump = host.clientWidth / 2;
    // Keep the full ellipse even when zoomed in: an offscreen seam must not draw a chord through the view.
    const rx = 180 * host.clientWidth / scaleFov();
    const ellipse = mol ? { cx: host.clientWidth / 2, cy: host.clientHeight / 2, rx, ry: rx / 2 } : undefined;
    const project = (p: SkyPos) => !mol && sepDeg(p, view()) > 90 ? null : rawScreen(p);
    function path(points: Vec[], closed: boolean) {
      if (!points.length) return;
      ctx!.moveTo(points[0].x, points[0].y);
      for (const p of points.slice(1)) ctx!.lineTo(p.x, p.y);
      if (closed) ctx!.closePath();
    }
    for (const a of model.areas) {
      if (fill > 0) {
        ctx.beginPath();
        for (const points of zoneFills(a, project, maxJump, ellipse)) path(points, true);
        ctx.fillStyle = rgba(zoneInk(), fill);
        ctx.fill();
      }
      ctx.beginPath();
      for (const points of zoneEdges(a, project, maxJump, ellipse)) path(points, false);
      ctx.strokeStyle = rgba(zoneInk(), edge);
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }

  function drawSignals() {
    signalCat.removeAll();
    if (!model || !model.layers.signals || !showsDots(level)) return;
    signalCat.addSources(model.candidates.map((c) => A.source(c.ra, c.dec, { c, id: c.id })));
  }

  function drawProvisional() {
    provisionalCat.removeAll();
    const p = model?.provisional;
    if (!p || !model?.layers.provisional || !showsDots(level) || level === "mid") return;
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
  const zoneCanvas = document.createElement('canvas');
  zoneCanvas.className = 'atl-zonecanvas';
  zoneCanvas.setAttribute('aria-hidden', 'true');
  searchCanvas.after(zoneCanvas);
  // SPHEREx now: over the zones, under Aladin's marks and the names.
  const nowCanvas = document.createElement("canvas");
  nowCanvas.className = "atl-nowcanvas";
  nowCanvas.setAttribute("aria-hidden", "true");
  zoneCanvas.after(nowCanvas);

  /** Size a canvas to the map at the screen's pixel ratio; its 2D context, scaled to CSS px. */
  function prepare(canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
    const w = host.clientWidth;
    const h = host.clientHeight;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = Math.round(w * dpr);
    const H = Math.round(h * dpr);
    if (canvas.width !== W || canvas.height !== H) {
      canvas.width = W;
      canvas.height = H;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  function drawSearch() {
    const ctx = prepare(searchCanvas);
    const layer = model?.search;
    if (!ctx || !layer || !model?.layers.search || !showsHeat(level) || !(host.clientWidth > 0)) return;
    paintSearch(ctx, layer, (ra, dec) => rawScreen({ ra, dec }), searchInk, { w: host.clientWidth, h: host.clientHeight });
  }

  // ---- SPHEREx now: the band the survey works in, where it is probably looking, its newest photos --
  /** Colours for the dark sky, or for the plate negative (light glass). */
  function nowInk() {
    return layers.negative
      ? { band: ink.violet, bandA: 0.14, look: ink.violet, lookA: 0.4, real: "#0e7f68", craft: ink.ink, sun: "#a35f00", halo: rgba(ink.glass, 0.95), text: ink.ink }
      : { band: "#9d7dff", bandA: 0.2, look: "#f1ebff", lookA: 0.5, real: "#4fd6b0", craft: "#ffffff", sun: "#ffc85c", halo: "rgba(0, 0, 0, 0.85)", text: "#f5f7f9" };
  }

  // The band's cells are painted as one opaque shape on their own canvas, then laid on the map at one
  // alpha (and softened), so they read as one smooth area instead of a field of dots.
  const bandCanvas = document.createElement("canvas");
  let bandCache: { model: NowModel; t: number; cells: BandCell[] } | null = null;

  function drawNow() {
    const ctx = prepare(nowCanvas);
    const now = model?.now;
    if (!ctx || !now || !model?.layers.now || !(host.clientWidth > 0)) return;
    const W = host.clientWidth;
    const H = host.clientHeight;
    const dpr = nowCanvas.width / W;
    const v = view();
    const c = nowInk();
    const t = now.t;
    // The globe shows its near side only; the oval shows everything.
    const at = (p: SkyPos) => (!mol && sepDeg(p, v) > 90 ? null : rawScreen(p));
    const inside = (q: { x: number; y: number } | null, m = 8) => !!q && q.x > -m && q.y > -m && q.x < W + m && q.y < H + m;
    // Pixels per degree near the middle of the view.
    const mid = rawScreen(v);
    const off = rawScreen({ ra: v.ra, dec: v.dec > 0 ? v.dec - 2 : v.dec + 2 });
    const perDeg = mid && off ? Math.hypot(off.x - mid.x, off.y - mid.y) / 2 : W / Math.max(1, v.fov);

    // 1. The band the survey works in that day (the Sun moves it about 1° a day: recomputed every ~15 min).
    if (!bandCache || bandCache.model !== now.model || Math.abs(bandCache.t - t) > 0.01) bandCache = { model: now.model, t, cells: bandAt(now.model, t) };
    const o = now.model.file.orbit;
    const here = o && Math.abs(t - o.epoch_mjd) < 21 ? overheadAt(o, t) : null;
    const b = bandCanvas.getContext("2d");
    if (b) {
      bandCanvas.width = nowCanvas.width;
      bandCanvas.height = nowCanvas.height;
      const seen = new Map<SkyPos, { x: number; y: number } | null>();
      const proj = (p: SkyPos) => {
        if (!seen.has(p)) seen.set(p, at(p));
        return seen.get(p)!;
      };
      const paint = (cells: readonly BandCell[], color: string, alpha: number) => {
        b.setTransform(1, 0, 0, 1, 0, 0);
        b.clearRect(0, 0, bandCanvas.width, bandCanvas.height);
        b.setTransform(dpr, 0, 0, dpr, 0, 0);
        b.beginPath();
        for (const cell of cells) {
          const [p0, p1, p2, p3] = cell.corners.map(proj);
          if (!p0 || !p1 || !p2 || !p3) continue; // past the globe's limb
          const x0 = Math.min(p0.x, p1.x, p2.x, p3.x);
          const x1 = Math.max(p0.x, p1.x, p2.x, p3.x);
          const y0 = Math.min(p0.y, p1.y, p2.y, p3.y);
          const y1 = Math.max(p0.y, p1.y, p2.y, p3.y);
          if (x1 < -4 || y1 < -4 || x0 > W + 4 || y0 > H + 4) continue;
          if (x1 - x0 > W / 4 || y1 - y0 > H / 4) continue; // split by the oval's seam
          b.moveTo(p0.x, p0.y);
          b.lineTo(p1.x, p1.y);
          b.lineTo(p2.x, p2.y);
          b.lineTo(p3.x, p3.y);
          b.closePath();
        }
        b.fillStyle = color;
        b.fill();
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalAlpha = alpha;
        ctx.filter = `blur(${Math.round(Math.max(1, Math.min(4, perDeg * 0.6)) * dpr)}px)`;
        ctx.drawImage(bandCanvas, 0, 0);
        ctx.restore();
      };
      paint(bandCache.cells, c.band, c.bandA);
      // 2. The part it is probably looking at now: the band within 35° of the point overhead.
      if (here) paint(bandCache.cells.filter((cell) => sepDeg(cell.centre, here) <= CONE_DEG), c.look, c.lookA);
    }

    // 3. Real photos, only on days the public data covers: the week up to the moment shown.
    if (t <= now.model.newest + 0.5) {
      const size = Math.max(2.5, Math.min(7, 2.2 * Math.sqrt(180 / Math.max(1, v.fov))));
      ctx.fillStyle = c.real;
      for (const p of now.model.photos) {
        if (p.mjd > t || p.mjd <= t - 7) continue;
        const q = at(p);
        if (!inside(q)) continue;
        ctx.globalAlpha = 0.35 + 0.65 * (1 - (t - p.mjd) / 7);
        ctx.fillRect(q!.x - size / 2, q!.y - size / 2, size, size);
      }
      ctx.globalAlpha = 1;
    }

    // 4. SPHEREx: the point overhead, with the last 12 minutes of its path as a fading trail.
    if (o && here) {
      const trail = overheadTrack(o, t - 12 / 1440, 12, 0.5);
      ctx.lineCap = "round";
      for (let i = 1; i < trail.length; i++) {
        const a = at(trail[i - 1]);
        const z = at(trail[i]);
        if (!a || !z || Math.abs(a.x - z.x) > W / 4 || Math.abs(a.y - z.y) > H / 4) continue;
        const f = i / (trail.length - 1);
        ctx.strokeStyle = rgba(c.craft, 0.05 + 0.75 * f);
        ctx.lineWidth = 1 + 2 * f;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(z.x, z.y);
        ctx.stroke();
      }
      const q = at(here);
      if (inside(q)) {
        ctx.fillStyle = rgba(c.look, 0.3);
        ctx.beginPath();
        ctx.arc(q!.x, q!.y, 11, 0, 2 * Math.PI);
        ctx.fill();
        ctx.strokeStyle = c.craft;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(q!.x, q!.y, 6.5, 0, 2 * Math.PI);
        ctx.stroke();
        ctx.fillStyle = c.craft;
        ctx.beginPath();
        ctx.arc(q!.x, q!.y, 3, 0, 2 * Math.PI);
        ctx.fill();
        drawLabel(ctx, ink, q!.x + 16, q!.y, "SPHEREx", { color: c.text, shadow: c.halo, weight: 700, size: 13, align: "left" });
      }
    }

    // 5. The Sun, for bearings: the survey works in a ring about 90° from it.
    const sun = at(sunAt(t));
    if (inside(sun)) {
      ctx.fillStyle = c.sun;
      ctx.beginPath();
      ctx.arc(sun!.x, sun!.y, 5, 0, 2 * Math.PI);
      ctx.fill();
      drawLabel(ctx, ink, sun!.x + 10, sun!.y, "Sun", { color: c.sun, shadow: c.halo, weight: 650, size: 12, align: "left" });
    }
  }

  // ---- labels: zone badges and target names, placed clear of each other --------------------------
  const labelCanvas = document.createElement("canvas");
  labelCanvas.className = "atl-labelcanvas";
  labelCanvas.setAttribute("aria-hidden", "true");
  host.appendChild(labelCanvas);
  let placed: LabelHit[] = [];

  const font = (weight: number, px: number) => `${weight} ${px}px Archivo, system-ui, sans-serif`;
  function measurer(ctx: CanvasRenderingContext2D | null, weight: number, px: number, padX: number, h: number): (t: string) => Size {
    if (!ctx) return (t) => estimateSize(t, px, padX, h);
    return (t) => {
      ctx.save();
      ctx.font = font(weight, px);
      try {
        (ctx as CanvasRenderingContext2D & { fontStretch?: string }).fontStretch = "semi-condensed";
      } catch {
        /* normal width */
      }
      const w = ctx.measureText(t).width;
      ctx.restore();
      return { w: Math.ceil(w + 2 * padX + 2), h };
    };
  }

  /** The area's screen box, from its outline's visible points; null when none is on screen. */
  function areaBox(a: ZoneArea): Box | null {
    const pts: { x: number; y: number }[] = [];
    for (const ring of a.rings) for (const [ra, dec] of densifyRing(ring, 1)) {
      const p = rawScreen({ ra, dec });
      if (p) pts.push(p);
    }
    return boundsOf(pts);
  }

  function drawLabels() {
    const ctx = prepare(labelCanvas);
    placed = [];
    if (!model || destroyed) return;
    const W = host.clientWidth;
    const H = host.clientHeight;
    if (!(W > 0) || !(H > 0)) return;
    const bounds: Box = { x0: 4, y0: 4, x1: W - 4, y1: H - 4 };
    const reserved = cb.reserved?.() ?? [];
    const obstacles: Box[] = [...reserved];
    // Under a panel: no name or badge (it would be drawn over the panel's edge).
    const covered = (p: { x: number; y: number }) => reserved.some((r) => boxHolds(r, p));
    // A star's dot and name stay well clear of a panel's edge (and its shadow).
    const nearPanel = (q: { x: number; y: number }) => reserved.some((r) => boxHolds({ x0: r.x0 - 14, y0: r.y0 - 14, x1: r.x1 + 14, y1: r.y1 + 14 }, q));
    const specs: (LabelSpec & ({ kind: "target"; target: Target } | { kind: "star" }))[] = [];
    // Zones carry no count badge: the outline is the zone, and a click on it opens it.
    if (model.layers.zones) {
      for (const a of model.areas) {
        const b = areaBox(a);
        if (b) obstacles.push(b);
      }
    }
    const marks: { t: Target; p: { x: number; y: number } }[] = [];
    const v = view();
    for (const t of model.targets) {
      const p = rawScreen(t);
      if (!p || p.x < 0 || p.y < 0 || p.x > W || p.y > H) continue;
      // Only the globe's near side: a name never floats over a target behind it.
      if (!mol && sepDeg(t, v) > 90) continue;
      if (covered(p)) continue;
      marks.push({ t, p });
      obstacles.push({ x0: p.x - 9, y0: p.y - 9, x1: p.x + 9, y1: p.y + 9 });
    }
    for (const { t, p } of marks) specs.push({ kind: "target", target: t, id: `target:${t.slug}`, texts: [t.short], anchor: p, places: (s) => aroundPoint(p.x, p.y, s) });
    // The bright named stars: a dot each, and a name where one fits after the targets'.
    const stars: { x: number; y: number }[] = [];
    for (const st of BRIGHT_STARS) {
      const p = rawScreen(st);
      if (!p || p.x < 0 || p.y < 0 || p.x > W || p.y > H) continue;
      if (!mol && sepDeg(st, v) > 90) continue;
      if (nearPanel(p)) continue;
      stars.push(p);
      obstacles.push({ x0: p.x - 4, y0: p.y - 4, x1: p.x + 4, y1: p.y + 4 });
      specs.push({ kind: "star", id: `star:${st.name}`, texts: [st.name], anchor: p, places: (s) => aroundPoint(p.x, p.y, s, 6) });
    }
    const measureStar = measurer(ctx, STAR.weight, STAR.font, 0, STAR.h);
    const measureName = measurer(ctx, NAME.weight, NAME.font, 0, NAME.h);
    const got = placeLabels(specs, (t, s) => (s.kind === "star" ? measureStar(t) : measureName(t)), obstacles, bounds);
    const starNames: Placed[] = [];
    for (const p of got) {
      const s = specs.find((x) => x.id === p.id)!;
      if (s.kind === "target") placed.push({ ...p, kind: "target", target: s.target });
      else starNames.push(p);
    }
    if (!ctx) return;
    for (const p of placed) drawLabel(ctx, ink, p.box.x0, (p.box.y0 + p.box.y1) / 2, p.text, { ...soft(), align: "left", weight: NAME.weight, size: NAME.font });
    ctx.save();
    ctx.fillStyle = layers.negative ? ink.ink : "#fff6d8";
    ctx.shadowColor = layers.negative ? rgba(ink.glass, 0.95) : "rgba(255, 236, 170, 0.9)";
    ctx.shadowBlur = 5;
    for (const p of stars) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 2.2, 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.restore();
    for (const p of starNames) drawLabel(ctx, ink, p.box.x0, (p.box.y0 + p.box.y1) / 2, p.text, { ...soft(), align: "left", weight: STAR.weight, size: STAR.font });
  }

  // ---- the whole sky as large as the map holds it; the area outside the projection stays open --
  /** The field last fitted to the map, and whether the view still sits at it (nobody zoomed since). */
  let lastFit = fitNow();
  let atFit = wantsWhole(init.view.fov);

  function setRange(fit: number) {
    try {
      al.setFoVRange?.(null, fit);
    } catch {
      /* the clamp in fitView holds the line */
    }
  }
  setRange(lastFit);
  // Aladin's own option stops at 360° (or 180°): past it, only setFoV takes the field.
  if (atFit) {
    try {
      al.setFoV(lastFit);
    } catch {
      /* the first view change refits */
    }
  }

  /** Keep the whole sky whole: never smaller than the map holds, and refitted when the map resizes. */
  function fitView(): number {
    const fit = fitNow();
    let tf = scaleFov();
    if (fit !== lastFit) setRange(fit);
    const refit = tf > fit * 1.002 || (atFit && Math.abs(fit - lastFit) > lastFit * 0.002);
    if (refit) {
      try {
        al.setFoV(fit);
        tf = fit;
      } catch {
        /* keep the view */
      }
    }
    atFit = Math.abs(tf - fit) <= fit * 0.01;
    lastFit = fit;
    return tf;
  }

  function clip(tf: number) {
    const e = limbEllipse(mol, tf, host.clientWidth, host.clientHeight);
    // Outside the projection Aladin paints its background: leave that open to the page instead.
    mapEl.style.clipPath = e ? `ellipse(${e.rx}px ${e.ry}px at ${e.cx}px ${e.cy}px)` : "";
  }

  /**
   * Aladin's events come throttled and stop before its animations do (a fly, the wheel's glide, a
   * resize it applies a frame late). So after each event the map is watched frame by frame and
   * redrawn whenever the view really changed, until it has been still for half a second.
   */
  let raf = 0;
  let still = 0;
  let lastSig = "";
  function signature(): string {
    try {
      const [ra, dec] = al.getRaDec();
      const c = al.world2pix(ra, dec);
      return `${ra.toFixed(6)} ${dec.toFixed(6)} ${scaleFov().toFixed(5)} ${host.clientWidth} ${host.clientHeight} ${c ? `${Math.round(c[0])} ${Math.round(c[1])}` : ""}`;
    } catch {
      return "";
    }
  }
  function onMove() {
    if (destroyed) return;
    still = 0;
    lastSig = "";
    if (raf) return;
    const tick = () => {
      raf = 0;
      if (destroyed) return;
      const sig = signature();
      if (sig !== lastSig) {
        redraw();
        lastSig = signature();
        still = 0;
      } else still++;
      if (still < 30) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }

  function redraw() {
    clip(fitView());
    const v = view();
    const nextLevel = levelFor(v.fov);
    const nextOrder = heatOrder(v.fov, host.clientWidth || 800);
    const wasLabelled = cellPx(order, lastFov, host.clientWidth || 800) >= LABEL_MIN_PX;
    const isLabelled = cellPx(nextOrder, v.fov, host.clientWidth || 800) >= LABEL_MIN_PX;
    lastFov = v.fov;
    if (nextLevel !== level || nextOrder !== order || wasLabelled !== isLabelled) {
      const dotsChanged = showsDots(nextLevel) !== showsDots(level) || (nextLevel === "mid") !== (level === "mid");
      level = nextLevel;
      order = nextOrder;
      drawHeat();
      if (dotsChanged) {
        drawSignals();
        drawProvisional();
      }
      drawTargets();
    }
    drawZones();
    drawNow();
    drawSearch();
    drawLabels();
    cb.onView(v);
  }
  let lastFov = init.view.fov;
  al.on("positionChanged", onMove);
  al.on("zoomChanged", onMove);
  // Past 360° (or 180°) Aladin reports no zoom change at all: the wheel and touches start the watch too.
  const onInput = () => onMove();
  for (const t of ["wheel", "pointerup", "touchend"]) mapEl.addEventListener(t, onInput, { passive: true, capture: true });
  /** At the whole sky, zooming further out is refused before Aladin sees it (else it shrinks the
   *  sky for a moment and the refit snaps it back, showing its background around the sky). */
  const atWhole = () => scaleFov() >= lastFit * 0.99;
  const onWheelOut = (e: WheelEvent) => {
    if (e.deltaY > 0 && atWhole()) {
      e.preventDefault();
      e.stopPropagation();
    }
  };
  host.addEventListener("wheel", onWheelOut, { passive: false, capture: true });
  clip(scaleFov());
  let ro: ResizeObserver | null = null;
  if (typeof ResizeObserver === "function") {
    ro = new ResizeObserver(() => onMove());
    ro.observe(host);
  }

  // ---- hit-testing (in Aladin's div, which fills the map) ------------------------------------
  function rawScreen(pos: SkyPos): { x: number; y: number } | null {
    try {
      const p = al.world2pix(pos.ra, pos.dec);
      if (!p || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) return null;
      return { x: p[0], y: p[1] };
    } catch {
      return null;
    }
  }

  function skyAt(x: number, y: number): SkyPos | null {
    try {
      const p = al.pix2world?.(x, y);
      return p && Number.isFinite(p[0]) && Number.isFinite(p[1]) ? { ra: p[0], dec: p[1] } : null;
    } catch {
      return null;
    }
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
    if (!model || !model.layers.signals || !showsDots(level)) return null;
    return near(model.candidates, (c) => c.id, x, y, level === "mid" ? 9 : 14);
  }

  function targetAt(x: number, y: number): Target | null {
    if (!model) return null;
    return near(model.targets, (t) => t.slug, x, y, 12);
  }

  function labelAt(x: number, y: number): LabelHit | null {
    return placed.find((p) => boxHolds(p.box, { x, y })) ?? null;
  }

  /** On the whole sky a zone is a small patch: a click anywhere on it opens it. */
  function zoneAt(pos: SkyPos | null): ZoneArea | null {
    if (!pos || !model || !model.layers.zones || level !== "whole") return null;
    return areaAt(model.areas, pos);
  }

  function cellAt(pos: SkyPos): Cell | null {
    if (!model || !model.layers.heat || !showsHeat(level)) return null;
    const pix = ang2pixNest(order, pos.ra, pos.dec);
    return model.cellsAt(order).find((c) => c.pix === pix) ?? null;
  }

  al.on("click", (e: AladinClick) => {
    if (destroyed || !e || e.isDragging || !Number.isFinite(e.ra) || !Number.isFinite(e.dec)) return;
    const l = labelAt(e.x, e.y);
    if (l) return l.kind === "zone" ? cb.onZone(l.area) : cb.onTarget(l.target);
    const s = signalAt(e.x, e.y);
    if (s) return cb.onSignal(s);
    const t = targetAt(e.x, e.y);
    if (t) return cb.onTarget(t);
    const z = zoneAt(e);
    if (z) return cb.onZone(z);
    const cell = cellAt(e);
    if (cell) return cb.onCell(cell);
    cb.onEmpty({ ra: e.ra, dec: e.dec }, e.x, e.y);
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
      const { x, y } = lastMouse;
      const s = signalAt(x, y);
      const pointing = !!(s || labelAt(x, y) || targetAt(x, y) || zoneAt(level === "whole" ? skyAt(x, y) : null));
      const id = s?.id ?? null;
      mapEl.classList.toggle("is-pointing", pointing);
      if (id !== hoverId) {
        hoverId = id;
        repaint();
      }
      cb.onHover(s, x, y);
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
      if (!prev || prev.candidates !== m.candidates || prev.layers.heat !== m.layers.heat) drawHeat();
      if (!prev || prev.candidates !== m.candidates || prev.layers.signals !== m.layers.signals) drawSignals();
      if (!prev || prev.targets !== m.targets) drawTargets();
      if (!prev || prev.provisional !== m.provisional || prev.layers.provisional !== m.layers.provisional) drawProvisional();
      if (!prev || prev.search !== m.search || prev.layers.search !== m.layers.search) drawSearch();
      if (!prev || prev.now !== m.now || prev.layers.now !== m.layers.now || layersChanged) drawNow();
      if (prev && prev.selectedId !== m.selectedId) repaint();
      // The page's panels may have moved (the header, a message): refit the whole sky, place names again.
      clip(fitView());
      drawZones();
      drawLabels();
    },
    view,
    flyTo(to, animate) {
      stopFlight();
      // The whole sky means as large as the map holds it.
      const whole = wantsWhole(to.fov);
      const fov = whole ? fitNow() : to.fov;
      try {
        if (animate && al.animateToRaDec && al.zoomToFoV) {
          al.animateToRaDec(to.ra, to.dec, 1.1);
          // Aladin's zoom animation stops at 360° (or 180°): the fit past it is set at once.
          if (whole) al.setFoV(fov!);
          else if (fov != null) al.zoomToFoV(fov, 1.1);
        } else {
          al.gotoRaDec(to.ra, to.dec);
          if (fov != null) al.setFoV(fov);
        }
      } catch {
        /* the view stays */
      }
      if (whole) atFit = true;
      onMove();
    },
    setProjection(m) {
      mol = m;
      setRange(fitNow());
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
        if (f > 1 && atWhole()) return;
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
      const w = host.clientWidth || 1;
      const h = host.clientHeight || 1;
      // Move the centre to the sky under the screen point (cx + dx·w, cy + dy·h).
      const p = skyAt(w / 2 + dx * w, h / 2 + dy * h);
      try {
        al.gotoRaDec(p ? p.ra : v.ra, p ? p.dec : v.dec);
      } catch {
        /* ignore */
      }
      onMove();
    },
    screenOf: rawScreen,
    destroy() {
      destroyed = true;
      if (raf) cancelAnimationFrame(raf);
      if (hoverRaf) cancelAnimationFrame(hoverRaf);
      mapEl.removeEventListener("mousemove", onMouseMove);
      mapEl.removeEventListener("mouseleave", onMouseLeave);
      for (const t of ["wheel", "pointerup", "touchend"]) mapEl.removeEventListener(t, onInput, { capture: true });
      host.removeEventListener("wheel", onWheelOut, { capture: true });
      ro?.disconnect();
      searchCanvas.remove();
      zoneCanvas.remove();
      nowCanvas.remove();
      labelCanvas.remove();
      mapEl.remove();
    },
  };
}
