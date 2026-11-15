// The sky map: Aitoff projection in SVG, equatorial or galactic, the whole sky or zoomed to the
// candidates. Every candidate is a point shaped by kind (circle, triangle, square: colour is never
// the only cue); the filtered set is drawn in colour, the rest as faint context. Hover and keyboard
// focus show a point's details. It fills the To check page's map view (#/candidates/…&map=1), so its
// height follows the window; close-ups go one search zone at a time.
import { GRADE_TEXT, gradeLabel, gradeOf } from "../api/grades";
import type { Candidate, CandidateKind } from "../api/types";
import { formatRaDec } from "../app/astro";
import { KIND_TEXT, KINDS } from "./text";

/** Grade C points are greyed (the list and the tooltip say why in words). */
const gradeCls = (c: Candidate) => (gradeOf(c) === "C" ? " is-gradec" : "");

const D2R = Math.PI / 180;
const html = String.raw;
/** What the zone row's names are (pipeline/src/skyshift_pipeline/config.py, ecliptic_zones). */
const ZONES_HINT = "The sky patches we searched: 10° boxes along the ecliptic, numbered from RA 0° (n1 is the box north of it).";
/** On the whole sky, ring and name each zone only while there are this few. */
const MAX_RINGS = 6;

// ---- projection maths --------------------------------------------------------------------------

/** Wrap an angle in degrees to [−180, 180). */
export function wrap180(deg: number): number {
  return ((((deg + 180) % 360) + 360) % 360) - 180;
}

/**
 * Aitoff projection. lon, lat and centreLon in degrees. Returns x in [−π, π], y in [−π/2, π/2],
 * with longitude increasing to the LEFT (east left, as the sky is seen from inside).
 */
export function aitoff(lonDeg: number, latDeg: number, centerLon = 0): { x: number; y: number } {
  const lam = wrap180(lonDeg - centerLon) * D2R;
  const phi = latDeg * D2R;
  const alpha = Math.acos(Math.max(-1, Math.min(1, Math.cos(phi) * Math.cos(lam / 2))));
  const sinc = alpha < 1e-12 ? 1 : Math.sin(alpha) / alpha;
  return { x: -(2 * Math.cos(phi) * Math.sin(lam / 2)) / sinc, y: Math.sin(phi) / sinc };
}

/** Invert the projection at the view centre, so the graticule follows a dragged view. */
export function inverseAitoff(x: number, y: number, centerLon = 0): { lon: number; lat: number } {
  let lon = -x / D2R;
  let lat = y / D2R;
  const limit = (v: number, max: number) => Math.max(-max, Math.min(max, v));
  const eps = 0.0001;
  for (let i = 0; i < 24; i++) {
    const p = aitoff(centerLon + lon, lat, centerLon);
    const dx = p.x - x;
    const dy = p.y - y;
    if (Math.hypot(dx, dy) < 1e-9) break;
    const a = aitoff(centerLon + lon + eps, lat, centerLon);
    const b = aitoff(centerLon + lon, lat + eps, centerLon);
    const ax = (a.x - p.x) / eps, ay = (a.y - p.y) / eps;
    const bx = (b.x - p.x) / eps, by = (b.y - p.y) / eps;
    const det = ax * by - ay * bx;
    if (Math.abs(det) < 1e-12) break;
    lon = limit(lon - limit((dx * by - dy * bx) / det, 30), 179.999);
    lat = limit(lat - limit((dy * ax - dx * ay) / det, 30), 89.999);
  }
  return { lon: centerLon + lon, lat };
}

// IAU 1958 galactic frame, J2000 rotation (ICRS → galactic).
const R = [
  [-0.0548755604162154, -0.873437090234885, -0.4838350155487132],
  [0.4941094278755837, -0.4448296299600112, 0.7469822444972189],
  [-0.8676661490190047, -0.1980763734312015, 0.4559837761750669],
];

function rotate(m: number[][], lon: number, lat: number, transpose: boolean): { lon: number; lat: number } {
  const v = [Math.cos(lat * D2R) * Math.cos(lon * D2R), Math.cos(lat * D2R) * Math.sin(lon * D2R), Math.sin(lat * D2R)];
  const at = (i: number, j: number) => (transpose ? m[j][i] : m[i][j]);
  const g = [0, 1, 2].map((i) => at(i, 0) * v[0] + at(i, 1) * v[1] + at(i, 2) * v[2]);
  const outLon = (Math.atan2(g[1], g[0]) / D2R + 360) % 360;
  const outLat = Math.asin(Math.max(-1, Math.min(1, g[2]))) / D2R;
  return { lon: outLon, lat: outLat };
}

/** ICRS (ra, dec) → galactic (l, b), degrees. */
export function eqToGal(ra: number, dec: number): { l: number; b: number } {
  const r = rotate(R, ra, dec, false);
  return { l: r.lon, b: r.lat };
}

/** Galactic (l, b) → ICRS (ra, dec), degrees. */
export function galToEq(l: number, b: number): { ra: number; dec: number } {
  const r = rotate(R, l, b, true);
  return { ra: r.lon, dec: r.lat };
}

/** Circular mean of longitudes, degrees in [0, 360). */
export function meanLon(lons: number[]): number {
  if (!lons.length) return 0;
  let sx = 0;
  let sy = 0;
  for (const l of lons) {
    sx += Math.cos(l * D2R);
    sy += Math.sin(l * D2R);
  }
  return ((Math.atan2(sy, sx) / D2R) % 360 + 360) % 360;
}

/** A graticule step (degrees) that gives about three to five lines across a span. */
export function gratStep(spanDeg: number): number {
  const steps = [0.25, 0.5, 1, 2, 5, 10, 15, 30];
  return steps.find((s) => spanDeg / s <= 5) ?? 30;
}

/** RA in hours and minutes for an axis label: "8h 12m". */
export function raLabel(raDeg: number): string {
  const totalMin = Math.round((((raDeg % 360) + 360) % 360) * 4);
  const h = Math.floor(totalMin / 60) % 24;
  const m = totalMin % 60;
  return m === 0 ? `${h}h` : `${h}h ${String(m).padStart(2, "0")}m`;
}

/** Signed degrees for an axis label, with arcminutes when the step is under a degree. */
export function latLabel(deg: number, fine: boolean): string {
  const sign = deg < 0 ? "−" : deg > 0 ? "+" : "";
  const a = Math.abs(deg);
  if (!fine) return `${sign}${Math.round(a)}°`;
  const d = Math.floor(a + 1e-9);
  const m = Math.round((a - d) * 60);
  return m ? `${sign}${d}°${String(m).padStart(2, "0")}′` : `${sign}${d}°`;
}

// ---- glyphs: shape carries kind -----------------------------------------------------------------

/** SVG path for a kind's glyph centred on (x, y); r is the glyph's half-size in px. */
export function glyphPath(kind: CandidateKind, x: number, y: number, r: number): string {
  const f = (n: number) => n.toFixed(1);
  if (kind === "mover") {
    const h = r * 1.25;
    return `M${f(x)},${f(y - h)}L${f(x + h * 0.95)},${f(y + h * 0.7)}L${f(x - h * 0.95)},${f(y + h * 0.7)}Z`;
  }
  if (kind === "spectral") {
    const s = r * 0.88;
    return `M${f(x - s)},${f(y - s)}H${f(x + s)}V${f(y + s)}H${f(x - s)}Z`;
  }
  if (kind === "shape") {
    // A diamond: one object or two?
    const h = r * 1.2;
    return `M${f(x)},${f(y - h)}L${f(x + h)},${f(y)}L${f(x)},${f(y + h)}L${f(x - h)},${f(y)}Z`;
  }
  return `M${f(x - r)},${f(y)}a${f(r)},${f(r)} 0 1,0 ${f(2 * r)},0a${f(r)},${f(r)} 0 1,0 ${f(-2 * r)},0Z`;
}

/** A small inline SVG of a kind's glyph, for the list, legend and filters. */
export function glyphSvg(kind: CandidateKind, size = 12, cls = ""): string {
  const r = size * 0.36;
  return html`<svg class="kind-glyph kind-${kind} ${cls}" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true" focusable="false"><path d="${glyphPath(kind, size / 2, size / 2 + (kind === "mover" ? size * 0.04 : 0), r)}"/></svg>`;
}

/** The map's own glyph (the whole-sky oval with two points), for the link that opens it. */
export function mapGlyph(width = 18): string {
  return html`<svg class="map-glyph" width="${width}" height="${Math.round(width * 0.6)}" viewBox="0 0 20 12" aria-hidden="true" focusable="false"><ellipse cx="10" cy="6" rx="9" ry="5.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M1 6h18" fill="none" stroke="currentColor" stroke-width="0.9" opacity="0.45"/><circle cx="6.5" cy="3.9" r="1.5" fill="currentColor"/><circle cx="13" cy="7.9" r="1.5" fill="currentColor"/></svg>`;
}

/** A search zone's name for people: "pilot" → "Pilot", "ecl12n1" → "Ecl12 n1". */
export function zoneLabel(zone: string): string {
  const s = zone.replace(/^(ecl\d+)([ns]\d+)$/i, "$1 $2");
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

// ---- the map ------------------------------------------------------------------------------------

export type Frame = "eq" | "gal";
export type Zoom = "fit" | "sky";

interface Pt {
  c: Candidate;
  lon: number;
  lat: number;
  sx: number;
  sy: number;
}

export interface SkyMapOptions {
  onOpen(id: string): void;
  /** Hover or keyboard focus moved to a point (null: left the map). */
  onHover?(id: string | null): void;
  frame?: Frame;
  zoom?: Zoom;
  onViewChange?(v: { frame: Frame; zoom: Zoom }): void;
}

export interface SkyMapHandle {
  setData(all: Candidate[]): void;
  /** The filtered set, in list order. */
  setFiltered(ids: string[]): void;
  /** Highlight a point from outside (a list row); null clears. */
  highlight(id: string | null): void;
  closeFullscreen(): void;
  destroy(): void;
}

export function mountSkyMap(el: HTMLElement, opts: SkyMapOptions): SkyMapHandle {
  let frame: Frame = opts.frame ?? "eq";
  let zoom: Zoom = opts.zoom ?? "fit";
  let all: Candidate[] = [];
  let filtered: string[] = [];
  let filteredSet = new Set<string>();
  let pts: Pt[] = [];
  let byId = new Map<string, Pt>();
  let byIdAll = new Map<string, Candidate>();
  let hot: string | null = null;
  let hotFrom: "map" | "list" | null = null;
  /** Close up fits one pipeline zone at a time; null = the zone with the most filtered candidates. */
  let zonePick: string | null = null;
  let zones: string[] = [];
  let W = 0;
  let H = 0;
  const view = { x: 0, y: 0, scale: 1 };
  let baseK = 1;
  let baseX = 0;
  let baseY = 0;
  let sceneKey = "";
  let drawn = { k: 1, cx: 0, cy: 0 };
  let previewFrame: number | null = null;
  let settleTimer: ReturnType<typeof setTimeout> | null = null;
  const pointers = new Map<number, { x: number; y: number }>();
  let gesture: { x: number; y: number; distance: number; view: typeof view; k: number } | null = null;
  let moved = false;
  let suppressClick = false;

  el.classList.add("skymap");
  el.innerHTML = html`
    <div class="skymap-head">
      <h2 id="dv-map-title" class="dv-h2">On the sky</h2>
      <button type="button" class="btn btn-sm" data-fullscreen aria-haspopup="dialog">Full screen</button>
    </div>
    <div class="skymap-bar">
      <div class="seg" role="group" aria-label="Map extent">
        <button type="button" data-zoom="fit" title="Zoom to the objects to check">Close up</button>
        <button type="button" data-zoom="sky" title="The whole sky">Whole sky</button>
      </div>
      <div class="seg" role="group" aria-label="Coordinates">
        <button type="button" data-frame="eq" title="Equatorial: right ascension and declination">RA, Dec</button>
        <button type="button" data-frame="gal" title="Galactic: longitude l and latitude b">Galactic</button>
      </div>
      <div class="skymap-navigation" role="group" aria-label="Map zoom">
        <button type="button" class="btn btn-sm" data-map-action="out" aria-label="Zoom out" title="Zoom out (−)">−</button>
        <output class="skymap-scale" aria-label="Zoom level">100%</output>
        <button type="button" class="btn btn-sm" data-map-action="in" aria-label="Zoom in" title="Zoom in (+)">+</button>
        <button type="button" class="btn btn-sm" data-map-action="reset" title="Return to the selected zone or whole sky (0)">Reset view</button>
      </div>
      <label class="skymap-zones" title="${ZONES_HINT}" hidden><span>Search zone</span><select class="skymap-zone-select" aria-label="Search zone shown close up"></select></label>
    </div>
    <div class="skymap-plot" tabindex="0" role="group"
      aria-roledescription="sky map" aria-describedby="skymap-help skymap-controls-help">
      <svg class="skymap-svg" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"></svg>
      <div class="skymap-tip" hidden></div>
    </div>
    <div class="skymap-foot">
      <svg class="skymap-loc" viewBox="0 0 100 52" width="100" height="52" aria-hidden="true"></svg>
      <div>
        <p class="skymap-caption" id="skymap-help"></p>
        <p class="skymap-help" id="skymap-controls-help">Drag to move. Scroll or pinch to zoom. Use +/− to zoom, Shift + arrows to move, and 0 to reset.</p>
      </div>
    </div>
    <ul class="skymap-legend" aria-label="Legend"></ul>
    <p class="visually-hidden" aria-live="polite" data-live></p>
  `;
  const plot = el.querySelector<HTMLDivElement>(".skymap-plot")!;
  const svg = el.querySelector<SVGSVGElement>(".skymap-svg")!;
  const tip = el.querySelector<HTMLDivElement>(".skymap-tip")!;
  const caption = el.querySelector<HTMLParagraphElement>(".skymap-caption")!;
  const loc = el.querySelector<SVGSVGElement>(".skymap-loc")!;
  const legend = el.querySelector<HTMLUListElement>(".skymap-legend")!;
  const live = el.querySelector<HTMLParagraphElement>("[data-live]")!;
  const zoomIn = el.querySelector<HTMLButtonElement>('[data-map-action="in"]')!;
  const zoomOut = el.querySelector<HTMLButtonElement>('[data-map-action="out"]')!;
  const reset = el.querySelector<HTMLButtonElement>('[data-map-action="reset"]')!;
  const scaleLabel = el.querySelector<HTMLOutputElement>(".skymap-scale")!;

  const minScale = () => zoom === "sky" ? 1 : 0.25;
  const clampScale = (s: number) => Math.max(minScale(), Math.min(64, s));
  const camera = () => {
    const k = baseK * view.scale;
    return { k, cx: W / 2 - (baseX + view.x) * k, cy: H / 2 + (baseY + view.y) * k };
  };
  function constrainView() {
    // Keep the centre on the projected sky; Reset view is always available after moving.
    let x = baseX + view.x, y = baseY + view.y;
    const r = Math.hypot(x / Math.PI, y / (Math.PI / 2));
    if (r > 0.999) { x *= 0.999 / r; y *= 0.999 / r; }
    view.x = x - baseX;
    view.y = y - baseY;
  }
  function updateNavigation() {
    zoomIn.disabled = view.scale >= 64;
    zoomOut.disabled = view.scale <= minScale();
    reset.disabled = view.scale === 1 && Math.hypot(view.x, view.y) < 1e-9;
    scaleLabel.value = `${Math.round(view.scale * 100)}%`;
  }
  function cancelPreview() {
    if (previewFrame !== null) cancelAnimationFrame(previewFrame);
    if (settleTimer !== null) clearTimeout(settleTimer);
    previewFrame = null;
    settleTimer = null;
  }
  function stopGesture() {
    pointers.clear();
    gesture = null;
    plot.classList.remove("is-dragging");
  }
  function resetView() {
    stopGesture();
    Object.assign(view, { x: 0, y: 0, scale: 1 });
    setHot(null, null);
    render();
  }
  function previewView() {
    const c = camera();
    const ratio = c.k / drawn.k;
    const box = { x: drawn.cx - c.cx / ratio, y: drawn.cy - c.cy / ratio, width: W / ratio, height: H / ratio };
    svg.setAttribute("viewBox", `${box.x} ${box.y} ${box.width} ${box.height}`);
    // Reveal points beyond the old viewport while dragging, without rebuilding 11,000 marks.
    for (const rect of svg.querySelectorAll("clipPath rect, .sm-bg")) {
      for (const [key, value] of Object.entries(box)) rect.setAttribute(key, String(value));
    }
    svg.style.setProperty("--sm-point-scale", String(1 / ratio));
  }
  function queueView() {
    updateNavigation();
    if (previewFrame === null) {
      if (typeof requestAnimationFrame === "function") {
        previewFrame = requestAnimationFrame(() => { previewFrame = null; previewView(); });
      } else previewView();
    }
    if (settleTimer !== null) clearTimeout(settleTimer);
    if (!pointers.size) settleTimer = setTimeout(() => { settleTimer = null; render(); }, 120);
  }
  function zoomAt(factor: number, x = W / 2, y = H / 2, preview = false) {
    const scale = clampScale(view.scale * factor);
    const ratio = scale / view.scale;
    const { k } = camera();
    view.x += (x - W / 2) / k * (1 - 1 / ratio);
    view.y += (H / 2 - y) / k * (1 - 1 / ratio);
    view.scale = scale;
    constrainView();
    setHot(null, null);
    if (preview) queueView();
    else render();
  }
  zoomIn.addEventListener("click", () => zoomAt(1.5));
  zoomOut.addEventListener("click", () => zoomAt(1 / 1.5));
  reset.addEventListener("click", resetView);

  const zonesEl = el.querySelector<HTMLLabelElement>(".skymap-zones")!;
  const zoneSelect = zonesEl.querySelector<HTMLSelectElement>("select")!;
  zoneSelect.addEventListener("change", () => {
    zonePick = zoneSelect.value;
    render();
  });

  // Keep one map instance and its selected zone/filters when moving into the top layer.
  // The modal also provides a full-window view if native fullscreen is unavailable or denied.
  const fullscreenBtn = el.querySelector<HTMLButtonElement>("[data-fullscreen]")!;
  const dialog = document.createElement("dialog");
  dialog.className = "skymap-dialog";
  dialog.setAttribute("aria-label", "On the sky");
  const placeholder = document.createComment("sky map position");
  let nativeFullscreen = false;

  function closeFullscreen(restoreFocus = false) {
    stopGesture();
    if (!placeholder.isConnected) {
      if (previewFrame !== null || settleTimer !== null) render();
      return;
    }
    nativeFullscreen = false;
    if (document.fullscreenElement === el) void document.exitFullscreen().catch(() => {});
    dialog.close();
    placeholder.replaceWith(el);
    dialog.remove();
    document.body.classList.remove("skymap-fullscreen-open");
    fullscreenBtn.textContent = "Full screen";
    render();
    if (restoreFocus) fullscreenBtn.focus({ preventScroll: true });
  }

  fullscreenBtn.addEventListener("click", () => {
    if (dialog.open) {
      closeFullscreen(true);
      return;
    }
    el.before(placeholder);
    document.body.append(dialog);
    dialog.append(el);
    dialog.showModal();
    document.body.classList.add("skymap-fullscreen-open");
    fullscreenBtn.textContent = "Exit full screen";
    render();
    fullscreenBtn.focus({ preventScroll: true });
    if (document.fullscreenEnabled && el.requestFullscreen) {
      void el.requestFullscreen().catch(() => {});
    }
  });
  dialog.addEventListener("cancel", (ev) => {
    ev.preventDefault();
    closeFullscreen(true);
  });
  const onFullscreenChange = () => {
    if (document.fullscreenElement === el) {
      if (dialog.open) nativeFullscreen = true;
      else void document.exitFullscreen().catch(() => {});
    } else if (nativeFullscreen) closeFullscreen(true);
  };
  document.addEventListener("fullscreenchange", onFullscreenChange);

  /** The zone shown close up: the person's pick, else the one with the most filtered candidates. */
  function activeZone(): string | null {
    if (zonePick && zones.includes(zonePick)) return zonePick;
    const n = new Map<string, number>();
    const pool = filtered.length ? filtered.map((id) => byIdAll.get(id)).filter((c): c is Candidate => !!c) : all;
    for (const c of pool) n.set(c.zone, (n.get(c.zone) ?? 0) + 1);
    let best: string | null = null;
    for (const [z, k] of n) if (best === null || k > (n.get(best) ?? 0)) best = z;
    return best;
  }

  function markSegs() {
    el.querySelectorAll<HTMLButtonElement>("[data-zoom]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.zoom === zoom)));
    el.querySelectorAll<HTMLButtonElement>("[data-frame]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.frame === frame)));
  }
  el.querySelectorAll<HTMLButtonElement>("[data-zoom]").forEach((b) =>
    b.addEventListener("click", () => {
      zoom = b.dataset.zoom as Zoom;
      opts.onViewChange?.({ frame, zoom });
      render();
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-frame]").forEach((b) =>
    b.addEventListener("click", () => {
      frame = b.dataset.frame as Frame;
      opts.onViewChange?.({ frame, zoom });
      render();
    }),
  );

  const coords = (c: Candidate): { lon: number; lat: number } => {
    if (frame === "eq") return { lon: c.ra, lat: c.dec };
    const g = eqToGal(c.ra, c.dec);
    return { lon: g.l, lat: g.b };
  };

  function render() {
    cancelPreview();
    markSegs();
    W = Math.max(1, Math.round(plot.clientWidth || 380));
    H = Math.max(1, Math.round(plot.clientHeight || W * 0.62));
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", String(W));
    svg.setAttribute("height", String(H));

    const raw = all.map((c) => ({ c, ...coords(c) }));
    const zone = zones.length > 1 ? activeZone() : null;
    const nextKey = `${frame}/${zoom}/${zone}`;
    if (nextKey !== sceneKey) {
      stopGesture();
      Object.assign(view, { x: 0, y: 0, scale: 1 });
      sceneKey = nextKey;
    }
    const focus = zoom === "fit" ? raw.filter((p) => zone === null || p.c.zone === zone) : raw;
    const centerLon = zoom === "fit" && focus.length ? meanLon(focus.map((p) => p.lon)) : 0;
    const proj = raw.map((p) => ({ ...p, u: aitoff(p.lon, p.lat, centerLon) }));
    const fproj = zoom === "fit" ? proj.filter((p) => zone === null || p.c.zone === zone) : proj;
    zonesEl.hidden = !(zoom === "fit" && zones.length > 1);
    zoneSelect.value = zone ?? "";

    // Screen transform: unit Aitoff coordinates → px.
    let k: number;
    let cx: number;
    let cy: number;
    const m = 10;
    if (zoom === "sky" || !fproj.length) {
      k = Math.min((W - 2 * m) / (2 * Math.PI), (H - 2 * m) / Math.PI);
      cx = W / 2;
      cy = H / 2;
    } else {
      const xs = fproj.map((p) => p.u.x);
      const ys = fproj.map((p) => p.u.y);
      let x0 = Math.min(...xs);
      let x1 = Math.max(...xs);
      let y0 = Math.min(...ys);
      let y1 = Math.max(...ys);
      const minSpan = 1.2 * D2R;
      const grow = (a: number, b: number) => {
        const span = Math.max(b - a, minSpan);
        const mid = (a + b) / 2;
        return [mid - span * 0.62, mid + span * 0.62];
      };
      [x0, x1] = grow(x0, x1);
      [y0, y1] = grow(y0, y1);
      k = Math.min(W / (x1 - x0), H / (y1 - y0));
      cx = W / 2 - ((x0 + x1) / 2) * k;
      cy = H / 2 + ((y0 + y1) / 2) * k;
    }
    baseK = k;
    baseX = (W / 2 - cx) / k;
    baseY = (cy - H / 2) / k;
    constrainView();
    ({ k, cx, cy } = camera());
    drawn = { k, cx, cy };
    svg.style.removeProperty("--sm-point-scale");
    updateNavigation();
    const viewCenter = inverseAitoff(baseX + view.x, baseY + view.y, centerLon);
    const closeView = zoom === "fit" || view.scale > 1.01;
    const toS = (u: { x: number; y: number }) => ({ sx: cx + u.x * k, sy: cy - u.y * k });

    pts = proj.map((p) => ({ c: p.c, lon: p.lon, lat: p.lat, ...toS(p.u) }));
    byId = new Map(pts.map((p) => [p.c.id, p]));

    const parts: string[] = [];
    parts.push(html`<defs><clipPath id="skymap-clip"><rect x="0" y="0" width="${W}" height="${H}"/></clipPath></defs>`);
    parts.push(html`<rect class="sm-bg" x="0" y="0" width="${W}" height="${H}"/>`);
    parts.push(`<g clip-path="url(#skymap-clip)">`);
    if (zoom === "sky") {
      parts.push(html`<path class="sm-sky" d="${outline(toS)}"/>`);
    }
    parts.push(html`<path class="sm-band" d="${bandPath(frame, centerLon, toS, zoom === "sky" ? 2 : 0.5)}"/>`);

    // Graticule
    const visLatSpan = H / k / D2R;
    const step = closeView ? gratStep(visLatSpan) : 30;
    const latC = viewCenter.lat;
    const latLo = closeView ? Math.max(-90, latC - visLatSpan) : -90;
    const latHi = closeView ? Math.min(90, latC + visLatSpan) : 90;
    const lonHalf = closeView ? Math.min(180, (W / k / D2R) / Math.max(0.2, Math.cos(latC * D2R))) : 180;
    const lonLo = (closeView ? viewCenter.lon : centerLon) - lonHalf;
    const lonHi = (closeView ? viewCenter.lon : centerLon) + lonHalf;
    const fine = closeView ? Math.max(step / 20, 0.02) : 2;
    const lines: string[] = [];
    const labels: string[] = [];
    const lonStep = closeView ? frame === "eq" ? raStep(step) : step : 30;
    for (let L = Math.ceil(lonLo / lonStep) * lonStep; L <= lonHi + 1e-9; L += lonStep) {
      if (zoom === "sky" && Math.abs(wrap180(L - centerLon)) >= 179.9) continue;
      const seg: string[] = [];
      let best: { sx: number; sy: number } | null = null;
      for (let b = Math.max(latLo, -89.9); b <= Math.min(latHi, 89.9) + 1e-9; b += fine) {
        const s = toS(aitoff(L, b, centerLon));
        seg.push(`${seg.length ? "L" : "M"}${s.sx.toFixed(1)},${s.sy.toFixed(1)}`);
        if (closeView && s.sy <= H - 6 && s.sy >= H - 30 && (!best || s.sy > best.sy)) best = s;
      }
      lines.push(seg.join(""));
      if (closeView && best && best.sx > 28 && best.sx < W - 28) {
        const txt = frame === "eq" ? raLabel(L) : `l ${Math.round(((L % 360) + 360) % 360 * 100) / 100}°`;
        labels.push(html`<text class="sm-tick" x="${best.sx.toFixed(1)}" y="${H - 6}" text-anchor="middle">${txt}</text>`);
      }
    }
    for (let B = Math.ceil(latLo / step) * step; B <= latHi + 1e-9; B += step) {
      if (zoom === "sky" && Math.abs(B) >= 90) continue;
      const seg: string[] = [];
      let best: { sx: number; sy: number } | null = null;
      const lstep = closeView ? Math.max(lonHalf / 120, 0.01) : 2;
      let previousX: number | null = null;
      for (let L = lonLo; L <= lonHi + 1e-9; L += lstep) {
        const s = toS(aitoff(closeView ? L : Math.min(L, centerLon + 179.99), B, centerLon));
        const connected = previousX !== null && Math.abs(s.sx - previousX) < Math.PI * k;
        seg.push(`${connected ? "L" : "M"}${s.sx.toFixed(1)},${s.sy.toFixed(1)}`);
        previousX = s.sx;
        if (closeView && s.sx >= 4 && (!best || s.sx < best.sx)) best = s;
      }
      lines.push(seg.join(""));
      if (closeView && best && best.sy > 14 && best.sy < H - 24) {
        const txt = frame === "eq" ? latLabel(B, step < 1) : `b ${latLabel(B, step < 1)}`;
        labels.push(html`<text class="sm-tick" x="6" y="${(best.sy - 4).toFixed(1)}">${txt}</text>`);
      }
    }
    parts.push(html`<path class="sm-grat" d="${lines.join("")}"/>`);
    if (zoom === "sky") parts.push(html`<path class="sm-edge" d="${outline(toS)}"/>`);

    // Context first (not in the filtered set, one faint path), then each filtered point as its own
    // element so its surface ring separates it from its neighbours. The list's first is drawn last,
    // on top.
    const ctx: string[] = [];
    const rr = zoom === "sky" ? 3 : 3.4;
    for (const p of pts)
      if (!filteredSet.has(p.c.id)) ctx.push(`M${(p.sx - 1.8).toFixed(1)},${p.sy.toFixed(1)}a1.8,1.8 0 1,0 3.6,0a1.8,1.8 0 1,0 -3.6,0Z`);
    parts.push(html`<path class="sm-ctx" d="${ctx.join("")}"/>`);
    const marks: string[] = [];
    for (let i = filtered.length - 1; i >= 0; i--) {
      const p = byId.get(filtered[i]);
      if (p) marks.push(html`<path class="sm-pt kind-${p.c.kind}${gradeCls(p.c)}" d="${glyphPath(p.c.kind, p.sx, p.sy, rr)}"/>`);
    }
    parts.push(`<g class="sm-pts">${marks.join("")}</g>`);
    // A few far-apart zones get a ring and a name; a run of them along the ecliptic shows by its points.
    if (zoom === "sky" && zones.length <= MAX_RINGS) parts.push(zoneRings(pts));
    parts.push(`<g class="sm-hot"></g>`);
    parts.push(labels.join(""));
    parts.push(`</g>`);

    svg.innerHTML = parts.join("");
    loc.innerHTML = closeView && fproj.length ? locator(frame, [viewCenter]) : "";
    loc.style.display = loc.innerHTML ? "" : "none";

    const spanDeg = Math.round((W / k / D2R) * 10) / 10;
    caption.textContent =
      closeView
        ? `${zoom === "fit" && zone ? `Search zone ${zoneLabel(zone)}` : "Zoomed to the objects to check"}: ${spanDeg}° across, north up, east left. The inset shows where on the sky.`
        : "The whole sky, north up, east left. The shaded band is the Milky Way plane, where stars crowd.";
    renderLegend();
    drawHot();
  }

  /** On the whole sky a zone is a few pixels: ring each one and name it so the eye finds it. */
  function zoneRings(points: Pt[]): string {
    const groups = new Map<string, Pt[]>();
    for (const p of points) groups.set(p.c.zone, [...(groups.get(p.c.zone) ?? []), p]);
    const out: string[] = [];
    for (const [zone, ps] of groups) {
      const cx = ps.reduce((a, p) => a + p.sx, 0) / ps.length;
      const cy = ps.reduce((a, p) => a + p.sy, 0) / ps.length;
      const r = Math.min(40, Math.max(...ps.map((p) => Math.hypot(p.sx - cx, p.sy - cy))) + 8);
      const name = zoneLabel(zone);
      const right = cx + r + 4 + name.length * 6.5 < W;
      out.push(
        html`<circle class="sm-zone" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}"/><text class="sm-zone-name" x="${(right ? cx + r + 4 : cx - r - 4).toFixed(1)}" y="${(cy + 4).toFixed(1)}" text-anchor="${right ? "start" : "end"}">${escAttr(name)}</text>`,
      );
    }
    return out.join("");
  }

  function renderLegend() {
    const shown = new Map<string, number>();
    const total = new Map<string, number>();
    for (const c of all) total.set(c.kind, (total.get(c.kind) ?? 0) + 1);
    for (const id of filtered) {
      const p = byId.get(id);
      if (p) shown.set(p.c.kind, (shown.get(p.c.kind) ?? 0) + 1);
    }
    const items = KINDS.filter((k) => total.get(k)).map(
      (k) => html`<li>${glyphSvg(k, 12)}<span>${KIND_TEXT[k].plural}</span><span class="sm-n">${(shown.get(k) ?? 0).toLocaleString("en-GB")} of ${(total.get(k) ?? 0).toLocaleString("en-GB")}</span></li>`,
    );
    const gradeC = filtered.filter((id) => {
      const p = byId.get(id);
      return !!p && gradeOf(p.c) === "C";
    }).length;
    if (gradeC)
      items.push(
        html`<li>${glyphSvg("mover", 12, "is-gradec")}<span title="${escAttr(GRADE_TEXT.C.plain)}">Grey: grade C, fails a check</span><span class="sm-n">${gradeC.toLocaleString("en-GB")}</span></li>`,
      );
    if (filtered.length < all.length)
      items.push(html`<li><svg class="kind-glyph" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><circle class="sm-ctx" cx="6" cy="6" r="2"/></svg><span>Not matching the filters</span></li>`);
    legend.innerHTML = items.join("");
  }

  function drawHot() {
    const g = svg.querySelector<SVGGElement>(".sm-hot");
    const p = hot ? byId.get(hot) : undefined;
    if (!g) return;
    if (!p) {
      g.innerHTML = "";
      tip.hidden = true;
      return;
    }
    const r = 7;
    g.innerHTML = html`<circle class="sm-halo" cx="${p.sx.toFixed(1)}" cy="${p.sy.toFixed(1)}" r="${r + 5}"/><path class="sm-pt sm-pt-hot kind-${p.c.kind}${gradeCls(p.c)}" d="${glyphPath(p.c.kind, p.sx, p.sy, r)}"/>`;
    const name = KIND_TEXT[p.c.kind]?.name ?? p.c.kind;
    tip.innerHTML = "";
    const head = document.createElement("p");
    head.className = "sm-tip-name";
    head.innerHTML = glyphSvg(p.c.kind, 12);
    head.append(` ${name}`);
    const pos = document.createElement("p");
    pos.className = "sm-tip-pos";
    pos.textContent = formatRaDec(p.c);
    const why = document.createElement("p");
    why.className = "sm-tip-why";
    why.textContent = p.c.why;
    tip.append(head, pos, why);
    const grade = gradeOf(p.c);
    if (grade) {
      const g = document.createElement("p");
      g.className = "sm-tip-note";
      g.textContent = grade === "C" ? `${GRADE_TEXT.C.short}: fails a check.` : `${gradeLabel(grade)}.`;
      tip.append(g);
    }
    const outside = p.sx < 0 || p.sx > W || p.sy < 0 || p.sy > H;
    if (outside) {
      const n = document.createElement("p");
      n.className = "sm-tip-note";
      n.textContent = zones.length > 1 ? `In search zone ${zoneLabel(p.c.zone)}, outside this close-up.` : "Outside this view.";
      tip.append(n);
    } else if (!filteredSet.has(p.c.id)) {
      const n = document.createElement("p");
      n.className = "sm-tip-note";
      n.textContent = "Outside the current filters.";
      tip.append(n);
    }
    tip.hidden = false;
    // Beside the point (right, else left), else below or above it: never over the point itself.
    const tw = Math.min(232, W - 16);
    tip.style.width = `${tw}px`;
    const th = tip.offsetHeight || 90;
    const gap = 14;
    const clampX = (x: number) => Math.min(Math.max(4, x), W - tw - 4);
    const clampY = (y: number) => Math.min(Math.max(4, y), Math.max(4, H - th - 4));
    let left: number;
    let top: number;
    if (outside) [left, top] = [4, 4];
    else if (p.sx + gap + tw <= W - 4) [left, top] = [p.sx + gap, clampY(p.sy - th / 2)];
    else if (p.sx - gap - tw >= 4) [left, top] = [p.sx - gap - tw, clampY(p.sy - th / 2)];
    else if (p.sy + gap + th <= H - 4) [left, top] = [clampX(p.sx - tw / 2), p.sy + gap];
    else [left, top] = [clampX(p.sx - tw / 2), Math.max(4, p.sy - gap - th)];
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
    if (hotFrom === "map") live.textContent = `${name}, ${formatRaDec(p.c)}. ${p.c.why}`;
  }

  function setHot(id: string | null, from: "map" | "list" | null) {
    if (id === null) plot.classList.remove("is-point");
    if (hot === id && hotFrom === from) return;
    hot = id;
    hotFrom = id ? from : null;
    drawHot();
    if (from === "map") opts.onHover?.(id);
  }

  function nearest(mx: number, my: number): Pt | null {
    let best: Pt | null = null;
    let bd = 16 * 16;
    for (const id of filtered) {
      const p = byId.get(id);
      if (!p) continue;
      const d = (p.sx - mx) ** 2 + (p.sy - my) ** 2;
      if (d < bd) {
        bd = d;
        best = p;
      }
    }
    return best;
  }

  const local = (ev: PointerEvent | MouseEvent) => {
    const r = svg.getBoundingClientRect();
    return { x: ((ev.clientX - r.left) / (r.width || W)) * W, y: ((ev.clientY - r.top) / (r.height || H)) * H };
  };
  const pointerCentre = () => {
    const [a, b] = [...pointers.values()];
    return b ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, distance: Math.hypot(b.x - a.x, b.y - a.y) }
      : { x: a.x, y: a.y, distance: 0 };
  };
  function beginGesture() {
    gesture = { ...pointerCentre(), view: { ...view }, k: camera().k };
  }
  plot.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    if (previewFrame !== null || settleTimer !== null) render();
    if (!pointers.size) { moved = false; suppressClick = false; }
    pointers.set(ev.pointerId, local(ev));
    plot.setPointerCapture?.(ev.pointerId);
    plot.focus({ preventScroll: true });
    if (pointers.size > 1) { moved = true; suppressClick = true; }
    beginGesture();
  });
  plot.addEventListener("pointermove", (ev) => {
    const { x, y } = local(ev);
    if (pointers.has(ev.pointerId) && gesture) {
      pointers.set(ev.pointerId, { x, y });
      const centre = pointerCentre();
      if (!moved && Math.hypot(centre.x - gesture.x, centre.y - gesture.y) < 4) return;
      moved = true;
      suppressClick = true;
      plot.classList.add("is-dragging");
      const scale = clampScale(gesture.view.scale * (gesture.distance > 0 ? centre.distance / gesture.distance : 1));
      const ratio = scale / gesture.view.scale;
      view.scale = scale;
      view.x = gesture.view.x + ((gesture.x - W / 2) - (centre.x - W / 2) / ratio) / gesture.k;
      view.y = gesture.view.y + ((H / 2 - gesture.y) - (H / 2 - centre.y) / ratio) / gesture.k;
      constrainView();
      setHot(null, null);
      queueView();
      return;
    }
    if (pointers.size || previewFrame !== null || settleTimer !== null) return;
    const p = nearest(x, y);
    plot.classList.toggle("is-point", !!p);
    setHot(p ? p.c.id : null, p ? "map" : null);
  });
  const endPointer = (ev: PointerEvent) => {
    if (!pointers.delete(ev.pointerId)) return;
    if (plot.hasPointerCapture?.(ev.pointerId)) plot.releasePointerCapture(ev.pointerId);
    if (moved || previewFrame !== null || settleTimer !== null) render();
    if (pointers.size) beginGesture();
    else { gesture = null; plot.classList.remove("is-dragging"); }
  };
  plot.addEventListener("pointerup", endPointer);
  plot.addEventListener("pointercancel", endPointer);
  plot.addEventListener("lostpointercapture", endPointer);
  plot.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const { x, y } = local(ev);
    const delta = ev.deltaY * (ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? H : 1);
    zoomAt(Math.exp(-Math.max(-0.7, Math.min(0.7, delta * 0.002))), x, y, true);
  }, { passive: false });
  plot.addEventListener("pointerleave", () => {
    if (hotFrom === "map") setHot(null, null);
  });
  plot.addEventListener("click", (ev) => {
    if (suppressClick) { suppressClick = false; return; }
    if (previewFrame !== null || settleTimer !== null) render();
    const { x, y } = local(ev);
    const p = nearest(x, y);
    if (p) {
      closeFullscreen();
      opts.onOpen(p.c.id);
    }
  });
  plot.addEventListener("keydown", (ev) => {
    if (ev.key === "+" || ev.key === "=" || ev.key === "-" || ev.key === "_") {
      ev.preventDefault();
      zoomAt(ev.key === "+" || ev.key === "=" ? 1.5 : 1 / 1.5);
      return;
    }
    if (ev.key === "0" || ev.key === "Home") {
      ev.preventDefault();
      resetView();
      return;
    }
    if (ev.shiftKey && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(ev.key)) {
      ev.preventDefault();
      const { k } = camera();
      view.x += (ev.key === "ArrowLeft" ? -40 : ev.key === "ArrowRight" ? 40 : 0) / k;
      view.y += (ev.key === "ArrowDown" ? -40 : ev.key === "ArrowUp" ? 40 : 0) / k;
      constrainView();
      setHot(null, null);
      render();
      return;
    }
    if (!filtered.length) return;
    const i = hot ? filtered.indexOf(hot) : -1;
    if (ev.key === "ArrowRight" || ev.key === "ArrowDown") {
      ev.preventDefault();
      setHot(filtered[(i + 1) % filtered.length], "map");
    } else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") {
      ev.preventDefault();
      setHot(filtered[(i - 1 + filtered.length) % filtered.length], "map");
    } else if (ev.key === "Enter" && hot) {
      ev.preventDefault();
      const id = hot;
      closeFullscreen();
      opts.onOpen(id);
    } else if (ev.key === "Escape") {
      setHot(null, null);
    }
  });
  plot.addEventListener("blur", () => {
    if (hotFrom === "map") setHot(null, null);
  });

  function updateLabel() {
    plot.setAttribute(
      "aria-label",
      filtered.length
        ? `Sky map of ${filtered.length.toLocaleString("en-GB")} objects to check. Arrow keys step through them; Enter opens one.`
        : "Sky map. Nothing matches the filters.",
    );
  }

  let ro: ResizeObserver | null = null;
  let lastW = 0;
  let lastH = 0;
  if (typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(() => {
      const w = Math.round(plot.clientWidth);
      const h = Math.round(plot.clientHeight);
      if (w && h && (Math.abs(w - lastW) > 2 || Math.abs(h - lastH) > 2)) {
        lastW = w;
        lastH = h;
        render();
      }
    });
    ro.observe(plot);
  }

  render();
  updateLabel();

  return {
    setData(next) {
      all = next;
      byIdAll = new Map(next.map((c) => [c.id, c]));
      zones = [...new Set(next.map((c) => c.zone))].sort();
      zoneSelect.innerHTML = zones
        .map((z) => html`<option value="${escAttr(z)}">${escAttr(zoneLabel(z))}</option>`)
        .join("");
      render();
    },
    setFiltered(ids) {
      filtered = ids;
      filteredSet = new Set(ids);
      render();
      updateLabel();
    },
    highlight(id) {
      if (id === null && hotFrom !== "list") return;
      setHot(id, id ? "list" : null);
    },
    closeFullscreen,
    destroy() {
      ro?.disconnect();
      cancelPreview();
      stopGesture();
      closeFullscreen();
      document.removeEventListener("fullscreenchange", onFullscreenChange);
      el.innerHTML = "";
    },
  };
}

const escAttr = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** Sky outline: the Aitoff ellipse. */
function outline(toS: (u: { x: number; y: number }) => { sx: number; sy: number }): string {
  const pts: string[] = [];
  for (let i = 0; i <= 180; i++) {
    const t = (i / 180) * 2 * Math.PI;
    const s = toS({ x: Math.PI * Math.cos(t), y: (Math.PI / 2) * Math.sin(t) });
    pts.push(`${i ? "L" : "M"}${s.sx.toFixed(1)},${s.sy.toFixed(1)}`);
  }
  return pts.join("") + "Z";
}

/** The Milky Way plane, |b| < 10°, as a set of small quads (skipping any that cross the map's edge). */
function bandPath(frame: Frame, centerLon: number, toS: (u: { x: number; y: number }) => { sx: number; sy: number }, stepDeg: number): string {
  const edge = (l: number, b: number) => {
    const p = frame === "gal" ? { lon: l, lat: b } : (() => { const e = galToEq(l, b); return { lon: e.ra, lat: e.dec }; })();
    return aitoff(p.lon, p.lat, centerLon);
  };
  const out: string[] = [];
  let prevTop = edge(0, 10);
  let prevBot = edge(0, -10);
  for (let l = stepDeg; l <= 360 + 1e-9; l += stepDeg) {
    const top = edge(l, 10);
    const bot = edge(l, -10);
    const jump = Math.max(Math.abs(top.x - prevTop.x), Math.abs(bot.x - prevBot.x), Math.abs(top.x - bot.x));
    if (jump < Math.PI * 0.5) {
      const q = [prevTop, top, bot, prevBot].map(toS);
      out.push(`M${q.map((s) => `${s.sx.toFixed(1)},${s.sy.toFixed(1)}`).join("L")}Z`);
    }
    prevTop = top;
    prevBot = bot;
  }
  return out.join("");
}

/** RA graticule step in degrees that lands on whole minutes of time. */
function raStep(decStep: number): number {
  const opts = [0.25, 0.5, 1, 2.5, 5, 7.5, 15, 30];
  return opts.find((s) => s >= decStep) ?? 30;
}

/** A small whole-sky locator (100 × 52) with a mark where the candidates are. */
function locator(frame: Frame, raw: { lon: number; lat: number }[]): string {
  const w = 96;
  const h = w / 2;
  const x0 = 2;
  const y0 = 2;
  const k = (w - 4) / (2 * Math.PI);
  const toS = (u: { x: number; y: number }) => ({ sx: x0 + w / 2 + u.x * k, sy: y0 + h / 2 - u.y * k });
  const c = { lon: meanLon(raw.map((p) => p.lon)), lat: raw.reduce((a, p) => a + p.lat, 0) / raw.length };
  const s = toS(aitoff(c.lon, c.lat, 0));
  return html`<g class="sm-locator" aria-hidden="true">
    <path class="sm-loc-sky" d="${outline(toS)}"/>
    <path class="sm-band" d="${bandPath(frame, 0, toS, 4)}"/>
    <circle class="sm-loc-mark" cx="${s.sx.toFixed(1)}" cy="${s.sy.toFixed(1)}" r="4"/>
  </g>`;
}

