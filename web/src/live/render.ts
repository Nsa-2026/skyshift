// Markup for the Live search page's text: the lede, the progress line and its legend, counters, time
// left, machines, the side lists and the zone panel. Strings only; index.ts puts them in place.
import { esc, fmtAbout, fmtAgo, fmtClock, fmtCount, fmtDec, fmtDuration, fmtPct, fmtRa, fmtTimePair, numberWord } from "./format";
import { zoneArea, zoneCentre, type RunSummary, type SkyRun, type SkyZone, type ZoneState } from "./model";
import { suspectLevel, zoneTitle } from "./map";

const html = String.raw;

/** A small square in a state's fill and pattern (the legend's key). */
export function swatch(state: ZoneState, level = state === "combined" ? "3" : ""): string {
  const cross = state === "failed" ? `<path class="z-x" d="M2 2L12 12M12 2L2 12"/>` : "";
  return `<svg class="lv-sw" viewBox="0 0 14 14" aria-hidden="true" data-state="${state}" data-level="${level}"><rect class="z-shape" x="1" y="1" width="12" height="12"/>${cross}</svg>`;
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

/** The big line, the bar of states, and the legend as one sentence. */
export function progressHtml(sum: RunSummary): string {
  const c = sum.counts;
  const order: ZoneState[] = ["combined", "combining", "extracted", "extracting", "failed", "queued"];
  let x = 0;
  const rects = order
    .filter((s) => c[s] > 0)
    .map((s) => {
      const w = (100 * c[s]) / sum.total;
      const r = `<g data-state="${s}" data-level="${s === "combined" ? "3" : ""}"><rect class="z-shape" x="${x.toFixed(3)}%" y="0" width="${w.toFixed(3)}%" height="100%"/></g>`;
      x += w;
      return r;
    })
    .join("");
  const pct = sum.combinedPct;
  const pctText = pct > 0 && pct < 1 ? "under 1%" : `${Math.floor(pct)}%`;
  const lowRed = swatch("combined", "1");
  const highRed = swatch("combined", "4");
  const parts = [
    `${lowRed}${highRed} <b>${fmtCount(c.combined)}</b> combined, solid red, darker with more suspects`,
    `${swatch("combining")} <b>${fmtCount(c.combining)}</b> combining and ${swatch("extracting")} <b>${fmtCount(c.extracting)}</b> extracting, hatched blue with the machine's name`,
    `${swatch("extracted")} <b>${fmtCount(c.extracted)}</b> extracted and waiting, dotted`,
    ...(c.failed ? [`${swatch("failed")} <b>${fmtCount(c.failed)}</b> failed, crossed`] : []),
    `${swatch("queued")} <b>${fmtCount(c.queued)}</b> queued, empty`,
  ];
  const legend = parts.slice(0, -1).join("; ") + "; and " + parts[parts.length - 1] + ".";
  return html`
    <p class="lv-big"><span class="lv-big-n">${fmtCount(c.combined)}</span> of ${fmtCount(sum.total)} zones combined <span class="lv-big-pct">${pctText}</span></p>
    <svg class="lv-bar" aria-hidden="true" width="100%" height="14"><rect class="lv-bar-bg" width="100%" height="100%"/>${rects}</svg>
    <p class="lv-legend">Zones: ${legend} The dashed line is the ecliptic; the heavy outline is the hunt zone, searched this morning.</p>`;
}

export function countersHtml(sum: RunSummary): string {
  const items: Array<[string, number, string]> = [
    ["photos", sum.photos, "Photos read"],
    ["dots", sum.dots, "Dots"],
    ["sources", sum.sources, "Sources"],
    ["movers", sum.movers, "Movers"],
    ["suspects", sum.suspects, "Suspects"],
  ];
  const rate = sum.photosPerHour ? `About ${fmtAbout(sum.photosPerHour)} photos an hour so far. ` : "";
  return html`
    <dl class="lv-counts">
      ${items.map(([k, n, label]) => `<div class="lv-count lv-count-${k}"><dt>${label}</dt><dd>${fmtCount(n)}</dd></div>`).join("")}
    </dl>
    <p class="lv-note">${rate}Dots, sources, movers and suspects count combined zones only.</p>`;
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
  if (!sum.finished && sum.etaCombined !== null) {
    const eta = sum.etaCombined;
    const when = `about ${fmtTimePair(eta, now)}`;
    let tail = "";
    if (eta > sum.deadline) tail = ", after the deadline";
    else if (sum.ends !== null && eta > sum.ends) tail = ", after the run's planned end";
    lines.push(`<p class="lv-muted">At this pace every zone is combined by ${when}${tail}.</p>`);
  } else if (sum.finished) {
    lines.push(`<p class="lv-muted">Every zone has been read.</p>`);
  }
  if (run.final_pass_at !== null) {
    lines.push(
      `<p class="lv-muted">${run.final_pass_at > now ? "The final pass, which merges zone edges and checks CatWISE, starts" : "The final pass, merging zone edges and checking CatWISE, ran"} at ${fmtTimePair(run.final_pass_at, now)}.</p>`,
    );
  }
  return lines.join("");
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
      const combining = m.running ? sum.active.filter((z) => z.state === "combining" && z.machine === m.label && z !== m.current) : [];
      if (combining.length) doing.push(`combining ${andList(combining.map(link))}`);
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
export function listsHtml(sum: RunSummary, now: number): string {
  const recent = sum.recent
    .map((z) => {
      const s = z.suspects !== null ? `, ${fmtCount(z.suspects)} ${z.suspects === 1 ? "suspect" : "suspects"}` : "";
      return `<li><button type="button" class="lv-zlink" data-zone="${esc(z.name)}" data-key="r-${esc(z.name)}">${esc(z.name)}</button> at ${fmtClock(z.combined_at!, now)}${s}</li>`;
    })
    .join("");
  return html`
    <p class="lv-hint">Pick a zone on the map for its numbers.</p>
    ${recent ? `<div><h2 class="lv-h">Just combined</h2><ul class="lv-list">${recent}</ul></div>` : ""}
    <div>
      <h2 class="lv-h">What the numbers mean</h2>
      <p class="lv-note">A <b>dot</b> is one detection on one photo. A <b>source</b> is the dots of one object, linked. A <b>mover</b> is a source that shifts between photos. A <b>suspect</b> is a mover that no catalogue explains: a candidate for people to review.</p>
    </div>`;
}

// ---- the zone panel ---------------------------------------------------------------------------------
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

export function zonePanelHtml(z: SkyZone, now: number): string {
  const c = zoneCentre(z);
  let state = STATE_SENTENCE[z.state];
  if (z.state === "extracting" && z.machine) state = `Extracting on ${esc(z.machine)}: reading its photos.`;
  if (z.state === "combining" && z.machine) state = `Combining on ${esc(z.machine)}: linking dots into sources and movers.`;
  if (z.state === "combined" && z.combined_at !== null) state = `Combined at ${fmtTimePair(z.combined_at, now)}.`;
  const rows: Array<[string, string]> = [
    ["Where", `RA ${fmtRa(z.ra[0])} to ${fmtRa(z.ra[1])}<br>Dec ${fmtDec(z.dec[0])} to ${fmtDec(z.dec[1])}<br>${fmtCount(Math.round(zoneArea(z)))} deg²`],
    ["Photos", num(z.photos, "photos")],
    ["Dots", num(z.dots, "dots")],
    ["Sources", num(z.sources, "sources")],
    ["Movers", num(z.movers, "movers")],
    ["Suspects", num(z.suspects, "suspects")],
    ["Known asteroids re-found", z.recall === null ? `<span class="lv-muted">not yet</span>` : fmtPct(z.recall)],
  ];
  if (z.machine) rows.push(["Machine", esc(z.machine)]);
  if (z.extracted_at !== null) rows.push(["Extracted", fmtTimePair(z.extracted_at, now)]);
  if (z.combined_at !== null) rows.push(["Combined", fmtTimePair(z.combined_at, now)]);
  const provisional =
    z.final || z.state !== "combined" ? "" : `<p class="lv-prov">Provisional: zone edges and the CatWISE check pending.</p>`;
  const err = z.error ? `<p class="lv-err">What went wrong: ${esc(z.error)}</p>` : "";
  const link = z.suspects_url
    ? `<p><a href="${esc(z.suspects_url)}" target="_blank" rel="noopener">This zone's suspects (JSON)</a></p>`
    : "";
  return html`
    <div class="lv-ptop">
      <h2 class="lv-ptitle" tabindex="-1">${esc(zoneTitle(z))}</h2>
      <button type="button" class="btn btn-ghost lv-close" data-close aria-label="Close the zone panel">Close</button>
    </div>
    <p class="lv-pstate">${swatch(z.state, z.state === "combined" ? suspectLevel(z.suspects) : "")} ${state}</p>
    ${provisional}${err}
    <dl class="lv-pdl">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
    ${link}
    <div class="lv-pact">
      <button type="button" class="btn btn-primary" data-blink>Blink the centre</button>
      <a class="btn" href="${skyHref(z)}">See it on the sky</a>
    </div>
    <p class="lv-note">Centre RA ${fmtRa(Math.round(c.ra * 100) / 100)}, Dec ${fmtDec(Math.round(c.dec * 100) / 100)}.</p>`;
}
