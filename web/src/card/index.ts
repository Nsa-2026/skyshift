// Object card (P3): what the object is, the measured shift against Gaia's prediction, the honest
// limits, and its 102-colour spectrum. Lives beside the blink in a 320–400 px column (full width on phones).
import type { Det, FramesResponse, ObjectResponse, SpectrumPoint } from "../api/types";
import { DETECTORS, formatMjd, formatRaDec } from "../app/astro";
import type { CardHandle, MountCard, View } from "../app/modules";
import { targetBySlug } from "../app/state";
import "./card.css";
import {
  PARALLAX_BETWEEN_VISITS_ARCSEC,
  agreement,
  arcsec,
  detectionLimit,
  distancePc,
  formatDistance,
  fullUjy,
  isDetectedMotion,
  isUpperLimit,
  listDets,
  median,
  noMeasureReason,
  paVector,
  paWord,
  pcToLy,
  pmPa,
  saturation,
  sigmaDiff,
  visitCountFromFrames,
  visitsOf,
} from "./format";
import { lightCurveDets, renderLightCurve } from "./lightcurve";
import { describePoint, renderSpectrum, type Chart, type SpectrumMode } from "./spectrum";
import { caret, esc, marker, swatch, visitColour, visitShape } from "./svg";

const html = String.raw;
let uid = 0;

interface Shown {
  view: View;
  object: ObjectResponse | null;
  frames: FramesResponse | null;
  /** Object not known yet (the page is still fetching it). */
  pending: boolean;
}

export const mountCard: MountCard = (el) => {
  const id = `cd${++uid}`;
  el.classList.add("cd");
  let shown: Shown | null = null;
  let mode: SpectrumMode = "det";
  let lcDet: Det | null = null;
  let loading = false;
  let staleObject: ObjectResponse | null = null;
  let width = 0;
  let chart: Chart | null = null;
  let cursor = -1;

  function measureWidth(): number {
    const w = el.clientWidth;
    if (!w) return 352;
    const cs = typeof getComputedStyle === "function" ? getComputedStyle(el) : null;
    const padX = cs ? (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0) : 0;
    return Math.max(280, w - padX);
  }

  function render() {
    if (!shown) return;
    width = measureWidth();
    const { view, object, pending } = shown;
    const target = targetBySlug(view.slug);
    const kind = target?.kind ?? (object?.known_ucd ? "ultracool dwarf" : object?.gaia ? "star in Gaia DR3" : null);
    el.dataset.state = "ready";
    el.setAttribute("aria-busy", String(pending));
    el.innerHTML = [
      headHtml(view, object, kind),
      pending ? skeletonBody() : measureHtml(shown, kind),
      pending ? "" : limitsHtml(object),
      pending ? "" : spectrumHtml(object),
      pending ? "" : lightCurveHtml(object),
      lookForHtml(view),
    ].join("");
    wire();
  }

  // ---- header ------------------------------------------------------------------------------
  function headHtml(view: View, object: ObjectResponse | null, kind: string | null): string {
    const named = object?.name ?? view.name;
    const name = named ?? "This spot";
    const g = object?.gaia ?? null;
    const pc = distancePc(g?.parallax);
    const facts: [string, string][] = [];
    if (g) {
      facts.push(["Gaia DR3", esc(g.id)]);
      if (g.g != null) facts.push(["G magnitude", g.g.toFixed(2)]);
      facts.push([
        "Distance",
        pc != null
          ? `${formatDistance(pc)} pc <span class="cd-dim">(${formatDistance(pcToLy(pc))} light-years), from Gaia</span>`
          : `<span class="cd-dim">Gaia has no parallax</span>`,
      ]);
    }
    const u = object?.known_ucd;
    if (u) facts.push(["Ultracool dwarf", `${esc(u.name)}, ${esc(u.spt)} <span class="cd-dim">(${esc(u.ref)})</span>`]);
    return html`<header class="cd-head">
      <h2 class="cd-name">${esc(name)}</h2>
      <p class="cd-sub">${kind ? `<span class="cd-kind">${esc(kind)}</span>` : ""}<span class="cd-pos">${formatRaDec(view)}</span></p>
      ${facts.length ? `<dl class="cd-facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>` : ""}
    </header>`;
  }

  // ---- measured motion ---------------------------------------------------------------------
  function measureHtml(s: Shown, kind: string | null): string {
    const m = s.object?.measure ?? null;
    if (!m) {
      const visits = visitCountFromFrames(s.frames, s.view, s.view.fov);
      const why = noMeasureReason({ object: s.object, kind, visits });
      return html`<section class="cd-sec cd-measure" aria-labelledby="${id}-m">
        <h3 class="cd-h" id="${id}-m">Shift between visits</h3>
        <p class="cd-none">${why}</p>
      </section>`;
    }
    const seen = isDetectedMotion(m);
    const nsig = sigmaDiff(m);
    const limit = detectionLimit(m);
    const who = s.object?.gaia ? "Gaia" : "The catalogue";
    let compare = "";
    if (m.predicted_arcsec != null && nsig != null) {
      const a = agreement(nsig);
      const n = Math.abs(nsig).toFixed(1);
      const verdict =
        a === "agrees" ? `Within ${n}σ: they agree.` : a === "marginal" ? `${n}σ apart: close, not a clean match.` : `${n}σ apart: they disagree.`;
      compare = `<p class="cd-compare">${who} predicts <b>${arcsec(m.predicted_arcsec)}</b>. ${verdict}</p>`;
    } else {
      compare = `<p class="cd-compare">No Gaia prediction here: nothing to compare with.</p>`;
    }
    const pmDir = pmPa(s.object?.gaia ?? null);
    const days = Math.round(m.dt_yr * 365.25);
    const stats: [string, string][] = [
      ["Rate", `${m.rate_arcsec_yr.toFixed(2)}″/yr`],
      ["Direction", seen ? `${m.pa_deg.toFixed(1)}°, ${paWord(m.pa_deg)}` : `<span class="cd-dim">none above the noise</span>`],
      ["Between visits", `${m.dt_yr.toFixed(2)} yr <span class="cd-dim">(${days} days)</span>`],
      ["Reference stars", String(m.n_ref_stars)],
      ["Slowest visible", `${arcsec(limit)} <span class="cd-dim">(3σ)</span>`],
    ];
    return html`<section class="cd-sec cd-measure" aria-labelledby="${id}-m">
      <h3 class="cd-h" id="${id}-m">${seen ? "Measured shift between visits" : "No shift between visits"}</h3>
      <div class="cd-hero">
        <p class="cd-big" data-seen="${seen}" aria-label="${m.shift_arcsec.toFixed(2)} plus or minus ${m.sigma_arcsec.toFixed(2)} arcseconds">
          <span class="cd-big-n">${arcsec(m.shift_arcsec)}</span><span class="cd-big-pm">± ${arcsec(m.sigma_arcsec)}</span>
        </p>
        ${compassSvg(seen ? m.pa_deg : null, pmDir)}
      </div>
      ${seen ? "" : `<p class="cd-compare">Below the ${arcsec(limit)} we can see (3σ): it stays put.</p>`}
      ${compare}
      <dl class="cd-stats">${stats.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
    </section>`;
  }

  /** North up, east left. Orange arrow: measured direction. Cyan tick: Gaia's proper-motion direction. */
  function compassSvg(pa: number | null, gaiaPa: number | null): string {
    const c = 32;
    const R = 22;
    const parts: string[] = [
      `<circle cx="${c}" cy="${c}" r="${R}" fill="none" stroke="var(--rule-strong)"/>`,
      `<text x="${c}" y="7" class="cd-ax" text-anchor="middle">N</text>`,
      `<text x="3" y="${c + 3.5}" class="cd-ax" text-anchor="start">E</text>`,
    ];
    if (gaiaPa != null) {
      const v = paVector(gaiaPa);
      parts.push(
        `<line x1="${c + v.dx * (R - 6)}" y1="${c + v.dy * (R - 6)}" x2="${c + v.dx * (R + 4)}" y2="${c + v.dy * (R + 4)}" stroke="var(--gaia)" stroke-width="2" stroke-linecap="round"/>`,
      );
    }
    if (pa != null) {
      const v = paVector(pa);
      const tx = c + v.dx * (R - 3);
      const ty = c + v.dy * (R - 3);
      const ang = Math.atan2(v.dy, v.dx);
      const h = 7;
      const a1 = ang + Math.PI * 0.82;
      const a2 = ang - Math.PI * 0.82;
      parts.push(
        `<line x1="${c}" y1="${c}" x2="${tx}" y2="${ty}" stroke="var(--mover)" stroke-width="2.5" stroke-linecap="round"/>`,
        `<path d="M${tx} ${ty}L${tx + Math.cos(a1) * h} ${ty + Math.sin(a1) * h}L${tx + Math.cos(a2) * h} ${ty + Math.sin(a2) * h}Z" fill="var(--mover)"/>`,
      );
    } else {
      parts.push(`<circle cx="${c}" cy="${c}" r="3.5" fill="var(--mover)"/>`);
    }
    const label =
      pa != null
        ? `Direction of the measured shift: ${pa.toFixed(0)}° east of north${gaiaPa != null ? `; Gaia's proper motion points ${gaiaPa.toFixed(0)}°` : ""}.`
        : "No direction: the shift is below the noise.";
    return `<figure class="cd-compass"><svg viewBox="0 0 64 64" width="64" height="64" role="img" aria-label="${esc(label)}">${parts.join("")}</svg>${
      gaiaPa != null && pa != null ? `<figcaption><span class="cd-key cd-key-m"></span>us <span class="cd-key cd-key-g"></span>Gaia</figcaption>` : ""
    }</figure>`;
  }

  // ---- honest limits -----------------------------------------------------------------------
  function limitsHtml(object: ObjectResponse | null): string {
    if (!object) return "";
    const notes: string[] = [];
    const sat = saturation(object.spectrum);
    if (sat) {
      notes.push(html`<li class="cd-limit cd-limit-sat">
        <span class="cd-limit-icon">${swatch(marker("circle", 7, 7, 4.5, "var(--sat)", true))}</span>
        <p><b>Core saturated in ${sat.dets.length === 6 ? "every detector" : listDets(sat.dets)}</b> (${sat.n} of ${sat.total} spectrum points). The shift is measured on the wings;
        saturated points are hollow in the spectrum and their flux reads low.</p>
      </li>`);
    }
    const sigma = object.measure?.sigma_arcsec;
    if (object.measure || object.gaia) {
      notes.push(html`<li class="cd-limit">
        <span class="cd-limit-icon">${swatch(`<circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--gaia)" stroke-width="1.3"/>`)}</span>
        <p><b>No SPHEREx parallax.</b> Between visits, parallax moves even the nearest star here by at most about
        ${PARALLAX_BETWEEN_VISITS_ARCSEC}″, close to our errors${sigma ? ` (±${sigma.toFixed(2)}″)` : ""}.
        ${distancePc(object.gaia?.parallax) != null ? "The distance above is Gaia's." : "Gaia has no distance for this object either."}</p>
      </li>`);
    }
    if (!notes.length) return "";
    return html`<section class="cd-sec cd-limits" aria-labelledby="${id}-l">
      <h3 class="cd-h" id="${id}-l">Limits</h3>
      <ul class="cd-limit-list">${notes.join("")}</ul>
    </section>`;
  }

  // ---- spectrum ----------------------------------------------------------------------------
  function spectrumHtml(object: ObjectResponse | null): string {
    if (!object) return "";
    const sp = object.spectrum;
    if (!sp.length) {
      return html`<section class="cd-sec cd-spec" aria-labelledby="${id}-s">
        <h3 class="cd-h" id="${id}-s">Spectrum</h3>
        <p class="cd-none">No spectrum measured at this spot yet. The shift above still comes from the visit stacks.</p>
      </section>`;
    }
    const visits = visitsOf(sp);
    if (visits.length < 2) mode = "det";
    chart = renderSpectrum(sp, { width, mode, id: `${id}-sp` });
    cursor = -1;
    const um0 = Math.min(...sp.map((p) => p.um));
    const um1 = Math.max(...sp.map((p) => p.um));
    const vText = visits.length === 1 ? "one visit" : `${visits.length} visits`;
    const toggle =
      visits.length > 1
        ? html`<div class="cd-seg" role="group" aria-label="Colour the spectrum by">
            <button type="button" class="cd-seg-b" data-mode="det" aria-pressed="${mode === "det"}">By detector</button>
            <button type="button" class="cd-seg-b" data-mode="visit" aria-pressed="${mode === "visit"}">By visit</button>
          </div>`
        : "";
    return html`<section class="cd-sec cd-spec" aria-labelledby="${id}-s">
      <div class="cd-row">
        <h3 class="cd-h" id="${id}-s">Spectrum</h3>
        ${toggle}
      </div>
      <p class="cd-note">${sp.length} points from ${um0.toFixed(2)} to ${um1.toFixed(2)} µm over ${vText}. Flux in µJy, log scale.</p>
      <div class="cd-plot">${chart.svg}</div>
      ${legendHtml(sp, visits)}
      <p class="cd-readout" id="${id}-ro" aria-live="polite">Point at a dot, or focus the chart and use the arrow keys, to read a value.</p>
      ${tableHtml(sp, visits)}
    </section>`;
  }

  function legendHtml(sp: SpectrumPoint[], visits: number[]): string {
    const items: string[] = [];
    if (mode === "visit") {
      visits.forEach((v, i) => {
        const mjd = median(sp.filter((p) => p.visit === v).map((p) => p.mjd));
        const d = formatMjd(mjd).split(" ").slice(1).join(" ");
        items.push(`<li>${swatch(marker(visitShape(i), 7, 7, 4, visitColour(i), false))}Visit ${v}, ${d}</li>`);
      });
    } else {
      items.push(`<li>${swatch(marker("circle", 7, 7, 4, "var(--emulsion)", false))}Measured, ±1σ bar</li>`);
    }
    if (sp.some((p) => p.saturated)) items.push(`<li>${swatch(marker("circle", 7, 7, 4.2, "var(--emulsion)", true))}Core saturated</li>`);
    if (sp.some((p) => isUpperLimit(p))) items.push(`<li>${swatch(caret(7, 6, "var(--emulsion)"))}Below 2σ: upper limit</li>`);
    items.push(`<li>${swatch(`<line x1="7" y1="1" x2="7" y2="13" stroke="var(--muted)" opacity="0.6"/>`)}Molecule band</li>`);
    return `<ul class="cd-legend">${items.join("")}</ul>`;
  }

  function tableHtml(sp: SpectrumPoint[], visits: number[]): string {
    const rows: string[] = [];
    for (const d of [1, 2, 3, 4, 5, 6] as Det[]) {
      for (const v of visits) {
        const pts = sp.filter((p) => p.det === d && p.visit === v);
        if (!pts.length) continue;
        const det = pts.filter((p) => !isUpperLimit(p));
        rows.push(
          `<tr><td>D${d}</td><td>${v}</td><td>${pts.length}</td><td>${Math.min(...pts.map((p) => p.um)).toFixed(2)}–${Math.max(...pts.map((p) => p.um)).toFixed(2)}</td>` +
            `<td>${det.length ? fullUjy(median(det.map((p) => p.flux_ujy))) : "not detected"}</td><td>${pts.filter((p) => p.saturated).length}</td></tr>`,
        );
      }
    }
    return html`<table class="visually-hidden">
      <caption>Spectrum summary by detector and visit</caption>
      <thead><tr><th scope="col">Detector</th><th scope="col">Visit</th><th scope="col">Points</th><th scope="col">Wavelength µm</th><th scope="col">Median flux µJy</th><th scope="col">Saturated</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>`;
  }

  // ---- light curve -------------------------------------------------------------------------
  function lightCurveHtml(object: ObjectResponse | null): string {
    if (!object || !object.spectrum.length) return "";
    const dets = lightCurveDets(object.spectrum);
    if (!dets.length) return "";
    const want = shown?.view.det;
    if (lcDet == null || !dets.includes(lcDet)) lcDet = want && dets.includes(want) ? want : dets[0];
    const r = DETECTORS[lcDet];
    return html`<section class="cd-sec cd-lc" aria-labelledby="${id}-c">
      <div class="cd-row">
        <h3 class="cd-h" id="${id}-c">Brightness by visit</h3>
        <div class="cd-dets" role="group" aria-label="Detector">
          ${([1, 2, 3, 4, 5, 6] as Det[])
            .map(
              (d) =>
                `<button type="button" class="cd-det det-${d}" data-det="${d}" aria-pressed="${d === lcDet}" ${dets.includes(d) ? "" : "disabled"} title="D${d}, ${DETECTORS[d].min}–${DETECTORS[d].max} µm">D${d}</button>`,
            )
            .join("")}
        </div>
      </div>
      <div class="cd-plot">${renderLightCurve(object.spectrum, lcDet, { width, id: `${id}-lc` })}</div>
      <p class="cd-note">Median flux of each visit in D${lcDet} (${r.min}–${r.max} µm), ±1σ. Faint dots are single exposures at different
      wavelengths: their spread is colour, not time.</p>
    </section>`;
  }

  // ---- what to look for ----------------------------------------------------------------------
  function lookForHtml(view: View): string {
    const t = targetBySlug(view.slug);
    if (t) {
      return html`<section class="cd-sec cd-look" aria-labelledby="${id}-w">
        <h3 class="cd-h" id="${id}-w">What to look for</h3>
        <p>${esc(t.look_for)}</p>
        <p class="cd-expected">Expected: ${esc(t.expected)}</p>
      </section>`;
    }
    const months = `<li><b>Months</b> (visits): a dot that steps between visits is a nearby star or a brown dwarf.</li>`;
    const hours = `<li><b>Hours</b> (exposures): a dot that crosses the field is an asteroid or a comet.</li>`;
    return html`<section class="cd-sec cd-look" aria-labelledby="${id}-w">
      <h3 class="cd-h" id="${id}-w">What to look for</h3>
      <ul class="cd-hints">
        ${view.clock === "hours" ? hours + months : months + hours}
        <li><b>Colour or motion?</b> D1+D4, D2+D5 and D3+D6 are taken at the same moment. A dot that jumps between those pairs is colour, not motion.</li>
      </ul>
    </section>`;
  }

  // ---- states --------------------------------------------------------------------------------
  function skeletonBody(): string {
    return html`<div class="cd-skel" aria-hidden="true">
      <span class="cd-sk cd-sk-l"></span>
      <span class="cd-sk cd-sk-big"></span>
      <span class="cd-sk"></span><span class="cd-sk cd-sk-s"></span>
      <span class="cd-sk cd-sk-chart"></span>
    </div>
    <p class="visually-hidden">Loading the object.</p>`;
  }

  function showLoading(message?: string) {
    el.dataset.state = "loading";
    el.setAttribute("aria-busy", "true");
    el.innerHTML = html`<div class="cd-skel" aria-hidden="true">
        <span class="cd-sk cd-sk-name"></span>
        <span class="cd-sk cd-sk-s"></span>
        <span class="cd-sk cd-sk-l"></span>
        <span class="cd-sk cd-sk-big"></span>
        <span class="cd-sk"></span><span class="cd-sk cd-sk-s"></span>
        <span class="cd-sk cd-sk-chart"></span>
      </div>
      <p class="cd-loading" role="status">${esc(message ?? "Reading the object, its shift and its spectrum…")}</p>`;
  }

  function showError(message?: string) {
    el.dataset.state = "error";
    el.setAttribute("aria-busy", "false");
    const head = shown ? headHtml(shown.view, null, targetBySlug(shown.view.slug)?.kind ?? null) : "";
    el.innerHTML = html`${head}
      <div class="cd-error" role="alert">
        <p><b>The object readout didn't load.</b></p>
        <p>${esc(message ?? "The live service can't be reached.")}</p>
        <p class="cd-dim">The blink still works. Pick a featured target above, or search the same spot again in a minute.</p>
      </div>
      ${shown ? lookForHtml(shown.view) : ""}`;
  }

  // ---- interaction ---------------------------------------------------------------------------
  function wire() {
    el.querySelectorAll<HTMLButtonElement>(".cd-seg-b").forEach((b) =>
      b.addEventListener("click", () => {
        const next = b.dataset.mode as SpectrumMode;
        if (next === mode) return;
        mode = next;
        rerenderKeepingFocus(`.cd-seg-b[data-mode="${next}"]`);
      }),
    );
    el.querySelectorAll<HTMLButtonElement>(".cd-det").forEach((b) =>
      b.addEventListener("click", () => {
        lcDet = Number(b.dataset.det) as Det;
        rerenderKeepingFocus(`.cd-det[data-det="${lcDet}"]`);
      }),
    );
    const svg = el.querySelector<SVGSVGElement>(".cd-spec .cd-chart");
    if (!svg || !chart) return;
    const order = chart.points.map((_, i) => i).sort((a, b) => chart!.points[a].p.um - chart!.points[b].p.um);
    const pick = (i: number) => {
      if (!chart) return;
      cursor = i;
      const pt = chart.points[i];
      const ring = svg.querySelector(".cd-hover");
      ring?.setAttribute("cx", String(pt.x));
      ring?.setAttribute("cy", String(pt.y));
      ring?.setAttribute("visibility", "visible");
      const ro = el.querySelector(`#${id}-ro`);
      if (ro) ro.textContent = describePoint(pt.p, pt.limit);
    };
    svg.addEventListener("pointermove", (ev) => {
      if (!chart) return;
      const r = svg.getBoundingClientRect();
      const vb = svg.viewBox.baseVal;
      const sx = vb.width / (r.width || vb.width);
      const x = (ev.clientX - r.left) * sx;
      const y = (ev.clientY - r.top) * sx;
      let best = -1;
      let bd = 24 * 24;
      chart.points.forEach((p, i) => {
        const d = (p.x - x) ** 2 + (p.y - y) ** 2;
        if (d < bd) {
          bd = d;
          best = i;
        }
      });
      if (best >= 0) pick(best);
    });
    svg.addEventListener("keydown", (ev) => {
      if (!chart || !chart.points.length) return;
      const k = ev.key;
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(k)) return;
      ev.preventDefault();
      const pos = cursor < 0 ? -1 : order.indexOf(cursor);
      const next =
        k === "Home" ? 0 : k === "End" ? order.length - 1 : k === "ArrowRight" ? Math.min(order.length - 1, pos + 1) : Math.max(0, pos - 1);
      pick(order[next]);
    });
  }

  function rerenderKeepingFocus(sel: string) {
    render();
    el.querySelector<HTMLElement>(sel)?.focus();
  }

  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => {
      if (!shown || el.dataset.state !== "ready") return;
      const w = measureWidth();
      if (Math.abs(w - width) > 12) render();
    });
    ro.observe(el);
  }

  const handle: CardHandle = {
    show(view, object, frames) {
      // While loading, the page may hand back the previous object with new frames: treat it as pending.
      const pending = loading && object != null && object === staleObject;
      if (!pending) loading = false;
      shown = { view, object: pending ? null : object, frames, pending };
      render();
    },
    status(kind, message) {
      if (kind === "loading") {
        loading = true;
        staleObject = shown?.object ?? null;
        showLoading(message);
      } else if (kind === "error") {
        loading = false;
        showError(message);
      } else if (shown) {
        loading = false;
        render();
      }
    },
  };
  return handle;
};
