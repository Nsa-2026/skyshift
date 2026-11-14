// HTML for the scoreboard (web/DESIGN.md, "Lightbox"). A page that reads top to bottom: the
// title, what was checked, one summary sentence, then one section per check and the suspects.
// Every value from the JSON goes through esc().
import type { CheckStatus, Suspect, SuspectsResponse, ValidationCheck, ValidationResponse } from "../api/types";
import { DETECTORS } from "../app/astro";
import { checkInfo, countStatuses, normStatus, pendingChecks, summaryClauses, withAllChecks } from "./checks";
import {
  bigValue,
  blinkHref,
  candidateHref,
  esc,
  evidenceText,
  formatDecimalPos,
  formatGenerated,
  formatNumber,
  formatPos,
  formatRowCell,
  isNumericColumn,
  keyLabel,
  noteText,
  prettyText,
  rowKeys,
  sentence,
  suspectDate,
} from "./format";

const html = String.raw;

export const STATUS_WORD: Record<CheckStatus, string> = {
  pass: "Pass",
  partial: "Partial",
  fail: "Fail",
  pending: "Pending",
};

/**
 * Grease-pencil status marks, one shape per status so colour is never the only cue:
 * a tick (pass), a half-filled ring (partial), a cross (fail), a dashed ring (pending).
 */
export function statusMark(status: CheckStatus, size = 20): string {
  const inner: Record<CheckStatus, string> = {
    pass: `<path d="M3.6 10.9c1.5 1.1 2.8 2.4 3.9 4C9.6 10.8 12.4 7.4 16.6 4.4" />`,
    partial: `<circle cx="10" cy="10" r="6.6" /><path d="M10 3.4a6.6 6.6 0 0 0 0 13.2z" fill="currentColor" stroke="none" />`,
    fail: `<path d="M5 5.2c3.4 3 6.6 6.4 10 9.8M15.2 5c-3.3 3.3-6.7 6.6-10.2 10" />`,
    pending: `<circle cx="10" cy="10" r="6.6" stroke-dasharray="2.6 2.6" />`,
  };
  return `<svg class="sc-mark" width="${size}" height="${size}" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${inner[status]}</svg>`;
}

// ---- head --------------------------------------------------------------------------------

function isSynthetic(x: unknown): boolean {
  return !!x && typeof x === "object" && (x as { synthetic?: unknown }).synthetic === true;
}

/** The one summary sentence, each count led by its status mark (the page's legend). */
export function renderSummary(checks: ValidationCheck[]): string {
  const clauses = summaryClauses(countStatuses(checks))
    .map(
      (c) =>
        html`<span class="sc-clause sc-${c.status}">${statusMark(c.status)}${esc(c.text)}</span>${
          c.after.length > 6
            ? html`${esc(c.after.slice(0, 1))} <span class="sc-summary-more">${esc(c.after.slice(1).trim())}</span>`
            : esc(c.after)
        }`,
    )
    .join("");
  return html`<p class="sc-summary">${clauses}</p>`;
}

function renderHead(checks: ValidationCheck[], v: ValidationResponse | null, synthetic: boolean): string {
  const meta = v
    ? html`<p class="sc-meta">Results from ${esc(prettyText(v.data_release))}, published
        <time datetime="${esc(v.generated)}">${esc(formatGenerated(v.generated))}</time>.</p>`
    : "";
  return html`
    <header class="sc-head">
      <h1 class="sc-title">How we know it works</h1>
      <p class="sc-lede">We checked SkyShift's measurements against what is already known: star motions from
        ESA's Gaia, NASA's asteroid predictions and catalogue brightness. Every result is printed as measured,
        beside the target we set before running it, including the poor ones.</p>
      ${
        synthetic
          ? html`<p class="sc-synthetic" role="note">These are synthetic example numbers, made up to test this page.
              They are not SPHEREx results.</p>`
          : ""
      }
      ${renderSummary(checks)}
      ${meta}
    </header>`;
}

// ---- checks ------------------------------------------------------------------------------

export function renderRowsTable(rows: Record<string, unknown>[], caption: string): string {
  const keys = rowKeys(rows);
  const num = new Set(keys.filter((k) => isNumericColumn(rows, k)));
  const head = keys
    .map((k) => html`<th scope="col" title="${esc(k)}"${num.has(k) ? ' class="num"' : ""}>${esc(keyLabel(k))}</th>`)
    .join("");
  const body = rows
    .map(
      (r) =>
        `<tr>${keys
          .map((k) => {
            const v = r[k];
            const cls = [num.has(k) ? "num" : "", v === null || v === undefined ? "nil" : ""].filter(Boolean).join(" ");
            return `<td${cls ? ` class="${cls}"` : ""}>${esc(formatRowCell(k, v))}</td>`;
          })
          .join("")}</tr>`,
    )
    .join("");
  return html`<div class="sc-scroll" tabindex="0" role="region" aria-label="${esc(caption)}">
    <table class="sc-table"><caption class="visually-hidden">${esc(caption)}</caption>
      <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

const CHEVRON = `<svg class="sc-chev" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M2.5 4.5 6 8l3.5-3.5"/></svg>`;

function rowsBlock(c: ValidationCheck): string {
  const rows = Array.isArray(c.rows) ? c.rows : [];
  if (!rows.length) return "";
  const show = rows.length === 1 ? "Show the row" : `Show the ${rows.length.toLocaleString("en-GB")} rows`;
  const hide = rows.length === 1 ? "Hide the row" : "Hide the rows";
  return html`<details class="sc-rows">
    <summary><span class="sc-show">${show}</span><span class="sc-hide">${hide}</span>${CHEVRON}</summary>
    ${renderRowsTable(rows, `${c.id}, ${c.claim}: one row per item`)}
  </details>`;
}

export function renderCheck(c: ValidationCheck, lead = false): string {
  const st = normStatus(c.status);
  const info = checkInfo(c.id);
  const id = esc(c.id);
  const headline = typeof c.headline === "string" ? c.headline.trim() : "";
  const pending = st === "pending";
  const big = pending ? null : bigValue(c.value, c.unit, headline);
  const figure = big
    ? html`<p class="sc-num"><span class="sc-num-v">${esc(big.num)}</span>${
        big.unit ? html`<span class="sc-num-u">${esc(prettyText(big.unit))}</span>` : ""
      }</p>`
    : "";
  const measured = headline ? html`<span class="sc-measured">${esc(sentence(prettyText(headline)))}</span> ` : "";
  const target = c.expected
    ? html`<span class="sc-target">Target set before the run: ${esc(sentence(prettyText(c.expected)))}</span>`
    : "";
  const note = noteText(c.note);
  // A check not run yet says what it will do, in plain words, where its figure would be.
  const plain = info && (pending || !note) ? info.plain : "";
  return html`
    <article class="sc-check sc-${st}${lead && big ? " sc-lead" : ""}" id="sc-${id}" aria-labelledby="sc-${id}-claim">
      <div class="sc-rail">
        <span class="sc-id">${id}</span>
        <span class="sc-status">${statusMark(st)}<span class="sc-word">${STATUS_WORD[st]}</span></span>
      </div>
      <h2 class="sc-claim" id="sc-${id}-claim">${esc(prettyText(c.claim))}</h2>
      <div class="sc-main">
        ${figure}
        ${pending && plain ? html`<p class="sc-plain">${esc(plain)}</p>` : ""}
        ${measured || target ? html`<p class="sc-vs">${measured}${target}</p>` : ""}
        ${!pending && info?.scope ? html`<p class="sc-vs sc-scope">${esc(info.scope)}</p>` : ""}
      </div>
      ${note ? html`<p class="sc-note">${esc(prettyText(note))}</p>` : !pending && plain ? html`<p class="sc-note">${esc(plain)}</p>` : ""}
      ${rowsBlock(c)}
    </article>`;
}

// ---- suspects ----------------------------------------------------------------------------

function suspectRow(s: Suspect): string {
  const det = DETECTORS[s.det as keyof typeof DETECTORS];
  return html`<tr>
    <th scope="row" class="sc-sid"><a class="sc-sid-id" href="${esc(candidateHref(s.id))}"
        title="Its page: why it's listed, cautions and votes">${esc(s.id)}</a>
      <a class="sc-blink" href="${esc(blinkHref(s))}" aria-label="Blink ${esc(s.id)} on the Hours clock">Blink this</a></th>
    <td>${esc(suspectDate(s.mjd))}</td>
    <td class="sc-pos"><span>${esc(formatPos(s))}</span><span class="sc-sub">${esc(formatDecimalPos(s))}</span></td>
    <td class="det-${esc(s.det)}"><span class="sc-det"><i aria-hidden="true"></i>D${esc(s.det)}</span>${
      det ? html`<span class="sc-sub">${det.min}–${det.max} µm</span>` : ""
    }</td>
    <td class="sc-evidence">${esc(evidenceText(s.evidence))}</td>
    <td class="num">${esc(formatNumber(s.snr))}</td>
    <td class="num">${esc(formatNumber(s.flux_ujy))}</td>
    <td class="num${s.nearest_known_arcsec === null ? " nil" : ""}">${
      s.nearest_known_arcsec === null ? "none within 600″" : `${esc(formatNumber(s.nearest_known_arcsec))}″`
    }</td>
  </tr>`;
}

function zoneName(zone: string): string {
  const z = (zone || "").trim();
  if (z === "pilot") return "the pilot zone";
  if (z === "hunt") return "the hunt zone";
  return /^[a-z]/.test(z) ? `the ${z}` : z;
}

export function renderSuspects(sr: SuspectsResponse | null): string {
  const intro = html`<p class="sc-intro">A suspect is a dot that seemed to move where no known asteroid or comet was
    predicted. Most are artefacts: in the hunt zone, 211 of 228 rest on just two dots in one session, which can't show
    motion by itself, and the 8 with the highest S/N are all artefacts. People decide which are real by blinking them
    and voting.</p>`;
  if (!sr) {
    return html`<section class="sc-suspects" aria-labelledby="sc-suspects-h">
      <h2 class="sc-h2" id="sc-suspects-h">Unknown movers</h2>
      ${intro}
      <p class="sc-wait"><span class="sc-pending">${statusMark("pending")}</span>The list of suspects comes after the hunt-zone run.</p>
    </section>`;
  }
  const list = Array.isArray(sr.suspects) ? sr.suspects : [];
  const n = list.length;
  const table = n
    ? html`<div class="sc-scroll" tabindex="0" role="region" aria-label="Suspects table">
        <table class="sc-table sc-suspect-table">
          <caption class="visually-hidden">Unknown-mover suspects in ${esc(zoneName(sr.zone))}</caption>
          <thead><tr>
            <th scope="col">Suspect</th><th scope="col">Date (UTC)</th><th scope="col">Position (RA, Dec)</th>
            <th scope="col">Detector</th><th scope="col">Evidence</th><th scope="col" class="num">S/N</th>
            <th scope="col" class="num">Flux (µJy)</th><th scope="col" class="num">Nearest known</th>
          </tr></thead>
          <tbody>${list.map(suspectRow).join("")}</tbody>
        </table></div>`
    : html`<p class="sc-wait">No suspects here: every mover matched a known object or was filtered out.</p>`;
  return html`<section class="sc-suspects" aria-labelledby="sc-suspects-h">
    <h2 class="sc-h2" id="sc-suspects-h">Unknown movers</h2>
    ${intro}
    <div class="sc-suspects-bar">
      <p class="sc-zone"><b>${n.toLocaleString("en-GB")}</b> ${n === 1 ? "suspect" : "suspects"} from ${esc(zoneName(sr.zone))}, in
        ${esc(prettyText(sr.data_release))}, published ${esc(formatGenerated(sr.generated))}.</p>
      <button type="button" class="btn btn-ghost sc-csv" data-csv ${n ? "" : "disabled"}>Download CSV</button>
    </div>
    ${table}
  </section>`;
}

// ---- page --------------------------------------------------------------------------------

export function renderPage(v: ValidationResponse | null, sr: SuspectsResponse | null): string {
  const checks = v ? withAllChecks(Array.isArray(v.checks) ? v.checks : []) : pendingChecks();
  const synthetic = isSynthetic(v) || isSynthetic(sr);
  return html`
    <div class="sc">
      ${renderHead(checks, v, synthetic)}
      <section class="sc-checks" aria-label="Checks">${checks.map((c, i) => renderCheck(c, i === 0)).join("")}</section>
      ${renderSuspects(sr)}
    </div>`;
}
