// The all-sky map of tonight's zones: an SVG Mollweide (RA increasing to the left), each zone a box
// filled by state with colour AND pattern, the ecliptic, the hunt zone outlined. Zones are buttons with a
// roving tabindex; arrow keys move between them in sky order (see neighbour()).
import { gradeOf } from "../api/grades";
import { esc, fmtCount, fmtDec, fmtRa } from "./format";
import { isProvisional, workerOf, type SkyZone } from "./model";
import {
  DEFAULT_FRAME,
  eclipticPath,
  frameSize,
  lambdaOf,
  lambdaToXY,
  meridianPath,
  outlinePath,
  parallelPath,
  raPieces,
  skyToXY,
  zoneCorners,
  zonePath,
  type MapFrame,
} from "./project";

export interface ZoneGeom {
  name: string;
  d: string;
  /** Box centre in SVG units. */
  cx: number;
  cy: number;
  /** Box width at its middle Dec, in SVG units. */
  w: number;
  h: number;
  /** Cross for a failed zone (corner to corner). */
  cross: string;
  /** Map longitude of the column's middle, for keyboard order. */
  lambda: number;
}

export interface MapGeometry {
  frame: MapFrame;
  w: number;
  h: number;
  zones: Map<string, ZoneGeom>;
  /** Names in reading order: columns left to right, each top to bottom. */
  order: string[];
  /** Columns left to right, each a list of names from top to bottom. */
  columns: string[][];
  /** For each name, its column index. */
  columnOf: Map<string, number>;
  key: string;
}

/** Geometry for a set of zones; reused while the names and boxes stay the same. */
export function buildGeometry(zones: SkyZone[], frame: MapFrame = DEFAULT_FRAME): MapGeometry {
  const { w, h } = frameSize(frame);
  const edge = (frame.centreRa + 180) % 360;
  const geoms = new Map<string, ZoneGeom>();
  for (const z of zones) {
    const corners = zoneCorners(z, frame);
    const [lo, hi] = raPieces(z.ra, edge).sort((a, b) => b[1] - b[0] - (a[1] - a[0]))[0];
    const mid = (lo + hi) / 2;
    const lMid = lambdaOf(mid, frame);
    const dMid = (z.dec[0] + z.dec[1]) / 2;
    const [xl] = lambdaToXY(lMid + (hi - lo) / 2, dMid, frame);
    const [xr] = lambdaToXY(lMid - (hi - lo) / 2, dMid, frame);
    const [cx, cy] = lambdaToXY(lMid, dMid, frame);
    const top = lambdaToXY(lMid, z.dec[1], frame)[1];
    const bottom = lambdaToXY(lMid, z.dec[0], frame)[1];
    const [a, b, c, d] = corners;
    geoms.set(z.name, {
      name: z.name,
      d: zonePath(z, frame),
      cx,
      cy,
      w: Math.abs(xl - xr),
      h: Math.abs(bottom - top),
      cross: `M${a[0].toFixed(1)} ${a[1].toFixed(1)}L${c[0].toFixed(1)} ${c[1].toFixed(1)}M${b[0].toFixed(1)} ${b[1].toFixed(1)}L${d[0].toFixed(1)} ${d[1].toFixed(1)}`,
      lambda: lMid,
    });
  }
  // Columns: zones sharing an RA range, ordered by their place on the map (left to right).
  const byRange = new Map<string, SkyZone[]>();
  for (const z of zones) {
    const k = `${z.ra[0]}:${z.ra[1]}`;
    byRange.set(k, [...(byRange.get(k) ?? []), z]);
  }
  const columns = [...byRange.values()]
    .map((col) => col.sort((p, q) => q.dec[1] + q.dec[0] - (p.dec[1] + p.dec[0])).map((z) => z.name))
    .sort((p, q) => geoms.get(p[0])!.lambda - geoms.get(q[0])!.lambda);
  const columnOf = new Map<string, number>();
  columns.forEach((col, i) => col.forEach((n) => columnOf.set(n, i)));
  return {
    frame,
    w,
    h,
    zones: geoms,
    order: columns.flat(),
    columns,
    columnOf,
    key: zones.map((z) => `${z.name}:${z.ra.join(",")}:${z.dec.join(",")}`).join("|"),
  };
}

export type NavKey = "ArrowLeft" | "ArrowRight" | "ArrowUp" | "ArrowDown" | "Home" | "End";

/**
 * The zone a key moves to. Left/right: the next column on the map (wrapping round the sky), the zone
 * nearest in Dec. Up/down: within the column. Home/End: first/last column, nearest in Dec.
 */
export function neighbour(geom: MapGeometry, name: string, key: NavKey): string {
  const ci = geom.columnOf.get(name);
  if (ci === undefined) return geom.order[0] ?? name;
  const col = geom.columns[ci];
  const ri = col.indexOf(name);
  const g = geom.zones.get(name)!;
  const nearest = (c: string[]) =>
    c.reduce((best, n) => (Math.abs(geom.zones.get(n)!.cy - g.cy) < Math.abs(geom.zones.get(best)!.cy - g.cy) ? n : best), c[0]);
  const n = geom.columns.length;
  switch (key) {
    case "ArrowUp":
      return col[Math.max(0, ri - 1)];
    case "ArrowDown":
      return col[Math.min(col.length - 1, ri + 1)];
    case "ArrowLeft":
      return nearest(geom.columns[(ci - 1 + n) % n]);
    case "ArrowRight":
      return nearest(geom.columns[(ci + 1) % n]);
    case "Home":
      return nearest(geom.columns[0]);
    case "End":
      return nearest(geom.columns[n - 1]);
  }
}

// ---- drawing -------------------------------------------------------------------------------------
/** Fill level of a combined zone by its suspects: 0 none, 1: 1–9, 2: 10–49, 3: 50–149, 4: 150 or more; "ink" when unknown. */
export function suspectLevel(s: number | null): string {
  if (s === null) return "ink";
  if (s <= 0) return "0";
  if (s < 10) return "1";
  if (s < 50) return "2";
  if (s < 150) return "3";
  return "4";
}
export const SUSPECT_LEVELS = [
  { level: "0", text: "none" },
  { level: "1", text: "1–9" },
  { level: "2", text: "10–49" },
  { level: "3", text: "50–149" },
  { level: "4", text: "150 or more" },
];

const STATE_WORDS: Record<SkyZone["state"], string> = {
  queued: "queued",
  extracting: "extracting photos",
  extracted: "extracted, waiting to be combined",
  combining: "combining",
  combined: "combined",
  failed: "failed",
};

export function zoneTitle(z: Pick<SkyZone, "name">): string {
  // Run zones (ecl…) are internal names: the public sees "Area of sky", the panel says where it is.
  return z.name === "hunt" ? "Hunt zone" : z.name === "pilot" ? "Pilot zone" : "Area of sky";
}

/** What a screen reader hears on a zone. */
export function zoneLabel(z: SkyZone): string {
  let s = `${zoneTitle(z)}: ${STATE_WORDS[z.state]}`;
  if (z.deferred && z.state === "queued") s += " last, a Galactic bulge zone";
  const who = workerOf(z);
  if ((z.state === "extracting" || z.state === "combining") && who) s += ` on ${who}`;
  if (z.state === "combined" && z.suspects !== null) {
    s += `, ${fmtCount(z.suspects)} ${isProvisional(z) ? "provisional " : ""}${z.suspects === 1 ? "suspect" : "suspects"}`;
  }
  return `${s}. RA ${fmtRa(z.ra[0])} to ${fmtRa(z.ra[1])}, Dec ${fmtDec(z.dec[0])} to ${fmtDec(z.dec[1])}.`;
}

/** Text drawn inside a box: the machine at work, or a combined zone's suspects. */
function boxText(z: SkyZone): string | null {
  const who = workerOf(z);
  if ((z.state === "extracting" || z.state === "combining") && who) return who;
  if (z.state === "combined" && z.suspects !== null && z.suspects > 0) return fmtCount(z.suspects);
  return null;
}

function fitFont(text: string, g: ZoneGeom): number | null {
  const fs = Math.min(10.5, (g.w * 0.86) / (text.length * 0.5), g.h * 0.42);
  return fs >= 6.5 ? fs : null;
}

/** Inner markup of a zone's <g>: shape, cross when failed, label. */
export function zoneInner(z: SkyZone, g: ZoneGeom): string {
  let s = `<path class="z-shape" d="${g.d}"/>`;
  if (z.state === "failed") s += `<path class="z-x" d="${g.cross}"/>`;
  const t = boxText(z);
  const fs = t ? fitFont(t, g) : null;
  if (t && fs) s += `<text class="z-text" x="${g.cx.toFixed(1)}" y="${(g.cy + fs * 0.36).toFixed(1)}" font-size="${fs.toFixed(1)}">${esc(t)}</text>`;
  return s;
}

function zoneAttrs(z: SkyZone): Record<string, string> {
  return {
    "data-state": z.state,
    "data-level": z.state === "combined" ? suspectLevel(z.suspects) : "",
    "data-deferred": z.deferred ? "1" : "",
    "aria-label": zoneLabel(z),
  };
}

/** Patterns shared by the map, the progress bar and the legend swatches (ids are page-unique). */
export const PATTERNS = `
  <pattern id="lv-p-extracting" patternUnits="userSpaceOnUse" width="4.5" height="4.5" patternTransform="rotate(45)">
    <rect class="lv-pat-bg" width="4.5" height="4.5"/><line class="lv-pat-hatch" x1="1" y1="0" x2="1" y2="4.5"/>
  </pattern>
  <pattern id="lv-p-combining" patternUnits="userSpaceOnUse" width="4.5" height="4.5" patternTransform="rotate(-45)">
    <rect class="lv-pat-dotbg" width="4.5" height="4.5"/><line class="lv-pat-hatch" x1="1" y1="0" x2="1" y2="4.5"/>
  </pattern>
  <pattern id="lv-p-extracted" patternUnits="userSpaceOnUse" width="3.6" height="3.6">
    <rect class="lv-pat-dotbg" width="3.6" height="3.6"/><circle class="lv-pat-dot" cx="1.8" cy="1.8" r="0.75"/>
  </pattern>
  <pattern id="lv-p-deferred" patternUnits="userSpaceOnUse" width="2.2" height="2.2">
    <rect class="lv-pat-bg" width="2.2" height="2.2"/><circle class="lv-pat-stipple" cx="0.55" cy="0.55" r="0.42"/><circle class="lv-pat-stipple" cx="1.65" cy="1.65" r="0.42"/>
  </pattern>`;

export interface MapOptions {
  hunt?: string;
  selected?: string | null;
  tabbable?: string | null;
}

/** The whole map as SVG markup. */
export function mapSvg(zones: SkyZone[], geom: MapGeometry, opts: MapOptions = {}): string {
  const f = geom.frame;
  const grid = [
    ...[-60, -30, 0, 30, 60].map((d) => parallelPath(d, f)),
    ...[90, 180, 270].map((ra) => meridianPath(ra, f)),
  ].join("");
  const tabbable = opts.tabbable ?? geom.order[0];
  const zoneEls = zones
    .map((z) => {
      const g = geom.zones.get(z.name)!;
      const attrs = Object.entries(zoneAttrs(z))
        .map(([k, v]) => `${k}="${esc(v)}"`)
        .join(" ");
      return `<g class="z" role="button" data-zone="${esc(z.name)}" data-text="${esc(boxText(z) ?? "")}" ${attrs} aria-pressed="${z.name === opts.selected}" tabindex="${z.name === tabbable ? 0 : -1}">${zoneInner(z, g)}</g>`;
    })
    .join("");
  const hunt = opts.hunt ? geom.zones.get(opts.hunt) : undefined;
  const huntTop = hunt ? lambdaToXY(hunt.lambda, 70, f) : null;
  const huntBoxTop = hunt ? hunt.cy - hunt.h / 2 : 0;
  return `<svg class="lv-svg" viewBox="0 0 ${geom.w} ${geom.h}" role="group" aria-label="Map of the whole sky with tonight's zones. Arrow keys move between zones; Enter opens one." aria-roledescription="sky map" xmlns="http://www.w3.org/2000/svg">
  <defs>${PATTERNS}</defs>
  <path class="lv-sky" d="${outlinePath(f)}"/>
  <path class="lv-grid" d="${grid}"/>
  <g class="lv-zones">${zoneEls}</g>
  <path class="lv-ecl" d="${eclipticPath(f)}"/>
  ${hunt ? `<path class="lv-hunt" d="${hunt.d}"/>` : ""}
  ${huntTop ? `<path class="lv-hunt-lead" d="M${huntTop[0].toFixed(1)} ${huntTop[1].toFixed(1)}V${(huntBoxTop - 1).toFixed(1)}"/>` : ""}
  <path class="lv-sky-rim" d="${outlinePath(f)}"/>
  <g class="lv-changed"></g>
  <g class="lv-sus-marks"></g>
  <path class="lv-sel-halo" d=""/>
  <path class="lv-sel" d=""/>
  <path class="lv-focus-halo" d=""/>
  <path class="lv-focus" d=""/>
</svg>`;
}

/** Labels laid over the map in HTML (so they keep their size at any width): positions in % of the SVG box. */
export function axisLabels(geom: MapGeometry, hunt?: string): string {
  const f = geom.frame;
  const pct = ([x, y]: [number, number]) => `left:${((100 * x) / geom.w).toFixed(2)}%;top:${((100 * y) / geom.h).toFixed(2)}%`;
  const dec = [60, 30, 0, -30, -60]
    .map((d) => `<span class="lv-ax lv-ax-dec" style="${pct(lambdaToXY(-180, d, f))}">${fmtDec(d)}</span>`)
    .join("");
  const ra = [
    // On the equator, where RA is read; small pills so they stay legible over the zones.
    ...[6, 12, 18].map((hr) => `<span class="lv-ax lv-ax-ra" style="${pct(skyToXY(hr * 15, 0, f))}">${hr}h</span>`),
    `<span class="lv-ax lv-ax-ra0" style="${pct(lambdaToXY(180, 0, f))}">0h</span>`,
  ].join("");
  const g = hunt ? geom.zones.get(hunt) : undefined;
  const h = g ? `<span class="lv-ax lv-ax-hunt" style="${pct(lambdaToXY(g.lambda, 71, f))}">Hunt zone</span>` : "";
  return `<div class="lv-axes" aria-hidden="true">${dec}${ra}${h}</div>`;
}

/** Update the zones in place (focus stays put). Returns false when the zone set changed (rebuild instead). */
export function updateZones(svg: SVGSVGElement, zones: SkyZone[], geom: MapGeometry): boolean {
  const els = new Map<string, SVGGElement>();
  svg.querySelectorAll<SVGGElement>("g.z").forEach((el) => els.set(el.dataset.zone ?? "", el));
  if (els.size !== zones.length || zones.some((z) => !els.has(z.name))) return false;
  for (const z of zones) {
    const el = els.get(z.name)!;
    const attrs = zoneAttrs(z);
    const same = Object.entries(attrs).every(([k, v]) => el.getAttribute(k) === v);
    if (same && el.dataset.text === (boxText(z) ?? "")) continue;
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    el.dataset.text = boxText(z) ?? "";
    el.innerHTML = zoneInner(z, geom.zones.get(z.name)!);
  }
  return true;
}

/** Outline the zones whose state changed since the last refresh (fades unless reduced motion). */
export function markChanged(svg: SVGSVGElement, names: Iterable<string>, geom: MapGeometry): void {
  const layer = svg.querySelector(".lv-changed");
  if (!layer) return;
  layer.innerHTML = [...names]
    .map((n) => geom.zones.get(n))
    .filter((g): g is ZoneGeom => !!g)
    .map((g) => `<path d="${g.d}"/>`)
    .join("");
}

/**
 * Small red marks at the suspects of the selected zone (empty list clears them): solid once the WISE check
 * has run, hollow and dimmer while provisional (Gaia check only).
 */
export function markSuspects(
  svg: SVGSVGElement,
  points: Array<{ ra: number; dec: number; grade?: unknown }>,
  geom: MapGeometry,
  provisional = false,
): void {
  const layer = svg.querySelector(".lv-sus-marks");
  if (!layer) return;
  layer.setAttribute("data-provisional", provisional && points.length ? "1" : "");
  // Grade C (failed a vetting check) in grey, drawn first so the others sit on top.
  const order = [...points].sort((a, b) => Number(gradeOf(b) === "C") - Number(gradeOf(a) === "C"));
  layer.innerHTML = order
    .map((p) => {
      const [x, y] = skyToXY(p.ra, p.dec, geom.frame);
      return `<circle${gradeOf(p) === "C" ? ' class="is-gradec"' : ""} cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="1.7"/>`;
    })
    .join("");
}

/** Point an overlay ring ("lv-sel" or "lv-focus", with its halo) at a zone, or hide it. */
export function ring(svg: SVGSVGElement, cls: "lv-sel" | "lv-focus", name: string | null, geom: MapGeometry): void {
  const d = name ? geom.zones.get(name)?.d ?? "" : "";
  svg.querySelectorAll<SVGPathElement>(`.${cls}, .${cls}-halo`).forEach((p) => p.setAttribute("d", d));
}
