// The Results overview (#/results): one screen, read in about twenty seconds. Four cards, each with
// one figure, one sentence and a link to its own page (#/results/<section>), then a line on the
// distant-planet search. Every figure is read from the published files or the live feed, never made
// up; a card whose file is missing says so in a sentence. Pure functions: data in, HTML out.
import type { CandidatesResponse, DistantResponse, ValidationResponse } from "../api/types";
import { CONSENSUS, CONSENSUS_PCT, type AstroRow } from "../community/stats";
import { fmtAbout, fmtCount } from "../live/format";
import type { RunSummary } from "../live/model";
import { pctWord, updatedText } from "../live/render";
import { pendingChecks, withAllChecks } from "./checks";
import { esc } from "./format";
import { distantVerdict, foundLine, sureFigure, sureLine } from "./plain";
import { statusMark } from "./render";

const html = String.raw;
const int = (n: number): string => Math.round(n).toLocaleString("en-GB");

/** The card's figure: a big number and what it counts, with an optional mark before it. */
function figure(num: string, unit: string, opts: { mark?: string; cls?: string } = {}): string {
  return html`<p class="rs-card-big${opts.cls ? ` ${opts.cls}` : ""}">${opts.mark ?? ""}<span class="rs-card-n">${esc(num)}</span>
    <span class="rs-card-unit">${esc(unit)}</span></p>`;
}
const line = (text: string, cls = ""): string => (text ? html`<p class="rs-card-line${cls}">${esc(text)}</p>` : "");

/** What we found: how many objects to check, and the strongest leads. */
export function foundCardHtml(c: CandidatesResponse | null): string {
  const list = c && Array.isArray(c.candidates) ? c.candidates : null;
  if (!list) return line("The list of objects to check is published once the search has run.", " rs-card-wait");
  return figure(int(list.length), list.length === 1 ? "object to check" : "objects to check") + line(foundLine(c));
}

/** How sure we are: how many of the checks that ran passed, and one result in plain words. */
export function sureCardHtml(v: ValidationResponse | null): string {
  const checks = v && Array.isArray(v.checks) ? withAllChecks(v.checks) : pendingChecks();
  const fig = sureFigure(checks);
  if (!fig) return line("The tests on things already known haven't run yet.", " rs-card-wait");
  return figure(fig.num, fig.label, { mark: statusMark(fig.status, 22), cls: `sc-${fig.status}` }) + line(sureLine(v));
}

/** Search progress: areas of sky searched, a thin bar, photos read, and when the feed last spoke. */
export function progressCardHtml(sum: RunSummary | null, error: string | null = null): string {
  if (!sum) return line(error ?? "Loading the search's progress…", error ? " rs-card-wait" : "");
  const c = sum.counts;
  const pct = pctWord(sum.combinedPct);
  return html`${figure(`${fmtCount(c.combined)} of ${fmtCount(sum.total)}`, "areas of sky searched")}
    <div class="rs-bar" role="img" aria-label="${esc(pct)} of the areas searched"><span style="width:${Math.min(100, Math.max(0, sum.combinedPct)).toFixed(2)}%"></span></div>
    ${line(sum.photos > 0 ? `${fmtCount(sum.photos)} photos read${sum.photosPerHour ? `, about ${fmtAbout(sum.photosPerHour)} an hour` : ""}.` : "")}
    <p class="rs-card-meta">${esc(updatedText(sum))}</p>`;
}

/** List for astronomers: how many objects people agree on, and the download (or a way to add one). */
export function astronomersCardHtml(rows: readonly AstroRow[] | null, error: string | null = null): string {
  if (!rows) return line(error ?? "Counting votes…", error ? " rs-card-wait" : "");
  const n = rows.length;
  if (!n) {
    return html`${figure("0", "objects on the list so far")}
      ${line(`An object joins at ${CONSENSUS.minVotes} votes, ${CONSENSUS_PCT}% agreeing.`)}
      <p class="rs-card-act"><a class="btn btn-ghost" href="#/candidates/one=1">Vote now</a></p>`;
  }
  return html`${figure(int(n), n === 1 ? "object on the list" : "objects on the list")}
    ${line("Leads people agree on, for astronomers to check.")}
    <p class="rs-card-act"><button type="button" class="btn btn-ghost" data-astro-csv>Download CSV</button></p>`;
}

/** The distant-planet search in one line, under the cards; "" until its file is there. */
export function planetLineHtml(d: DistantResponse | null): string {
  const verdict = distantVerdict(d);
  if (!verdict) return "";
  return html`<h2 class="rs-strip-h" id="rs-strip-h">A distant planet?</h2>
    <p class="rs-strip-text">${esc(verdict)}</p>
    <a class="rs-card-more" href="#/results/found/planet">More<span class="visually-hidden"> on the distant planet</span> <span aria-hidden="true">→</span></a>`;
}
