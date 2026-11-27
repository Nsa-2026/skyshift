// Object card (P3): a reading column beside the plate (web/DESIGN.md, "Lightbox").
// Name, one plain sentence of what it is, then the measured shift against the prediction: the
// loudest thing beside the plate. Details as a quiet list, limits as footnotes, then the
// spectrum, brightness by visit, and what to look for on the plate.
import type { Det, FramesResponse, ObjectResponse, SpectrumPoint } from "../api/types";
import { DETECTORS, formatMjd, formatRaDec } from "../app/astro";
import type { CardHandle, MountCard, View } from "../app/modules";
import { targetBySlug } from "../app/state";
import "./card.css";
import {
  PARALLAX_BETWEEN_VISITS_ARCSEC,
  aboutSentence,
  arcsec,
  detectionLimit,
  distancePc,
  formatDistance,
  framesFollow,
  fullUjy,
  isDetectedMotion,
  isUpperLimit,
  listDets,
  measureReasonOf,
  measureSentence,
  measureSource,
  measuredIn,
  measuredVisitCount,
  median,
  missingVisitsSentence,
  noMeasureReason,
  paVector,
  paWord,
  pcToLy,
  pmPa,
  saturation,
  visitCountFromFrames,
  visitTimesFromFrames,
  visitTimesFromSpectrum,
  visitsMissingFromSpectrum,
  visitsOf,
} from "./format";
import { describeVisit, lightCurveDets, renderLightCurve } from "./lightcurve";
import { describePoint, renderSpectrum, type PlotPoint, type SpectrumMode } from "./spectrum";
import { caret, esc, marker, r1, swatch, visitColour, visitShape } from "./svg";

const html = String.raw;
let uid = 0;

interface Shown {
  view: View;
  object: ObjectResponse | null;
  frames: FramesResponse | null;
  /** Object not known yet (the page is still fetching it). */
  pending: boolean;
  /** Frames are here but the object is still being measured (a live /api/object can take a minute). */
  waiting?: boolean;
}

/** A hoverable chart: drawn positions plus the one line each reads as. */
interface Readable {
  svg: SVGSVGElement;
  points: { x: number; y: number }[];
  text(i: number): string;
  readout: Element;
}

interface Note {
  key: "sat" | "parallax";
  html: string;
}

export const mountCard: MountCard = (el, options = {}) => {
  let reference: {um: number; flux_ujy: number}[] = [];
  let section: 'all' | 'summary' | 'motion' | 'spectrum' | 'lightcurve' | ('motion' | 'spectrum')[] = 'all';
  const id = `cd${++uid}`;
  let noteId = id;
  el.classList.add("cd");
  let shown: Shown | null = null;
  let mode: SpectrumMode = "det";
  let lcDet: Det | null = null;
  let loading = false;
  let staleObject: ObjectResponse | null = null;
  let width = 0;
  let resizeObserver: ResizeObserver | undefined;
  const readables: Readable[] = [];
  /** A candidate's panel in place of the name and the measurement (CardHandle.lead). */
  let lead: HTMLElement | null = null;
  const LEAD = `<div class="cd-lead" data-card-lead></div>`;
  /** Put the lead element into its placeholder after a render. */
  function placeLead() {
    if (lead) el.querySelector<HTMLElement>("[data-card-lead]")?.append(lead);
  }

  function measureWidth(): number {
    const panel = el.querySelector<HTMLElement>('[data-card-dashboard="spectrum"]') ?? el.querySelector<HTMLElement>('[data-card-dashboard]');
    const measured = panel ?? el;
    const w = measured.clientWidth;
    if (!w) return 352;
    const cs = typeof getComputedStyle === "function" ? getComputedStyle(measured) : null;
    const padX = cs ? (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0) : 0;
    return Math.max(280, w - padX);
  }

  function kindOf(view: View, object: ObjectResponse | null): string | null {
    const target = targetBySlug(view.slug);
    return target?.kind ?? (object?.known_ucd ? "ultracool dwarf" : object?.gaia ? "star in Gaia DR3" : null);
  }

  function render() {
    if (!shown) return;
    width = measureWidth();
    const { view, object, pending } = shown;
    el.dataset.state = "ready";
    el.setAttribute("aria-busy", String(pending));
    if (Array.isArray(section)) {
      el.innerHTML = section.map(key => {
        noteId = `${id}-${key}`;
        const notes = notesFor(object);
        const content = key === 'motion' && lead ? LEAD : pending ? skeletonHtml(false) : headHtml(view, object, false) + (key === 'motion'
          ? measureHtml(shown!, notes)
          : (options.plots === false ? '' : spectrumHtml(object, notes, shown!.waiting)) + notesHtml(notes));
        return dashboardHtml(key, content);
      }).join('');
      noteId = id;
      placeLead();
      wire();
      return;
    }
    if (pending) {
      el.innerHTML = (lead ? LEAD : headHtml(view, null, true) + skeletonHtml(false)) + lookForHtml(view);
      placeLead();
      return;
    }
    const notes = section === 'summary'
      ? notesFor(object).filter(n=>n.key==='sat').map(n=>({...n,html:object&&satQualifiesShift(object)
        ? 'The target core is saturated. Its motion is measured on the wings, outside the saturated core.'
        : 'Some spectral measurements are saturated. Brightness estimates in those bands are approximate.'}))
      : notesFor(object);
    el.innerHTML = [
      lead ? LEAD : headHtml(view, object, false),
      !lead && ['all','summary','motion'].includes(section) ? measureHtml(shown, notes) : '',
      options.plots === false || !['all','spectrum'].includes(section) ? "" : spectrumHtml(object, notes, shown.waiting),
      options.plots === false || !['all','lightcurve'].includes(section) ? "" : lightCurveHtml(object),
      ['all','summary'].includes(section) ? lookForHtml(view) : '',
    ].join("");
    placeLead();
    wire();
  }

  function dashboardHtml(key:'motion'|'spectrum',content:string):string {
    const label=key==='motion'?'Motion':'Spectrum';
    return `<section class="wb-dashboard" data-card-dashboard="${key}" aria-label="${label} dashboard"><header class="wb-dashboard-head"><h2>${label}</h2><button class="wb-dashboard-close" type="button" data-close-dashboard="${key}" aria-label="Close ${label} dashboard">×</button></header><div class="wb-dashboard-body">${content}</div></section>`;
  }

  // ---- name and what it is -------------------------------------------------------------------
  function headHtml(view: View, object: ObjectResponse | null, pending: boolean): string {
    const name = object?.name ?? view.name ?? "This spot";
    const about = pending
      ? `<span class="cd-sk cd-sk-about" aria-hidden="true"></span>`
      : `<p class="cd-about">${esc(aboutSentence({ slug: view.slug, kind: kindOf(view, object), object, where: formatRaDec(view) }))}</p>`;
    return html`<header class="cd-head">
      <h2 class="cd-name">${esc(name)}</h2>
      ${about}
    </header>`;
  }

  // ---- limits, as numbered footnotes ---------------------------------------------------------
  function notesFor(object: ObjectResponse | null): Note[] {
    if (!object) return [];
    const notes: Note[] = [];
    const sat = saturation(object.spectrum);
    if (sat) {
      const where = sat.dets.length === 6 ? "every detector" : listDets(sat.dets);
      notes.push({
        key: "sat",
        html:
          `The core is saturated in ${where} (${sat.n} of ${sat.total} spectrum points)` +
          (object.measure && satQualifiesShift(object) ? ", so the shift is measured on the wings." : ".") +
          " Saturated points are hollow in the charts; their flux is an estimate good to about ±50%.",
      });
    }
    if (object.measure || object.gaia) {
      const g = object.gaia;
      const sigma = object.measure?.sigma_arcsec;
      const dist =
        g && distancePc(g.parallax) != null
          ? `The distance is Gaia DR3's (source ${esc(g.id)}).`
          : g
            ? "Gaia has no parallax for it either."
            : "Gaia has no distance for it either.";
      notes.push({
        key: "parallax",
        html:
          `SPHEREx doesn't measure parallax. Between visits, parallax moves even the nearest star here by at most about ` +
          `${PARALLAX_BETWEEN_VISITS_ARCSEC}″, close to our errors${sigma ? ` (±&nbsp;${sigma.toFixed(2)}″)` : ""}. ${dist}`,
      });
    }
    // Number the notes in the order their marks appear: on the shift, then the distance, then the spectrum.
    return satQualifiesShift(object) ? notes : [...notes.filter((n) => n.key !== "sat"), ...notes.filter((n) => n.key === "sat")];
  }

  /** Saturation qualifies the shift only when most of the object's points are saturated; otherwise it qualifies the spectrum. */
  function satQualifiesShift(object: ObjectResponse): boolean {
    const sat = saturation(object.spectrum);
    return sat != null && sat.n > sat.total / 2;
  }

  function ref(notes: Note[], key: Note["key"]): string {
    const i = notes.findIndex((n) => n.key === key);
    if (i < 0) return "";
    return `<sup class="cd-ref"><a href="#${noteId}-fn-${key}" data-note="${key}" aria-label="Note ${i + 1}">${i + 1}</a></sup>`;
  }

  function notesHtml(notes: Note[]): string {
    if (!notes.length) return "";
    return html`<ol class="cd-notes" aria-label="Notes">
      ${notes.map((n, i) => `<li id="${noteId}-fn-${n.key}" tabindex="-1"><span class="cd-note-n" aria-hidden="true">${i + 1}</span><p>${n.html}</p></li>`).join("")}
    </ol>`;
  }

  // ---- the measurement -----------------------------------------------------------------------
  function measureHtml(s: Shown, notes: Note[]): string {
    const object = s.object;
    // A measurement without its numbers (the server's Gaia reference stars failed: sigma null) is
    // no measurement: reading it threw, and the throw stopped every later Viewer change (Hours, D1–D6).
    const raw = object?.measure ?? null;
    const m = raw && [raw.shift_arcsec, raw.sigma_arcsec, raw.rate_arcsec_yr].every(Number.isFinite) ? raw : null;
    const src = m ? measureSource(m) : null; // v3.9; absent in older files: then we don't name it
    const g = object?.gaia ?? null;
    const pc = distancePc(g?.parallax);
    const items: [string, string][] = [];
    if (m) {
      const seen = isDetectedMotion(m);
      const gaiaPa = pmPa(g);
      items.push(["Rate", `${m.rate_arcsec_yr.toFixed(2)}″/yr`]);
      items.push([
        "Direction",
        seen
          ? `${Math.round(m.pa_deg)}°, ${paWord(m.pa_deg)}${gaiaPa != null ? ` <span class="cd-dim">(Gaia ${Math.round(gaiaPa)}°)</span>` : ""}`
          : `<span class="cd-dim">none above the noise</span>`,
      ]);
      items.push(["Reference stars", `${m.n_ref_stars} Gaia stars`]);
      items.push(["Slowest visible", `${arcsec(detectionLimit(m))} <span class="cd-dim">(3σ)</span>`]);
      if (src) items.push(["Measured in", measuredIn(src, s.view.det)]);
    }
    if (m || g) {
      const value = pc != null ? `${formatDistance(pc)} pc <span class="cd-dim">(${formatDistance(pcToLy(pc))} ly)</span>` : g ? "no parallax" : "not in Gaia";
      items.push(["Gaia distance", value + ref(notes, "parallax")]);
    }
    const u = object?.known_ucd;
    if (u) items.push(["Spectral type", `${esc(u.spt)} <span class="cd-dim">(${esc(u.ref)})</span>`]);
    const dl = items.length && section !== 'summary'
      ? `<dl class="cd-dl">${items.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`
      : "";

    if (!m) {
      const kind = kindOf(s.view, object);
      const visits = visitCountFromFrames(s.frames, s.view, s.view.fov);
      const why = noMeasureReason({ object, kind, visits, follows: framesFollow(s.frames), at: s.view });
      return html`<section class="cd-measure" aria-labelledby="${id}-m">
        <h3 class="visually-hidden" id="${id}-m">Shift between visits</h3>
        <p class="cd-why">${esc(why)}</p>
        ${movedAwayLink(s)}
        ${dl}${notesHtml(notes)}
      </section>`;
    }

    const seen = isDetectedMotion(m);
    const who = g ? "Gaia" : "The published proper motion";
    const times = visitTimesFromFrames(s.frames, s.view, s.view.fov) ?? visitTimesFromSpectrum(object?.spectrum ?? []);
    const said = measureSentence(m, { visits: src?.visits ?? measuredVisitCount(m, times), who });
    const spoken = `${m.shift_arcsec.toFixed(2)} arcseconds, plus or minus ${m.sigma_arcsec.toFixed(2)}.`;
    return html`<section class="cd-measure" aria-labelledby="${id}-m">
      <h3 class="visually-hidden" id="${id}-m">Shift between visits</h3>
      <p class="cd-hero" data-seen="${seen}">
        <span class="visually-hidden">${spoken}</span>
        <span class="cd-n" aria-hidden="true">${arcsec(m.shift_arcsec)}</span>${object && satQualifiesShift(object) ? ref(notes, "sat") : ""}
        <span class="cd-pm" aria-hidden="true">± ${arcsec(m.sigma_arcsec)}</span>
        ${seen ? arrowSvg(m.pa_deg) : ""}
      </p>
      <p class="cd-says">${said}</p>
      ${dl}${notesHtml(notes)}
    </section>`;
  }

  /** "Open it where it is now": a star catalogued here that had moved away by SPHEREx's visits. */
  function movedAwayLink(s: Shown): string {
    const hint = s.object?.measure_hint ?? null;
    if (measureReasonOf(s.object) !== "moved_away" || !hint || !Number.isFinite(hint.ra) || !Number.isFinite(hint.dec)) return "";
    const v = s.view;
    const q = new URLSearchParams({ ra: hint.ra.toFixed(5), dec: hint.dec.toFixed(5), clock: v.clock, fov: String(v.fov), det: String(v.det) });
    // The sentence just above names the star; a button with a long Gaia id wouldn't fit a phone.
    return html`<p><a class="btn btn-ghost cd-open" href="?${esc(q.toString())}#/explore" data-ra="${hint.ra}" data-dec="${hint.dec}">Open it where it is now</a></p>`;
  }

  /** A pencil arrow in the plate's orientation (north up, east left). */
  function arrowSvg(pa: number): string {
    const v = paVector(pa);
    const c = { x: 14, y: 24 };
    const L = 10;
    const tail = { x: c.x - v.dx * L, y: c.y - v.dy * L };
    const tip = { x: c.x + v.dx * L, y: c.y + v.dy * L };
    const ang = Math.atan2(v.dy, v.dx);
    const h = 5.5;
    const a1 = ang + Math.PI * 0.8;
    const a2 = ang - Math.PI * 0.8;
    const head = `M${r1(tip.x + Math.cos(a1) * h)} ${r1(tip.y + Math.sin(a1) * h)}L${r1(tip.x)} ${r1(tip.y)}L${r1(tip.x + Math.cos(a2) * h)} ${r1(tip.y + Math.sin(a2) * h)}`;
    const label = `Moving ${Math.round(pa)}° east of north (${paWord(pa)}), drawn north up and east left as on the plate.`;
    return html`<svg class="cd-arrow" viewBox="0 0 28 38" width="28" height="38" role="img" aria-label="${esc(label)}">
      <text x="14" y="7.5" class="cd-arrow-n" text-anchor="middle">N</text>
      <g stroke="var(--pencil-red)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.85">
        <path d="M${r1(tail.x)} ${r1(tail.y)}L${r1(tip.x)} ${r1(tip.y)}"/><path d="${head}"/>
      </g>
    </svg>`;
  }

  // ---- spectrum --------------------------------------------------------------------------------
  /** Positions of the spectrum as last drawn, for hover and keyboard reading. */
  let spPoints: PlotPoint[] = [];
  function spectrumHtml(object: ObjectResponse | null, notes: Note[], waiting = false): string {
    spPoints = [];
    if (!object) return waiting
      ? html`<section class="cd-sec cd-spec" aria-labelledby="${id}-s" aria-busy="true">
        <h3 class="cd-h" id="${id}-s">Spectrum</h3>
        <p class="cd-cap" role="status">Measuring the spectrum at this spot from every SPHEREx exposure… This can take up to a minute.</p>
      </section>`
      : "";
    const sp = object.spectrum;
    if (!sp.length) {
      return html`<section class="cd-sec cd-spec" aria-labelledby="${id}-s">
        <h3 class="cd-h" id="${id}-s">Spectrum</h3>
        <p class="cd-cap">${emptySpectrumSentence(object)}</p>
      </section>`;
    }
    const visits = visitsOf(sp);
    if (visits.length < 2) mode = "det";
    const chart = renderSpectrum(sp, { width, mode, reference, id: `${id}-sp` });
    spPoints = chart.points;
    const um0 = Math.min(...sp.map((p) => p.um));
    const um1 = Math.max(...sp.map((p) => p.um));
    const vText = visits.length === 1 ? "one visit" : `${visits.length} visits`;
    const satRef = object.measure && satQualifiesShift(object) ? "" : ref(notes, "sat");
    const missing = missingHtml(sp);
    const toggle =
      visits.length > 1
        ? html`<div class="seg cd-seg" role="group" aria-label="Colour the spectrum by">
            <button type="button" data-mode="det" aria-pressed="${mode === "det"}">By detector</button>
            <button type="button" data-mode="visit" aria-pressed="${mode === "visit"}">By visit</button>
          </div>`
        : "";
    return html`<section class="cd-sec cd-spec" aria-labelledby="${id}-s">
      <div class="cd-row">
        <h3 class="cd-h" id="${id}-s">Spectrum</h3>
        ${toggle}
      </div>
      <p class="cd-cap">${sp.length} points from ${um0.toFixed(2)} to ${um1.toFixed(2)} µm over ${vText}. Flux in µJy, log scale.${satRef}${missing}</p>
      <div class="cd-plot" data-chart="spectrum">${chart.svg}</div>
      ${keysHtml(sp, visits)}
      ${reference.length ? '<p class="cd-cap">Outlined points: DR1 reference on the same flux scale.</p>' : ""}
      <p class="cd-readout" id="${id}-ro" aria-live="polite">Point at a dot, or focus the chart and use the arrow keys.</p>
      ${tableHtml(sp, visits)}
    </section>`;
  }

  /** " Visit 3, May 2026, is in the blink but not here: …", when the months blink has visits the spectrum (or one detector of it) lacks. */
  function missingHtml(sp: SpectrumPoint[], det?: Det): string {
    if (!shown) return "";
    const said = missingVisitsSentence(visitsMissingFromSpectrum(shown.frames, shown.view, shown.view.fov, sp, det), det);
    return said ? ` <span class="cd-missing">${esc(said)}</span>` : "";
  }

  /** Why the spectrum is empty, from the optional spectrum_reason (contracts/api.md v3; absent reads as null). */
  function emptySpectrumSentence(object: ObjectResponse): string {
    const reason = (object as ObjectResponse & { spectrum_reason?: string | null }).spectrum_reason ?? null;
    const shift = object.measure ? " The shift above comes from the visit stacks." : "";
    if (reason === "saturated") return `No spectrum: it is so bright that no exposure left a usable aperture around its saturated core.${shift}`;
    if (reason === "no_data") return `No spectrum: no usable exposure covers it yet.${shift}`;
    return `No spectrum measured here yet.${shift}`;
  }

  function keysHtml(sp: SpectrumPoint[], visits: number[]): string {
    const items: string[] = [];
    if (mode === "visit") {
      visits.forEach((v, i) => {
        const mjd = median(sp.filter((p) => p.visit === v).map((p) => p.mjd));
        const d = formatMjd(mjd).split(" ").slice(1).join(" ");
        items.push(`<li>${swatch(marker(visitShape(i), 7, 7, 3.6, visitColour(i, visits.length), false))}Visit ${v}, ${d}</li>`);
      });
    }
    if (sp.some((p) => p.saturated)) items.push(`<li>${swatch(marker("circle", 7, 7, 3.6, "var(--ink-2)", true))}Core saturated</li>`);
    if (sp.some((p) => isUpperLimit(p))) items.push(`<li>${swatch(caret(7, 6, "var(--ink-2)"))}Upper limit, below 2σ</li>`);
    items.push(`<li>${swatch(`<rect x="3" y="1" width="8" height="12" fill="var(--ink)" opacity="0.09"/>`)}Molecule band</li>`);
    return `<ul class="cd-keys">${items.join("")}</ul>`;
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
    return html`<div class="visually-hidden"><table>
      <caption>Spectrum summary by detector and visit</caption>
      <thead><tr><th scope="col">Detector</th><th scope="col">Visit</th><th scope="col">Points</th><th scope="col">Wavelength µm</th><th scope="col">Median flux µJy</th><th scope="col">Saturated</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table></div>`;
  }

  // ---- brightness by visit -------------------------------------------------------------------
  let lcPoints: ReturnType<typeof renderLightCurve>["points"] = [];
  function lightCurveHtml(object: ObjectResponse | null): string {
    lcPoints = [];
    if (!object || !object.spectrum.length) return "";
    const dets = lightCurveDets(object.spectrum);
    if (!dets.length) return "";
    const want = shown?.view.det;
    if (lcDet == null || !dets.includes(lcDet)) lcDet = want && dets.includes(want) ? want : dets[0];
    const r = DETECTORS[lcDet];
    const chart = renderLightCurve(object.spectrum, lcDet, { width, id: `${id}-lc` });
    lcPoints = chart.points;
    return html`<section class="cd-sec cd-lc" aria-labelledby="${id}-c">
      <div class="cd-row">
        <h3 class="cd-h" id="${id}-c">Brightness by visit</h3>
        <div class="seg cd-seg cd-dets" role="group" aria-label="Detector">
          ${([1, 2, 3, 4, 5, 6] as Det[])
            .map(
              (d) =>
                `<button type="button" class="det-${d}" data-det="${d}" aria-pressed="${d === lcDet}" ${dets.includes(d) ? "" : "disabled"} title="D${d}, ${DETECTORS[d].min}–${DETECTORS[d].max} µm${dets.includes(d) ? "" : ": fewer than two visits"}"><span class="cd-det-dot" aria-hidden="true"></span>D${d}</button>`,
            )
            .join("")}
        </div>
      </div>
      <p class="cd-cap">Median flux of each visit in D${lcDet}, ${r.min}–${r.max} µm, ±1σ. Faint dots are single exposures at different wavelengths: their spread is colour, not time.${missingHtml(object.spectrum, lcDet)}</p>
      <div class="cd-plot" data-chart="lightcurve">${chart.svg}</div>
      <p class="cd-readout" id="${id}-lro" aria-live="polite">Point at a visit to read its brightness.</p>
    </section>`;
  }

  // ---- what to look for on the plate ---------------------------------------------------------
  function lookForHtml(view: View): string {
    const t = targetBySlug(view.slug);
    if (t) {
      return html`<section class="cd-sec cd-look" aria-labelledby="${id}-w">
        <h3 class="cd-h" id="${id}-w">What to look for</h3>
        <p class="cd-look-p">${esc(t.look_for)}</p>
        <p class="cd-expected"><span>Expected</span> ${esc(t.expected)}</p>
      </section>`;
    }
    const months = `<p class="cd-look-p"><b>Months.</b> A dot that steps between visits is a nearby star or a brown dwarf.</p>`;
    const hours = `<p class="cd-look-p"><b>Hours.</b> A dot that crosses the field between exposures is an asteroid or a comet.</p>`;
    return html`<section class="cd-sec cd-look" aria-labelledby="${id}-w">
      <h3 class="cd-h" id="${id}-w">What to look for</h3>
      ${view.clock === "hours" ? hours + months : months + hours}
      <p class="cd-look-p"><b>Colour or motion?</b> D1+D4, D2+D5 and D3+D6 are taken at the same moment, so a dot that jumps between those pairs is colour, not motion.</p>
    </section>`;
  }

  // ---- states --------------------------------------------------------------------------------
  /** A quiet, static skeleton in the shapes of the card. */
  function skeletonHtml(withHead: boolean): string {
    const head = withHead
      ? `<span class="cd-sk cd-sk-name"></span><span class="cd-sk cd-sk-about"></span>`
      : "";
    return html`<div class="cd-skel" aria-hidden="true">
        ${head}
        <span class="cd-sk cd-sk-hero"></span>
        <span class="cd-sk cd-sk-line"></span><span class="cd-sk cd-sk-line cd-sk-short"></span>
        <span class="cd-sk-dl">${"<span><span class=\"cd-sk cd-sk-dt\"></span><span class=\"cd-sk cd-sk-dd\"></span></span>".repeat(4)}</span>
        <span class="cd-sk cd-sk-chart"></span>
      </div>
      <p class="cd-loading" role="status">Reading the object, its shift and its spectrum…</p>`;
  }

  function showLoading() {
    el.dataset.state = "loading";
    el.setAttribute("aria-busy", "true");
    el.innerHTML = Array.isArray(section)
      ? section.map(key=>dashboardHtml(key,key==='motion'&&lead?LEAD:skeletonHtml(true))).join('')
      : (lead ? LEAD : '') + skeletonHtml(true);
    placeLead();
  }

  function showError(message?: string) {
    el.dataset.state = "error";
    el.setAttribute("aria-busy", "false");
    const text = message ?? "The object details can't be reached right now: pick a featured target above, or try this spot again in a minute.";
    const content = html`${shown ? headHtml(shown.view, null, false) : ""}
      <p class="cd-error" role="alert">${esc(text)}</p>
      ${shown ? lookForHtml(shown.view) : ""}`;
    el.innerHTML = Array.isArray(section)
      ? section.map(key=>dashboardHtml(key,key==='motion'&&lead?LEAD:content)).join('')
      : (lead ? LEAD : content);
    placeLead();
  }

  // ---- interaction ---------------------------------------------------------------------------
  function wire() {
    el.querySelector<HTMLAnchorElement>(".cd-open")?.addEventListener("click", (ev) => {
      // no app around it, or a new tab asked for: the link loads the page at the star
      if (!options.onExplore || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      ev.preventDefault();
      const a = ev.currentTarget as HTMLAnchorElement;
      options.onExplore({ ra: Number(a.dataset.ra), dec: Number(a.dataset.dec) });
    });
    el.querySelectorAll<HTMLButtonElement>(".cd-spec .cd-seg button").forEach((b) =>
      b.addEventListener("click", () => {
        const next = b.dataset.mode as SpectrumMode;
        if (next === mode) return;
        mode = next;
        rerenderKeepingFocus(`.cd-spec .cd-seg button[data-mode="${next}"]`);
      }),
    );
    el.querySelectorAll<HTMLButtonElement>(".cd-dets button").forEach((b) =>
      b.addEventListener("click", () => {
        const next = Number(b.dataset.det) as Det;
        if (next === lcDet) return;
        lcDet = next;
        rerenderKeepingFocus(`.cd-dets button[data-det="${lcDet}"]`);
      }),
    );
    // Footnote marks: move focus to the note without touching the URL hash (the app routes on it).
    el.querySelectorAll<HTMLAnchorElement>(".cd-ref a").forEach((a) =>
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const note = el.querySelector<HTMLElement>(a.getAttribute('href')!);
        if (!note) return;
        note.scrollIntoView?.({ block: "nearest" });
        note.focus({ preventScroll: true });
      }),
    );

    readables.length = 0;
    const sp = el.querySelector<SVGSVGElement>('[data-chart="spectrum"] svg');
    const spRo = el.querySelector(`#${id}-ro`);
    if (sp && spRo && spPoints.length) {
      const pts = spPoints;
      readables.push({ svg: sp, points: pts, text: (i) => describePoint(pts[i].p, pts[i].limit), readout: spRo });
    }
    const lc = el.querySelector<SVGSVGElement>('[data-chart="lightcurve"] svg');
    const lcRo = el.querySelector(`#${id}-lro`);
    if (lc && lcRo && lcDet != null && lcPoints.length) {
      const pts = lcPoints;
      const det = lcDet;
      readables.push({ svg: lc, points: pts, text: (i) => describeVisit(pts[i].v, det), readout: lcRo });
    }
    readables.forEach(wireReadable);
  }

  function wireReadable(r: Readable) {
    let cursor = -1;
    const order = r.points.map((_, i) => i).sort((a, b) => r.points[a].x - r.points[b].x);
    const pick = (i: number) => {
      cursor = i;
      const pt = r.points[i];
      const ring = r.svg.querySelector(".cd-hover");
      ring?.setAttribute("cx", String(pt.x));
      ring?.setAttribute("cy", String(pt.y));
      ring?.setAttribute("visibility", "visible");
      r.readout.textContent = r.text(i);
      r.readout.setAttribute("data-reading", "");
    };
    // Nearest point within 24 px: pointer hover on desktop, a tap or drag on phones.
    const nearest = (ev: PointerEvent) => {
      const box = r.svg.getBoundingClientRect();
      const vb = r.svg.viewBox.baseVal;
      const s = vb.width / (box.width || vb.width);
      const x = (ev.clientX - box.left) * s;
      const y = (ev.clientY - box.top) * s;
      let best = -1;
      let bd = 24 * 24;
      r.points.forEach((p, i) => {
        const d = (p.x - x) ** 2 + (p.y - y) ** 2;
        if (d < bd) {
          bd = d;
          best = i;
        }
      });
      if (best >= 0) pick(best);
    };
    r.svg.addEventListener("pointermove", nearest);
    r.svg.addEventListener("pointerdown", nearest);
    r.svg.addEventListener("keydown", (ev) => {
      if (!r.points.length) return;
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
    resizeObserver = new ResizeObserver(() => {
      if (!shown || el.dataset.state !== "ready") return;
      const w = measureWidth();
      if (Math.abs(w - width) > 12) render();
    });
    resizeObserver.observe(el);
  }

  const handle: CardHandle = {
    section(value) {
      if(JSON.stringify(section) === JSON.stringify(value)) return;
      section = value;
      if(Array.isArray(value))el.dataset.dashboardCount=String(value.length);else delete el.dataset.dashboardCount;
      resizeObserver?.observe(el.closest('.wb-dashboards') ?? el);
      el.classList.toggle('cd-summary',value === 'summary');
      if(shown) render();
    },
    reference(points) { reference = points; if (shown) render(); },
    lead(value) {
      if (value === lead) return;
      lead?.remove();
      lead = value;
      if (shown) render();
    },
    show(view, object, frames) {
      // While loading, the page may hand back the previous object with new frames: treat it as pending.
      const pending = loading && object != null && object === staleObject;
      // Frames first, object still on its way: keep loading so its own show() ends it.
      const waiting = loading && object == null;
      if (!pending && !waiting) loading = false;
      shown = { view, object: pending ? null : object, frames, pending, waiting };
      render();
    },
    status(kind, message) {
      if (kind === "loading") {
        loading = true;
        staleObject = shown?.object ?? null;
        showLoading();
      } else if (kind === "error") {
        loading = false;
        showError(message);
      } else {
        loading = false;
        if (shown) render();
      }
    },
  };
  return handle;
};
