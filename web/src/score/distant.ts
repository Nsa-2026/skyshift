// The distant-planet search on "How we know it works" (contracts/api.md v3.6, /data/distant.json).
// What we looked for, what we tested, what we found against chance, how sensitive the search is, and its limits.
// Plain words, every number from the file; every value goes through esc().
import type { DistantPair, DistantResponse } from "../api/types";
import { formatMjd } from "../app/astro";
import { esc, formatDecimalPos, formatGenerated, formatPos, prettyText } from "./format";

const html = String.raw;

const DROP_WORDS: Record<string, string> = {
  bright_fragment: "pieces of bright stars",
  zone_edge: "sources near the zone's edge",
  saturated: "saturated sources",
};

const int = (n: number): string => Math.round(n).toLocaleString("en-GB");
const pct = (f: number): string => `${Math.round(f * 100)}%`;
const arcsec = (x: number): string => `${int(x)}″`;
/** A small decimal for prose: 1.5, 1.25 → 1.2 (one decimal), 0.7813 → 0.78. */
const dec = (x: number, digits = 1): string => x.toFixed(digits).replace(/\.0+$/, "");
const signed = (x: number): string => `${x < 0 ? "−" : "+"}${Math.abs(x).toFixed(1)}`;

/** "the hunt zone (RA 120.5–130.5°, Dec +14.4° to +24.4°)" */
export function zoneText(d: Pick<DistantResponse, "zone" | "zone_box">): string {
  const z = (d.zone || "").trim();
  const name = z === "hunt" ? "the hunt zone" : z === "pilot" ? "the pilot zone" : `the ${z} zone`;
  const b = d.zone_box;
  if (!b) return name;
  return `${name} (RA ${b.ra[0]}–${b.ra[1]}°, Dec ${signed(b.dec[0])}° to ${signed(b.dec[1])}°)`;
}

/** "pieces of bright stars (314), sources near the zone's edge (26) and saturated sources (5)" */
export function dropText(dropped: Record<string, number>): string {
  const parts = Object.entries(dropped || {})
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${DROP_WORDS[k] ?? k.replace(/_/g, " ")} (${int(n)})`);
  if (parts.length < 2) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** The one-line verdict, from the file's own rule (beyond_chance: Poisson p < 0.01). */
export function verdictText(d: DistantResponse): string {
  const n = d.counts.pairs;
  if (d.control.beyond_chance) return "More pairs passed than chance explains: worth a closer look, not yet a discovery.";
  if (n === 0) return "No pair passed: no sign of a distant planet that this search could see.";
  return "Nothing beyond chance: no sign of a distant planet that this search could see.";
}

/** "Chance alone gives 1.5 ± 1.2 passing pairs (50 shuffled runs). The probability of 1 or more by chance: 0.78." */
export function chanceText(d: DistantResponse): string {
  const k = d.control;
  if (!k.n || k.expected_pairs == null) return "The chance controls did not run.";
  const spread = k.spread_pairs != null ? ` ± ${dec(k.spread_pairs)}` : "";
  const first = `Chance alone gives ${dec(k.expected_pairs)}${spread} passing pairs (the same search on ${int(k.n)} shuffled copies of the data).`;
  if (k.poisson_p == null || d.counts.pairs === 0) return first;
  const p = k.poisson_p < 0.01 ? k.poisson_p.toExponential(1) : dec(k.poisson_p, 2);
  const n = d.counts.pairs;
  return `${first} The probability of ${int(n)} or more by chance: ${p}.`;
}

function funnel(d: DistantResponse): string {
  const c = d.counts;
  const s = d.searched;
  const drops = dropText(c.dropped);
  const rows: [string, number][] = [
    ["Sources seen in one visit only", c.single_visit_sources],
    ["… with no entry in Gaia, CatWISE or 2MASS", c.uncatalogued],
    ["… whose spot the other visit photographed, so the absence is real", c.covered],
    [`Orphans left after dropping ${drops || "nothing"}`, c.orphans],
    ["Orphans whose drift inside their visit we could measure", c.drift_ok],
    [`Pairs tested between the two visits, ${arcsec(s.sep_arcsec[0])}–${arcsec(s.sep_arcsec[1])} apart`, c.pairs_tested],
    ["Pairs that passed every test", c.pairs],
  ];
  return html`<div class="sc-scroll" tabindex="0" role="region" aria-label="What the distant-planet search tested">
    <table class="sc-table sc-distant-funnel">
      <caption class="visually-hidden">From single-visit sources to tested pairs</caption>
      <tbody>${rows.map(([label, n]) => html`<tr><th scope="row">${esc(label)}</th><td class="num">${esc(int(n))}</td></tr>`).join("")}</tbody>
    </table></div>`;
}

function pairRow(p: DistantPair): string {
  const range =
    p.distance_lo_au != null && p.distance_hi_au != null && p.distance_hi_au > p.distance_lo_au
      ? html`<span class="sc-sub">${esc(int(p.distance_lo_au))}–${esc(int(p.distance_hi_au))} AU</span>`
      : "";
  const side = (ra: number, de: number, mjd: number, visit: number, known: string | null) =>
    html`<td class="sc-pos"><span>Visit ${esc(visit)}, ${esc(formatMjd(mjd))}</span><span class="sc-sub">${esc(formatPos({ ra, dec: de }))}</span><span class="sc-sub">${esc(formatDecimalPos({ ra, dec: de }))}</span>${
      known ? html`<span class="sc-sub">Known: ${esc(known)}</span>` : ""
    }</td>`;
  return html`<tr>
    ${side(p.ra_a, p.dec_a, p.mjd_a, p.visit_a, p.known_a)}
    ${side(p.ra_b, p.dec_b, p.mjd_b, p.visit_b, p.known_b)}
    <td class="num">${esc(arcsec(p.sep_arcsec))}</td>
    <td class="num">${esc(int(p.distance_au))} AU${range}</td>
    <td class="num">${p.flux_ratio == null ? "—" : esc(dec(p.flux_ratio, 2))}<span class="sc-sub">${esc(int(p.n_common_det))} ${p.n_common_det === 1 ? "detector" : "detectors"}</span></td>
    <td class="num">${esc(dec(p.p, 2))}</td>
  </tr>`;
}

function pairsTable(d: DistantResponse): string {
  if (!d.pairs.length) return "";
  return html`<div class="sc-scroll" tabindex="0" role="region" aria-label="Pairs that passed">
    <table class="sc-table sc-distant-pairs">
      <caption class="visually-hidden">Pairs that passed: the earlier and later visit's source, how far apart, the distance one orbit implies</caption>
      <thead><tr>
        <th scope="col">Earlier visit</th><th scope="col">Later visit</th><th scope="col" class="num">Apart</th>
        <th scope="col" class="num">Implied distance</th><th scope="col" class="num">Brightness later / earlier</th>
        <th scope="col" class="num">Fit p</th>
      </tr></thead>
      <tbody>${d.pairs.map(pairRow).join("")}</tbody>
    </table></div>`;
}

function onePairText(d: DistantResponse): string {
  if (d.pairs.length !== 1) return "";
  const p = d.pairs[0];
  const along = Math.abs(p.dlat_arcsec) <= 0.1 * Math.abs(p.sep_arcsec) ? ", almost exactly along the ecliptic" : "";
  const flux =
    p.flux_ratio != null
      ? ` Its brightness changed by a factor ${dec(p.flux_ratio, 2)} in the ${p.n_common_det === 1 ? "one detector" : `${int(p.n_common_det)} detectors`} both visits measured.`
      : "";
  const chance = d.control.beyond_chance ? "" : " The shuffled copies produce pairs like it just as often, so it is what chance gives, not a planet.";
  return `The pair: two faint sources ${arcsec(p.sep_arcsec)} apart${along}, which one orbit about ${int(p.distance_au)} AU from the Sun would explain.${flux}${chance}`;
}

function injectionPart(d: DistantResponse): string {
  const inj = d.injection;
  if (!inj || !inj.distances.length) {
    return html`<p class="sc-note">The sensitivity test has not been run for this zone yet.</p>`;
  }
  const added = inj.distances.reduce((a, r) => a + r.injected, 0);
  const found = inj.distances.reduce((a, r) => a + r.recovered, 0);
  const lo = Math.min(...inj.distances.map((r) => r.distance_au));
  const hi = Math.max(...inj.distances.map((r) => r.distance_au));
  const fr = inj.distances.map((r) => r.recovered_frac).filter((x): x is number => x != null);
  const worst = fr.length ? Math.min(...fr) : null;
  const share = inj.recovered_frac ?? (added ? found / added : 0);
  const bits = [`The search found ${int(found)} of them (${pct(share)}).`];
  if (worst != null) {
    const cell = inj.min_cell_recovered_frac;
    bits.push(`At every distance it found at least ${pct(worst)}${cell != null ? `, and ${pct(cell)} in the hardest single setting` : ""}.`);
  }
  if (inj.distance_err_median_frac != null) {
    bits.push(`The distance it gave was typically ${dec(inj.distance_err_median_frac * 100)}% off the true one.`);
  }
  const rows = inj.distances
    .map(
      (r) => html`<tr><th scope="row">${esc(int(r.distance_au))} AU</th><td class="num">${esc(int(r.injected))}</td>
        <td class="num">${esc(int(r.recovered))}${r.recovered_frac != null ? html` <span class="sc-sub">${esc(pct(r.recovered_frac))}</span>` : ""}</td>
        <td class="num${r.distance_fit_median_au == null ? " nil" : ""}">${r.distance_fit_median_au == null ? "—" : `${esc(int(r.distance_fit_median_au))} AU`}</td></tr>`,
    )
    .join("");
  return html`<p class="sc-vs">We added ${esc(int(added))} made-up planets at ${esc(int(lo))}–${esc(int(hi))} AU to the
      real orphans, seen at the times SPHEREx really looked at each spot and with the same position errors. ${esc(bits.join(" "))}</p>
    <div class="sc-scroll" tabindex="0" role="region" aria-label="Made-up planets found, by distance">
      <table class="sc-table sc-distant-injection">
        <caption class="visually-hidden">Made-up planets added and found, by distance from the Sun</caption>
        <thead><tr><th scope="col">Distance</th><th scope="col" class="num">Added</th><th scope="col" class="num">Found</th>
          <th scope="col" class="num" title="The median distance the search gave for the planets it found">Fitted</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    <p class="sc-note">This tests the pairing only. It assumes SPHEREx saw the planet as an orphan in both visits;
      whether a planet is bright enough for that is the first limit below.</p>`;
}

function limitsList(d: DistantResponse): string {
  const depth = Object.entries(d.limits.depth_ab_snr5 || {}).sort(([a], [b]) => a.localeCompare(b));
  const reach = d.limits.reach_au || {};
  const items: string[] = [];
  if (depth.length) {
    const [d0, v0] = depth[0];
    const [d1, v1] = depth[depth.length - 1];
    const span = depth.length > 1 ? `AB ${dec(v0)} (${d0}) to ${dec(v1)} (${d1})` : `AB ${dec(v0)} (${d0})`;
    const nep = reach["neptune_p0.4"];
    const earth = reach["earth_p0.4"];
    const far =
      nep != null
        ? ` A Neptune-size planet that reflects 40% of the sunlight is that bright out to about ${int(nep)} AU${
            earth != null ? `; an Earth-size one to about ${int(earth)} AU` : ""
          }. Anything fainter is missed.`
        : "";
    items.push(`Brightness. We search single photos, not stacks: the faintest dots we keep are about ${span}.${far}`);
  }
  items.push(
    "It must be seen in at least 3 sessions of each visit, with no catalogued star or galaxy at the same spot (one would hide it).",
  );
  if (d.counts.open_orphans > 0) {
    items.push(
      `${int(d.counts.open_orphans)} orphans sit where their partner could lie outside this zone. We have not searched across zones yet.`,
    );
  }
  const fast = d.searched.rate_arcsec_h?.[1];
  if (fast != null) {
    items.push(
      `Anything drifting more than ${dec(fast)}″ an hour within a visit (nearer than about 50 AU) is not this search's job: the moving-object search above looks for those.`,
    );
  }
  return html`<ul class="sc-note sc-distant-limits">${items.map((t) => html`<li>${esc(t)}</li>`).join("")}</ul>`;
}

export function renderDistant(d: DistantResponse | null): string {
  const intro = html`<p class="sc-intro">A planet 70 to 1,000 times farther from the Sun than Earth (70–1000 AU) moves so
    slowly that within one visit it looks like a star that stays put. Six months later Earth is on the other side of
    the Sun, so the planet appears somewhere else: 390″ away at 1000 AU, up to 4,800″ at 70 AU, along the ecliptic. It
    shows up as a source seen in one visit only, missing from the star catalogues, with a partner of the same
    brightness in the other visit. We call these single-visit sources orphans.</p>`;
  const head = html`<h2 class="sc-h2" id="sc-distant-h">A distant planet?</h2>`;
  if (!d || !d.counts || !d.control) {
    return html`<section class="sc-distant" aria-labelledby="sc-distant-h">
      ${head}
      ${intro}
      <p class="sc-wait">The distant-planet search is published after its run.</p>
    </section>`;
  }
  const c = d.counts;
  return html`<section class="sc-distant" aria-labelledby="sc-distant-h">
    ${head}
    ${intro}

    <h3 class="sc-claim">What we tested</h3>
    <p class="sc-vs">We took every source in ${esc(zoneText(d))} that SPHEREx saw in one visit only and kept the
      orphans. Then we paired each orphan with those of the other visit and asked: can one orbit around the Sun,
      ${esc(int(d.searched.distance_au[0]))} to ${esc(int(d.searched.distance_au[1]))} AU out, explain both positions
      and the slow drift measured inside each visit, with the same brightness?</p>
    ${funnel(d)}

    <h3 class="sc-claim">What we found</h3>
    <p class="sc-num"><span class="sc-num-v">${esc(int(c.pairs))}</span><span class="sc-num-u">${
      c.pairs === 1 ? "pair passed" : "pairs passed"
    }, of ${esc(int(c.pairs_tested))} tested</span></p>
    <p class="sc-vs"><span class="sc-measured">${esc(verdictText(d))}</span> ${esc(chanceText(d))}</p>
    ${onePairText(d) ? html`<p class="sc-note">${esc(onePairText(d))}</p>` : ""}
    ${pairsTable(d)}
    ${c.known_orphans > 0 ? html`<p class="sc-note">${esc(int(c.known_orphans))} orphans turned out to be known slow solar-system bodies: the search sees what it should.</p>` : ""}

    <h3 class="sc-claim">How sensitive it is</h3>
    ${injectionPart(d)}

    <h3 class="sc-claim">What it can't see</h3>
    ${limitsList(d)}

    <p class="sc-zone">Search run ${esc(formatGenerated(d.run_created))} on ${esc(prettyText(d.data_release))}; published
      ${esc(formatGenerated(d.generated))}. Every number: <a href="/data/distant.json">distant.json</a>.</p>
  </section>`;
}
