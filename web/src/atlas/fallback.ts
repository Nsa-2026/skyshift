// The map without WebGL2 (or when Aladin can't start): an SVG of the same heat map, zones,
// featured targets and signals, with no background image. The whole sky is an Aitoff map; click
// a red cell (or zoom) and it becomes a tangent-plane close-up where single signals show.
// Same callbacks as the globe, so the drawer, pin and list work the same.
// Tonight's search is drawn under the heat map, its fills from SVG patterns.
import type { Candidate, SkyPos } from "../api/types";
import type { Target } from "../app/state";
import { ang2pixNest } from "./healpix";
import { cellOutline, isNested, rampAlpha, rampIndex, rampSteps, zoneLabelAnchor, zoneOutline, zoneSideAnchor, type Cell } from "./heat";
import { countText, zoneName } from "./kinds";
import type { MapCallbacks, MapHandle, MapModel, MapView } from "./map";
import { arrowLengthPx, diamond, moverTick, nearestHit, paToScreen, starArrow, MARK_R, type Vec } from "./marks";
import { suspectAlpha } from "./search";
import { zoneLook } from "./searchdraw";
import { aitoff, aitoffInverse, fromGalactic, gnomonic, gnomonicInverse, lonLat, offsetPa, wrap360 } from "./sphere";
import { LABEL_MIN_PX, LEVEL_FOV, WHOLE_FOV, cellPx, heatOrder, levelFor, showsHeat, showsSignals } from "./zoom";

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
 * ellipse is 4s × 2s), centred; on a tall stage (a phone) it sits near the top instead, clear of
 * the status line and zoom buttons at the bottom.
 */
export function wholeGeometry(w: number, h: number): { s: number; cy: number } {
  const s = Math.max(1, Math.min((w - 24) / 4, (h - 24) / 2));
  const cy = h > w * 0.75 ? Math.min(h / 2, s + 36) : h / 2;
  return { s, cy };
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
      const { s, cy } = wholeGeometry(w, h);
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
    const color = c.kind === "spectral" ? "var(--pencil-violet)" : "var(--pencil-red)";
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
    return `<g class="atl-svg-mark${selected ? " is-selected" : ""}"><path class="atl-svg-halo" d="${d}"/><path d="${d}" stroke="${color}"/>${reticle}</g>`;
  }

  function render() {
    if (destroyed) return;
    const { w, h } = size();
    const p = makeProjector();
    proj = p;
    const level = levelFor(v.fov);
    const order = heatOrder(v.fov, w);
    const maxJump = w * 0.4;
    const parts: string[] = [];
    /** Names go on last, over the marks, so no mark crosses one out. */
    const labels: string[] = [];

    // The sky's outline (whole sky) or the glass (close-up).
    if (p.whole) {
      const { s, cy } = wholeGeometry(w, h);
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
      if (model.layers.zones) {
        for (const z of model.zones) {
          const d = path(zoneOutline(z, 0.5), true, p, maxJump);
          if (d) parts.push(`<path class="atl-svg-zone" d="${d}"/>`);
          // As on the globe: beside the zone on the whole sky (with its count), under its box closer in.
          const side = level === "whole";
          const at = p.project(side ? zoneSideAnchor(z) : zoneLabelAnchor(z));
          const name = side ? `${zoneName(z.zone)}, ${new Intl.NumberFormat("en-GB").format(z.count)}` : zoneName(z.zone);
          if (at && level !== "detail" && !(side && isNested(z, model.zones)))
            labels.push(
              side
                ? `<text class="atl-svg-zone-label" x="${f1(at.x - 6)}" y="${f1(at.y)}" text-anchor="end">${esc(name)}</text>`
                : `<text class="atl-svg-zone-label" x="${f1(at.x + 2)}" y="${f1(at.y + 12)}">${esc(name)}</text>`,
            );
        }
      }
      // A phone's whole sky has no room for names: the marks stay, and a tap opens each one.
      const named = !(p.whole && w < 640);
      for (const t of model.targets) {
        const q = p.project(t);
        if (!q || q.x < -20 || q.y < -20 || q.x > w + 20 || q.y > h + 20) continue;
        const r = `M${f1(q.x + 3.5)} ${f1(q.y)}h4.5M${f1(q.x - 3.5)} ${f1(q.y)}h-4.5M${f1(q.x)} ${f1(q.y + 3.5)}v4.5M${f1(q.x)} ${f1(q.y - 3.5)}v-4.5`;
        const left = q.x > w * 0.78;
        const label = named
          ? `<text x="${f1(left ? q.x - 11 : q.x + 11)}" y="${f1(q.y)}"${left ? ' text-anchor="end"' : ""}>${esc(t.short)}</text>`
          : "";
        parts.push(`<g class="atl-svg-target"><path class="atl-svg-halo" d="${r}"/><path d="${r}"/>${label}</g>`);
      }
      if (showsSignals(level) && model.layers.signals) {
        for (const c of model.candidates) {
          const q = p.project(c);
          if (!q || q.x < -30 || q.y < -30 || q.x > w + 30 || q.y > h + 30) continue;
          parts.push(markSvg(c, q, p, model.selectedId === c.id));
        }
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
      parts.push(
        `<g class="atl-svg-scale"><path d="M16 ${h - 18}h${f1(len)}"/><text x="${f1(16 + len + 6)}" y="${h - 18}">${label}</text></g>`,
      );
    }
    wrap.innerHTML = `<svg class="atl-svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="Map of the sky with the objects to check${p.whole ? ": the whole sky, east to the left" : ", north up and east to the left"}">${parts.join("")}</svg>`;
  }

  // ---- interaction ---------------------------------------------------------------------------
  function local(ev: { clientX: number; clientY: number }): Vec {
    const r = wrap.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  function signalAt(x: number, y: number): Candidate | null {
    if (!model || !proj || !model.layers.signals || !showsSignals(levelFor(v.fov))) return null;
    const pts: { id: string; x: number; y: number; c: Candidate }[] = [];
    for (const c of model.candidates) {
      const q = proj.project(c);
      if (q) pts.push({ id: c.id, x: q.x, y: q.y, c });
    }
    return nearestHit(pts, x, y, 14)?.c ?? null;
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
      wrap.classList.toggle("is-pointing", !!(s || t || cellEl));
      cb.onHover(s, at.x, at.y);
    }
  });
  const endDrag = (ev: PointerEvent) => {
    if (!drag || drag.id !== ev.pointerId) return;
    const wasMoved = drag.moved;
    drag = null;
    if (wasMoved) return;
    const at = local(ev);
    const s = signalAt(at.x, at.y);
    if (s) return cb.onSignal(s);
    const t = targetAt(at.x, at.y);
    if (t) return cb.onTarget(t);
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
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
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
