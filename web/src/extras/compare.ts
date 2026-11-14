// "Compare short and long wavelengths": the same 5′ field in D3 (1.6–2.4 µm) and D6 (4.4–5.0 µm),
// side by side for a visit both cover. A cold-source check: a cold brown dwarf or planet is bright
// in D6 and faint in D3, an ordinary star the other way round. D3 and D6 are exposed at the same
// moment (CLAUDE.md), so a difference between them is colour, never motion.
// Reworked from PR #214 (春。 and Codex): web/src/product/comparison.ts and observations.ts.
import { ApiFailure, epochsFor, loadFrameData, loadFrames, toMJySr } from "../api/client";
import type { Epoch, LoadedFrames, SpectrumPoint } from "../api/types";
import { DETECTORS, formatMjd } from "../app/astro";
import { targetBySlug } from "../app/state";
import { burntCores, median, offset, robustLimits, robustSigma } from "../blink/math";
import { buildLut, paint, plainIndex, readPalette } from "../blink/paint";
import { type Ctx, esc, isAbort, sectionShell, umRange } from "./shared";

export interface BandPair {
  short: Epoch;
  long: Epoch;
}

/** D3 and D6 epochs whose time spans overlap, each D6 epoch used once, in time order. */
export function bandPairs(short: Epoch[], long: Epoch[]): BandPair[] {
  const unused = new Set(long);
  const pairs: BandPair[] = [];
  for (const a of [...short].sort((x, y) => x.mjd_mid - y.mjd_mid)) {
    const b = [...unused]
      .filter((b) => Math.max(a.mjd_start, b.mjd_start) <= Math.min(a.mjd_end, b.mjd_end))
      .sort((x, y) => Math.abs(x.mjd_mid - a.mjd_mid) - Math.abs(y.mjd_mid - a.mjd_mid))[0];
    if (!b) continue;
    unused.delete(b);
    pairs.push({ short: a, long: b });
  }
  return pairs;
}

/** The pair to show first: the deepest (most exposures in both), the latest on a tie. */
export function bestPair(pairs: BandPair[]): number {
  let best = -1;
  let depth = -1;
  pairs.forEach((p, i) => {
    const d = Math.min(p.short.n_exposures, p.long.n_exposures);
    if (d >= depth) {
      depth = d;
      best = i;
    }
  });
  return best;
}

/** One detector's brightness from the object's spectrum: a stack of its unsaturated points. */
export interface BandFlux {
  /** Median flux, µJy. */
  flux: number;
  /** 1σ of that median, µJy (1.2533 × median error / √n). */
  err: number;
  n: number;
  detected: boolean;
}

export type ColourRead =
  | { kind: "ratio"; d3: BandFlux; d6: BandFlux; ratio: number }
  | { kind: "d6only"; d3: BandFlux; d6: BandFlux; atLeast: number }
  | { kind: "d3only"; d3: BandFlux; d6: BandFlux; atMost: number }
  | { kind: "none"; why: "saturated" | "missing" | "faint" };

const med = (v: number[]): number => {
  const s = [...v].sort((a, b) => a - b);
  const n = s.length;
  return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : NaN;
};

export function bandFlux(spectrum: SpectrumPoint[], det: 3 | 6): BandFlux | "saturated" | null {
  const all = spectrum.filter((p) => p.det === det && Number.isFinite(p.flux_ujy));
  if (!all.length) return null;
  const ok = all.filter((p) => !p.saturated && p.err_ujy > 0 && Number.isFinite(p.err_ujy));
  if (ok.length < Math.max(3, all.length / 2)) return ok.length ? null : "saturated";
  const flux = med(ok.map((p) => p.flux_ujy));
  const err = (1.2533 * med(ok.map((p) => p.err_ujy))) / Math.sqrt(ok.length);
  return { flux, err, n: ok.length, detected: flux > 0 && flux / err >= 5 };
}

/** D6 over D3 from the object's spectrum, or a limit when one band shows nothing. */
export function colourRead(spectrum: SpectrumPoint[]): ColourRead {
  const d3 = bandFlux(spectrum, 3);
  const d6 = bandFlux(spectrum, 6);
  if (d3 === "saturated" || d6 === "saturated") return { kind: "none", why: "saturated" };
  if (!d3 || !d6) return { kind: "none", why: "missing" };
  const limit = (b: BandFlux) => Math.max(b.flux, 0) + 3 * b.err;
  if (d3.detected && d6.detected) return { kind: "ratio", d3, d6, ratio: d6.flux / d3.flux };
  if (d6.detected) return { kind: "d6only", d3, d6, atLeast: d6.flux / limit(d3) };
  if (d3.detected) return { kind: "d3only", d3, d6, atMost: limit(d6) / d3.flux };
  return { kind: "none", why: "faint" };
}

const SCALE = "An ordinary star gives about 0.25, a T-type brown dwarf about 1 to 5, and the coldest (Y-type) brown dwarfs more than 10.";

/** The colour line under the intro, in words with numbers; null when the spectrum can't say. */
export function colourSentence(c: ColourRead): string | null {
  const ujy = (v: number) => `${Math.round(v).toLocaleString("en-US")} µJy`;
  switch (c.kind) {
    case "ratio":
      return `In this object's spectrum D6 is ${c.ratio.toFixed(c.ratio < 10 ? 2 : 0)} times as bright as D3 (median ${ujy(c.d6.flux)} against ${ujy(c.d3.flux)}, unsaturated points only). ${SCALE}`;
    case "d6only":
      return `In this object's spectrum D6 has ${ujy(c.d6.flux)} and D3 nothing above 3σ (under ${ujy(Math.max(c.d3.flux, 0) + 3 * c.d3.err)}): D6 is at least ${c.atLeast.toFixed(0)} times as bright. ${SCALE}`;
    case "d3only":
      return `In this object's spectrum D3 has ${ujy(c.d3.flux)} and D6 nothing above 3σ: D6 is at most ${c.atMost.toFixed(2)} times as bright, as for an ordinary star.`;
    default:
      return c.why === "saturated" ? "Its core is saturated in D3 or D6, so the spectrum can't give a colour: compare the images instead." : null;
  }
}

interface Shown {
  pairs: BandPair[];
  short: LoadedFrames;
  long: LoadedFrames;
}

export function mountCompare(host: HTMLElement) {
  host.insertAdjacentHTML(
    "beforeend",
    sectionShell(
      "compare",
      "Compare short and long wavelengths",
      "D3 (1.6–2.4 µm) beside D6 (4.4–5.0 µm): a cold brown dwarf or planet is bright in D6 and faint in D3.",
    ),
  );
  const details = host.querySelector<HTMLDetailsElement>('[data-sec="compare"]')!;
  const body = details.querySelector<HTMLElement>("[data-body]")!;
  let ctx: Ctx | null = null;
  let ctl: AbortController | null = null;
  let loaded: string | null = null;
  let data: Shown | null = null;
  let pick = 0;
  let drawId = 0;

  const intro =
    `<p class="ex-p">SPHEREx takes D3 and D6 at the same moment, so a dot that differs between them shows its colour, not motion. ` +
    `Ordinary stars are brighter in D3; a cold brown dwarf or planet is bright in D6 and faint or missing in D3.</p>`;

  function colourHtml(): string {
    const said = ctx?.object ? colourSentence(colourRead(ctx.object.spectrum)) : null;
    return said ? `<p class="ex-p ex-colour" data-colour>${esc(said)}</p>` : `<p class="ex-p ex-colour" data-colour hidden></p>`;
  }

  function say(text: string, retry = false) {
    body.innerHTML = `${intro}${colourHtml()}<p class="ex-status" role="status">${esc(text)}</p>${retry ? `<button type="button" class="btn btn-ghost ex-retry">Try again</button>` : ""}`;
    body.querySelector(".ex-retry")?.addEventListener("click", () => {
      loaded = null;
      void load();
    });
  }

  function figure(det: 3 | 6): string {
    const r = DETECTORS[det];
    return `<figure class="ex-fig" data-det="${det}">
      <figcaption class="ex-fig-h"><span class="ex-det-dot" style="--det: var(--d${det})" aria-hidden="true"></span>D${det}, ${umRange(det)} µm</figcaption>
      <div class="ex-plate"><canvas width="1" height="1" role="img" aria-label="D${det} visit stack, ${r.min} to ${r.max} µm"></canvas>
        <svg class="ex-mark" aria-hidden="true"></svg></div>
      <p class="ex-fig-cap" data-cap></p>
    </figure>`;
  }

  function render() {
    if (!data) return;
    const { pairs } = data;
    const options = pairs
      .map((p, i) => {
        const n = Math.min(p.short.n_exposures, p.long.n_exposures);
        return `<option value="${i}" ${i === pick ? "selected" : ""}>Visit ${i + 1} of ${pairs.length}, ${formatMjd((p.short.mjd_mid + p.long.mjd_mid) / 2)}, ${n} exposure${n === 1 ? "" : "s"}</option>`;
      })
      .join("");
    body.innerHTML = `${intro}${colourHtml()}
      <div class="ex-row">
        <label class="ex-label" for="ex-cmp-visit">Visit</label>
        <select class="input ex-select" id="ex-cmp-visit" ${pairs.length < 2 ? "disabled" : ""}>${options}</select>
      </div>
      <div class="ex-pair">${figure(3)}${figure(6)}</div>
      <p class="ex-note">Visit stacks of the 5′ field, north up and east left. The red mark is the centre of the blink. Each image has its own stretch, so compare a dot with the stars around it, not one image's darkness with the other's.</p>
      <p class="ex-status" role="status" data-draw></p>`;
    body.querySelector<HTMLSelectElement>(".ex-select")!.addEventListener("change", (ev) => {
      pick = Number((ev.currentTarget as HTMLSelectElement).value);
      void draw();
    });
    void draw();
  }

  async function draw() {
    if (!data || !ctl) return;
    const id = ++drawId;
    const signal = ctl.signal;
    const pair = data.pairs[pick];
    const status = body.querySelector<HTMLElement>("[data-draw]");
    if (status) status.textContent = "Loading the pixels…";
    try {
      const [a, b] = await Promise.all([
        loadFrameData(pair.short, data.short.jsonUrl, signal),
        loadFrameData(pair.long, data.long.jsonUrl, signal),
      ]);
      if (signal.aborted || id !== drawId) return;
      paintInto(3, data.short, pair.short, a);
      paintInto(6, data.long, pair.long, b);
      if (status) status.textContent = "";
    } catch (e) {
      if (isAbort(e) || signal.aborted || id !== drawId) return;
      if (status) status.textContent = "The pixels of this visit couldn't be loaded. Pick another visit, or try again in a minute.";
    }
  }

  function paintInto(det: 3 | 6, set: LoadedFrames, epoch: Epoch, raw: Uint16Array) {
    const fig = body.querySelector<HTMLElement>(`.ex-fig[data-det="${det}"]`);
    const canvas = fig?.querySelector("canvas");
    if (!fig || !canvas) return;
    const [nx, ny] = set.frames.size;
    if (raw.length !== nx * ny) return;
    const mjy = toMJySr(raw, epoch);
    const sky = median(mjy);
    const values = offset(mjy, sky);
    const sigma = robustSigma([values]);
    const burnt = burntCores(values, nx, ny, 0, sigma);
    canvas.width = nx;
    canvas.height = ny;
    const g = canvas.getContext("2d");
    if (g) {
      const img = g.createImageData(nx, ny);
      paint(values, img.data, buildLut(readPalette(canvas), false, false), plainIndex(robustLimits([values], 0.45), "asinh"), burnt);
      g.putImageData(img, 0, 0);
    }
    const svg = fig.querySelector("svg")!;
    const cx = (nx - 1) / 2 + 0.5;
    const cy = (ny - 1) / 2 + 0.5;
    const gap = nx * 0.045;
    const len = nx * 0.06;
    svg.setAttribute("viewBox", `0 0 ${nx} ${ny}`);
    svg.innerHTML = `<path d="M${cx + gap} ${cy}h${len}M${cx - gap} ${cy}h${-len}M${cx} ${cy + gap}v${len}M${cx} ${cy - gap}v${-len}" stroke="var(--pencil-red)" stroke-width="${nx / 160}" stroke-linecap="round" opacity="0.85" fill="none"/>`;
    const cap = fig.querySelector<HTMLElement>("[data-cap]")!;
    cap.textContent = `${formatMjd(epoch.mjd_mid)}, ${epoch.n_exposures} exposure${epoch.n_exposures === 1 ? "" : "s"}`;
    canvas.setAttribute(
      "aria-label",
      `D${det} visit stack, ${DETECTORS[det].min} to ${DETECTORS[det].max} µm, ${formatMjd(epoch.mjd_mid)}, drawn as a negative: dark dots are sources.`,
    );
  }

  async function load() {
    if (!ctx || !details.open || loaded === ctx.key) return;
    const at = ctx;
    loaded = at.key;
    ctl?.abort();
    ctl = new AbortController();
    const signal = ctl.signal;
    data = null;
    // Featured months targets have static stacks; anything else is built live by /api/frames.
    const prepared = targetBySlug(at.view.slug)?.clock === "months";
    say(prepared ? "Loading the D3 and D6 visit stacks…" : "Building the D3 and D6 visit stacks from SPHEREx: this can take up to a minute.");
    try {
      const q = { ra: at.view.ra, dec: at.view.dec, slug: at.view.slug, clock: "months" as const, fov: 5 };
      const short = await loadFrames({ ...q, det: 3 }, signal);
      const long = short.frames.epochs.some((e) => e.det === 6) ? short : await loadFrames({ ...q, det: 6 }, signal);
      if (signal.aborted || ctx?.key !== at.key) return;
      const pairs = bandPairs(epochsFor(short.frames, 3), epochsFor(long.frames, 6));
      if (!pairs.length) return say("D3 and D6 share no visit here, so there is nothing to compare.");
      data = { pairs, short, long };
      pick = bestPair(pairs);
      render();
    } catch (e) {
      if (isAbort(e) || signal.aborted) return;
      loaded = null;
      const text =
        e instanceof ApiFailure && e.code === "not_found"
          ? "SPHEREx has no D3 or D6 frames of this spot yet."
          : e instanceof ApiFailure && e.code === "upstream"
            ? "NASA's archive didn't answer, so the D3 and D6 stacks couldn't be built. Nothing is wrong with the spot: try again in a minute."
            : e instanceof ApiFailure
              ? `SkyShift's frame service failed (${e.status}). Try again in a minute.`
              : "SkyShift's frame service can't be reached. Check your connection, then try again.";
      say(text, true);
    }
  }

  details.addEventListener("toggle", () => void load());

  return {
    /** A new target: forget everything at once (the stale field must never show under a new name). */
    clear() {
      ctx = null;
      ctl?.abort();
      ctl = null;
      loaded = null;
      data = null;
      drawId++;
      body.replaceChildren();
    },
    show(next: Ctx) {
      const same = ctx?.key === next.key;
      if (!same) this.clear();
      ctx = next;
      // The object may arrive after the frames: refresh the colour line in place.
      const line = body.querySelector<HTMLElement>("[data-colour]");
      if (line && next.object) {
        const said = colourSentence(colourRead(next.object.spectrum));
        line.hidden = !said;
        line.textContent = said ?? "";
      }
      void load();
    },
  };
}
