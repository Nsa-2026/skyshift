// One real example per lesson, built from the static SPHEREx frames and object files.
// Every number in a caption comes from the data it describes.
import { epochsFor, loadObject, loadSuspects, loadValidation } from "../api/client";
import type { Det, Epoch, ObjectResponse } from "../api/types";
import { DETECTORS, formatMjd, gaiaAt, sepArcsec } from "../app/astro";
import {
  centreCrop,
  classifyFakes,
  cropAround,
  epochValues,
  frameSet,
  gaiaInCrop,
  knownIn,
  moverExposures,
  noDataShare,
  rateArcsecPerHour,
  target,
  toPixel,
  type FrameSet,
} from "./data";
import { esc } from "./glossary";
import type { FigureId } from "./lessons";
import { exploreHref } from "./lessons";
import { mountPlate, type PlateHandle, type PlateMark } from "./plate";
import { lessonSpectrumSvg } from "./spectrum";
import { mountVoteDemo } from "./votes";

export interface FigureHost {
  signal: AbortSignal;
  /** Plates register here so the page can pause them when hidden. */
  plates: PlateHandle[];
  /** Other cleanups (listeners, demos). */
  cleanups: (() => void)[];
  /** Width of the figure's column when it was built (px), for charts drawn at their real size. */
  width?: number;
}

type Builder = (el: HTMLElement, host: FigureHost) => Promise<void>;

const fmtUm = (n: number) => n.toFixed(n < 1 ? 2 : 1);
const um = (d: Det) => `${fmtUm(DETECTORS[d].min)}–${fmtUm(DETECTORS[d].max)} µm`;
const date = (mjd: number) => formatMjd(mjd);
const fmt = (n: number, digits = 2) => n.toFixed(digits);
const hours = (a: Epoch, b: Epoch) => (b.mjd_mid - a.mjd_mid) * 24;

function plateBox(parent: HTMLElement, cls = ""): HTMLElement {
  const d = document.createElement("div");
  if (cls) d.className = cls;
  parent.appendChild(d);
  return d;
}

function caption(parent: HTMLElement, html: string): HTMLElement {
  const c = document.createElement("p");
  c.className = "ln-cap";
  c.innerHTML = html;
  parent.appendChild(c);
  return c;
}

/** "Measured 10.83″ ± 0.36″ … Gaia predicts …": the shift is the largest number in the lesson. */
function measureBlock(m: ObjectResponse["measure"], opts: { versus: string | null; expectedText?: string }): string {
  if (!m) return "";
  const n = m.predicted_arcsec != null && m.sigma_arcsec > 0 ? Math.abs(m.shift_arcsec - m.predicted_arcsec) / m.sigma_arcsec : null;
  const verdict = n == null ? "" : n <= 2 ? "They agree." : n <= 3 ? "Close, within 3 errors." : "They differ: worth a look.";
  const vs =
    m.predicted_arcsec != null && opts.versus
      ? `${opts.versus} predicts ${fmt(m.predicted_arcsec)}″. ${verdict}`
      : opts.expectedText ?? "";
  return `
    <div class="ln-measure">
      <p class="ln-measure-n"><span class="ln-big">${fmt(m.shift_arcsec)}″</span> <span class="ln-pm">± ${fmt(m.sigma_arcsec)}″</span></p>
      <p class="ln-measure-t">SkyShift's measurement of the shift between the first and last visit, ${fmt(m.dt_yr)} years apart (${fmt(
        m.rate_arcsec_yr,
      )}″ a year). ${vs}</p>
    </div>`;
}

// ---- 1: six detectors ------------------------------------------------------------------------
const six: Builder = async (el, host) => {
  const set = await frameSet("barnard", "months", 15, 2, host.signal);
  const dets: Det[] = [1, 2, 3, 4, 5, 6];
  const eps = dets.map((d) => epochsFor(set.frames, d)[0]).filter((e): e is Epoch => !!e);
  const vals = await Promise.all(eps.map((e) => epochValues(set, e, host.signal)));
  const crop = centreCrop(set.frames.size, 150);
  const grid = plateBox(el, "ln-six");
  eps.forEach((e, i) => {
    const cell = plateBox(grid, `ln-six-cell det-${e.det}`);
    const box = plateBox(cell);
    host.plates.push(
      mountPlate(box, {
        nx: set.frames.size[0],
        ny: set.frames.size[1],
        frames: [{ values: vals[i], label: "", step: "" }],
        crop,
        compact: true,
        pixelArcsec: set.frames.pixel_arcsec,
        description: `Barnard's Star's field in detector D${e.det}, ${um(e.det)}, ${date(e.mjd_mid)}.`,
        contrast: 0.35,
      }),
    );
    const lab = document.createElement("p");
    lab.className = "ln-six-label";
    lab.innerHTML = `<strong>D${e.det}</strong> ${um(e.det)}`;
    cell.appendChild(lab);
  });
  const t0 = Math.min(...eps.map((e) => e.mjd_mid));
  const t1 = Math.max(...eps.map((e) => e.mjd_mid));
  const across = ((crop.w * set.frames.pixel_arcsec) / 60).toFixed(1);
  caption(
    el,
    `Barnard's Star's field, ${across}′ across, in all six detectors during its first visit (${date(t0)} to ${date(t1)}). North is up, east left.
    The same stars in six ranges of colour: they look different because stars have different colours, not because anything changed.
    Each detector is drawn with its own contrast; Barnard's Star's overflowing centre is hatched.`,
  );
};

// ---- 2: Barnard's Star steps north -----------------------------------------------------------
const barnard: Builder = async (el, host) => {
  const t = target("barnard")!;
  const [set, obj] = await Promise.all([
    frameSet("barnard", "months", 5, 2, host.signal),
    loadObject({ ra: t.ra, dec: t.dec, slug: t.slug }, host.signal).catch(() => null),
  ]);
  const eps = epochsFor(set.frames, 2);
  const vals = await Promise.all(eps.map((e) => epochValues(set, e, host.signal)));
  const star =
    set.frames.gaia.find((s) => s.id === obj?.gaia?.id) ?? [...set.frames.gaia].sort((a, b) => (a.g ?? 99) - (b.g ?? 99))[0];
  // Gaia's position moved to each visit (proper motion; parallax is under a pixel here).
  const at = eps.map((e) => (star ? toPixel(set, e, gaiaAt(star, e.mjd_mid)) : null));
  const first = at[0];
  const crop = cropAround(at.filter((p): p is { x: number; y: number } => !!p), set.frames.size, 40, 100);
  const marks: PlateMark[] = first ? [{ kind: "reticle", x: first.x, y: first.y, r: 7, label: "Where the star was in visit 1" }] : [];
  const box = plateBox(el);
  host.plates.push(
    mountPlate(box, {
      nx: set.frames.size[0],
      ny: set.frames.size[1],
      frames: eps.map((e, i) => ({
        values: vals[i],
        label: `Visit ${i + 1} of ${eps.length}, ${date(e.mjd_mid)}`,
        step: `Visit ${i + 1}`,
      })),
      crop,
      marks,
      pixelArcsec: set.frames.pixel_arcsec,
      description: `Barnard's Star in D2 (${um(2)}) at ${eps.length} visits, blinking. It moves up, north, past a red mark at its first position.`,
      contrast: 0.3,
    }),
  );
  const across = ((crop.w * set.frames.pixel_arcsec) / 60).toFixed(1);
  caption(
    el,
    `Barnard's Star in D2 (${um(2)}), ${eps.length} visits from ${date(eps[0].mjd_mid)} to ${date(eps[eps.length - 1].mjd_mid)}, ${across}′ across.
    The red marks show where it was in visit 1.`,
  );
  if (obj?.measure) el.insertAdjacentHTML("beforeend", measureBlock(obj.measure, { versus: "Gaia" }));
};

// ---- 3: two clocks (an asteroid, the interstellar comet) -------------------------------------
async function moverPlate(
  el: HTMLElement,
  host: FigureHost,
  slug: "elpis" | "3i-atlas",
  det: Det,
): Promise<{ set: FrameSet; eps: Epoch[] }> {
  const set = await frameSet(slug, "hours", 20, det, host.signal);
  const pool = moverExposures(set, det, 8);
  const loaded = await Promise.all(pool.map((e) => epochValues(set, e, host.signal)));
  const ok = pool.map((e, i) => ({ e, v: loaded[i] })).filter((x) => noDataShare(x.v) < 0.2);
  const pick = ok.length <= 4 ? ok : Array.from({ length: 4 }, (_, i) => ok[Math.round((i * (ok.length - 1)) / 3)]);
  const eps = pick.map((x) => x.e);
  const pts = eps.map((e) => toPixel(set, e, knownIn(set, e)!));
  const crop = cropAround(pts, set.frames.size, slug === "elpis" ? 26 : 44, 84);
  const t0 = eps[0];
  const box = plateBox(el);
  host.plates.push(
    mountPlate(box, {
      nx: set.frames.size[0],
      ny: set.frames.size[1],
      frames: pick.map(({ e, v }, i) => ({
        values: v,
        label: `Photo ${i + 1} of ${pick.length}, ${formatMjd(e.mjd_mid, true)}`,
        step: i === 0 ? "0 h" : `+${hours(t0, e).toFixed(1)} h`,
        marks: [{ kind: "known", x: pts[i].x, y: pts[i].y, r: 4.5, label: "Predicted position" }],
      })),
      crop,
      pixelArcsec: set.frames.pixel_arcsec,
      description:
        slug === "elpis"
          ? "The asteroid Elpis crossing a field of stars in four photos, inside a violet diamond."
          : "Comet 3I/ATLAS, a fuzzy dot with a tail, staying in the middle while the stars stream past.",
      contrast: 0.3,
    }),
  );
  return { set, eps };
}

const clocks: Builder = async (el, host) => {
  el.insertAdjacentHTML(
    "beforeend",
    `<div class="seg ln-fig-seg" role="group" aria-label="Example">
      <button type="button" data-k="elpis" aria-pressed="true">An asteroid</button>
      <button type="button" data-k="3i-atlas" aria-pressed="false">A comet</button>
    </div>`,
  );
  const stage = plateBox(el, "ln-stage");
  let ctl: AbortController | null = null;
  let mine: PlateHandle[] = [];

  async function show(k: "elpis" | "3i-atlas") {
    ctl?.abort();
    ctl = new AbortController();
    const sig = ctl.signal;
    host.signal.addEventListener("abort", () => ctl?.abort(), { once: true });
    mine.forEach((p) => p.destroy());
    host.plates.splice(0, host.plates.length, ...host.plates.filter((p) => !mine.includes(p)));
    mine = [];
    el.querySelectorAll<HTMLButtonElement>(".ln-fig-seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.k === k)));
    stage.innerHTML = `<div class="ln-loading" role="status">Loading SPHEREx photos…</div>`;
    const sub: FigureHost = { signal: sig, plates: [], cleanups: [] };
    try {
      const inner = document.createElement("div");
      const det: Det = k === "elpis" ? 2 : 3;
      const { set, eps } = await moverPlate(inner, sub, k, det);
      if (sig.aborted) return sub.plates.forEach((p) => p.destroy());
      stage.innerHTML = "";
      stage.appendChild(inner);
      mine = sub.plates;
      host.plates.push(...mine);
      const a = eps[0];
      const b = eps[eps.length - 1];
      const rate = rateArcsecPerHour(set, a, b);
      const ka = knownIn(set, a)!;
      const kb = knownIn(set, b)!;
      const moved = sepArcsec(ka, kb) / 60;
      const span = hours(a, b).toFixed(1);
      caption(
        inner,
        k === "elpis"
          ? `(59) Elpis, a main-belt asteroid, in ${eps.length} photos (D${det}, ${um(det)}) over ${span} hours on ${date(a.mjd_mid)}.
            The violet diamond is where its known orbit puts it; the dot inside is the asteroid. It crossed ${moved.toFixed(1)}′ of sky,
            about ${Math.round(rate ?? 0)}″ an hour, while the stars stayed put.`
          : `3I/ATLAS, the third object known to come from another star system, in ${eps.length} photos (D${det}, ${um(det)}) over ${span} hours on ${date(
              a.mjd_mid,
            )}. These frames follow the comet, so it stays near the middle while the stars stream past.
            It moved ${moved.toFixed(1)}′, about ${Math.round(rate ?? 0)}″ an hour.`,
      );
    } catch (e) {
      if (sig.aborted) return;
      stage.innerHTML = failure(k === "elpis" ? "elpis" : "3i-atlas");
    }
  }
  el.querySelectorAll<HTMLButtonElement>(".ln-fig-seg button").forEach((b) =>
    b.addEventListener("click", () => show(b.dataset.k as "elpis" | "3i-atlas")),
  );
  await show("elpis");
};

// ---- 4: fakes --------------------------------------------------------------------------------
/** Four D2 photos of Barnard's Star's field that hold an edge, one-photo blobs and burnt cores. */
export const FAKE_IDS = ["d2-2026W34_1A_0363_1", "d2-2026W34_1A_0456_2", "d2-2026W34_2A_0206_1", "d2-2026W35_2A_0126_1"];

const fakes: Builder = async (el, host) => {
  const set = await frameSet("barnard", "hours", 20, 2, host.signal);
  const all = epochsFor(set.frames, 2);
  let eps = FAKE_IDS.map((id) => all.find((e) => e.id === id)).filter((e): e is Epoch => !!e);
  if (eps.length < 2) eps = all.slice(0, 4);
  const vals = await Promise.all(eps.map((e) => epochValues(set, e, host.signal)));
  const [nx, ny] = set.frames.size;
  const spots = vals.map((v, i) => classifyFakes(v, vals.filter((_, j) => j !== i), nx, ny, 5));
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "btn ln-fig-toggle";
  toggle.setAttribute("aria-pressed", "false");
  toggle.textContent = "Circle the fakes";
  const box = plateBox(el);
  const plate = mountPlate(box, {
    nx,
    ny,
    frames: eps.map((e, i) => ({
      values: vals[i],
      label: `Photo ${i + 1} of ${eps.length}, ${formatMjd(e.mjd_mid, true)}`,
      step: `Photo ${i + 1}`,
      marks: spots[i].oneOff.map((b) => ({ kind: "flag" as const, x: b.x, y: b.y, r: Math.max(3, Math.sqrt(b.area / Math.PI) + 2.5) })),
    })),
    pixelArcsec: set.frames.pixel_arcsec,
    description: "Four photos of the same field. Hatched patches come and go: the photo's edge and pixels flagged in one photo only.",
    contrast: 0.3,
  });
  plate.setMarks(false);
  host.plates.push(plate);
  box.querySelector(".lp-strip")?.appendChild(toggle);
  toggle.addEventListener("click", () => {
    const on = toggle.getAttribute("aria-pressed") !== "true";
    toggle.setAttribute("aria-pressed", String(on));
    plate.setMarks(on);
  });
  const oneOff = spots.reduce((n, s) => n + s.oneOff.length, 0);
  const edges = spots.filter((s) => s.edges.length).length;
  caption(
    el,
    `${eps.length} photos of Barnard's Star's field (D2, ${um(2)}), ${Math.round((nx * set.frames.pixel_arcsec) / 60)}′ across, from one visit.
    Hatched means no data. ${edges} of the ${eps.length} photos run off the detector's edge, and ${oneOff} spots are blank in one photo only:
    pixels NASA flagged, such as a cosmic ray or an afterimage. Press <em>Circle the fakes</em> to mark them.
    Barnard's Star's own centre is blank in every photo: it overflows.`,
  );
};

// ---- 5: colours ------------------------------------------------------------------------------
const colours: Builder = async (el, host) => {
  const w = target("wise0855")!;
  const e = target("epsindib")!;
  const [set, eps] = await Promise.all([
    frameSet("wise0855", "months", 5, 2, host.signal),
    loadObject({ ra: e.ra, dec: e.dec, slug: e.slug }, host.signal).catch(() => null),
  ]);
  const e2 = epochsFor(set.frames, 2)[0];
  const e5 = epochsFor(set.frames, 5).find((x) => Math.abs(x.mjd_mid - e2.mjd_mid) < 0.01) ?? epochsFor(set.frames, 5)[0];
  const [v2, v5] = await Promise.all([epochValues(set, e2, host.signal), epochValues(set, e5, host.signal)]);
  const crop = centreCrop(set.frames.size, 56);
  const [nx, ny] = set.frames.size;
  const centre: PlateMark = { kind: "reticle", x: (nx - 1) / 2, y: (ny - 1) / 2, r: 6, label: "WISE 0855" };
  const pair = plateBox(el, "ln-pair");
  for (const [ep, v] of [
    [e2, v2],
    [e5, v5],
  ] as const) {
    const cell = plateBox(pair, `ln-pair-cell det-${ep.det}`);
    const box = plateBox(cell);
    const rings: PlateMark[] = gaiaInCrop(set, ep, crop, 18.5).map((s) => ({ kind: "gaia", x: s.x, y: s.y, r: 3.4 }));
    host.plates.push(
      mountPlate(box, {
        nx,
        ny,
        frames: [{ values: v, label: "", step: "" }],
        crop,
        marks: [...rings, centre],
        compact: true,
        pixelArcsec: set.frames.pixel_arcsec,
        description:
          ep.det === 2
            ? "Detector D2: only a blue-ringed star near the red marks."
            : "Detector D5: a second dot appears just left of the blue-ringed star, between the red marks.",
        contrast: 0.35,
      }),
    );
    const lab = document.createElement("p");
    lab.className = "ln-six-label";
    lab.innerHTML = `<strong>D${ep.det}</strong> ${um(ep.det)}`;
    cell.appendChild(lab);
  }
  caption(
    el,
    `${esc(w.name)}, the coldest brown dwarf known, on ${date(e2.mjd_mid)}: D2 and D5 photograph this sky at the same moment,
    ${((crop.w * set.frames.pixel_arcsec) / 60).toFixed(1)}′ across. At ${um(2)} there is only a star Gaia knows (blue ring).
    At ${um(5)} a second dot sits just east of it, between the red marks: that is WISE 0855, invisible at short wavelengths.`,
  );

  if (eps?.spectrum?.length) {
    const spec = plateBox(el, "ln-spec");
    const pc = eps.gaia?.parallax ? 1000 / eps.gaia.parallax : null;
    const width = Math.min(640, host.width || 560);
    spec.innerHTML = lessonSpectrumSvg(eps.spectrum, { width, id: "ln-spec-epsindib", name: "Epsilon Indi B" });
    const visits = new Set(eps.spectrum.map((p) => p.visit)).size;
    caption(
      spec,
      `${esc(e.name)}, a pair of brown dwarfs${pc ? ` ${pc.toFixed(1)} pc away` : ""}, as SPHEREx measured it: ${eps.spectrum.length} points from
      0.75 to 5.0 µm over ${visits} visits (dots in detector colours, ink line the median). It is faint at the short end and brightest near 4 µm.
      The dips are water and methane in their air; past 4.4 µm carbon monoxide takes another bite.`,
    );
  }
};

// ---- 6: votes --------------------------------------------------------------------------------
const votes: Builder = async (el, host) => {
  const demo = plateBox(el, "ln-votes-wrap");
  const d = mountVoteDemo(demo);
  host.cleanups.push(() => d.destroy());
  const [val, sus] = await Promise.all([loadValidation(host.signal), loadSuspects(host.signal)]);
  const rows: string[] = [];
  const v4 = val?.checks.find((c) => c.id === "V4");
  const v5 = val?.checks.find((c) => c.id === "V5");
  if (sus) rows.push(`<div><dt>Proposed</dt><dd>${sus.suspects.length.toLocaleString("en-GB")} suspects from the ${esc(sus.zone)} zone (${esc(sus.data_release)}).</dd></div>`);
  if (v4) rows.push(`<div><dt>Known asteroids, targeted</dt><dd>${esc(v4.headline)}.</dd></div>`);
  if (v5) rows.push(`<div><dt>Known asteroids, blind</dt><dd>${esc(v5.headline.replace(/^pilot:\s*/i, ""))}.</dd></div>`);
  if (rows.length)
    el.insertAdjacentHTML(
      "beforeend",
      `<div class="ln-proof"><h3>Why the pipeline's suggestions are worth your time</h3><dl>${rows.join("")}</dl>
       <p><a href="#/results">All the checks on the Results page</a></p></div>`,
    );
};

// ---- 7: Planet X -----------------------------------------------------------------------------
const planetx: Builder = async (el, host) => {
  const w = target("wise0855")!;
  const [set, obj] = await Promise.all([
    frameSet("wise0855", "months", 5, 5, host.signal),
    loadObject({ ra: w.ra, dec: w.dec, slug: w.slug }, host.signal).catch(() => null),
  ]);
  const eps = epochsFor(set.frames, 5);
  const vals = await Promise.all(eps.map((e) => epochValues(set, e, host.signal)));
  const crop = centreCrop(set.frames.size, 64);
  const box = plateBox(el);
  host.plates.push(
    mountPlate(box, {
      nx: set.frames.size[0],
      ny: set.frames.size[1],
      frames: eps.map((e, i) => ({
        values: vals[i],
        label: `Visit ${i + 1} of ${eps.length}, ${date(e.mjd_mid)}`,
        step: `Visit ${i + 1}`,
        marks: gaiaInCrop(set, e, crop, 18.5).map((s) => ({ kind: "gaia" as const, x: s.x, y: s.y, r: 3.4 })),
      })),
      crop,
      pixelArcsec: set.frames.pixel_arcsec,
      description: "WISE 0855 in detector D5 at three visits: a dot without a blue ring walks right, west, onto a ringed star.",
      contrast: 0.35,
    }),
  );
  caption(
    el,
    `${esc(w.name)} in D5 (${um(5)}), ${eps.length} visits from ${date(eps[0].mjd_mid)} to ${date(eps[eps.length - 1].mjd_mid)},
    ${((crop.w * set.frames.pixel_arcsec) / 60).toFixed(1)}′ across. Blue rings are stars Gaia knows; the dot without a ring is the brown dwarf,
    walking west (right) until it passes in front of a background star.`,
  );
  if (obj?.measure) el.insertAdjacentHTML("beforeend", measureBlock(obj.measure, { versus: null, expectedText: `${esc(w.expected)}` }));
};

export const FIGURES: Record<FigureId, { builder: Builder; slug: string | null; what: string }> = {
  six: { builder: six, slug: "barnard", what: "Barnard's Star in six detectors" },
  barnard: { builder: barnard, slug: "barnard", what: "Barnard's Star across three visits" },
  clocks: { builder: clocks, slug: "elpis", what: "an asteroid and a comet on the Hours clock" },
  fakes: { builder: fakes, slug: "barnard", what: "four photos with artefacts" },
  colours: { builder: colours, slug: "wise0855", what: "WISE 0855 in two detectors" },
  votes: { builder: votes, slug: null, what: "the consensus rule" },
  planetx: { builder: planetx, slug: "wise0855", what: "WISE 0855 across three visits" },
};

function failure(slug: string | null): string {
  return `<p class="ln-fail" role="status">This example didn't load. ${
    slug ? `<a href="${exploreHref(slug)}">Open it on the Blink page</a> instead.` : "Reload the page to try again."
  }</p>`;
}

/** Build a lesson's figure into el. Resolves when it is drawn (or has shown why it couldn't be). */
export async function buildFigure(id: FigureId, el: HTMLElement, host: FigureHost): Promise<void> {
  const f = FIGURES[id];
  el.innerHTML = `<div class="ln-loading" role="status">Loading ${esc(f.what)}…</div>`;
  const inner = document.createElement("div");
  inner.className = "ln-fig-inner";
  host.width = el.clientWidth || host.width;
  try {
    await f.builder(inner, host);
    if (host.signal.aborted) return;
    el.innerHTML = "";
    el.appendChild(inner);
  } catch (e) {
    if (host.signal.aborted) return;
    console.warn("learn: figure failed", id, e);
    el.innerHTML = failure(f.slug);
  }
}
