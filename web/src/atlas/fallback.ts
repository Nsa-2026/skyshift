// The map without WebGL2 (or when Aladin can't start): an SVG of the same heat map, zones,
// featured targets and signals, with no background image. The whole sky is an Aitoff map; click
// a red cell (or zoom) and it becomes a tangent-plane close-up where single signals show.
// Same callbacks as the globe, so the drawer, pin and list work the same.
// Tonight's search is drawn under the heat map, its fills from SVG patterns.
import type { Candidate, SkyPos } from "../api/types";
import type { Target } from "../app/state";
import { ang2pixNest } from "./healpix";
import { cellOutline, rampAlpha, rampIndex, rampSteps, type Cell } from "./heat";
import { countText, isGradeC } from "./kinds";
import { NAME, STAR, aroundPoint, boundsOf, boxHolds, boxesHit, estimateSize, placeLabels, type Box, type LabelSpec, type Placed } from "./labels";
import type { MapCallbacks, MapHandle, MapModel, MapView } from "./map";
import { arrowLengthPx, diamond, moverTick, nearestHit, paToScreen, starArrow, MARK_R, type Vec } from "./marks";
import { suspectAlpha } from "./search";
import { zoneLook } from "./searchdraw";
import { aitoff, aitoffInverse, fromGalactic, gnomonic, gnomonicInverse, lonLat, offsetPa, wrap360 } from "./sphere";
import { LABEL_MIN_PX, LEVEL_FOV, WHOLE_FOV, cellPx, heatOrder, levelFor, showsDots, showsHeat, showsSignals } from "./zoom";
import { areaAt, densifyRing, type ZoneArea } from "./zones";
import { BRIGHT_STARS } from "./stars";

type Kinded = { kind: "zone"; area: ZoneArea } | { kind: "target"; target: Target };
type FallbackSpec = LabelSpec & (Kinded | { kind: "star" });
type FallbackLabel = Placed & Kinded;

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const f1 = (n: number) => n.toFixed(1);

interface Projector {
  whole: boolean;
  /** Screen point of a sky position, or null when it can't be drawn. */
  project(p: SkyPos): Vec | null;
  unproject(x: number, y: number): SkyPos | null;
  /** Pixels per degree near the centre. */
  pxPerDeg: number;
}

export interface FallbackInit {
  view: MapView;
  gal: boolean;
}

/**
 * Size and height of the whole-sky Aitoff map in a w × h stage: `s` px per Aitoff unit (the
 * ellipse is 4s × 2s), centred as the globe is (the map fills the page and the search panel
 * floats over it), and clear of the `inset` the site header covers at the top, as on the globe.
 */
export function wholeGeometry(w: number, h: number, inset = 0): { s: number; cy: number } {
  const clear = h - 24 - 2 * Math.max(0, inset);
  const room = clear >= h / 2 ? clear : h - 24;
  const s = Math.max(1, Math.min((w - 24) / 4, room / 2));
  return { s, cy: h / 2 };
}

export function createFallbackMap(host: HTMLElement, init: FallbackInit, cb: MapCallbacks): MapHandle {
  const wrap = document.createElement("div");
  wrap.className = "atl-svgmap";
  host.appendChild(wrap);

  let v: MapView = { ...init.view };
  let gal = init.gal;
  let model: MapModel | null = null;
  let proj: Projector | null = null;
  let destroyed = false;
  /** Badges and names as last placed, for clicks on them. */
  let placed: FallbackLabel[] = [];

  /** Text width from a 2D canvas when the browser has one (it does without WebGL), else an estimate. */
  let measureCtx: CanvasRenderingContext2D | null | undefined;
  function measure(text: string, px: number, padX: number, h: number): { w: number; h: number } {
    if (measureCtx === undefined) {
      try {
        measureCtx = typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent) ? null : document.createElement("canvas").getContext("2d");
      } catch {
        measureCtx = null;
      }
    }
    if (!measureCtx) return estimateSize(text, px, padX, h);
    const family = getComputedStyle(host).fontFamily || "sans-serif";
    measureCtx.font = `650 ${px}px ${family}`;
    try {
      (measureCtx as CanvasRenderingContext2D & { fontStretch?: string }).fontStretch = "semi-condensed";
    } catch {
      /* normal width */
    }
    return { w: Math.ceil(measureCtx.measureText(text).width + 2 * padX + 2), h };
  }

  const size = () => ({ w: Math.max(200, host.clientWidth || 800), h: Math.max(200, host.clientHeight || 500) });

  /** Frame longitude/latitude as a SkyPos-shaped pair, so the projections stay frame-blind. */
  const inFrame = (p: SkyPos): SkyPos => {
    const [lon, lat] = lonLat(p, gal);
    return { ra: lon, dec: lat };
  };
  const fromFrame = (p: SkyPos): SkyPos => (gal ? fromGalactic(p.ra, p.dec) : p);

  function makeProjector(): Projector {
    const { w, h } = size();
    if (v.fov > LEVEL_FOV.whole) {
      const { s, cy } = wholeGeometry(w, h, cb.insetTop?.() ?? 0);
      const lon0 = gal ? 0 : wrap360(Math.round(inFrame(v).ra / 15) * 15);
      return {
        whole: true,
        pxPerDeg: s / 90,
        project(p) {
          const f = inFrame(p);
          const a = aitoff(f.ra, f.dec, lon0);
          return { x: w / 2 + a.x * s, y: cy - a.y * s };
        },
        unproject(x, y) {
          const r = aitoffInverse((x - w / 2) / s, -(y - cy) / s, lon0);
          return r ? fromFrame({ ra: r.lon, dec: r.lat }) : null;
        },
      };
    }
    // The field of view spans the map's width, as on the globe.
    const scale = w / v.fov;
    const c = inFrame(v);
    return {
      whole: false,
      pxPerDeg: scale,
      project(p) {
        const g = gnomonic(inFrame(p), c);
        if (!g) return null;
        const x = w / 2 + g.x * scale;
        const y = h / 2 - g.y * scale;
        // Keep far-off points from producing enormous coordinates.
        if (Math.abs(x - w / 2) > 8 * w || Math.abs(y - h / 2) > 8 * h) return null;
        return { x, y };
      },
      unproject(x, y) {
        return fromFrame(gnomonicInverse((x - w / 2) / scale, -(y - h / 2) / scale, c));
      },
    };
  }

  function path(points: [number, number][], closed: boolean, p: Projector, maxJump: number): string | null {
    const pts: Vec[] = [];
    for (const [ra, dec] of points) {
      const q = p.project({ ra, dec });
      if (!q) return null;
      pts.push(q);
    }
    for (let i = 1; i < pts.length; i++) {
      if (Math.abs(pts[i].x - pts[i - 1].x) > maxJump) return null; // crosses the Aitoff seam
    }
    return `M${pts.map((q) => `${f1(q.x)} ${f1(q.y)}`).join("L")}${closed ? "Z" : ""}`;
  }

  /** Polyline of a constant-latitude or constant-longitude line in the frame, split at the seam. */
  function gridLine(points: SkyPos[], p: Projector, maxJump: number): string {
    let d = "";
    let prev: Vec | null = null;
    for (const s of points) {
      const q = p.project(fromFrame(s));
      if (!q) {
        prev = null;
        continue;
      }
      d += prev && Math.abs(q.x - prev.x) < maxJump ? `L${f1(q.x)} ${f1(q.y)}` : `M${f1(q.x)} ${f1(q.y)}`;
      prev = q;
    }
    return d;
  }

  function markSvg(c: Candidate, at: Vec, p: Projector, selected: boolean): string {
    const muted = isGradeC(c);
    const color = muted ? "var(--ink-2)" : c.kind === "spectral" ? "var(--pencil-violet)" : "var(--pencil-red)";
    let dir: Vec | null = null;
    if (c.pa_deg != null && Number.isFinite(c.pa_deg)) {
      const step = Math.max(1e-4, v.fov / 40);
      const n = p.project(offsetPa(c, 0, step));
      const e = p.project(offsetPa(c, 90, step));
      if (n && e) dir = paToScreen(c.pa_deg, { x: n.x - at.x, y: n.y - at.y }, { x: e.x - at.x, y: e.y - at.y });
    }
    let d = "";
    if (c.kind === "spectral") {
      const q = diamond(at.x, at.y);
      d = `M${q.map((k) => `${f1(k.x)} ${f1(k.y)}`).join("L")}Z`;
    } else {
      d = `M${f1(at.x + MARK_R)} ${f1(at.y)}A${MARK_R} ${MARK_R} 0 1 0 ${f1(at.x - MARK_R)} ${f1(at.y)}A${MARK_R} ${MARK_R} 0 1 0 ${f1(at.x + MARK_R)} ${f1(at.y)}`;
      if (c.kind === "mover") {
        const [a, b] = moverTick(at.x, at.y, dir);
        d += `M${f1(a.x)} ${f1(a.y)}L${f1(b.x)} ${f1(b.y)}`;
      } else if (dir) {
        const len = arrowLengthPx(c.pm_mas_yr, p.pxPerDeg / 3600);
        const { shaft, barbs } = starArrow(at.x, at.y, dir, len);
        d += `M${f1(shaft[0].x)} ${f1(shaft[0].y)}L${f1(shaft[1].x)} ${f1(shaft[1].y)}`;
        d += `M${f1(barbs[0].x)} ${f1(barbs[0].y)}L${f1(shaft[1].x)} ${f1(shaft[1].y)}L${f1(barbs[1].x)} ${f1(barbs[1].y)}`;
      }
    }
    const reticle = selected
      ? `<path class="atl-svg-reticle" d="M${f1(at.x + 13)} ${f1(at.y)}h6M${f1(at.x - 13)} ${f1(at.y)}h-6M${f1(at.x)} ${f1(at.y + 13)}v6M${f1(at.x)} ${f1(at.y - 13)}v-6"/>`
      : "";
    return `<g class="atl-svg-mark${selected ? " is-selected" : ""}${muted ? " is-gradec" : ""}"><path class="atl-svg-halo" d="${d}"/><path d="${d}" stroke="${color}"/>${reticle}</g>`;
  }

  /** Hollow rings and diamonds match the survey annotations without covering the source. */
  function dotSvg(c: Candidate, at: Vec): string {
    const kind = isGradeC(c) ? " is-gradec" : c.kind === "spectral" ? " is-spectral" : "";
    const geometry = c.kind === "spectral"
      ? `path d="M${diamond(at.x, at.y, 3.5).map((p) => `${f1(p.x)} ${f1(p.y)}`).join("L")}Z"`
      : `circle cx="${f1(at.x)}" cy="${f1(at.y)}" r="3"`;
    return `<${geometry} class="atl-svg-dot-halo"/><${geometry} class="atl-svg-dot${kind}"/>`;
  }

  function render() {
    if (destroyed) return;
    const { w, h } = size();
    const p = makeProjector();
    proj = p;
    placed = [];
    const level = levelFor(v.fov);
    const order = heatOrder(v.fov, w);
    const maxJump = w * 0.4;
    const parts: string[] = [];
    /** Names go on last, over the marks, so no mark crosses one out. */
    const labels: string[] = [];

    // The sky's outline (whole sky) or the glass (close-up).
    if (p.whole) {
      const { s, cy } = wholeGeometry(w, h, cb.insetTop?.() ?? 0);
      parts.push(`<ellipse class="atl-svg-sky" cx="${f1(w / 2)}" cy="${f1(cy)}" rx="${f1(2 * s)}" ry="${f1(s)}"/>`);
      let grid = "";
      for (let lon = 0; lon < 360; lon += 30) {
        const pts: SkyPos[] = [];
        for (let lat = -90; lat <= 90; lat += 3) pts.push({ ra: lon, dec: lat });
        grid += gridLine(pts, p, maxJump);
      }
      for (let lat = -60; lat <= 60; lat += 30) {
        const pts: SkyPos[] = [];
        for (let lon = 0; lon <= 360; lon += 3) pts.push({ ra: lon, dec: lat });
        grid += gridLine(pts, p, maxJump);
      }
      parts.push(`<path class="atl-svg-grid" d="${grid}"/>`);
      if (!gal) {
        // The Milky Way's mid-line, for orientation.
        const pts: SkyPos[] = [];
        for (let l = 0; l <= 360; l += 3) pts.push(fromGalactic(l, 0));
        const d = gridLine(
          pts.map((q) => inFrame(q)),
          p,
          maxJump,
        );
        parts.push(`<path class="atl-svg-plane" d="${d}"/>`);
        const lab = p.project(fromGalactic(60, 0));
        if (lab) labels.push(`<text class="atl-svg-note" x="${f1(lab.x + 6)}" y="${f1(lab.y - 6)}">Milky Way</text>`);
      }
    } else {
      parts.push(`<rect class="atl-svg-glass" x="0" y="0" width="${w}" height="${h}"/>`);
    }

    if (model?.search && model.layers.search && showsHeat(level)) {
      const layer = model.search;
      const fills: Record<string, string> = {
        hatch: "url(#atl-srch-hatch)",
        "hatch-dense": "url(#atl-srch-dense)",
        stipple: "url(#atl-srch-stipple)",
      };
      let g = "";
      for (const z of layer.zones) {
        const look = zoneLook(z.state);
        for (const ring of z.rings) {
          const d = path(ring, true, p, maxJump);
          if (!d) continue;
          // Inline styles: they win over the stylesheet's `fill: none`.
          const fill =
            look.fill === "wash"
              ? ` style="fill-opacity:${suspectAlpha(z.suspects, layer.maxSuspects).toFixed(3)}"`
              : look.fill !== "none"
                ? ` style="fill:${fills[look.fill]}"`
                : "";
          g += `<path class="atl-svg-search is-${z.state}" d="${d}"${fill}/>`;
          if (look.cross) {
            const pts = ring.map(([ra, dec]) => p.project({ ra, dec })).filter((q): q is Vec => !!q);
            const xs = pts.map((q) => q.x);
            const ys = pts.map((q) => q.y);
            const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
            g += `<path class="atl-svg-search-cross" d="M${f1(x0)} ${f1(y0)}L${f1(x1)} ${f1(y1)}M${f1(x1)} ${f1(y0)}L${f1(x0)} ${f1(y1)}"/>`;
          }
        }
      }
      parts.push(
        `<defs><pattern id="atl-srch-hatch" width="8" height="8" patternUnits="userSpaceOnUse"><path class="atl-svg-hatch" d="M0 8L8 0"/></pattern>` +
          `<pattern id="atl-srch-dense" width="5" height="5" patternUnits="userSpaceOnUse"><path class="atl-svg-hatch" d="M0 5L5 0"/></pattern>` +
          `<pattern id="atl-srch-stipple" width="5" height="5" patternUnits="userSpaceOnUse"><rect class="atl-svg-stipple" x="1" y="1" width="1.2" height="1.2"/></pattern></defs>`,
        `<g class="atl-svg-searchlayer" aria-hidden="true">${g}</g>`,
      );
    }

    if (model) {
      if (showsHeat(level) && model.layers.heat) {
        const cells = model.cellsAt(order);
        const steps = rampSteps(cells.reduce((m, c) => Math.max(m, c.count), 0));
        const labelled = !p.whole && cellPx(order, v.fov, w) >= LABEL_MIN_PX;
        for (const cell of cells) {
          const d = path(cellOutline(cell, order <= 3 ? 4 : 2), true, p, maxJump);
          const a = rampAlpha(rampIndex(cell.count, steps), steps.length);
          if (d) {
            parts.push(
              `<path class="atl-svg-cell" data-cell="${cell.pix}" d="${d}" fill-opacity="${a.toFixed(3)}" stroke-opacity="${Math.min(1, a + 0.2).toFixed(3)}"><title>${countText(cell.count)} object${cell.count === 1 ? "" : "s"} to check</title></path>`,
            );
          } else {
            const q = p.project(cell.center);
            if (q) parts.push(`<circle class="atl-svg-cell" data-cell="${cell.pix}" cx="${f1(q.x)}" cy="${f1(q.y)}" r="5" fill-opacity="${a.toFixed(3)}"/>`);
          }
          if (labelled) {
            const q = p.project(cell.center);
            if (q) parts.push(`<text class="atl-svg-count${a > 0.5 ? " is-dark" : ""}" x="${f1(q.x)}" y="${f1(q.y)}">${countText(cell.count)}</text>`);
          }
        }
      }
      // Each searched area once: the union of its zones' boxes, one outline and one faint fill.
      const reserved = cb.reserved?.() ?? [];
      const obstacles: Box[] = [...reserved];
      // Under a panel: no name or badge (it would be drawn over the panel's edge).
      const covered = (q: Vec) => reserved.some((r) => boxHolds(r, q));
      // A star's dot and name stay well clear of a panel's edge (and its shadow).
      const nearPanel = (q: { x: number; y: number }) => reserved.some((r) => boxHolds({ x0: r.x0 - 14, y0: r.y0 - 14, x1: r.x1 + 14, y1: r.y1 + 14 }, q));
      const specs: FallbackSpec[] = [];
      const bounds: Box = { x0: 4, y0: 4, x1: w - 4, y1: h - 4 };
      if (model.layers.zones) {
        for (const a of model.areas) {
          let d = "";
          const pts: Vec[] = [];
          for (const ring of a.rings) {
            const dense = densifyRing(ring, 0.5);
            const r = path(dense, true, p, maxJump);
            if (!r) continue;
            d += r;
            for (const [ra, dec] of dense) {
              const q = p.project({ ra, dec });
              if (q) pts.push(q);
            }
          }
          const b = boundsOf(pts);
          if (!d || !b) continue;
          parts.push(`<path class="atl-svg-zone is-${level}" fill-rule="evenodd" d="${d}"/>`);
          // No count badge: the outline is the zone, and a click on it opens it.
          obstacles.push(b);
        }
      }
      for (const t of model.targets) {
        const q = p.project(t);
        if (!q || q.x < -20 || q.y < -20 || q.x > w + 20 || q.y > h + 20) continue;
        const r = `M${f1(q.x + 3.5)} ${f1(q.y)}h4.5M${f1(q.x - 3.5)} ${f1(q.y)}h-4.5M${f1(q.x)} ${f1(q.y + 3.5)}v4.5M${f1(q.x)} ${f1(q.y - 3.5)}v-4.5`;
        parts.push(`<g class="atl-svg-target"><path class="atl-svg-halo" d="${r}"/><path d="${r}"/></g>`);
        if (q.x < 0 || q.y < 0 || q.x > w || q.y > h || covered(q)) continue;
        obstacles.push({ x0: q.x - 9, y0: q.y - 9, x1: q.x + 9, y1: q.y + 9 });
        specs.push({ kind: "target", target: t, id: `target:${t.slug}`, texts: [t.short], anchor: q, places: (s) => aroundPoint(q.x, q.y, s) });
      }
      // The bright named stars: a dot each, and a name where one fits after the targets'.
      for (const st of BRIGHT_STARS) {
        const q = p.project(st);
        if (!q || q.x < 0 || q.y < 0 || q.x > w || q.y > h || nearPanel(q)) continue;
        parts.push(`<circle class="atl-svg-star" cx="${f1(q.x)}" cy="${f1(q.y)}" r="2.2"/>`);
        obstacles.push({ x0: q.x - 4, y0: q.y - 4, x1: q.x + 4, y1: q.y + 4 });
        specs.push({ kind: "star", id: `star:${st.name}`, texts: [st.name], anchor: q, places: (s) => aroundPoint(q.x, q.y, s, 6) });
      }
      if (showsDots(level) && model.layers.signals) {
        for (const c of model.candidates) {
          const q = p.project(c);
          if (!q || q.x < -30 || q.y < -30 || q.x > w + 30 || q.y > h + 30) continue;
          parts.push(level === "mid" ? dotSvg(c, q) : markSvg(c, q, p, model.selectedId === c.id));
        }
      }
      // Names and badges go on last, placed clear of the zones, the marks and each other.
      const got = placeLabels(specs, (t, s) => (s.kind === "star" ? measure(t, STAR.font, 0, STAR.h) : measure(t, NAME.font, 0, NAME.h)), obstacles, bounds);
      for (const pl of got) {
        const s = specs.find((x) => x.id === pl.id)!;
        const cy = (pl.box.y0 + pl.box.y1) / 2;
        if (s.kind === "star") {
          labels.push(`<text class="atl-svg-starname" x="${f1(pl.box.x0)}" y="${f1(cy)}">${esc(pl.text)}</text>`);
          continue;
        }
        placed.push(s.kind === "zone" ? { ...pl, kind: "zone", area: s.area } : { ...pl, kind: "target", target: s.target });
        labels.push(`<text class="atl-svg-name" x="${f1(pl.box.x0)}" y="${f1(cy)}">${esc(pl.text)}</text>`);
      }
      // Provisional suspects: hollow dashed rings, never a pencil mark (they are not candidates).
      if (showsSignals(level) && model.layers.provisional && model.provisional) {
        let g = "";
        for (const x of model.provisional.suspects) {
          const q = p.project(x);
          if (!q || q.x < -10 || q.y < -10 || q.x > w + 10 || q.y > h + 10) continue;
          g += `<circle cx="${f1(q.x)}" cy="${f1(q.y)}" r="6"/>`;
        }
        if (g) parts.push(`<g class="atl-svg-provisional" aria-hidden="true">${g}</g>`);
      }
    }
    parts.push(...labels);
    if (!p.whole) {
      // Scale bar: a round number of degrees or arcmin about a fifth of the width.
      const want = (w / 5 / p.pxPerDeg) * 60; // arcmin
      const nice = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200].reduce((b, n) => (Math.abs(n - want) < Math.abs(b - want) ? n : b), 1);
      const len = (nice / 60) * p.pxPerDeg;
      const label = nice >= 60 ? `${nice / 60}°` : `${nice}′`;
      // Bottom left, lifted above the status line (or anything else floating there).
      let y = h - 18;
      const reserved = cb.reserved?.() ?? [];
      for (let i = 0; i < 4; i++) {
        const box = { x0: 16, y0: y - 8, x1: 16 + len + 40, y1: y + 8 };
        const hit = reserved.find((r) => boxesHit(box, r, 0));
        if (!hit) break;
        y = hit.y0 - 14;
      }
      parts.push(`<g class="atl-svg-scale"><path d="M16 ${f1(y)}h${f1(len)}"/><text x="${f1(16 + len + 6)}" y="${f1(y)}">${label}</text></g>`);
    }
    wrap.innerHTML = `<svg class="atl-svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="Map of the sky with the objects to check${p.whole ? ": the whole sky, east to the left" : ", north up and east to the left"}">${parts.join("")}</svg>`;
  }

  // ---- interaction ---------------------------------------------------------------------------
  function local(ev: { clientX: number; clientY: number }): Vec {
    const r = wrap.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  function signalAt(x: number, y: number): Candidate | null {
    const level = levelFor(v.fov);
    if (!model || !proj || !model.layers.signals || !showsDots(level)) return null;
    const pts: { id: string; x: number; y: number; c: Candidate }[] = [];
    for (const c of model.candidates) {
      const q = proj.project(c);
      if (q) pts.push({ id: c.id, x: q.x, y: q.y, c });
    }
    return nearestHit(pts, x, y, showsSignals(level) ? 14 : 9)?.c ?? null;
  }

  function labelAt(x: number, y: number): FallbackLabel | null {
    return placed.find((p) => boxHolds(p.box, { x, y })) ?? null;
  }

  /** On the whole sky a zone is a small patch: a click anywhere on it opens it. */
  function zoneAt(x: number, y: number): ZoneArea | null {
    if (!model || !proj || !model.layers.zones || !proj.whole) return null;
    const pos = proj.unproject(x, y);
    return pos ? areaAt(model.areas, pos) : null;
  }

  function targetAt(x: number, y: number): Target | null {
    if (!model || !proj) return null;
    const pts: { id: string; x: number; y: number; t: Target }[] = [];
    for (const t of model.targets) {
      const q = proj.project(t);
      if (q) pts.push({ id: t.slug, x: q.x, y: q.y, t });
    }
    return nearestHit(pts, x, y, 12)?.t ?? null;
  }

  let drag: { x: number; y: number; center: SkyPos; moved: boolean; id: number } | null = null;

  wrap.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    const at = local(ev);
    drag = { x: at.x, y: at.y, center: { ra: v.ra, dec: v.dec }, moved: false, id: ev.pointerId };
  });
  wrap.addEventListener("pointermove", (ev) => {
    const at = local(ev);
    if (drag && drag.id === ev.pointerId && proj && !proj.whole) {
      const dx = at.x - drag.x;
      const dy = at.y - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      if (!drag.moved) {
        try {
          wrap.setPointerCapture(ev.pointerId);
        } catch {
          /* fine */
        }
      }
      drag.moved = true;
      const { w } = size();
      const scale = w / v.fov;
      const c = gnomonicInverse(-dx / scale, dy / scale, inFrame(drag.center));
      const next = fromFrame(c);
      v = { ...v, ra: next.ra, dec: next.dec };
      schedule();
      return;
    }
    if (ev.pointerType === "mouse") {
      const s = signalAt(at.x, at.y);
      const t = s ? null : targetAt(at.x, at.y);
      const cellEl = (ev.target as Element | null)?.closest?.("[data-cell]");
      wrap.classList.toggle("is-pointing", !!(s || t || cellEl || labelAt(at.x, at.y) || zoneAt(at.x, at.y)));
      cb.onHover(s, at.x, at.y);
    }
  });
  const endDrag = (ev: PointerEvent) => {
    if (!drag || drag.id !== ev.pointerId) return;
    const wasMoved = drag.moved;
    drag = null;
    if (wasMoved) return;
    const at = local(ev);
    const l = labelAt(at.x, at.y);
    if (l) return l.kind === "zone" ? cb.onZone(l.area) : cb.onTarget(l.target);
    const s = signalAt(at.x, at.y);
    if (s) return cb.onSignal(s);
    const t = targetAt(at.x, at.y);
    if (t) return cb.onTarget(t);
    const z = zoneAt(at.x, at.y);
    if (z) return cb.onZone(z);
    const cellEl = (ev.target as Element | null)?.closest?.("[data-cell]");
    if (cellEl && model && proj) {
      const order = heatOrder(v.fov, size().w);
      const pix = Number(cellEl.getAttribute("data-cell"));
      const cell = model.cellsAt(order).find((c) => c.pix === pix);
      if (cell) return cb.onCell(cell);
    }
    const pos = proj?.unproject(at.x, at.y);
    if (pos && model?.layers.heat && showsHeat(levelFor(v.fov))) {
      const order = heatOrder(v.fov, size().w);
      const pix = ang2pixNest(order, pos.ra, pos.dec);
      const cell = model.cellsAt(order).find((c: Cell) => c.pix === pix);
      if (cell) return cb.onCell(cell);
    }
    if (pos) cb.onEmpty(pos, at.x, at.y);
  };
  wrap.addEventListener("pointerup", endDrag);
  wrap.addEventListener("pointercancel", () => (drag = null));
  wrap.addEventListener("pointerleave", (ev) => {
    if (ev.pointerType === "mouse" && !drag) cb.onHover(null, 0, 0);
  });
  wrap.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      setFov(v.fov * (ev.deltaY > 0 ? 1.25 : 0.8));
    },
    { passive: false },
  );

  let raf = 0;
  function schedule() {
    if (raf || destroyed) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (destroyed) return;
      render();
      cb.onView({ ...v });
    });
  }

  function setFov(fov: number) {
    const next = Math.max(0.05, Math.min(WHOLE_FOV, fov));
    // Leaving the close-up past 60° jumps back to the whole sky in one step.
    v = { ...v, fov: next > LEVEL_FOV.whole ? WHOLE_FOV : next };
    schedule();
  }

  let ro: ResizeObserver | null = null;
  if (typeof ResizeObserver === "function") {
    ro = new ResizeObserver(() => schedule());
    ro.observe(host);
  }

  render();
  // Badges are sized with the page's font: measure again once it has loaded.
  try {
    void document.fonts?.ready.then(() => {
      if (!destroyed) schedule();
    });
  } catch {
    /* sizes stay as measured */
  }

  return {
    kind: "svg",
    update(m) {
      model = m;
      render();
    },
    view: () => ({ ...v }),
    flyTo(to) {
      v = { ra: wrap360(to.ra), dec: to.dec, fov: to.fov ?? v.fov };
      if (v.fov > LEVEL_FOV.whole) v.fov = WHOLE_FOV;
      schedule();
    },
    setProjection() {
      /* the fallback is always an Aitoff map of the whole sky */
    },
    setFrame(g) {
      gal = g;
      schedule();
    },
    zoomBy(f) {
      setFov(v.fov === WHOLE_FOV && f < 1 ? LEVEL_FOV.whole * 0.75 : v.fov * f);
    },
    panBy(dx, dy) {
      if (!proj || proj.whole) return;
      const { w, h } = size();
      const pos = proj.unproject(w / 2 + dx * w, h / 2 + dy * h);
      if (pos) {
        v = { ...v, ra: pos.ra, dec: pos.dec };
        schedule();
      }
    },
    screenOf(p) {
      return proj?.project(p) ?? null;
    },
    destroy() {
      destroyed = true;
      ro?.disconnect();
      if (raf) cancelAnimationFrame(raf);
      wrap.remove();
    },
  };
}
