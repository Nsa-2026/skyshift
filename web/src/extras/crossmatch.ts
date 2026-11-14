// "Known objects and long-term observations": catalogues around the spot, asked only when the
// section is opened. The five fields with science products ask /api/enrichment (contracts/api.md,
// "Science workspace": CatWISE2020 and NEOWISE from NASA/IPAC IRSA, Gaia DR3 co-movers from ESA,
// known asteroids from Minor Planet Center orbits with kete); every spot also reads the Gaia stars
// and known objects already in its frames. A service that fails says which one and what to do; it
// never reads as "nothing there". Reworked from PR #214 (春。 and Codex), web/src/card/science.ts.
import { getJson } from "../api/client";
import { hasScience } from "../api/science-client";
import type { FramesResponse, GaiaStar } from "../api/types";
import { formatMjd, gaiaAt, mjdToDate, sepArcsec } from "../app/astro";
import { type Ctx, andList, esc, failText, framesMjd, grouped, isAbort, plural, sectionShell } from "./shared";

// ---- answers (contracts/api.md, GET /api/enrichment) ------------------------------------------

export type Kind = "asteroids" | "comovers" | "catwise" | "relay";
export interface AsteroidsAnswer {
  source: string;
  objects: { name: string; ra: number; dec: number; v: number | null; visit: number; mjd: number }[];
  errors: { visit: number; mjd: number; error: string }[];
  note?: string;
}
export interface Comover {
  ra: number;
  dec: number;
  parallax: number | null;
  pmra: number | null;
  pmdec: number | null;
  phot_g_mean_mag: number | null;
  pm_chi: number | null;
  sep_arcsec: number | null;
  distance_pc: number | null;
  projected_sep_au: number | null;
}
export interface ComoversAnswer {
  source: string;
  candidates: Comover[];
  luminosity?: { L_sun: number; note?: string } | null;
  note?: string;
}
export interface CatwiseRow {
  name: string;
  ra: number | null;
  dec: number | null;
  w1mpro: number | null;
  w2mpro: number | null;
  /** arcsec/yr */
  pmra: number | null;
  pmdec: number | null;
  candidate?: number;
  separation_arcsec?: number;
  pm_difference_arcsec_yr?: number;
}
export interface CatwiseAnswer {
  source: string;
  sources: CatwiseRow[];
  note?: string;
}
export interface RelayPoint {
  mjd: number;
  W1?: number | null;
  W1_err?: number | null;
  W2?: number | null;
  W2_err?: number | null;
  n?: number;
  source: string;
}
export interface RelayAnswer {
  source: string;
  points: RelayPoint[];
  error: string | null;
}

/** Each service by name, as errors say it. */
export const SERVICE: Record<Kind, string> = {
  asteroids: "SkyShift's index of Minor Planet Center orbits",
  comovers: "The ESA Gaia archive",
  catwise: "NASA/IPAC IRSA's CatWISE2020 catalogue",
  relay: "NASA/IPAC IRSA's NEOWISE archive",
};

const num = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const fix = (x: number, d: number) => x.toFixed(d).replace("-", "−");

// ---- sentences (pure, tested) ----------------------------------------------------------------

/** Known asteroids and comets from /api/enrichment?kind=asteroids, one line per object. */
export function asteroidsHtml(a: AsteroidsAnswer, centre: { ra: number; dec: number }): string {
  const byName = new Map<string, AsteroidsAnswer["objects"]>();
  for (const o of [...a.objects].sort((x, y) => x.mjd - y.mjd)) byName.set(o.name, [...(byName.get(o.name) ?? []), o]);
  const incomplete = a.errors?.length
    ? `<p class="ex-p ex-warn">The orbit index doesn't cover ${plural(a.errors.length, "observing session")} here, so this list is incomplete.</p>`
    : "";
  const note = a.note ? `<p class="ex-note">${esc(a.note)}.</p>` : "";
  if (!byName.size)
    return `<p class="ex-p">No known asteroid or comet brighter than V 21 was in this field during SPHEREx's visits.</p>${incomplete}${note}`;
  const items = [...byName.entries()].map(([name, seen]) => {
    const v = seen.map((s) => s.v).find(num);
    const near = Math.min(...seen.map((s) => sepArcsec(s, centre)));
    const days = andList([...new Set(seen.map((s) => `${formatMjd(s.mjd)} (visit ${s.visit})`))]);
    return `<li><b>${esc(name)}</b>${v != null ? `, V ${v.toFixed(1)}` : ""}: ${grouped(near)}″ from the centre at its closest, on ${esc(days)}</li>`;
  });
  return `<p class="ex-p">${plural(byName.size, "known asteroid")} passed through this field during SPHEREx's visits:</p>
    <ul class="ex-list">${items.join("")}</ul>${incomplete}${note}`;
}

/** The known objects already in the frames (any spot): what the plate's violet diamonds are. */
export function knownInFramesHtml(frames: FramesResponse | null): string {
  if (!frames) return `<p class="ex-p ex-status">This fills in once the plate's frames have loaded.</p>`;
  const known = frames.known ?? [];
  const what = frames.clock === "hours" ? "exposure" : "visit";
  if (!known.length)
    return `<p class="ex-p">No known asteroid or comet is predicted in these frames, checked at the time of each ${what} on the plate.</p>`;
  const byName = new Map<string, number[]>();
  for (const k of known) {
    const t = frames.epochs.find((e) => e.id === k.epoch_id)?.mjd_mid;
    byName.set(k.name, [...(byName.get(k.name) ?? []), ...(t != null ? [t] : [])]);
  }
  const items = [...byName.entries()].map(([name, ts]) => {
    const v = known.find((k) => k.name === name && num(k.v))?.v;
    const when = ts.length ? `, in ${plural(new Set(ts).size, what)} from ${formatMjd(Math.min(...ts))}` : "";
    return `<li><b>${esc(name)}</b>${v != null ? `, V ${v.toFixed(1)}` : ""}${when}</li>`;
  });
  return `<p class="ex-p">Predicted in these frames (the violet diamonds on the plate):</p><ul class="ex-list">${items.join("")}</ul>`;
}

/** The Gaia DR3 star nearest the centre at the frames' time, with its motion and distance. */
export function gaiaInFramesHtml(frames: FramesResponse | null, centre: { ra: number; dec: number }): string {
  if (!frames) return `<p class="ex-p ex-status">This fills in once the plate's frames have loaded.</p>`;
  const stars = frames.gaia ?? [];
  if (!stars.length) return `<p class="ex-p">No Gaia DR3 star in this field.</p>`;
  const t = framesMjd(frames) ?? 61000;
  let best: { s: GaiaStar; sep: number } | null = null;
  for (const s of stars) {
    const sep = sepArcsec(gaiaAt(s, t), centre);
    if (!best || sep < best.sep) best = { s, sep };
  }
  const s = best!.s;
  const pm = num(s.pmra) && num(s.pmdec) ? Math.hypot(s.pmra, s.pmdec) / 1000 : null;
  const dist = num(s.parallax) && s.parallax > 0 ? 1000 / s.parallax : null;
  const bits = [
    num(s.g) ? `G ${s.g.toFixed(1)}` : null,
    pm != null ? `moving ${pm.toFixed(pm < 1 ? 3 : 2)}″/yr` : null,
    dist != null && dist < 2000 ? `${dist.toFixed(dist < 10 ? 2 : 0)} pc away` : null,
  ].filter(Boolean);
  return `<p class="ex-p">${plural(stars.length, "Gaia DR3 star")} in this field (the blue rings on the plate). The nearest to the centre, moved to ${formatMjd(t)}, is ${best!.sep.toFixed(1)}″ away${bits.length ? `: ${andList(bits as string[])}` : ""}<span class="pro-only">, source ${esc(s.id)}</span>.</p>`;
}

/** Gaia position (J2016.0, mas/yr) moved to an MJD, for a co-mover row. */
function comoverAt(c: Comover, mjd: number) {
  return gaiaAt({ id: "", ra: c.ra, dec: c.dec, pmra: c.pmra, pmdec: c.pmdec, parallax: c.parallax, g: c.phot_g_mean_mag }, mjd);
}

/** Within this of the centre (after its motion), a co-mover is the target itself. */
export const SELF_ARCSEC = 10;

/** Gaia DR3 stars that share the target's motion, with the target itself picked out. */
export function comoversHtml(a: ComoversAnswer, centre: { ra: number; dec: number }, mjd: number, name: string): string {
  if (a.note && !a.candidates.length)
    return `<p class="ex-p">No search for stars that move with it: SkyShift measured no clear motion for this target.</p>`;
  const rows = a.candidates.map((c) => ({ c, self: sepArcsec(comoverAt(c, mjd), centre) < SELF_ARCSEC }));
  const self = rows.find((r) => r.self)?.c ?? null;
  const others = rows.filter((r) => !r.self).map((r) => r.c);
  const out: string[] = [];
  // The target itself is the star the line above names; here only its companions count.
  if (!others.length) out.push(`<p class="ex-p">No ${self ? "other " : ""}Gaia DR3 star nearby moves with ${esc(name)}.</p>`);
  else {
    const items = others.map((c) => {
      const bits = [
        num(c.phot_g_mean_mag) ? `G ${c.phot_g_mean_mag.toFixed(1)}` : null,
        num(c.sep_arcsec) ? `${grouped(c.sep_arcsec)}″ away` : null,
        num(c.projected_sep_au) && num(c.distance_pc) ? `${grouped(c.projected_sep_au)} au apart at ${c.distance_pc.toFixed(2)} pc` : null,
        num(c.pm_chi) ? `motion agrees within ${c.pm_chi.toFixed(1)}σ` : null,
      ].filter(Boolean);
      return `<li>${esc(bits.join(", "))}</li>`;
    });
    out.push(`<p class="ex-p">${plural(others.length, "Gaia DR3 star")} nearby ${others.length === 1 ? "moves" : "move"} with it, a possible wide companion:</p><ul class="ex-list">${items.join("")}</ul>`);
  }
  const L = a.luminosity?.L_sun;
  if (num(L) && L > 0) {
    const [m, e] = L.toExponential(1).split("e");
    out.push(
      `<p class="ex-p">From its 0.75–5 µm spectrum at the Gaia distance it gives off at least ${m} × 10<sup>${Number(e).toString().replace("-", "−")}</sup> times the Sun's light (a lower limit: SPHEREx sees only part of it).</p>`,
    );
  }
  return out.join("");
}

/** CatWISE2020 sources, nearest the centre first. */
export function catwiseHtml(a: CatwiseAnswer, centre: { ra: number; dec: number }): string {
  const rows = a.sources
    .filter((r) => num(r.ra) && num(r.dec))
    .map((r) => ({ r, sep: sepArcsec({ ra: r.ra!, dec: r.dec! }, centre) }))
    .sort((x, y) => x.sep - y.sep);
  if (!rows.length) return `<p class="ex-p">CatWISE2020 has no source in this field.</p>`;
  const first = rows[0];
  const colour = (r: CatwiseRow) => (num(r.w1mpro) && num(r.w2mpro) ? r.w1mpro - r.w2mpro : null);
  const c0 = colour(first.r);
  const lead =
    `${plural(rows.length, "CatWISE2020 source")} in the field. The nearest is ${first.sep.toFixed(1)}″ from the centre` +
    (num(first.r.w2mpro) ? `, W2 ${first.r.w2mpro.toFixed(2)} mag` : "") +
    (c0 != null ? `, W1 − W2 ${fix(c0, 2)} mag` : "") +
    ". Brown dwarfs are red in W1 − W2: above about 0.5 mag for T types, above 2 for the coldest.";
  const body = rows
    .slice(0, 8)
    .map(({ r, sep }) => {
      const c = colour(r);
      const pm = num(r.pmra) && num(r.pmdec) ? Math.hypot(r.pmra, r.pmdec) : null;
      return `<tr><td>${sep.toFixed(1)}</td><td>${num(r.w1mpro) ? r.w1mpro.toFixed(2) : "–"}</td><td>${num(r.w2mpro) ? r.w2mpro.toFixed(2) : "–"}</td><td>${c != null ? fix(c, 2) : "–"}</td><td>${pm != null ? pm.toFixed(2) : "–"}</td><td class="pro-only">${esc(r.name)}</td></tr>`;
    })
    .join("");
  const matches = rows.filter(({ r }) => num(r.candidate));
  const matchText = matches.length
    ? `<p class="ex-p">${andList(matches.map(({ r }) => `${esc(r.name)} sits on SkyShift's slow mover ${r.candidate}${num(r.pm_difference_arcsec_yr) ? ` (motions differ by ${r.pm_difference_arcsec_yr.toFixed(2)}″/yr)` : ""}`))}.</p>`
    : "";
  return `<p class="ex-p">${lead}</p>
    <div class="ex-table-wrap"><table class="ex-table">
      <caption class="visually-hidden">CatWISE2020 sources nearest the centre</caption>
      <thead><tr><th scope="col">From the centre ″</th><th scope="col">W1 mag</th><th scope="col">W2 mag</th><th scope="col">W1 − W2</th><th scope="col">Motion ″/yr</th><th scope="col" class="pro-only">Name</th></tr></thead>
      <tbody>${body}</tbody></table></div>
    ${rows.length > 8 ? `<p class="ex-note">And ${plural(rows.length - 8, "more source")} further out.</p>` : ""}${matchText}
    <p class="ex-note">CatWISE positions are for 2015.4, so a fast star sits away from where SPHEREx sees it now. A match in position alone is not an identification.</p>`;
}

/** NEOWISE visits (2014–2024) and SPHEREx's synthetic W1 and W2: a ten-year light curve. */
export function relayHtml(a: RelayAnswer, id: string): string {
  const pts = a.points.filter((p) => num(p.mjd) && (num(p.W1) || num(p.W2)));
  if (!pts.length) return `<p class="ex-p">NEOWISE has no clean measurement at this spot.</p>`;
  const neo = pts.filter((p) => p.source.startsWith("NEOWISE"));
  const spx = pts.filter((p) => !p.source.startsWith("NEOWISE"));
  const w2 = (ps: RelayPoint[]) => ps.map((p) => p.W2).filter(num).sort((x, y) => x - y);
  const m = (v: number[]) => (v.length % 2 ? v[v.length >> 1] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2);
  const nw = w2(neo);
  const sw = w2(spx);
  const years = (ps: RelayPoint[]) => ps.map((p) => mjdToDate(p.mjd).getUTCFullYear());
  let lead = neo.length
    ? `NEOWISE measured it in ${plural(neo.length, "visit")} from ${Math.min(...years(neo))} to ${Math.max(...years(neo))}`
    : "NEOWISE has no clean visit here";
  lead += spx.length ? `; SPHEREx adds ${plural(spx.length, "visit")} of synthetic W1 and W2 from its spectrum.` : ".";
  if (nw.length && sw.length) {
    const d = m(sw) - m(nw);
    lead += ` SPHEREx's W2 is ${Math.abs(d).toFixed(2)} mag ${d > 0 ? "fainter" : "brighter"} than NEOWISE's median.`;
  }
  return `<p class="ex-p">${lead}</p>${relayChart(pts, id)}
    <ul class="ex-keys"><li><span class="ex-key-dot" style="--det: var(--d4)"></span>W1 3.4 µm</li><li><span class="ex-key-dot" style="--det: var(--d6)"></span>W2 4.6 µm</li><li><span class="ex-key-shape">●</span>NEOWISE</li><li><span class="ex-key-shape">■</span>SPHEREx, synthetic</li></ul>
    <p class="ex-note">Vega magnitudes, brighter up. SPHEREx's W1 and W2 come from its spectrum through approximate filter curves, so an offset of about 0.1 mag is expected.</p>`;
}

function relayChart(pts: RelayPoint[], id: string): string {
  const W = 560;
  const H = 200;
  const box = { l: 40, r: W - 10, t: 10, b: H - 24 };
  const ts = pts.map((p) => p.mjd);
  const mags = pts.flatMap((p) => [p.W1, p.W2].filter(num));
  const t0 = Math.min(...ts) - 60;
  const t1 = Math.max(...ts) + 60;
  const lo = Math.floor(Math.min(...mags) * 2) / 2 - 0.25;
  const hi = Math.ceil(Math.max(...mags) * 2) / 2 + 0.25;
  const x = (t: number) => box.l + ((t - t0) / (t1 - t0)) * (box.r - box.l);
  const y = (m: number) => box.t + ((m - lo) / (hi - lo)) * (box.b - box.t); // brighter (smaller) up
  const out: string[] = [];
  const step = hi - lo > 4 ? 1 : 0.5;
  for (let m = Math.ceil(lo / step) * step; m <= hi; m += step) {
    out.push(`<line x1="${box.l}" x2="${box.r}" y1="${y(m).toFixed(1)}" y2="${y(m).toFixed(1)}" stroke="var(--hair)"/>`);
    out.push(`<text x="${box.l - 6}" y="${(y(m) + 3.5).toFixed(1)}" class="ex-ax" text-anchor="end">${m.toFixed(step < 1 ? 1 : 0)}</text>`);
  }
  const y0 = mjdToDate(t0).getUTCFullYear() + 1;
  const y1 = mjdToDate(t1).getUTCFullYear();
  const every = y1 - y0 > 6 ? 2 : 1;
  for (let yr = y0; yr <= y1; yr += every) {
    const t = Date.UTC(yr, 0, 1) / 86400000 + 40587;
    out.push(`<text x="${x(t).toFixed(1)}" y="${box.b + 16}" class="ex-ax" text-anchor="middle">${yr}</text>`);
  }
  for (const p of pts) {
    const isNeo = p.source.startsWith("NEOWISE");
    for (const [band, det] of [["W1", 4], ["W2", 6]] as const) {
      const m = p[band];
      if (!num(m)) continue;
      const e = p[`${band}_err`];
      const cx = x(p.mjd);
      const cy = y(m);
      if (num(e) && e > 0) out.push(`<line x1="${cx.toFixed(1)}" x2="${cx.toFixed(1)}" y1="${y(m - e).toFixed(1)}" y2="${y(m + e).toFixed(1)}" stroke="var(--d${det})" opacity="0.5"/>`);
      const tip = `<title>${esc(`${band} ${m.toFixed(2)} mag, ${formatMjd(p.mjd)}, ${isNeo ? "NEOWISE" : "SPHEREx synthetic"}`)}</title>`;
      out.push(
        isNeo
          ? `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="3.2" fill="var(--d${det})" stroke="var(--glass)" stroke-width="0.8">${tip}</circle>`
          : `<rect x="${(cx - 3.4).toFixed(1)}" y="${(cy - 3.4).toFixed(1)}" width="6.8" height="6.8" fill="var(--d${det})" stroke="var(--glass)" stroke-width="0.8">${tip}</rect>`,
      );
    }
  }
  return `<svg class="ex-chart" viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="${id}-t"><title id="${id}-t">W1 and W2 magnitudes against date: NEOWISE visits, then SPHEREx synthetic</title>${out.join("")}</svg>`;
}

// ---- the section --------------------------------------------------------------------------------

const KINDS: Kind[] = ["asteroids", "comovers", "catwise", "relay"];
const WAIT: Record<Kind, string> = {
  asteroids: "Moving every known asteroid and comet to each observing session here (Minor Planet Center orbits): this can take up to a minute.",
  comovers: "Asking the ESA Gaia archive for stars that move with it…",
  catwise: "Asking NASA/IPAC IRSA for CatWISE2020 sources…",
  relay: "Asking NASA/IPAC IRSA for NEOWISE visits…",
};

let uid = 0;

export function mountCrossmatch(host: HTMLElement) {
  const id = `exx${++uid}`;
  host.insertAdjacentHTML(
    "beforeend",
    sectionShell(
      "known",
      "Known objects and long-term observations",
      "Known asteroids, Gaia DR3, CatWISE2020 and ten years of NEOWISE around this spot, asked when you open it.",
    ),
  );
  const details = host.querySelector<HTMLDetailsElement>('[data-sec="known"]')!;
  const body = details.querySelector<HTMLElement>("[data-body]")!;
  let ctx: Ctx | null = null;
  let built: string | null = null;
  let ctl: AbortController | null = null;
  const answers = new Map<Kind, unknown>();

  const sub = (kind: string, title: string) =>
    `<section class="ex-sub" data-kind="${kind}" aria-labelledby="${id}-${kind}"><h3 class="ex-h" id="${id}-${kind}">${title}</h3><div data-out></div></section>`;

  function out(kind: string): HTMLElement | null {
    return body.querySelector<HTMLElement>(`[data-kind="${kind}"] [data-out]`);
  }

  function build(at: Ctx) {
    const published = hasScience(at.view.slug);
    body.innerHTML = `<p class="ex-p">${
      published
        ? "Catalogues around this spot, asked now. A service that doesn't answer says so: that is never the same as nothing there."
        : "What the frames on the plate already know about this spot. The catalogue checks (CatWISE2020, NEOWISE, Gaia co-movers, every observing session's asteroids) run for the five featured stars for now."
    }</p>
      ${sub("asteroids", "Known asteroids and comets")}
      ${sub("gaia", "Gaia DR3 stars")}
      ${published ? sub("catwise", "CatWISE2020 sources") + sub("relay", "Ten years in WISE light") : ""}
      <p class="ex-note">Sources: Minor Planet Center orbits computed with kete (seen from the Earth's centre), ESA Gaia DR3${published ? ", NASA/IPAC IRSA (CatWISE2020, NEOWISE)" : ""}.</p>`;
    out("gaia")!.innerHTML = `<div data-gf></div>${published ? "<div data-co></div>" : ""}`;
    fromFrames(at);
  }

  /** The parts every spot has: what the frames carry. Refreshed when new frames arrive. */
  function fromFrames(at: Ctx) {
    const gf = body.querySelector<HTMLElement>('[data-kind="gaia"] [data-gf]');
    if (gf) gf.innerHTML = gaiaInFramesHtml(at.frames, { ra: at.view.ra, dec: at.view.dec });
    // The target-itself check moves Gaia positions to the frames' time: redraw once they are here.
    if (answers.has("comovers")) draw("comovers", at);
    if (!hasScience(at.view.slug)) {
      const ast = out("asteroids");
      if (ast) ast.innerHTML = knownInFramesHtml(at.frames);
    }
  }

  const nameOf = (at: Ctx) => at.object?.name ?? at.view.name ?? "the target";

  function target(kind: Kind): HTMLElement | null {
    return kind === "comovers" ? body.querySelector<HTMLElement>('[data-kind="gaia"] [data-co]') : out(kind);
  }

  async function ask(kind: Kind, at: Ctx, signal: AbortSignal) {
    const el = target(kind);
    if (!el) return;
    el.innerHTML = `<p class="ex-p ex-status" role="status">${esc(WAIT[kind])}</p>`;
    try {
      const answer = await getJson<unknown>(`/api/enrichment?ra=${at.view.ra}&dec=${at.view.dec}&kind=${kind}`, signal);
      if (signal.aborted || ctx?.key !== at.key) return;
      answers.set(kind, answer);
      draw(kind, at);
    } catch (e) {
      if (isAbort(e) || signal.aborted || ctx?.key !== at.key) return;
      const now = target(kind);
      if (!now) return;
      now.innerHTML = `<p class="ex-p ex-warn" role="alert">${esc(failText(e, SERVICE[kind]))}</p><button type="button" class="btn btn-ghost ex-retry" data-retry="${kind}">Ask again</button>`;
      now.querySelector(".ex-retry")?.addEventListener("click", () => {
        if (ctl && ctx) void ask(kind, ctx, ctl.signal);
      });
    }
  }

  function draw(kind: Kind, at: Ctx) {
    const el = target(kind);
    if (!el) return;
    const centre = { ra: at.view.ra, dec: at.view.dec };
    const a = answers.get(kind);
    try {
      if (kind === "asteroids") el.innerHTML = asteroidsHtml(a as AsteroidsAnswer, centre);
      else if (kind === "comovers") el.innerHTML = comoversHtml(a as ComoversAnswer, centre, framesMjd(at.frames) ?? 61000, nameOf(at));
      else if (kind === "catwise") el.innerHTML = catwiseHtml(a as CatwiseAnswer, centre);
      else el.innerHTML = relayHtml(a as RelayAnswer, `${id}-relay`);
    } catch {
      el.innerHTML = `<p class="ex-p ex-warn">${esc(SERVICE[kind])} answered in a shape SkyShift can't read, so nothing is shown. Please tell us.</p>`;
    }
  }

  function open() {
    if (!ctx || !details.open || built === ctx.key) return;
    const at = ctx;
    built = at.key;
    ctl?.abort();
    ctl = new AbortController();
    answers.clear();
    build(at);
    if (hasScience(at.view.slug)) for (const k of KINDS) void ask(k, at, ctl.signal);
  }

  details.addEventListener("toggle", open);

  return {
    clear() {
      ctx = null;
      built = null;
      ctl?.abort();
      ctl = null;
      answers.clear();
      body.replaceChildren();
    },
    show(next: Ctx) {
      if (ctx?.key !== next.key) this.clear();
      ctx = next;
      if (built === next.key) fromFrames(next);
      else open();
    },
  };
}
