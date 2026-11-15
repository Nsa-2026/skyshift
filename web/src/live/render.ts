// Markup for the Live search page's text: the lede, the progress line and its legend, counters, time
// left, machines, the side lists and the zone panel. Strings only; index.ts puts them in place.
import { esc, fmtAbout, fmtAgo, fmtClock, fmtCount, fmtDec, fmtDuration, fmtPct, fmtRa, fmtTimePair, numberWord } from "./format";
import type { Suspect } from "../api/types";
import {
  FILE_PROVISIONAL_TEXT,
  NEAR_SOURCE_HINT,
  failedGates,
  gateText,
  gradeALine,
  gradeBCLine,
  gradeCSentence,
  gradeLabel,
  gradeOf,
  nearSourceOf,
  nearSourceText,
  trackOf,
  trackText,
  vetoesText,
} from "../api/grades";
import type { ZoneSuspects } from "./load";
import {
  isProvisional,
  workerOf,
  zoneArea,
  zoneCentre,
  type RunSummary,
  type SkyNotes,
  type SkyRun,
  type SkyZone,
  type ZoneState,
} from "./model";
import { suspectLevel, zoneTitle } from "./map";

const html = String.raw;

/** A small square in a state's fill and pattern (the legend's key). */
export function swatch(state: ZoneState, level = state === "combined" ? "3" : "", deferred = false): string {
  const cross = state === "failed" ? `<path class="z-x" d="M2 2L12 12M12 2L2 12"/>` : "";
  return `<svg class="lv-sw" viewBox="0 0 14 14" aria-hidden="true" data-state="${state}" data-level="${level}" data-deferred="${deferred ? "1" : ""}"><rect class="z-shape" x="1" y="1" width="12" height="12"/>${cross}</svg>`;
}

const NO_NOTES: SkyNotes = { deferred: null, provisional: null };

/** The run's note on the bulge zones, as written, then whether tonight reaches them. */
function deferredText(sum: RunSummary, notes: SkyNotes): string {
  const d = sum.deferred;
  if (!d.queued || sum.finished) return "";
  const why = notes.deferred ? `${esc(notes.deferred)} ` : "Galactic bulge zones are very crowded and slow to extract, so they come last. ";
  const which = d.queued === d.total ? `The ${fmtCount(d.total)}` : `${fmtCount(d.queued)} of the ${fmtCount(d.total)}`;
  return `${why}${which} likely won't be reached tonight.`;
}

// ---- head ------------------------------------------------------------------------------------------
export function ledeText(run: SkyRun, sum: RunSummary): string {
  const n = run.machines.length;
  const who = n ? `${numberWord(n)} cloud ${n === 1 ? "machine" : "machines"}` : "Cloud machines";
  return sum.finished
    ? `${who} read every SPHEREx photo along the ecliptic overnight, looking for things that move.`
    : `${who} ${n === 1 ? "is" : "are"} reading every SPHEREx photo along the ecliptic tonight, looking for things that move.`;
}

export function updatedText(sum: RunSummary): string {
  return sum.age === null ? "Update time unknown" : `Updated ${fmtAgo(sum.age)}`;
}

/** The exact percentage to one decimal, e.g. "0.3%", "3.0%", "100%". */
export function pctWord(p: number): string {
  return p >= 100 ? "100%" : `${(Math.floor(p * 10) / 10).toFixed(1)}%`;
}

/**
 * The stacked bar: combined (solid dark), extracted but not yet combined (mid, hatched), not yet extracted
 * (light, dotted), with the projection for `ends` as a marker; under it, the time gone towards the deadline.
 */
export function barHtml(sum: RunSummary, now: number, notes: SkyNotes = NO_NOTES, plain = false): string {
  // plain (Results): the bar alone shows with Advanced off; its key, the pace and the deadline sums are pro-only.
  const pro = plain ? " pro-only" : "";
  const b = sum.bar;
  const ext = Math.max(0, b.extractedPct - b.combinedPct);
  const rest = Math.max(0, 100 - b.extractedPct);
  const endsAt = sum.ends !== null ? fmtTimePair(sum.ends, now) : null;
  const by = endsAt ? `by ${endsAt}` : "by the run's end";
  let marker = "";
  // Results (plain) drops the projection, the deadline bar and the pace sums altogether.
  if (b.projectedPct !== null && !plain) {
    const x = b.projectedPct;
    const side = x < 25 ? "start" : x > 75 ? "end" : "mid";
    marker = html`<span class="lv-proj${pro}" style="left:${x.toFixed(2)}%" aria-hidden="true"></span>
      <p class="lv-proj-label${pro}" data-side="${side}" style="--x:${x.toFixed(2)}%">${pctWord(x)} expected ${by}</p>`;
  }
  const c = sum.counts;
  const nComb = c.combined;
  const nExt = c.extracted + c.combining;
  const nRest = Math.max(0, sum.total - nComb - nExt);
  const zn = (n: number) => `${fmtCount(n)} ${n === 1 ? "zone" : "zones"}`;
  const key = [
    `<li><span class="lv-key lv-seg-comb"></span>${zn(nComb)} (${pctWord(b.combinedPct)}) combined</li>`,
    `<li><span class="lv-key lv-seg-ext"></span>${zn(nExt)} (${pctWord(ext)}) extracted, not yet combined</li>`,
    `<li><span class="lv-key lv-seg-rest"></span>${zn(nRest)} (${pctWord(rest)}) not yet extracted</li>`,
  ].join("");
  const time =
    b.timePct !== null && !plain
      ? html`<div class="lv-tbar${pro}">
          <div class="lv-tbar-track" aria-hidden="true"><span style="width:${b.timePct.toFixed(2)}%"></span></div>
          <p class="lv-note">${pctWord(b.timePct)} of the time to the deadline at ${fmtTimePair(sum.deadline, now)} has gone.</p>
        </div>`
      : "";
  const zones = b.projectedZones !== null ? ` (about ${fmtCount(b.projectedZones)} zones)` : "";
  const honest =
    `${fmtCount(sum.total)} zones is the plan for the whole ecliptic band; tonight's run reaches only part of it. ` +
    (b.projectedPct !== null
      ? `At the current pace about ${pctWord(b.projectedPct)}${zones} will be extracted ${by}.`
      : "How much it reaches shows here once the run has a steady pace.");
  const bulge = deferredText(sum, notes);
  return html`
    <div class="lv-pbar"${marker ? " data-proj" : ""}>
      <div class="lv-pbar-track" role="img" aria-label="${pctWord(b.combinedPct)} of zones combined, ${pctWord(b.extractedPct)} extracted${b.projectedPct !== null ? `, ${pctWord(b.projectedPct)} expected ${esc(by)}` : ""}.">
        <span class="lv-seg lv-seg-comb" style="width:${b.combinedPct.toFixed(2)}%"></span><span class="lv-seg lv-seg-ext" style="width:${ext.toFixed(2)}%"></span>
      </div>
      ${marker}
    </div>
    <ul class="lv-pkey${pro}">${key}</ul>
    ${time}
    ${plain ? "" : `<p class="lv-honest">${honest}</p>`}
    ${bulge && !plain ? `<p class="lv-honest lv-deferred">${bulge}</p>` : ""}`;
}

/**
 * The plain view's lines (Results with Advanced off): areas of sky searched, photos read, and the
 * map's colours in a few words. Shown only while Advanced is off.
 */
export function plainProgressHtml(sum: RunSummary): string {
  const c = sum.counts;
  const busy = c.extracting + c.combining;
  const rate = sum.photosPerHour ? `, about <b>${fmtAbout(sum.photosPerHour)}</b> an hour` : "";
  const photos = sum.photos > 0 ? `<b>${fmtCount(sum.photos)}</b> photos read${rate}. ` : "";
  const map = busy ? "Map: red searched, striped in progress." : "Map: red searched.";
  return html`
    <p class="lv-big plain-only"><span class="lv-big-n">${fmtCount(c.combined)}</span> of ${fmtCount(sum.total)} areas of sky searched <span class="lv-big-pct">${pctWord(sum.combinedPct)}</span></p>
    <p class="lv-plain-note plain-only">${photos}${map}</p>`;
}

/** The big line, the progress bars, and the map's legend as one sentence (plain: see plainProgressHtml). */
export function progressHtml(sum: RunSummary, now: number, notes: SkyNotes = NO_NOTES, plain = false): string {
  const pro = plain ? " pro-only" : "";
  const c = sum.counts;
  const pctText = pctWord(sum.combinedPct);
  const lowRed = swatch("combined", "1");
  const highRed = swatch("combined", "4");
  const parts = [
    `${lowRed}${highRed} <b>${fmtCount(c.combined)}</b> combined, solid red, darker with more suspects`,
    `${swatch("combining")} <b>${fmtCount(c.combining)}</b> combining and ${swatch("extracting")} <b>${fmtCount(c.extracting)}</b> extracting, hatched blue with the machine's name`,
    `${swatch("extracted")} <b>${fmtCount(c.extracted)}</b> extracted and waiting, dotted`,
    ...(c.failed ? [`${swatch("failed")} <b>${fmtCount(c.failed)}</b> failed, crossed`] : []),
    `${swatch("queued")} <b>${fmtCount(c.queued - sum.deferred.queued)}</b> queued, empty`,
    ...(sum.deferred.queued
      ? [`${swatch("queued", "", true)} <b>${fmtCount(sum.deferred.queued)}</b> Galactic bulge zones, fine grey stipple, queued last and likely not reached tonight`]
      : []),
  ];
  const legend = parts.slice(0, -1).join("; ") + "; and " + parts[parts.length - 1] + ".";
  return html`
    <p class="lv-big${pro}"><span class="lv-big-n">${fmtCount(c.combined)}</span> of ${fmtCount(sum.total)} zones combined <span class="lv-big-pct">${pctText}</span></p>
    ${plain ? plainProgressHtml(sum) : ""}
    ${barHtml(sum, now, notes, plain)}
    <p class="lv-legend${pro}">On the map: ${legend} The dashed line is the ecliptic; the heavy outline is the hunt zone, searched this morning.</p>`;
}

export function countersHtml(sum: RunSummary): string {
  const items: Array<[string, number, string]> = [
    ["photos", sum.photos, "Photos read"],
    ["dots", sum.dots, "Dots"],
    ["sources", sum.sources, "Sources"],
    ["movers", sum.movers, "Movers"],
    ["suspects", sum.suspects, "Suspects"],
  ];
  const rate = sum.photosPerHour
    ? `About ${fmtAbout(sum.photosPerHour)} photos an hour${sum.paceFrom === "run" ? " now" : " so far"}. `
    : "";
  return html`
    <dl class="lv-counts">
      ${items.map(([k, n, label]) => `<div class="lv-count lv-count-${k}"><dt>${label}</dt><dd>${fmtCount(n)}</dd></div>`).join("")}
    </dl>
    <p class="lv-note">${rate}Dots, sources, movers and suspects count combined zones only.${provisionalCount(sum)}</p>`;
}

function provisionalCount(sum: RunSummary): string {
  const n = sum.provisionalCombined;
  if (!n) return "";
  return n === sum.counts.combined
    ? " The suspects are provisional: checked against Gaia only, before the WISE check."
    : ` Suspects in ${fmtCount(n)} of the ${fmtCount(sum.counts.combined)} combined zones are provisional: checked against Gaia only, before the WISE check.`;
}

/** Time left to the end of the run and to the deadline, and where the pace points. */
export function timeHtml(sum: RunSummary, run: SkyRun, now: number): string {
  const lines: string[] = [];
  if (sum.ends !== null && sum.endsIn !== null) {
    lines.push(
      sum.endsIn > 0
        ? `<p><span class="lv-t-big">${fmtDuration(sum.endsIn)}</span> until the run ends at ${fmtTimePair(sum.ends, now)}.</p>`
        : `<p>The run ended at ${fmtTimePair(sum.ends, now)}.</p>`,
    );
  }
  lines.push(
    sum.deadlineIn > 0
      ? `<p><span class="lv-t-big">${fmtDuration(sum.deadlineIn)}</span> until the deadline at ${fmtTimePair(sum.deadline, now)}.</p>`
      : `<p>The deadline passed at ${fmtTimePair(sum.deadline, now)}.</p>`,
  );
  if (!sum.finished && sum.photosByEnds !== null && sum.ends !== null) {
    lines.push(
      `<p class="lv-muted">At this pace about ${fmtCount(roundTo(sum.photosByEnds, 3))} photos will be read by the run's end.</p>`,
    );
  } else if (sum.finished) {
    lines.push(`<p class="lv-muted">Every zone has been read.</p>`);
  }
  if (run.final_pass_at !== null) {
    lines.push(`<p class="lv-muted">Final pass (zone edges joined, WISE check) finished at ${fmtTimePair(run.final_pass_at, now)}.</p>`);
  } else if (run.final_pass_starts !== null) {
    lines.push(
      `<p class="lv-muted">Final pass (zone edges joined, WISE check) planned from ${fmtTimePair(run.final_pass_starts, now)}; until then results are provisional.</p>`,
    );
  }
  return lines.join("");
}

/** Round to n significant figures: 1,234,567 → 1,230,000. */
function roundTo(x: number, n: number): number {
  if (x <= 0) return 0;
  const p = 10 ** Math.max(0, Math.floor(Math.log10(x)) - n + 1);
  return Math.round(x / p) * p;
}

const kindText = (k: string | null) => (k === "on-demand" ? "on demand" : k ?? "");

/** ["a", "b", "c"] → "a, b and c". */
export function andList(items: string[]): string {
  return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

export function machinesHtml(sum: RunSummary): string {
  if (!sum.machines.length) return "";
  const rows = sum.machines
    .map((m) => {
      const meta = [m.cpus !== null ? `${m.cpus} CPUs` : "", kindText(m.kind)].filter(Boolean).join(", ");
      const link = (z: SkyZone) =>
        `<button type="button" class="lv-zlink" data-zone="${esc(z.name)}" data-key="m-${esc(m.name)}-${esc(z.name)}">${esc(z.name)}</button>`;
      const doing: string[] = [];
      if (m.running && m.current) doing.push(`${m.current.state === "combining" ? "combining" : "extracting"} ${link(m.current)}`);
      if (m.combining.length) doing.push(`combining ${andList(m.combining.map(link))}`);
      const work = doing.length ? `, ${doing.join("; ")}` : "";
      return html`<li class="lv-machine">
        <span class="lv-mname">${esc(m.label)}</span>
        <span class="lv-mmeta">${esc(meta)}</span>
        <span class="lv-mstate" data-running="${m.running}"><span class="lv-dot" aria-hidden="true"></span>${esc(m.statusText)}${work}</span>
      </li>`;
    })
    .join("");
  const cpus = sum.cpus ? ` <span class="lv-muted">${fmtCount(sum.cpus)} CPUs in all.</span>` : "";
  return `<h2 class="lv-h">Machines</h2><ul class="lv-machines">${rows}</ul><p class="lv-note">${cpus.trim()}</p>`;
}

// ---- side: what's at work, what just finished ------------------------------------------------------
export function listsHtml(sum: RunSummary, now: number, plain = false): string {
  const pro = plain ? ` class="pro-only"` : "";
  const recent = sum.recent
    .map((z) => {
      const s = z.suspects !== null ? `, ${fmtCount(z.suspects)} ${z.suspects === 1 ? "suspect" : "suspects"}` : "";
      return `<li><button type="button" class="lv-zlink" data-zone="${esc(z.name)}" data-key="r-${esc(z.name)}">${esc(z.name)}</button> at ${fmtClock(z.combined_at!, now)}${s}</li>`;
    })
    .join("");
  return html`
    <p class="lv-hint">${plain ? "Pick an area for its numbers." : "Pick a zone on the map for its numbers."}</p>
    ${recent && !plain ? `<div><h2 class="lv-h">Just combined</h2><ul class="lv-list">${recent}</ul></div>` : ""}
    <div${pro}>
      <h2 class="lv-h">What the numbers mean</h2>
      <p class="lv-note">A <b>dot</b> is one detection on one photo. A <b>source</b> is the dots of one object, linked. A <b>mover</b> is a source that shifts between photos. A <b>suspect</b> is a mover that no catalogue explains: something for people to check, most often a detector artefact.</p>
    </div>`;
}

// ---- the zone panel ---------------------------------------------------------------------------------
/** What "Known asteroids re-found" covers (the V5 check on Results): not slow movers. */
const RECALL_NOTE =
  "Re-found: known asteroids brighter than V 16 that the blind search caught, counted once per session. Tested so far on asteroids moving 10–90″/h; slower movers haven't been checked against real objects.";

const STATE_SENTENCE: Record<ZoneState, string> = {
  queued: "Queued: no machine has started it yet.",
  extracting: "Extracting: a machine is reading its photos.",
  extracted: "Extracted: every photo is read; it waits to be combined.",
  combining: "Combining: linking dots into sources and movers.",
  combined: "Combined.",
  failed: "Failed.",
};

function num(n: number | null, unit: string, one = unit.replace(/s$/, "")): string {
  return n === null ? `<span class="lv-muted">not yet</span>` : `${fmtCount(n)} ${n === 1 ? one : unit}`;
}

export function skyHref(z: SkyZone): string {
  const c = zoneCentre(z);
  return `#/sky/${round2(c.ra)},${round2(c.dec)},12`;
}
const round2 = (x: number) => String(Math.round(x * 100) / 100);

/** The checks run on a zone's suspects, in plain words: "Gaia", "Gaia and WISE (CatWISE)". */
export function checksWords(checks: string | null): string | null {
  if (!checks) return null;
  const words: Record<string, string> = { gaia: "Gaia", catwise: "WISE (CatWISE)", wise: "WISE" };
  const parts = checks
    .split("+")
    .map((c) => c.trim())
    .filter(Boolean)
    .map((c) => words[c.toLowerCase()] ?? c);
  return parts.length ? andList(parts) : null;
}

export function zonePanelHtml(z: SkyZone, now: number, notes: SkyNotes = NO_NOTES): string {
  const c = zoneCentre(z);
  let state = STATE_SENTENCE[z.state];
  const who = workerOf(z);
  if (z.state === "extracting" && who) state = `Extracting on ${esc(who)}: reading its photos.`;
  if (z.state === "combining" && who) state = `Combining on ${esc(who)}: linking dots into sources and movers.`;
  if (z.state === "combined" && z.combined_at !== null) state = `Combined at ${fmtTimePair(z.combined_at, now)}.`;
  if (z.state === "queued" && z.deferred) state = "Queued last: a Galactic bulge zone, likely not reached tonight.";
  const prov = z.state === "combined" && isProvisional(z);
  const rows: Array<[string, string]> = [
    ["Where", `RA ${fmtRa(z.ra[0])} to ${fmtRa(z.ra[1])}<br>Dec ${fmtDec(z.dec[0])} to ${fmtDec(z.dec[1])}<br>${fmtCount(Math.round(zoneArea(z)))} deg²`],
    ["Photos", num(z.photos, "photos")],
    ["Dots", num(z.dots, "dots")],
    ["Sources", num(z.sources, "sources")],
    ["Movers", num(z.movers, "movers")],
    [prov ? "Suspects, provisional" : "Suspects", num(z.suspects, "suspects")],
    ["Known asteroids re-found", z.recall === null ? `<span class="lv-muted">not yet</span>` : fmtPct(z.recall)],
  ];
  if (z.machine && z.state !== "queued") rows.push([z.state === "extracting" ? "Extracting on" : "Extracted by", esc(z.machine)]);
  if (z.state === "combining" && z.combiner) rows.push(["Combining on", esc(z.combiner)]);
  if (z.extracted_at !== null) rows.push(["Extracted", fmtTimePair(z.extracted_at, now)]);
  if (z.combined_at !== null) rows.push(["Combined", fmtTimePair(z.combined_at, now)]);
  const checks = z.state === "combined" ? checksWords(z.checks) : null;
  if (checks) rows.push(["Checked against", checks]);
  const provisional =
    z.final || z.state !== "combined" ? "" : `<p class="lv-prov">Provisional: zone edges and the CatWISE check pending.</p>`;
  const err = z.error ? `<p class="lv-err">What went wrong: ${esc(z.error)}</p>` : "";
  const bulge = z.deferred && z.state === "queued" && notes.deferred ? `<p class="lv-note">${esc(notes.deferred)}</p>` : "";
  return html`
    <div class="lv-ptop">
      <h2 class="lv-ptitle" tabindex="-1">${esc(zoneTitle(z))}</h2>
      <button type="button" class="btn btn-ghost lv-close" data-close aria-label="Close the zone panel">Close</button>
    </div>
    <p class="lv-pstate">${swatch(z.state, z.state === "combined" ? suspectLevel(z.suspects) : "")} ${state}</p>
    ${provisional}${err}${bulge}
    <dl class="lv-pdl">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
    ${z.recall === null ? "" : `<p class="lv-note">${RECALL_NOTE}</p>`}
    <div class="lv-pact">
      <button type="button" class="btn btn-primary" data-blink>Blink the centre</button>
      <a class="btn" href="${skyHref(z)}">See it on the sky</a>
    </div>
    <p class="lv-note">Centre RA ${fmtRa(Math.round(c.ra * 100) / 100)}, Dec ${fmtDec(Math.round(c.dec * 100) / 100)}.</p>
    ${z.suspects_url ? `<section class="lv-sus" data-sus aria-label="Suspects in this zone"></section>` : ""}`;
}

/** What the zone panel's suspects list shows while it loads, if it fails, and once it's there. */
export type SuspectsState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ok"; rows: Suspect[]; file?: Pick<ZoneSuspects, "grades" | "vetoes" | "provisional" | "gates"> };

export const TOP_SUSPECTS = 8;

/** The vetting's zone totals: 0 A said as the expected result, B and C, what was checked, why C. */
export function gradesHtml(file: Pick<ZoneSuspects, "grades" | "vetoes" | "provisional" | "gates">): string {
  const n = file.grades;
  const vetoes = vetoesText(file.vetoes);
  const parts: string[] = [];
  if (n) {
    parts.push(html`<p class="lv-grades-a">${esc(gradeALine(n.A))}</p>`);
    parts.push(html`<p class="lv-note lv-grades-bc">${esc(gradeBCLine(n))} Grade C stays listed, greyed, with its reasons.</p>`);
  }
  if (vetoes) parts.push(html`<p class="lv-note">Every suspect here was checked against ${esc(vetoes)}.</p>`);
  if (file.provisional) parts.push(html`<p class="lv-prov-note">${esc(FILE_PROVISIONAL_TEXT)}</p>`);
  if (n && n.C > 0 && file.gates.length)
    parts.push(html`<details class="lv-gates"><summary>Why ${fmtCount(n.C)} ${n.C === 1 ? "is" : "are"} grade C</summary>
      <ul>${file.gates.map(([code, k]) => html`<li title="${esc(gateText(code).plain)}"><span>${esc(gateText(code).label)}</span><span class="lv-gates-n">${fmtCount(k)}</span></li>`).join("")}</ul>
      <p class="lv-note">A suspect can fail several checks.</p></details>`);
  return parts.length ? html`<div class="lv-grades">${parts.join("")}</div>` : "";
}

/** One suspect's grade and warnings, in words (grey for C): nothing for an ungraded row. */
function suspectGradeHtml(r: Suspect): string {
  const g = gradeOf(r);
  const lines: string[] = [];
  if (g === "C") lines.push(html`<span class="lv-sus-grade">${esc(gradeCSentence(failedGates(r)))}</span>`);
  else if (g) lines.push(html`<span class="lv-sus-grade">${esc(gradeLabel(g))}</span>`);
  const near = nearSourceOf(r);
  const t = trackOf(r);
  if (t) lines.push(html`<span class="lv-sus-track">${esc(trackText(t))}</span>`);
  if (near != null) lines.push(html`<span class="lv-sus-near" title="${esc(NEAR_SOURCE_HINT)}">${esc(nearSourceText(near))}</span>`);
  return lines.join("");
}

/** The top suspects (by grade, then S/N), each with a Blink on the Hours clock at its detector. */
export function suspectsHtml(z: SkyZone, s: SuspectsState, notes: SkyNotes = NO_NOTES): string {
  const prov = isProvisional(z);
  const note = prov
    ? `<p class="lv-prov-note">${esc(notes.provisional ?? "Provisional: checked against Gaia only. The WISE check in the final pass removes most of these; they aren't on the list to check yet.")}</p>`
    : "";
  const head = `<h3 class="lv-h">${prov ? "Provisional suspects, before the WISE check" : "Top suspects"}</h3>${note}`;
  if (s.status === "loading") return `${head}<p class="lv-note">Loading this zone's suspects…</p>`;
  if (s.status === "error") return `${head}<p class="lv-note">This zone's suspects couldn't be loaded. They appear here once the file is published.</p>`;
  const vetting = s.file ? gradesHtml(s.file) : "";
  if (!s.rows.length) return `${head}${vetting}<p class="lv-note">No suspects in this zone.</p>`;
  const graded = s.rows.some((r) => gradeOf(r) !== null);
  const top = s.rows.slice(0, TOP_SUSPECTS);
  const items = top
    .map((r, i) => {
      const flux = Number.isFinite(r.flux_ujy) ? `, ${fmtCount(Math.round(r.flux_ujy))} µJy` : "";
      const c = gradeOf(r) === "C";
      return html`<li class="lv-sus-row${c ? " is-gradec" : ""}">
        <span class="lv-sus-pos">RA ${r.ra.toFixed(4)}°, Dec ${r.dec < 0 ? "−" : "+"}${Math.abs(r.dec).toFixed(4)}°</span>
        <span class="lv-sus-meta">D${esc(r.det)}, S/N ${fmtCount(Math.round(r.snr))}${flux}</span>
        ${suspectGradeHtml(r)}
        <button type="button" class="btn btn-ghost lv-sus-blink" data-sus="${i}" data-key="s-${esc(r.id)}" aria-label="Blink ${prov ? "provisional " : ""}suspect ${i + 1} on the Hours clock">Blink</button>
      </li>`;
    })
    .join("");
  const all = z.suspects_url
    ? ` <a href="${esc(z.suspects_url)}" target="_blank" rel="noopener">All ${fmtCount(s.rows.length)} as JSON</a>.`
    : "";
  const lead = graded
    ? `The first ${top.length} of ${fmtCount(s.rows.length)} by vetting grade, then S/N, marked on the map ${prov ? "as hollow red rings" : "in solid red"} (grade C in grey).${all} Grade B passes every check but can't show motion yet: blink before you trust one.`
    : `The ${top.length} with the highest S/N of ${fmtCount(s.rows.length)}, marked on the map ${prov ? "as hollow red rings" : "in solid red"}.${all} High S/N doesn't mean real: in the hunt zone, all 8 brightest were artefacts, so blink before you trust one.`;
  return html`${head}${vetting}
    <p class="lv-note">${lead}</p>
    <ol class="lv-sus-list">${items}</ol>`;
}
