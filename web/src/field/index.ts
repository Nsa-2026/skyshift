// "Signals in this field" and "Could a Planet X hide here?" under the Blink page's card (P3).
// Brought back from PR #214 (春。 and Codex) onto the Lightbox shell: the cached analysis of a
// featured field (slow movers, fast movers, cold sources, each with its background false-alarm
// chip and a "Show on plate" button) and that field's sensitivity to a distant planet.
//
// Loads lazily: nothing is fetched until the sections scroll into view. A new field aborts the
// old requests and clears the old content at once, and every answer is checked against the field
// it was asked for, so one target's signals never appear under another's name.
import "./field.css";
import { ApiFailure, getJson, loadTargetIndex } from "../api/client";
import { hasScience, loadAnalysis, loadScience, type Analysis } from "../api/science-client";
import type { Det, FramesResponse, GaiaStar, SkyPos } from "../api/types";
import { DETECTORS, formatRaDec } from "../app/astro";
import type { View } from "../app/modules";
import { TARGETS, targetBySlug } from "../app/state";
import {
  ALBEDO,
  SNR_NEEDED,
  countSentence,
  planetSummary,
  signalAt,
  signalRows,
  type ScienceDoc,
  type SignalGroups,
  type SignalRow,
} from "./model";

const html = String.raw;
const KIND_NAME = { slow: "slow mover", fast: "fast mover", cold: "cold source" } as const;
/** Rows shown per group before "Show N more". */
const FIRST_ROWS = 3;

export function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** Keep numbers with their units and ranges whole across line breaks ("1.1–1.6 µm", "4.5 pc"). */
export function keepTogether(s: string): string {
  return s
    .replace(/(\d)–(\d)/g, "$1–⁠$2")
    .replace(/(\d) (µm|µJy|pc|AU|R⊕|%|light-years|exposures|visits)/g, "$1 $2");
}

function put(part: HTMLElement, markup: string) {
  part.innerHTML = keepTogether(markup);
}

export interface FieldData {
  analysis(v: View, signal: AbortSignal): Promise<Analysis>;
  science(v: View, signal: AbortSignal): Promise<ScienceDoc>;
  /** Gaia DR3 stars of a prepared field and the middle of its visits, or null when there are none. */
  gaia(v: View, signal: AbortSignal): Promise<{ stars: GaiaStar[]; mjdMid: number | null } | null>;
}

export interface FieldOptions {
  /** Mark a sky position on the plate (BlinkHandle.highlight), or clear the mark with null. */
  highlight(pos: SkyPos | null): void;
  /** Scrolled into view by "Show on plate". */
  plate?: HTMLElement | null;
  /** Data loaders; tests replace them. */
  data?: Partial<FieldData>;
  /** Calls show() once the sections are near the viewport; returns a stop function. Tests replace it. */
  whenVisible?(el: HTMLElement, show: (visible: boolean) => void): () => void;
}

export interface FieldHandle {
  /** The page's view changed. A new field clears the sections and loads again when visible. */
  view(v: View): void;
  /** The blink showed another epoch: a slow mover on the plate follows its motion. */
  epoch(mjd: number): void;
  /** Someone clicked the plate: their mark replaced ours. */
  picked(): void;
  destroy(): void;
}

/** One field, whatever the clock, detector or field of view: the analysis belongs to the field. */
export function fieldKey(v: Pick<View, "slug" | "ra" | "dec">): string {
  return v.slug ? `t:${v.slug}` : `${v.ra.toFixed(4)},${v.dec.toFixed(4)}`;
}

/** The detector a field's analysis was published for: the featured target's own (D6 for WISE 0855). */
export function analysisDet(v: View): Det {
  return hasScience(v.slug) ? targetBySlug(v.slug)?.det ?? v.det : v.det;
}

export const defaultData: FieldData = {
  analysis(v, signal) {
    const det = analysisDet(v);
    const t = { slug: v.slug ?? "", name: v.name ?? "", ra: v.ra, dec: v.dec, default_det: det, n_frames: 0, n_visits: 0 };
    return loadAnalysis(t, det, signal);
  },
  science(v, signal) {
    return loadScience<ScienceDoc>({ slug: v.slug, ra: v.ra, dec: v.dec }, signal);
  },
  async gaia(v, signal) {
    // Static only: the same months file the blink already read (a browser-cache hit), never /api.
    if (!v.slug || !hasScience(v.slug)) return null;
    const idx = await loadTargetIndex(v.slug, signal);
    const file = idx?.frames.find((f) => f.clock === "months" && f.fov === 5)?.file ?? "frames_months_5.json";
    const f = await getJson<FramesResponse>(`/data/${v.slug}/${file}`, signal);
    const mjds = f.epochs.map((e) => e.mjd_mid).filter(Number.isFinite);
    return { stars: f.gaia ?? [], mjdMid: mjds.length ? (Math.min(...mjds) + Math.max(...mjds)) / 2 : null };
  },
};

function observeVisible(el: HTMLElement, show: (visible: boolean) => void): () => void {
  if (typeof IntersectionObserver === "undefined") {
    show(true);
    return () => {};
  }
  const io = new IntersectionObserver((entries) => show(entries.some((e) => e.isIntersecting)), { rootMargin: "300px 0px" });
  io.observe(el);
  return () => io.disconnect();
}

const PREPARED = TARGETS.filter((t) => hasScience(t.slug));

function preparedLinks(): string {
  const links = PREPARED.map((t) => `<a href="?t=${esc(t.slug)}#/explore">${esc(t.short).replace(/ /g, " ")}</a>`);
  return links.length > 1 ? `${links.slice(0, -1).join(", ")} and ${links.at(-1)}` : links.join("");
}

/** What went wrong, in plain words, with what to do next. Null: show the "not prepared" text. */
function failure(e: unknown, what: string): string | null {
  if (e instanceof ApiFailure) {
    if (e.status === 404) return null;
    if (e.code === "upstream" || e.status >= 500)
      return `The ${what} service didn't answer (${e.status}). Try again in a minute; the five prepared fields load without it.`;
    return `Couldn't load the ${what}: ${e.message}`;
  }
  return `Couldn't load the ${what}. Check the connection and try again.`;
}

export function mountField(el: HTMLElement, opts: FieldOptions): FieldHandle {
  const data: FieldData = { ...defaultData, ...opts.data };
  el.classList.add("fd");
  el.innerHTML = html`
    <section class="fd-sec" aria-labelledby="fd-sig-h">
      <h2 class="fd-h" id="fd-sig-h">Signals in this field</h2>
      <div class="fd-body" data-part="signals"></div>
    </section>
    <section class="fd-sec" aria-labelledby="fd-px-h">
      <h2 class="fd-h" id="fd-px-h">Could a Planet X hide here?</h2>
      <div class="fd-body" data-part="planet"></div>
    </section>
    <p class="visually-hidden" role="status" aria-live="polite" data-part="say"></p>
  `;
  const sigEl = el.querySelector<HTMLElement>('[data-part="signals"]')!;
  const pxEl = el.querySelector<HTMLElement>('[data-part="planet"]')!;
  const sayEl = el.querySelector<HTMLElement>('[data-part="say"]')!;

  let v: View | null = null;
  let key: string | null = null;
  /** Bumped on every new field: an answer for an older generation is dropped. */
  let gen = 0;
  let ctl: AbortController | null = null;
  let loaded = false;
  let visible = false;
  let lastMjd: number | null = null;
  /** The signal marked on the plate (rows of the current field only). */
  let shown: SignalRow | null = null;
  let rowsByKey = new Map<string, SignalRow>();

  const nameOf = (x: View) => x.name ?? formatRaDec(x);

  function waiting() {
    const name = v ? esc(nameOf(v)) : "this spot";
    sigEl.innerHTML = `<p class="fd-wait">Signals found around ${name} load here.</p>`;
    pxEl.innerHTML = `<p class="fd-wait">How deep SPHEREx goes around ${name} loads here.</p>`;
  }

  function busy(part: HTMLElement, text: string) {
    part.setAttribute("aria-busy", "true");
    part.innerHTML = `<p class="fd-wait">${text}</p>`;
  }

  function retryable(part: HTMLElement, text: string, again: () => void) {
    part.removeAttribute("aria-busy");
    put(part, `<p class="fd-err">${esc(text)}</p><button class="btn btn-ghost fd-retry" type="button">Try again</button>`);
    part.querySelector<HTMLButtonElement>(".fd-retry")!.addEventListener("click", again);
  }

  function clearShown(tellPlate: boolean) {
    if (!shown) return;
    shown = null;
    sigEl.querySelectorAll<HTMLButtonElement>(".fd-show").forEach((b) => {
      b.setAttribute("aria-pressed", "false");
    });
    if (tellPlate) opts.highlight(null);
  }

  // ---- signals ------------------------------------------------------------------------------
  async function loadSignals(g: number, signal: AbortSignal, at: View) {
    busy(sigEl, "Loading the analysis…");
    const [a, gaia] = await Promise.allSettled([data.analysis(at, signal), data.gaia(at, signal)]);
    if (g !== gen || signal.aborted) return;
    if (a.status === "rejected") {
      const text = failure(a.reason, "analysis");
      if (text == null) return notPrepared(at);
      return retryable(sigEl, text, () => {
        if (g === gen && !signal.aborted) void loadSignals(g, signal, at);
      });
    }
    const gaiaList = gaia.status === "fulfilled" ? gaia.value : null;
    const groups = signalRows(a.value, {
      centre: at.slug ? { ra: at.ra, dec: at.dec } : null,
      name: at.slug ? at.name : null,
      gaia: gaiaList?.stars ?? null,
      mjdMid: gaiaList?.mjdMid ?? null,
    });
    renderSignals(a.value, groups, at, gaiaList != null);
  }

  function notPrepared(at: View) {
    sigEl.removeAttribute("aria-busy");
    put(sigEl, html`
      <p class="fd-lede">No prepared analysis for ${esc(nameOf(at))}. Signals are worked out ahead of time for five
        fields only: ${preparedLinks()}.</p>
      <p class="fd-cap">Open one of them, or blink this spot yourself and flag anything that jumps in Flag what moved,
        just below.</p>
    `);
  }

  function renderSignals(a: Analysis, g: SignalGroups, at: View, gaiaChecked: boolean) {
    sigEl.removeAttribute("aria-busy");
    rowsByKey = new Map([...g.slow, ...g.fast, ...g.cold].map((r) => [r.key, r]));
    const d = DETECTORS[a.detector as Det];
    const src = a.source ?? ({} as Analysis["source"]);
    const cover = Number.isFinite(src.coverage_arcmin) ? `${src.coverage_arcmin}′` : "small patch";
    const lede = `A prepared analysis of the ${cover} around ${esc(nameOf(at))} checked ${src.n_frames ?? "its"} exposures from ${
      src.n_visits ?? "several"
    } visits for three kinds of signal. Motion was measured in D${a.detector}${d ? ` (${d.min.toFixed(1)}–${d.max.toFixed(1)} µm)` : ""}.`;
    const found = g.total
      ? `It found ${countSentence(g)}. None is confirmed: each is something to check.`
      : `Nothing passed the checks here. That doesn't mean the field is empty: faint, very fast or saturated objects can be missed, and the analysis covers only the ${cover} around the centre.`;
    const vetting = gaiaChecked
      ? "SkyShift compared each signal with Gaia DR3 here; known asteroids and CatWISE are not checked yet."
      : "Not yet compared with Gaia, known asteroids or CatWISE.";
    const n = Number.isFinite(a.n_slides) ? a.n_slides : 100;

    const group = (kind: "slow" | "fast" | "cold", title: string, what: string, rows: SignalRow[]) => {
      const items = rows.map((r, i) => row(r, i >= FIRST_ROWS)).join("");
      const more = rows.length > FIRST_ROWS ? `<button class="fd-more" type="button" aria-expanded="false">Show ${rows.length - FIRST_ROWS} more</button>` : "";
      return html`
        <section class="fd-group" aria-labelledby="fd-${kind}-h">
          <h3 class="fd-h3" id="fd-${kind}-h">${title} <span class="fd-n">(${rows.length})</span></h3>
          <p class="fd-cap">${what}</p>
          ${rows.length ? `<ol class="fd-list">${items}</ol>${more}` : `<p class="fd-none">None passed the checks.</p>`}
        </section>
      `;
    };
    const single = g.coldSingle
      ? ` ${g.coldSingle}${g.cold.length ? " more" : ""} seen in one visit only ${g.coldSingle === 1 ? "is" : "are"} left out: a real one would be there again six months later.`
      : "";

    put(sigEl, html`
      <p class="fd-lede">${lede}</p>
      <p class="fd-found">${found}</p>
      <p class="fd-cap">${vetting}</p>
      <p class="fd-key">
        <span class="fd-chip" data-tone="good">Under 2 %</span>
        <span class="fd-chip" data-tone="caution">2–10 %</span>
        <span class="fd-chip" data-tone="risk">Over 10 %</span>
        False alarms: how often ${n} scrambled copies of this field, with any real motion destroyed, made a signal
        at least as strong. Not the chance that the object is real.
      </p>
      ${group("slow", "Slow movers", "Moved between visits six months apart, as a nearby star or brown dwarf does.", g.slow)}
      ${group("fast", "Fast movers", "Moved between exposures within one visit, as an asteroid or comet does. A visit stack smears them out, so on Months the mark can sit on empty sky.", g.fast)}
      ${group(
        "cold",
        "Cold sources",
        `Bright at 4.4–5.0 µm and dark at 1.6–2.4 µm in the same exposure, as a very cold body is. Listed when seen in two visits or more; their false alarms aren't measured. Pick D6 on the plate to look for them.${single}`,
        g.cold,
      )}
      ${
        a.warnings?.length
          ? `<details class="fd-limits"><summary>Limits of this analysis (${a.warnings.length})</summary><ul>${a.warnings
              .map((w) => `<li>${esc(w)}</li>`)
              .join("")}</ul></details>`
          : ""
      }
    `);
    sigEl.querySelectorAll<HTMLButtonElement>(".fd-more").forEach((b) =>
      b.addEventListener("click", () => {
        const list = b.previousElementSibling as HTMLElement;
        const open = b.getAttribute("aria-expanded") !== "true";
        list.querySelectorAll<HTMLElement>(".fd-item[data-extra]").forEach((li) => (li.hidden = !open));
        b.setAttribute("aria-expanded", String(open));
        b.textContent = open ? "Show fewer" : `Show ${list.querySelectorAll(".fd-item[data-extra]").length} more`;
      }),
    );
    sigEl.querySelectorAll<HTMLButtonElement>(".fd-show").forEach((b) => b.addEventListener("click", () => toggleShow(b)));
  }

  function row(r: SignalRow, extra: boolean): string {
    const dir = r.direction ? `<span class="fd-dir">towards the ${esc(r.direction)}</span>` : "";
    return html`
      <li class="fd-item" data-key="${esc(r.key)}" ${extra ? "data-extra hidden" : ""}>
        <p class="fd-top"><span class="fd-val">${esc(r.value)}</span>${dir}<span class="fd-chip" data-tone="${r.alarm.tone}">${esc(r.alarm.text)}</span></p>
        <p class="fd-facts">${[...r.facts, r.alarm.note].filter(Boolean).map(esc).join(" ")}</p>
        <p class="fd-raw pro-only">${esc(r.raw)}</p>
        <div class="fd-acts">
          <button class="btn btn-ghost fd-show" type="button" aria-pressed="false" data-key="${esc(r.key)}"
            aria-label="Show on plate: ${KIND_NAME[r.kind]}, ${esc(r.value)}">Show on plate</button>
          <details class="fd-next">
            <summary>Next checks</summary>
            <ol>${r.next.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
          </details>
        </div>
      </li>
    `;
  }

  function toggleShow(b: HTMLButtonElement) {
    const r = rowsByKey.get(b.dataset.key ?? "");
    if (!r) return;
    if (shown === r) {
      clearShown(true);
      sayEl.textContent = "Mark removed from the plate.";
      return;
    }
    clearShown(false);
    shown = r;
    b.setAttribute("aria-pressed", "true");
    opts.highlight(signalAt(r, lastMjd));
    sayEl.textContent = "Marked on the plate with a red circle.";
    opts.plate?.scrollIntoView?.({ block: "start" });
  }

  // ---- Planet X -----------------------------------------------------------------------------
  async function loadPlanet(g: number, signal: AbortSignal, at: View) {
    busy(pxEl, "Loading this field's depth…");
    let doc: ScienceDoc;
    try {
      doc = await data.science(at, signal);
    } catch (e) {
      if (g !== gen || signal.aborted) return;
      const text = failure(e, "sensitivity");
      if (text == null) {
        pxEl.removeAttribute("aria-busy");
        put(pxEl, html`<p class="fd-lede">No depth measured for ${esc(nameOf(at))}. It comes from the same prepared
          analysis as the signals, so it exists for the same five fields.</p>`);
        return;
      }
      return retryable(pxEl, text, () => {
        if (g === gen && !signal.aborted) void loadPlanet(g, signal, at);
      });
    }
    if (g !== gen || signal.aborted) return;
    renderPlanet(doc, at);
  }

  function renderPlanet(doc: ScienceDoc, at: View) {
    pxEl.removeAttribute("aria-busy");
    const s = planetSummary(doc.sensitivity);
    if (!s) {
      pxEl.innerHTML = `<p class="fd-lede">This field's analysis has no sensitivity estimate.</p>`;
      return;
    }
    const cover = Number.isFinite(doc.source?.coverage_arcmin) ? `${doc.source!.coverage_arcmin}′` : "small patch";
    const frames = doc.source?.n_frames;
    const sizes = s.sizes.length
      ? html`
          <h3 class="fd-h3">Smallest planet SPHEREx could see here</h3>
          <p class="fd-cap">The radius a planet would need to show at ${SNR_NEEDED}σ in reflected sunlight, at the least:</p>
          <dl class="fd-dl">
            ${s.sizes
              .map(
                (z) =>
                  `<div><dt>At ${z.au.toLocaleString("en-US")} AU</dt><dd><span class="fd-num">${esc(z.text)}</span> <span class="fd-dim">${esc(z.compare)}</span></dd></div>`,
              )
              .join("")}
          </dl>
          <p class="fd-cap">For scale: Earth is 1 R⊕, Neptune 3.9 R⊕ and Jupiter 11.2 R⊕.</p>
        `
      : "";
    const depths = s.depths.length
      ? html`
          <h3 class="fd-h3">Faintest dot it shows</h3>
          <p class="fd-cap">${SNR_NEEDED}σ, with every exposure in each wavelength range stacked.</p>
          <dl class="fd-dl fd-dl-3">
            ${s.depths
              .map(
                (d) =>
                  `<div><dt>${d.um.toFixed(2)} µm</dt><dd><span class="fd-num">${esc(d.text)}</span>${
                    d.n != null ? `<span class="pro-only fd-dim"> ${d.n} exposures</span>` : ""
                  }</dd></div>`,
              )
              .join("")}
          </dl>
        `
      : "";
    put(pxEl, html`
      <p class="fd-answer" data-verdict="${s.verdict}">${esc(s.answer)}</p>
      ${sizes} ${depths}
      ${s.twin ? `<p class="fd-twin">${esc(s.twin)}</p>` : ""}
      <p class="fd-assume">
        This assumes sunlight reflected with albedo ${ALBEDO}, no heat of the planet's own, and a planet that holds still
        while every exposure is stacked; a real one drifts between visits, so it would need to be larger still. It covers
        only the ${esc(cover)} around ${esc(nameOf(at))} and is a model estimate, not a search: it can't show that a planet
        is or isn't there.
      </p>
      <p class="fd-src">
        Depth from the noise left after removing the static sky${frames ? ` in ${frames} exposures` : ""}. Planet Nine
        models: Brown and Batygin (2021), sunlight's colours. WISE 0855: W2 13.9 mag at 2.28 pc.
      </p>
    `);
  }

  // ---- loading and the field guard -----------------------------------------------------------
  function load() {
    if (!v || loaded || !visible) return;
    loaded = true;
    ctl = new AbortController();
    const g = gen;
    const signal = ctl.signal;
    const at = v;
    void loadSignals(g, signal, at);
    void loadPlanet(g, signal, at);
  }

  const stop = (opts.whenVisible ?? observeVisible)(el, (on) => {
    visible = on;
    if (on) load();
  });

  waiting();

  return {
    view(next) {
      const k = fieldKey(next);
      if (k === key) {
        v = next;
        return;
      }
      key = k;
      v = next;
      gen++;
      ctl?.abort();
      ctl = null;
      loaded = false;
      rowsByKey = new Map();
      // The blink clears its own mark when the spot moves; ours goes with the old field.
      if (shown) clearShown(true);
      waiting();
      load();
    },
    epoch(mjd) {
      lastMjd = mjd;
      if (shown && shown.kind === "slow" && shown.pmra != null) opts.highlight(signalAt(shown, mjd));
    },
    picked() {
      clearShown(false);
    },
    destroy() {
      gen++;
      ctl?.abort();
      stop();
      el.innerHTML = "";
    },
  };
}
