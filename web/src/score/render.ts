// HTML for Results (web/DESIGN.md, "Lightbox"). Two of its sections are drawn here: "What we found"
// (the objects to check, the unknown movers and the distant-planet search) and "How sure we are"
// (what was checked, one summary sentence, what the search can find, then one block per check).
// Each section leads with a short plain view (plain.ts); the codes, tables and caveats sit in a
// "pro-only" block that only Advanced shows. Every value from the JSON goes through esc().
import {
  FILE_PROVISIONAL_TEXT,
  GRADE_TEXT,
  NEAR_SOURCE_HINT,
  countGrades,
  failedGates,
  gateText,
  gateTotals,
  gatesPhrase,
  gradeALine,
  gradeBCLine,
  gradeCounts,
  gradeOf,
  nearSourceOf,
  nearSourceText,
  rankByGrade,
  trackOf,
  trackText,
  vetoesText,
} from "../api/grades";
import type {
  CandidateKind,
  CandidatesResponse,
  CheckStatus,
  DistantResponse,
  Suspect,
  SuspectsResponse,
  ValidationCheck,
  ValidationResponse,
} from "../api/types";
import { DETECTORS } from "../app/astro";
import { RECALL_SCOPE, checkInfo, countStatuses, normStatus, pendingChecks, summaryClauses, withAllChecks } from "./checks";
import { renderDistant } from "./distant";
import { distantPlain, plainResults, plainTally, strongestMovers } from "./plain";
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

/** One calm sentence when any file holds made-up numbers; "" otherwise. */
export function syntheticNote(...files: unknown[]): string {
  return files.some(isSynthetic)
    ? html`<p class="sc-synthetic" role="note">These are synthetic example numbers, made up to test this page.
        They are not SPHEREx results.</p>`
    : "";
}

/** Every heading one level down (h2 → h3 …), for a part drawn inside a Results section. */
export function demote(markup: string): string {
  return markup.replace(/<(\/?)h([1-5])\b/g, (_, slash: string, n: string) => `<${slash}h${Number(n) + 1}`);
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

/** What was checked, the one summary sentence, and which release and date the checks are from. */
function renderSureHead(checks: ValidationCheck[], v: ValidationResponse | null): string {
  const meta = v
    ? html`<p class="sc-meta">Checks on ${esc(prettyText(v.data_release))}, published
        <time datetime="${esc(v.generated)}">${esc(formatGenerated(v.generated))}</time>.</p>`
    : "";
  return html`
    <div class="sc-head">
      <p class="sc-lede">We checked SkyShift's measurements against what is already known: star motions from
        ESA's Gaia, NASA's asteroid predictions and catalogue brightness. Every result is printed as measured,
        beside the target we set before running it, including the poor ones.</p>
      ${renderSummary(checks)}
      ${meta}
    </div>`;
}

// ---- what the search can find (completeness) --------------------------------------------------

export interface CompletenessRow {
  /** The large figure: "93%", "60 of 60". */
  figure: string;
  /** What the figure counts, as the rest of a sentence. */
  text: string;
  /** What it does and doesn't cover. */
  scope: string;
  href: string;
  link: string;
}

const pctText = (f: number): string => `${Math.round(f * 100)}%`;
const intText = (n: number): string => Math.round(n).toLocaleString("en-GB");

/**
 * The completeness figures, each from its file: known asteroids found blind (V5) and where NASA
 * predicted them (V4), and made-up distant planets the pairing step found (distant.json).
 * A figure whose check hasn't run, or whose file is missing, is left out.
 */
export function completenessRows(v: ValidationResponse | null, dist: DistantResponse | null): CompletenessRow[] {
  const rows: CompletenessRow[] = [];
  const ran = (id: string) => {
    const c = Array.isArray(v?.checks) ? v!.checks.find((x) => x.id === id) : undefined;
    return c && normStatus(c.status) !== "pending" ? c : undefined;
  };
  const v5 = ran("V5");
  if (v5 && typeof v5.value === "number" && v5.value >= 0 && v5.value <= 1) {
    rows.push({
      figure: pctText(v5.value),
      text: "of known asteroids found without being told where to look, counted session by session.",
      scope: RECALL_SCOPE,
      href: "#/results/sure/V5",
      link: "The check, V5",
    });
  }
  const v4 = ran("V4");
  const big4 = v4 ? bigValue(v4.value, v4.unit, typeof v4.headline === "string" ? v4.headline : "") : null;
  if (v4 && big4) {
    rows.push({
      figure: big4.num,
      text: `${big4.unit ? `${prettyText(big4.unit)} ` : ""}found where IRSA's Moving Object Search Tool predicted them.`,
      scope: "Asteroids numbered 1 to 60, looked for at their predicted positions; the same search 3′ away gives the chance rate.",
      href: "#/results/sure/V4",
      link: "The check, V4",
    });
  }
  const inj = dist?.injection;
  if (inj && Array.isArray(inj.distances) && inj.distances.length) {
    const added = inj.distances.reduce((a, r) => a + r.injected, 0);
    const found = inj.distances.reduce((a, r) => a + r.recovered, 0);
    const share = inj.recovered_frac ?? (added ? found / added : null);
    if (share != null && added > 0) {
      const lo = Math.min(...inj.distances.map((r) => r.distance_au));
      const hi = Math.max(...inj.distances.map((r) => r.distance_au));
      rows.push({
        figure: pctText(share),
        text: `of ${intText(added)} made-up distant planets, ${intText(lo)}–${intText(hi)} AU from the Sun, paired up across visits by the distant-planet search.`,
        scope: "This tests the pairing only: it assumes SPHEREx saw the planet in both visits. How bright a planet must be for that is under What it can't see.",
        href: "#/results/found/distant",
        link: "The distant-planet search",
      });
    }
  }
  return rows;
}

export function renderCompleteness(v: ValidationResponse | null, dist: DistantResponse | null): string {
  const rows = completenessRows(v, dist);
  if (!rows.length) return "";
  return html`<div class="rs-complete">
    <h3 class="rs-h3">What the search can find</h3>
    <dl class="rs-figures">${rows
      .map(
        (r) => html`<div class="rs-figure">
          <dt class="rs-fig">${esc(r.figure)}</dt>
          <dd><p class="rs-fig-text">${esc(r.text)}</p><p class="sc-sub">${esc(r.scope)}</p>
            <a class="rs-fig-link" href="${esc(r.href)}">${esc(r.link)}</a></dd>
        </div>`,
      )
      .join("")}</dl>
  </div>`;
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

/** The grade cell: the grade in words, why a C failed, and the near-source warning (quiet). */
function gradeCell(s: Suspect): string {
  const g = gradeOf(s);
  const near = nearSourceOf(s);
  const t = trackOf(s);
  const track = t ? html`<span class="sc-sub sc-track">${esc(trackText(t))}</span>` : "";
  const warn = (near != null ? html`<span class="sc-sub sc-near" title="${esc(NEAR_SOURCE_HINT)}">${esc(nearSourceText(near))}</span>` : "") + track;
  if (!g) return html`<td class="sc-grade"><span class="sc-sub">Not vetted yet</span>${warn}</td>`;
  const sub = g === "C" ? gatesPhrase(failedGates(s)) || "fails a pipeline check" : GRADE_TEXT[g].name.toLowerCase();
  return html`<td class="sc-grade" data-grade="${g}"><span class="sc-grade-name">${esc(GRADE_TEXT[g].short)}</span><span class="sc-sub">${esc(sub.charAt(0).toUpperCase() + sub.slice(1))}</span>${warn}</td>`;
}

function suspectRow(s: Suspect, graded: boolean): string {
  const det = DETECTORS[s.det as keyof typeof DETECTORS];
  return html`<tr${gradeOf(s) === "C" ? ` class="is-gradec"` : ""}>
    <th scope="row" class="sc-sid"><a class="sc-sid-id" href="${esc(candidateHref(s.id))}"
        title="Its page: why it's listed, cautions and votes">${esc(s.id)}</a>
      <a class="sc-blink" href="${esc(blinkHref(s))}" aria-label="Blink ${esc(s.id)} on the Hours clock">Blink this</a></th>
    ${graded ? gradeCell(s) : ""}
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

/**
 * The vetting's totals for the file: 0 grade A said as the expected result (not a failure), B and C,
 * the zone-level checks, provisional, and why the C failed. Nothing for an ungraded file.
 */
export function vettingHtml(sr: SuspectsResponse, list: readonly Suspect[]): string {
  const n = gradeCounts(sr.grades) ?? countGrades(list);
  if (!n) return "";
  const vetoes = vetoesText(sr.vetoes);
  const gates = n.C > 0 ? gateTotals(sr.gates) : [];
  return html`<div class="sc-vetting">
    <p class="sc-grades-a">${esc(gradeALine(n.A))}</p>
    <p class="sc-grades-bc">${esc(gradeBCLine(n))} Grade C rows stay in the table, greyed, with what they failed; they aren't put to a vote.</p>
    ${vetoes ? html`<p class="sc-sub">Every suspect was checked against ${esc(vetoes)}.</p>` : ""}
    ${sr.provisional === true ? html`<p class="sc-sub sc-provisional">${esc(FILE_PROVISIONAL_TEXT)}</p>` : ""}
    ${
      gates.length
        ? html`<details class="sc-gates"><summary>Why ${n.C.toLocaleString("en-GB")} ${n.C === 1 ? "is" : "are"} grade C</summary>
            <dl>${gates.map(([code, k]) => html`<div title="${esc(gateText(code).plain)}"><dt>${esc(gateText(code).label)}</dt><dd>${k.toLocaleString("en-GB")}</dd></div>`).join("")}</dl>
            <p class="sc-sub">A suspect can fail several checks. Hover a line for what it means, or see the <a href="#/learn/glossary">glossary</a>.</p>
          </details>`
        : ""
    }
  </div>`;
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
  const raw = Array.isArray(sr.suspects) ? sr.suspects : [];
  // A graded file is ranked by grade (A, B, C), then S/N; an older, ungraded one stays in file order.
  const graded = raw.some((s) => gradeOf(s) !== null);
  const list = graded ? rankByGrade(raw) : raw;
  const n = list.length;
  // The table sits behind a disclosure, closed, like a check's rows: Results reads top to bottom.
  const table = n
    ? html`<details class="sc-rows sc-suspect-rows">
      <summary><span class="sc-show">${n === 1 ? "Show the suspect" : `Show the ${n.toLocaleString("en-GB")} suspects`}</span><span class="sc-hide">${
        n === 1 ? "Hide the suspect" : "Hide the suspects"
      }</span>${CHEVRON}</summary>
      <div class="sc-scroll" tabindex="0" role="region" aria-label="Suspects table">
        <table class="sc-table sc-suspect-table">
          <caption class="visually-hidden">Unknown-mover suspects in ${esc(zoneName(sr.zone))}</caption>
          <thead><tr>
            <th scope="col">Suspect</th>${graded ? html`<th scope="col">Vetting grade</th>` : ""}<th scope="col">Date (UTC)</th><th scope="col">Position (RA, Dec)</th>
            <th scope="col">Detector</th><th scope="col">Evidence</th><th scope="col" class="num">S/N</th>
            <th scope="col" class="num">Flux (µJy)</th><th scope="col" class="num">Nearest known</th>
          </tr></thead>
          <tbody>${list.map((s) => suspectRow(s, graded)).join("")}</tbody>
        </table></div></details>`
    : html`<p class="sc-wait">No suspects here: every mover matched a known object or was filtered out.</p>`;
  return html`<section class="sc-suspects" aria-labelledby="sc-suspects-h">
    <h2 class="sc-h2" id="sc-suspects-h">Unknown movers</h2>
    ${intro}
    ${vettingHtml(sr, list)}
    <div class="sc-suspects-bar">
      <p class="sc-zone"><b>${n.toLocaleString("en-GB")}</b> ${n === 1 ? "suspect" : "suspects"} from ${esc(zoneName(sr.zone))}, in
        ${esc(prettyText(sr.data_release))}, published ${esc(formatGenerated(sr.generated))}.</p>
      <button type="button" class="btn btn-ghost sc-csv" data-csv ${n ? "" : "disabled"}>Download CSV</button>
    </div>
    ${table}
  </section>`;
}

// ---- the objects to check ----------------------------------------------------------------------

const KIND_WORDS: Record<CandidateKind, [string, string]> = {
  mover: ["fast mover", "fast movers"],
  high_pm: ["moving star", "moving stars"],
  spectral: ["odd spectrum", "odd spectra"],
  shape: ["that may be one object or two", "that may be one object or two"],
};
const KIND_ORDER: CandidateKind[] = ["mover", "high_pm", "spectral", "shape"];

function zoneList(zones: unknown): string[] {
  return (Array.isArray(zones) ? zones : []).filter((x): x is string => typeof x === "string" && !!x.trim());
}

function zonesText(zones: unknown): string {
  const z = zoneList(zones);
  if (!z.length) return "";
  if (z.length === 1) return ` in ${zoneName(z[0])}`;
  return ` in the ${z.slice(0, -1).join(", ")} and ${z[z.length - 1]} zones`;
}

/**
 * How many objects were put up to check, by kind (counted from the list itself), what they are in one
 * plain line, the strongest leads, and where to look at them. The zone ids are for Advanced only.
 */
export function renderObjects(c: CandidatesResponse | null): string {
  const list = c && Array.isArray(c.candidates) ? c.candidates : null;
  if (!list) {
    return html`<div class="rs-objects"><p class="sc-wait"><span class="sc-pending">${statusMark("pending")}</span>The list of objects to check is published once the search has run.</p></div>`;
  }
  const n = new Map<CandidateKind, number>();
  for (const x of list) n.set(x.kind, (n.get(x.kind) ?? 0) + 1);
  const parts = KIND_ORDER.filter((k) => n.get(k)).map((k) => `${intText(n.get(k)!)} ${KIND_WORDS[k][n.get(k) === 1 ? 0 : 1]}`);
  const kinds = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0] ?? "";
  const areas = zoneList(c!.zones).length;
  const where = areas > 1 ? `, from ${intText(areas)} areas of sky` : "";
  const best = strongestMovers(c);
  return html`<div class="rs-objects">
    <p class="rs-big"><span class="rs-big-n">${esc(intText(list.length))}</span> ${list.length === 1 ? "object" : "objects"} to check</p>
    ${kinds ? html`<p class="rs-count">${esc(`${kinds}${where}.`)}</p>` : ""}
    <p class="sc-sub pro-only">Listed${esc(zonesText(c!.zones))}.</p>
    <p class="sc-intro">Things we couldn't explain with Gaia, WISE or a known asteroid. Most turn out to be detector
      glitches once people look.</p>
    ${
      best
        ? html`<p class="rs-best">Strongest leads: <b>${esc(intText(best))}</b> fast ${best === 1 ? "mover" : "movers"} seen on the same
            steady path at different times. <a href="#/candidates/kind=mover&amp;grade=A">See ${best === 1 ? "it" : "them"}</a></p>`
        : ""
    }
    <p class="rs-links"><a href="#/candidates">See the list</a> <a href="#/candidates/one=1">Check them one at a time</a></p>
  </div>`;
}

/** The distant-planet search in a line or two, with the way into the full account (Advanced). */
export function renderDistantPlain(d: DistantResponse | null): string {
  const text = distantPlain(d);
  if (!text) return "";
  return html`<div class="rs-planet plain-only">
    <h3 class="rs-h3">A distant planet?</h3>
    <p>${esc(text)} <a href="#/results/found/distant">How we looked</a></p>
  </div>`;
}

// ---- the two sections drawn here ---------------------------------------------------------------

/**
 * "What we found": the objects to check and the distant-planet search in plain words; with Advanced
 * on, the unknown movers of the first zones and the distant-planet search in full.
 */
export function renderFound(
  sr: SuspectsResponse | null,
  dist: DistantResponse | null,
  cands: CandidatesResponse | null | undefined = undefined,
): string {
  return html`<div class="rs-found">${cands === undefined ? "" : renderObjects(cands)}${renderDistantPlain(dist)}
    <div class="rs-pro pro-only">${demote(renderSuspects(sr) + renderDistant(dist))}</div></div>`;
}

/** The plain view of "How sure we are": what we tested, a few results with their marks, the tally. */
export function renderSurePlain(v: ValidationResponse | null): string {
  const results = plainResults(v);
  const checks = v ? withAllChecks(Array.isArray(v.checks) ? v.checks : []) : pendingChecks();
  const tally = plainTally(checks);
  const list = results.length
    ? html`<ul class="rs-tests">${results
        .map(
          (r) => html`<li class="sc-${r.status}">${statusMark(r.status)}<span><span class="visually-hidden">${STATUS_WORD[r.status]}: </span>${esc(r.text)}</span></li>`,
        )
        .join("")}</ul>`
    : "";
  return html`<div class="rs-plain plain-only">
    <p class="rs-say">We first tested the search on things already known${results.length ? ":" : "."}</p>
    ${list}
    <p class="rs-tally">${esc(tally)} <button type="button" class="rs-more-btn" data-advanced-on>See every check and its numbers</button></p>
  </div>`;
}

/** "How sure we are": in plain words; with Advanced on, the summary, what the search can find, and every check V1–V11. */
export function renderSure(v: ValidationResponse | null, dist: DistantResponse | null): string {
  const checks = v ? withAllChecks(Array.isArray(v.checks) ? v.checks : []) : pendingChecks();
  return html`<div class="rs-sure">
    ${renderSurePlain(v)}
    <div class="rs-pro pro-only">
      ${renderSureHead(checks, v)}
      ${renderCompleteness(v, dist)}
      <div class="sc-checks" role="group" aria-label="The checks">${demote(checks.map((c, i) => renderCheck(c, i === 0)).join(""))}</div>
    </div>
  </div>`;
}

/** Both sections in one block, for a page without the Results sections around them. */
export function renderPage(
  v: ValidationResponse | null,
  sr: SuspectsResponse | null,
  dist: DistantResponse | null = null,
  cands: CandidatesResponse | null | undefined = undefined,
): string {
  return html`
    <div class="sc">
      ${syntheticNote(v, sr, dist, cands)}
      ${renderSure(v, dist)}
      ${renderFound(sr, dist, cands)}
    </div>`;
}
