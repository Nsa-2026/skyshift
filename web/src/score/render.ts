// HTML for the scoreboard. Every value from the JSON goes through esc().
import type { CheckStatus, Suspect, SuspectsResponse, ValidationCheck, ValidationResponse } from "../api/types";
import { DETECTORS } from "../app/astro";
import { checkInfo, countStatuses, normStatus, pendingChecks, STATUSES, withAllChecks } from "./checks";
import {
  blinkHref,
  esc,
  evidenceText,
  formatCell,
  formatDecimalPos,
  formatGenerated,
  formatNumber,
  formatPos,
  formatValue,
  isNumericColumn,
  keyLabel,
  rowKeys,
  suspectDate,
} from "./format";

const html = String.raw;

export const STATUS_WORD: Record<CheckStatus, string> = {
  pass: "Pass",
  partial: "Partial",
  fail: "Fail",
  pending: "Pending",
};

/** A shape per status, so the status never rests on colour alone. */
export function statusMark(status: CheckStatus, size = 16): string {
  const s = size;
  const inner: Record<CheckStatus, string> = {
    // filled disc with a tick
    pass: `<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M4.6 8.3l2.3 2.3 4.6-4.9" fill="none" stroke="var(--plate)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>`,
    // ring with the left half filled
    partial: `<circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 1.8a6.2 6.2 0 0 0 0 12.4z" fill="currentColor"/>`,
    // filled square with a cross
    fail: `<rect x="1.5" y="1.5" width="13" height="13" rx="1.5" fill="currentColor"/><path d="M5.2 5.2l5.6 5.6M10.8 5.2l-5.6 5.6" stroke="var(--plate)" stroke-width="1.8" stroke-linecap="round"/>`,
    // dashed open ring
    pending: `<circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.4 2.2"/>`,
  };
  return `<svg class="sc-mark sc-${status}" width="${s}" height="${s}" viewBox="0 0 16 16" aria-hidden="true" focusable="false">${inner[status]}</svg>`;
}

function statusBadge(status: CheckStatus): string {
  return html`<span class="sc-badge sc-${status}">${statusMark(status)}<span>${STATUS_WORD[status]}</span></span>`;
}

// ---- summary -----------------------------------------------------------------------------

export function renderSummary(checks: ValidationCheck[], v: ValidationResponse | null): string {
  const n = countStatuses(checks);
  const counts = STATUSES.map(
    (s) => html`<li class="sc-count sc-${s}${n[s] ? "" : " sc-zero"}">${statusMark(s, 18)}<b>${n[s]}</b> <span>${STATUS_WORD[s].toLowerCase()}</span></li>`,
  ).join("");
  const tally = checks
    .map((c) => {
      const st = normStatus(c.status);
      return html`<li><button type="button" class="sc-tick sc-${st}" data-jump="${esc(c.id)}"
        aria-label="${esc(c.id)} ${esc(c.claim)}: ${STATUS_WORD[st]}">${statusMark(st, 20)}<span class="sc-tick-id">${esc(c.id)}</span></button></li>`;
    })
    .join("");
  const meta = v
    ? html`<div><dt>Data release</dt><dd>${esc(v.data_release)}</dd></div>
        <div><dt>Generated</dt><dd><time datetime="${esc(v.generated)}">${esc(formatGenerated(v.generated))}</time></dd></div>`
    : html`<div><dt>Data release</dt><dd>not published yet</dd></div>
        <div><dt>Generated</dt><dd>waiting for the validation run</dd></div>`;
  return html`
    <section class="sc-summary" aria-label="Summary">
      <ul class="sc-counts" aria-label="Checks by status">${counts}</ul>
      <ol class="sc-tally" aria-label="Jump to a check">${tally}</ol>
      <dl class="sc-meta">${meta}</dl>
    </section>`;
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
            return `<td${cls ? ` class="${cls}"` : ""}>${esc(formatCell(v))}</td>`;
          })
          .join("")}</tr>`,
    )
    .join("");
  return html`<div class="sc-scroll" tabindex="0" role="region" aria-label="${esc(caption)}">
    <table class="sc-table"><caption class="visually-hidden">${esc(caption)}</caption>
      <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

export function renderCheck(c: ValidationCheck): string {
  const st = normStatus(c.status);
  const info = checkInfo(c.id);
  const rows = Array.isArray(c.rows) ? c.rows : [];
  const rowsBlock = rows.length
    ? html`<details class="sc-rows">
        <summary>Show ${rows.length} ${rows.length === 1 ? "row" : "rows"}</summary>
        ${renderRowsTable(rows, `${c.id} rows`)}
      </details>`
    : st === "pending"
      ? ""
      : html`<p class="sc-norows">No per-item rows published for this check.</p>`;
  return html`
    <article class="sc-check sc-${st}" id="sc-${esc(c.id)}" tabindex="-1" aria-labelledby="sc-${esc(c.id)}-claim">
      <div class="sc-rail">
        <span class="sc-id">${esc(c.id)}</span>
        ${statusBadge(st)}
      </div>
      <div class="sc-body">
        <h2 class="sc-claim" id="sc-${esc(c.id)}-claim">${esc(c.claim)}</h2>
        ${info ? html`<p class="sc-plain">${esc(info.plain)}</p>` : ""}
        <p class="sc-headline">${esc(c.headline)}</p>
        ${c.note ? html`<p class="sc-note">${esc(c.note)}</p>` : ""}
      </div>
      <dl class="sc-vs">
        <div><dt>Measured</dt><dd class="sc-value${c.value === null ? " nil" : ""}">${esc(formatValue(c.value, c.unit))}</dd></div>
        <div><dt>Expected</dt><dd>${esc(c.expected)}</dd></div>
      </dl>
      ${rowsBlock ? html`<div class="sc-more">${rowsBlock}</div>` : ""}
    </article>`;
}

// ---- suspects ----------------------------------------------------------------------------

function suspectRow(s: Suspect): string {
  const det = DETECTORS[s.det as keyof typeof DETECTORS];
  return html`<tr>
    <th scope="row" class="sc-sid"><span>${esc(s.id)}</span>
      <a class="btn sc-blink" href="${esc(blinkHref(s))}" aria-label="Blink ${esc(s.id)} on the Hours clock">Blink this</a></th>
    <td class="sc-pos"><span>${esc(formatPos(s))}</span><span class="muted">${esc(formatDecimalPos(s))}</span></td>
    <td>${esc(suspectDate(s.mjd))}</td>
    <td class="det-${esc(s.det)}"><span class="sc-det"><i aria-hidden="true"></i>D${esc(s.det)}</span>${
      det ? html`<span class="muted sc-det-um">${det.min}–${det.max} µm</span>` : ""
    }</td>
    <td class="sc-evidence">${esc(evidenceText(s.evidence))}</td>
    <td class="num">${esc(formatNumber(s.snr))}</td>
    <td class="num">${esc(formatNumber(s.flux_ujy))}</td>
    <td class="num${s.nearest_known_arcsec === null ? " nil" : ""}">${
      s.nearest_known_arcsec === null ? "none found" : `${esc(formatNumber(s.nearest_known_arcsec))}″`
    }</td>
  </tr>`;
}

export function renderSuspects(sr: SuspectsResponse | null): string {
  const intro = html`<p class="sc-lede">Moving dots the pipeline found where no known asteroid or comet should be. Each could be
    a new object or an artefact: open one in the blink, look, and vote.</p>`;
  if (!sr) {
    return html`<section class="sc-suspects" aria-labelledby="sc-suspects-h">
      <h2 class="sc-h2" id="sc-suspects-h">Unknown movers</h2>
      ${intro}
      <p class="sc-empty">${statusMark("pending")} The suspect list is published after the hunt-zone run. Nothing to show yet.</p>
    </section>`;
  }
  const list = Array.isArray(sr.suspects) ? sr.suspects : [];
  const n = list.length;
  const table = n
    ? html`<div class="sc-scroll" tabindex="0" role="region" aria-label="Suspects table">
        <table class="sc-table sc-suspect-table">
          <caption class="visually-hidden">Unknown-mover suspects in ${esc(sr.zone)}</caption>
          <thead><tr>
            <th scope="col">ID</th><th scope="col">Position (RA, Dec)</th><th scope="col">Date (UTC)</th>
            <th scope="col">Detector</th><th scope="col">Evidence</th><th scope="col" class="num">S/N</th>
            <th scope="col" class="num">Flux µJy</th><th scope="col" class="num">Nearest known</th>
          </tr></thead>
          <tbody>${list.map(suspectRow).join("")}</tbody>
        </table></div>`
    : html`<p class="sc-empty">No suspects in this zone: every mover matched a known object or was filtered out.</p>`;
  return html`<section class="sc-suspects" aria-labelledby="sc-suspects-h">
    <div class="sc-suspects-head">
      <h2 class="sc-h2" id="sc-suspects-h"><span class="sc-big">${n}</span> unknown ${n === 1 ? "mover" : "movers"}</h2>
      <button type="button" class="btn sc-csv" data-csv ${n ? "" : "disabled"}>Download CSV</button>
    </div>
    <p class="sc-zone">Zone: ${esc(sr.zone)}. Data release ${esc(sr.data_release)}, generated ${esc(formatGenerated(sr.generated))}.</p>
    ${intro}
    ${table}
  </section>`;
}

// ---- page --------------------------------------------------------------------------------

function isSynthetic(x: unknown): boolean {
  return !!x && typeof x === "object" && (x as { synthetic?: unknown }).synthetic === true;
}

export function renderPage(v: ValidationResponse | null, sr: SuspectsResponse | null): string {
  const checks = v ? withAllChecks(Array.isArray(v.checks) ? v.checks : []) : pendingChecks();
  const synthetic = isSynthetic(v) || isSynthetic(sr);
  return html`
    <div class="sc">
      <header class="sc-head">
        <h1 class="sc-title">How we know it works</h1>
        <p class="sc-lede">Eight checks of SkyShift's measurements against Gaia, NASA's asteroid predictions and
          catalogue brightness. Every number is printed as measured, including the poor ones.</p>
        ${
          synthetic
            ? html`<p class="sc-synthetic" role="note">Synthetic example data: these numbers are made up to test the page.</p>`
            : ""
        }
        ${
          v
            ? ""
            : html`<p class="sc-pending-note" role="status">${statusMark("pending")} The validation run hasn't published its results
                yet. The eight checks are listed below and fill in when it does.</p>`
        }
      </header>
      ${renderSummary(checks, v)}
      <section class="sc-checks" aria-label="Checks">${checks.map(renderCheck).join("")}</section>
      ${renderSuspects(sr)}
    </div>`;
}
