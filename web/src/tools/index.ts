// Tools and data (#/tools, web/DESIGN.md "Pages" and "Two audiences"): for astronomers, teachers
// and developers. Five quiet sections: download the candidates, batch coordinates, the API, how to
// cite, and data versions. Everything runs in the browser; the only live API call is a per-row
// "Check" in the batch table, one at a time. Sections deep-link as #/tools/<section>.
import "./tools.css";
import "../prefs/details";
import { ApiFailure, loadCandidates, loadFrames, loadSuspects, loadValidation } from "../api/client";
import type { CandidateKind, CandidatesResponse, SuspectsResponse, ValidationResponse } from "../api/types";
import { formatMjd } from "../app/astro";
import type { MountPage, PageContext, PageHandle } from "../app/modules";
import { TARGETS, featuredNear } from "../app/state";
import { BATCH_LIMIT, type BatchResult, type BatchRow, batchCSV, blinkHref, decimalText, parseBatch, rowStatus, sexagesimal, skyHref } from "./batch";
import {
  ACK_GAIA,
  ACK_IRSA,
  ACK_SPHEREX,
  DATASETS,
  ENDPOINTS,
  GAIA_PAPERS,
  REPO_URL,
  SPHEREX_PAPERS,
  STATIC_FILES,
  type Endpoint,
  type Paper,
  adsAbs,
  adsBibtex,
  curlSnippet,
  embedSnippet,
  exampleUrl,
  pythonSnippet,
  siteBase,
  skyshiftBibtex,
  staticPython,
} from "./docs";
import {
  KIND_NAMES,
  candidateColumns,
  candidateSubset,
  candidatesCSV,
  candidatesFileStem,
  candidatesJSON,
  candidatesVOTable,
} from "./export";

export const SECTIONS = [
  { id: "downloads", title: "Download the list" },
  { id: "batch", title: "Batch coordinates" },
  { id: "api", title: "API" },
  { id: "cite", title: "Cite and acknowledge" },
  { id: "data", title: "Data and versions" },
] as const;

const LANG_KEY = "skyshift:tools-lang";
type Lang = "curl" | "python";

export function esc(s: unknown): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** "2026-11-14T13:12:18Z" → "14 Nov 2026, 13:12 UTC"; a bare date → "14 Nov 2026". */
export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(iso);
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    ...(dateOnly ? {} : { hour: "2-digit", minute: "2-digit" }),
    timeZone: "UTC",
  }) + (dateOnly ? "" : " UTC");
}

const fmtDay = (mjd: number) => formatMjd(mjd);

const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;

function readLang(): Lang {
  try {
    return localStorage.getItem(LANG_KEY) === "python" ? "python" : "curl";
  } catch {
    return "curl";
  }
}

function saveFile(name: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.hidden = true;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.append(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

// ---- static markup ---------------------------------------------------------------------------

function code(text: string, label: string, attrs = ""): string {
  return `<div class="tl-code" ${attrs}><pre tabindex="0" aria-label="${esc(label)}"><code>${esc(text)}</code></pre><button type="button" class="btn btn-ghost tl-copy" data-copy aria-label="Copy ${esc(label)}">Copy</button></div>`;
}

function langSeg(lang: Lang): string {
  return `<div class="seg tl-lang" role="group" aria-label="Examples in">
    <button type="button" data-lang="curl" aria-pressed="${lang === "curl"}">curl</button>
    <button type="button" data-lang="python" aria-pressed="${lang === "python"}">Python</button>
  </div>`;
}

function endpointHtml(ep: Endpoint, base: string, lang: Lang): string {
  const url = exampleUrl(ep, base);
  return `<article class="tl-ep" id="tl-ep-${ep.id}" aria-labelledby="tl-ep-${ep.id}-h">
    <h4 class="tl-ep-h" id="tl-ep-${ep.id}-h">${esc(ep.path)}</h4>
    <p>${esc(ep.summary)}</p>
    <dl class="tl-params">
      ${ep.params
        .map(
          (p) => `<div class="tl-param"><dt><span class="tl-pname">${esc(p.name)}</span> <span class="tl-punit">${esc(p.unit)}</span></dt>
          <dd><span class="tl-pneed">${p.need === "required" ? "Required" : `Default ${esc(p.need)}`}.</span> ${esc(p.meaning)}.</dd></div>`,
        )
        .join("")}
    </dl>
    <p class="tl-example">Example: <a href="${esc(url)}" target="_blank" rel="noopener">${esc(decodeURIComponent(url))}</a>. ${esc(ep.exampleNote)}</p>
    ${code(curlSnippet(ep, base), `curl example for ${ep.path}`, `data-lang-block="curl" ${lang === "curl" ? "" : "hidden"}`)}
    ${code(pythonSnippet(ep, base), `Python example for ${ep.path}`, `data-lang-block="python" ${lang === "python" ? "" : "hidden"}`)}
    <details class="tl-more">
      <summary>What comes back</summary>
      <dl class="tl-fields">${ep.fields.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      ${ep.note ? `<p class="tl-note">${esc(ep.note)}</p>` : ""}
    </details>
  </article>`;
}

function paperLi(p: Paper): string {
  return `<li><span class="tl-role">${esc(p.role)}:</span> <a href="${esc(adsAbs(p.bibcode))}" target="_blank" rel="noopener">${esc(p.text)}</a>
    <a class="tl-quiet" href="${esc(adsBibtex(p.bibcode))}" target="_blank" rel="noopener" aria-label="BibTeX for ${esc(p.text)} at ADS">BibTeX at ADS</a></li>`;
}

const EXAMPLE_BATCH = `# One position per line. A name before or after it is optional.
Barnard's Star, 269.4462, 4.76883
WISE 0855, 08h55m10.831s, -7d14m42.53s
Eps Indi B, 22:04:23.2, -56:48:02
RW Aur 05 07 49.46 +30 24 04
150.1 2.2
133.7951292 -7.2451472
Nowhere, 400, 95`;

/** Every heading one level down (h2 → h3, h3 → h4, h4 → h5), for the page shown as a section. */
export function demoteHeadings(markup: string): string {
  return markup.replace(/<(\/?)h([2-5])\b/g, (_, slash: string, n: string) => `<${slash}h${Number(n) + 1}`);
}

function pageHtml(base: string, lang: Lang, opts: ToolsOptions = {}): string {
  const api = new URL("api/", base).href;
  const hashBase = opts.hashBase ?? "#/tools";
  const markup = `<div class="tl${opts.embedded ? " tl-embedded" : ""}">
  ${
    opts.embedded
      ? ""
      : `<header class="tl-head">
    <h1 class="tl-title">Tools and data</h1>
    <p class="tl-lede">For astronomers, teachers and developers: download the objects to check, look up a list of positions, call the API, and cite the data.</p>
  </header>`
  }
  <nav class="tl-toc" aria-label="${opts.embedded ? "In Tools" : "On this page"}">
    <ul>${SECTIONS.map((s) => `<li><a href="${hashBase}/${s.id}" data-jump="${s.id}">${esc(s.title)}</a></li>`).join("")}</ul>
  </nav>
  <div class="tl-main">

  <section class="tl-sec" id="tl-downloads" aria-labelledby="tl-downloads-h">
    <h2 id="tl-downloads-h" class="tl-h2" tabindex="-1">Download the list</h2>
    <p>Every object the pipeline put up to check: things it couldn't explain with Gaia, WISE or a known asteroid. Most are detector artefacts until people check them. Positions in ICRS degrees, times in MJD (UTC), brightness in µJy, motions in ″/h (fast movers) or mas/yr (moving stars).</p>
    <div class="tl-dl" aria-live="polite"><p class="muted">Loading the list…</p></div>
    <details class="tl-more">
      <summary>Columns and units</summary>
      <dl class="tl-fields">${candidateColumns(base)
        .map((c) => `<div><dt>${esc(c.name)}${c.unit ? ` <span class="tl-punit">${esc(c.unit.replace("uJy", "µJy"))}</span>` : ""}</dt><dd>${esc(c.description ?? "")}</dd></div>`)
        .join("")}</dl>
      <p class="tl-note">In CSV, lists (labels, reasons, flags) are joined with semicolons and empty cells mean null. The VOTable (1.4) opens in TOPCAT, or in Python with astropy: Table.read(path, format="votable").</p>
    </details>
  </section>

  <section class="tl-sec" id="tl-batch" aria-labelledby="tl-batch-h">
    <h2 id="tl-batch-h" class="tl-h2" tabindex="-1">Batch coordinates</h2>
    <p>Paste up to ${BATCH_LIMIT} positions, one per line, or drop a CSV file on the box. They are read here in your browser; nothing is sent anywhere until you press Check on a row.</p>
    <details class="tl-more">
      <summary>Formats it reads</summary>
      <ul class="tl-list">
        <li>Decimal degrees: 133.795 −7.245, or 133.795, −7.245. RA in degrees.</li>
        <li>Sexagesimal: 08h55m10.831s −7d14m42.53s, 08:55:10.8 −07:14:42, or 8 55 10.8 −7 14 42. RA in hours.</li>
        <li>A name before or after the position: WISE 0855, 133.7597, −7.2422.</li>
        <li>A CSV, TSV or semicolon file with a header row: the columns named ra and dec (or RA_deg, RAJ2000, DEJ2000, …) are used, and name or id becomes the name.</li>
        <li>Blank lines and lines starting with # are skipped.</li>
      </ul>
    </details>
    <div class="tl-drop">
      <label class="tl-label" for="tl-batch-in">Positions</label>
      <textarea id="tl-batch-in" class="tl-textarea" rows="8" spellcheck="false" autocomplete="off"
        placeholder="Barnard's Star, 269.4462, 4.76883&#10;08h55m10.831s -7d14m42.53s&#10;150.1 2.2"></textarea>
      <p class="tl-drop-hint" aria-hidden="true">Drop a CSV file here</p>
    </div>
    <div class="tl-actions tl-batch-actions">
      <button type="button" class="btn btn-primary" data-act="read">Make the table</button>
      <button type="button" class="btn btn-ghost" data-act="file">Choose a file…</button>
      <input type="file" class="tl-file" accept=".csv,.tsv,.txt,text/csv,text/plain,text/tab-separated-values" hidden />
      <button type="button" class="btn btn-ghost" data-act="example">Try an example</button>
      <button type="button" class="btn btn-ghost" data-act="clear">Clear</button>
    </div>
    <p class="tl-status" role="status" aria-live="polite"></p>
    <div class="tl-result" hidden></div>
  </section>

  <section class="tl-sec" id="tl-api" aria-labelledby="tl-api-h">
    <h2 id="tl-api-h" class="tl-h2" tabindex="-1">API</h2>
    <p>The same data the site uses, as JSON. No key and no sign-up. Static first: the featured targets and the lists are plain files on the site's CDN, so read those before you call the live API.</p>

    <h3 class="tl-h3">Static files</h3>
    <p>Same shapes as the API. The featured targets live in these folders: ${TARGETS.map((t) => `<span class="tl-slug">${esc(t.slug)}</span>: ${esc(t.name)}`).join("; ")}.</p>
    <dl class="tl-fields tl-static">${STATIC_FILES.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
    <p class="tl-example">Example: <a href="${esc(new URL("data/wise0855/index.json", base).href)}" target="_blank" rel="noopener">${esc(new URL("data/wise0855/index.json", base).href)}</a></p>
    ${code(staticPython(base), "Python example for the static files")}

    <h3 class="tl-h3">Live endpoints</h3>
    <p>All are GET, under ${esc(api)}. Answers are cached on our side, so a repeat comes back quickly.</p>
    ${langSeg(lang)}
    ${ENDPOINTS.map((ep) => endpointHtml(ep, base, lang)).join("")}

    <h3 class="tl-h3">Units and errors</h3>
    <dl class="tl-fields">
      <div><dt>Units</dt><dd>Positions in ICRS degrees. Field (fov) and radius (r) in arcmin. Shifts and errors in arcsec. Wavelength in µm. Time in MJD (UTC). Flux in µJy. Frame values in MJy/sr. Gaia keeps Gaia's units: pmra and pmdec in mas/yr, parallax in mas.</dd></div>
      <div><dt>Nulls</dt><dd>A missing value is null, never left out. Gaia ids are strings, because they exceed 2⁵³.</dd></div>
      <div><dt>Errors</dt><dd>An HTTP status with {"error": {"code", "message"}}: 400 bad_request (the message says which parameter), 404 not_found (no SPHEREx frames there, or an unknown name), 502 upstream (IRSA or the Gaia archive didn't answer), 503 upstream (the build ran past 48 s; Retry-After says when to ask again), 500 internal.</dd></div>
      <div><dt>Schema</dt><dd><a href="${esc(new URL("api/openapi.json", base).href)}" target="_blank" rel="noopener">OpenAPI JSON</a>, and <a href="${esc(new URL("api/docs", base).href)}" target="_blank" rel="noopener">interactive docs</a> to try a call. Health: <a href="${esc(new URL("api/health", base).href)}" target="_blank" rel="noopener">/api/health</a>.</dd></div>
      <div><dt>Contract</dt><dd><a href="${REPO_URL}/blob/main/contracts/api.md" target="_blank" rel="noopener">contracts/api.md</a> in the repository is the full specification; fixtures/api/ holds an example of every answer.</dd></div>
    </dl>

    <h3 class="tl-h3">Limits, honestly</h3>
    <p>The live API is one small service with no quota per person: up to 8 machines, each working on 8 requests at a time. A spot nobody has asked for before takes from a few seconds to about 45, because the server cuts SPHEREx images out of NASA's archive and stacks them; identical requests share one build, so a retry joins the one already running. A build that runs out of its 40 s answers with what it has, sent with Cache-Control: no-store and not kept: ask again later for the whole answer. Past 48 s you get 503 upstream; when every machine is busy, HTTP 429. Please send one request at a time, wait for it, and keep what you get. For many positions, use the static files, or SPHEREx itself through <a href="https://irsa.ipac.caltech.edu/Missions/spherex.html" target="_blank" rel="noopener">IRSA</a>.</p>

    <h3 class="tl-h3">Embed a blink</h3>
    <p>Any blink can sit in another page. embed=1 hides the header and footer. Use ra and dec (degrees), clock (months or hours), fov (arcmin) and det (1 to 6), or t=&lt;slug&gt; for a featured target.</p>
    ${code(embedSnippet(base), "HTML to embed a blink")}

    <h3 class="tl-h3">AI assistants (MCP)</h3>
    <p>An MCP server lets an AI assistant (Claude Code, Claude Desktop, Cursor or any MCP client) use SkyShift: browse the objects to check, look at blink frames, measure motion and check known objects. It only reads this public site, the static files first and then the API, so it needs no account or key. From a clone of the repository:</p>
    ${code("claude mcp add skyshift -- uv run --project mcp skyshift-mcp", "Command to add the SkyShift MCP server to Claude Code")}
    <p class="tl-note">Its tools include top_candidates, candidates_near, blink, spectrum, measure, known_objects, gaia_stars, batch_check and validation. Setup for Claude Desktop, streamable HTTP and the full list: <a href="${REPO_URL}/blob/main/mcp/README.md" target="_blank" rel="noopener">mcp/README.md</a>.</p>
  </section>

  <section class="tl-sec" id="tl-cite" aria-labelledby="tl-cite-h">
    <h2 id="tl-cite-h" class="tl-h2" tabindex="-1">Cite and acknowledge</h2>
    <p>If SkyShift helped your work, cite it and the data underneath it.</p>
    <h3 class="tl-h3">SkyShift</h3>
    ${code(skyshiftBibtex(), "BibTeX for SkyShift")}
    <h3 class="tl-h3">SPHEREx data sets (IRSA)</h3>
    <ul class="tl-list tl-refs">${DATASETS.map((d) => `<li>${esc(d.name)}, <a href="https://doi.org/${esc(d.doi)}" target="_blank" rel="noopener">doi:${esc(d.doi)}</a></li>`).join("")}</ul>
    <h3 class="tl-h3">SPHEREx papers</h3>
    <p class="tl-note">As listed on <a href="https://irsa.ipac.caltech.edu/Missions/spherex.html" target="_blank" rel="noopener">IRSA's SPHEREx mission page</a>.</p>
    <ul class="tl-list tl-refs">${SPHEREX_PAPERS.map(paperLi).join("")}</ul>
    <h3 class="tl-h3">Gaia</h3>
    <ul class="tl-list tl-refs">${GAIA_PAPERS.map(paperLi).join("")}</ul>
    <h3 class="tl-h3">Acknowledgements</h3>
    <p>Required wording, to copy into your paper's acknowledgements.</p>
    <div class="tl-ack">
      <p>${esc(ACK_SPHEREX)}</p>
      <p>${esc(ACK_IRSA)}</p>
      <p>${esc(ACK_GAIA)}</p>
      <button type="button" class="btn btn-ghost" data-copy-ack>Copy all three</button>
    </div>
    <p class="tl-note">Other data in SkyShift keep their own credit lines: 2MASS, AllWISE and CatWISE2020 (served by IRSA), Minor Planet Center orbits (computed with kete, BSD-3-Clause), and SDSS DR17. See <a href="${REPO_URL}#data-and-credits" target="_blank" rel="noopener">Data and credits</a> in the README.</p>
  </section>

  <section class="tl-sec" id="tl-data" aria-labelledby="tl-data-h">
    <h2 id="tl-data-h" class="tl-h2" tabindex="-1">Data and versions</h2>
    <p>What this site is built from, and when.</p>
    <dl class="tl-fields tl-versions" aria-live="polite"><div><dt>Files</dt><dd class="muted">Loading…</dd></div></dl>
    <h3 class="tl-h3">Reproduce it</h3>
    <p>Everything is in <a href="${REPO_URL}" target="_blank" rel="noopener">github.com/cucuradu/skyshift</a> (Apache-2.0). You need Git, Node 22, Python 3.12 and uv.</p>
    ${code(
      `git clone ${REPO_URL}.git && cd skyshift

# the site; featured targets work from static files
cd web && npm ci && npm run dev

# the API for any coordinates (the dev site sends /api to port 8000)
cd server && uv sync && uv run uvicorn skyshift_server.app:app --port 8000

# static files of the featured targets
cd server && uv run python ../scripts/precompute/precompute.py

# tests
uv run pytest     # in core/, pipeline/, server/, scripts/validate/
npm test          # in web/`,
      "Commands to reproduce SkyShift",
    )}
    <p class="tl-note">The checks V1–V11 and the inputs each one needs: <a href="${REPO_URL}/blob/main/scripts/validate/README.md" target="_blank" rel="noopener">scripts/validate/README.md</a>. The pipeline that finds movers: <a href="${REPO_URL}/tree/main/pipeline" target="_blank" rel="noopener">pipeline/</a>.</p>
  </section>

  </div>
  <p class="visually-hidden" role="status" aria-live="polite" data-announce></p>
</div>`;
  return opts.embedded ? demoteHeadings(markup) : markup;
}

// ---- dynamic parts ---------------------------------------------------------------------------

/** ["pilot"] → "pilot zone"; ["pilot", "hunt"] → "pilot and hunt zones". */
export function zoneText(zones: string[] | null | undefined): string {
  const z = (zones ?? []).filter(Boolean);
  if (!z.length) return "pipeline";
  return `${z.length > 1 ? `${z.slice(0, -1).join(", ")} and ${z[z.length - 1]}` : z[0]} ${z.length > 1 ? "zones" : "zone"}`;
}

function downloadsHtml(data: CandidatesResponse | null, kind: CandidateKind | "all", suspects: SuspectsResponse | null): string {
  if (!data) {
    return `<p class="tl-empty">The list of objects to check isn't published yet. The pipeline writes it to /data/candidates.json; the downloads appear here once it does.${
      suspects
        ? ` Until then, the pilot's ${plural(suspects.suspects.length, "unknown mover is", "unknown movers are")} in <a href="/data/suspects.json" download>suspects.json</a>.`
        : ""
    }</p>`;
  }
  const total = data.candidates.length;
  const n = candidateSubset(data, "all").counts;
  const kinds = (Object.keys(KIND_NAMES) as CandidateKind[]).map((k) => {
    const c = n[k] ?? 0;
    return `<button type="button" data-kind="${k}" aria-pressed="${kind === k}" ${c === 0 ? "disabled" : ""}>${esc(KIND_NAMES[k].many[0].toUpperCase() + KIND_NAMES[k].many.slice(1))} <span class="tl-count">${c.toLocaleString("en-GB")}</span></button>`;
  });
  return `<p class="tl-sum">${plural(total, "object", "objects")} to check from the ${esc(zoneText(data.zones))}: ${(Object.keys(KIND_NAMES) as CandidateKind[])
    .map((k) => plural(n[k] ?? 0, KIND_NAMES[k].one, KIND_NAMES[k].many))
    .join(", ")}. Generated ${esc(formatWhen(data.generated))} from SPHEREx ${esc(data.data_release)}.</p>
    <div class="tl-choose">
      <div class="seg tl-kinds" role="group" aria-label="Which objects">
        <button type="button" data-kind="all" aria-pressed="${kind === "all"}">All <span class="tl-count">${total.toLocaleString("en-GB")}</span></button>
        ${kinds.join("")}
      </div>
    </div>
    <div class="tl-actions">
      <button type="button" class="btn" data-dl="csv">Download CSV</button>
      <button type="button" class="btn" data-dl="json">Download JSON</button>
      <button type="button" class="btn" data-dl="vot">Download VOTable</button>
    </div>`;
}

function versionsHtml(c: CandidatesResponse | null, v: ValidationResponse | null, s: SuspectsResponse | null): string {
  const raw = (x: string | null | undefined) => (x ? ` <span class="pro-only tl-raw">${esc(x)}</span>` : "");
  const rows: string[] = [];
  rows.push(
    c
      ? `<div><dt><a href="/data/candidates.json" download>candidates.json</a></dt><dd>${plural(c.candidates.length, "object", "objects")} to check from the ${esc(zoneText(c.zones))}. SPHEREx ${esc(c.data_release)}. Generated ${esc(formatWhen(c.generated))}.${raw(c.generated)}</dd></div>`
      : `<div><dt>candidates.json</dt><dd>Not published yet.</dd></div>`,
  );
  if (v) {
    const passed = v.checks.filter((x) => x.status === "pass").length;
    rows.push(
      `<div><dt><a href="/data/validation.json" download>validation.json</a></dt><dd>${plural(v.checks.length, "check", "checks")}, ${passed} passing (see <a href="#/results">Results</a>). ${esc(v.data_release)}. Generated ${esc(formatWhen(v.generated))}.${raw(v.generated)}</dd></div>`,
    );
  } else rows.push(`<div><dt>validation.json</dt><dd>Not published yet.</dd></div>`);
  if (s) {
    rows.push(
      `<div><dt><a href="/data/suspects.json" download>suspects.json</a></dt><dd>${plural(s.suspects.length, "mover", "movers")} with no known asteroid within 30″, from the ${esc(s.zone)} zone. SPHEREx ${esc(s.data_release)}. Generated ${esc(formatWhen(s.generated))}.${raw(s.generated)}</dd></div>`,
    );
  } else rows.push(`<div><dt>suspects.json</dt><dd>Not published yet.</dd></div>`);
  rows.push(
    `<div><dt>Featured targets</dt><dd>${TARGETS.length} targets with static frames in /data/&lt;slug&gt;/. Each frames file records its data_release and archive_checked, the date of its IRSA search.</dd></div>`,
    `<div><dt>Live API</dt><dd>Every /api/frames answer carries the same two fields, so you can tell which archive state it was built from.</dd></div>`,
  );
  return rows.join("");
}

interface Coverage {
  state: "running" | "done" | "error";
  html: string;
}

const posKey = (r: BatchRow) => (r.pos ? `${r.pos.ra.toFixed(5)},${r.pos.dec.toFixed(5)}` : "");

function coverageCell(r: BatchRow, cov: Coverage | undefined, busy: boolean): string {
  if (!r.pos || r.dupOf != null) return "";
  if (featuredNear(r.pos)) return `<span class="muted">Static frames</span>`;
  if (cov?.state === "running") return `<span class="tl-running">Checking…</span>`;
  if (cov?.state === "done") return `<span class="tl-cov-text" tabindex="-1">${cov.html}</span>`;
  const again = cov?.state === "error" ? `<span class="tl-cov-text tl-warn" tabindex="-1">${cov.html}</span> ` : "";
  return `${again}<button type="button" class="btn btn-ghost tl-check" data-check="${r.line}" ${busy ? "disabled" : ""}>${cov ? "Try again" : "Check"}</button>`;
}

function batchTable(res: BatchResult, cov: Map<string, Coverage>, busy: boolean): string {
  const ok = res.rows.filter((r) => r.pos).length;
  const bad = res.rows.length - ok;
  const dups = res.rows.filter((r) => r.dupOf != null).length;
  const parts = [`${plural(ok, "position", "positions")} read.`];
  if (bad) parts.push(`${plural(bad, "line", "lines")} couldn't be read; ${bad === 1 ? "it is" : "they are"} marked below.`);
  if (dups) parts.push(`${plural(dups, "position repeats", "positions repeat")} an earlier line.`);
  if (res.dropped) parts.push(`Only the first ${BATCH_LIMIT} are read: ${plural(res.dropped, "more line was", "more lines were")} left out.`);
  if (res.header) parts.push(`Columns taken from the header row.`);

  const body = res.rows
    .map((r) => {
      const status = rowStatus(r);
      if (!r.pos) {
        return `<tr data-line="${r.line}" class="tl-bad"><td class="tl-num">${r.line}</td><td colspan="3"><span class="tl-input">${esc(r.input)}</span></td><td class="tl-warn">${esc(status)}</td><td></td><td></td><td class="pro-only"></td></tr>`;
      }
      const sx = sexagesimal(r.pos);
      const dd = decimalText(r.pos);
      return `<tr data-line="${r.line}">
        <td class="tl-num">${r.line}</td>
        <td class="tl-name">${r.name ? esc(r.name) : ""}</td>
        <td class="tl-num">${esc(sx.ra)} ${esc(sx.dec)}</td>
        <td class="tl-num">${esc(dd.ra)} ${esc(dd.dec)}</td>
        <td>${esc(status)}</td>
        <td class="tl-links"><a href="${esc(blinkHref(r.pos))}" data-blink="${r.line}">Blink</a> <a href="${esc(skyHref(r.pos))}">Sky</a></td>
        <td class="tl-cov">${coverageCell(r, cov.get(posKey(r)), busy)}</td>
        <td class="pro-only"><span class="tl-input">${esc(r.input)}</span> <span class="muted">${esc(r.format ?? "")}</span></td>
      </tr>`;
    })
    .join("");

  return `<p class="tl-sum">${parts.join(" ")}</p>
    <div class="tl-scroll" role="region" aria-label="Positions read" tabindex="0">
      <table class="tl-table">
        <thead><tr>
          <th scope="col">Line</th><th scope="col">Name</th><th scope="col">RA, Dec</th><th scope="col">RA, Dec (°)</th>
          <th scope="col">Status</th><th scope="col">Open</th><th scope="col">SPHEREx coverage</th><th scope="col" class="pro-only">As typed</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
    <p class="tl-note">Check asks the live API to build the Months frames there (5′ field, D2), which can take about 45 seconds, so it runs one row at a time. A later Blink of that row is then quick.</p>
    <div class="tl-actions"><button type="button" class="btn" data-act="batch-csv">Download the table (CSV)</button></div>`;
}

// ---- the page --------------------------------------------------------------------------------

export interface ToolsOptions {
  /** A section of another page (Results → Tools): no page title, headings one level down. */
  embedded?: boolean;
  /** The route the in-page links point at, as <hashBase>/<section>. Default "#/tools". */
  hashBase?: string;
}

export const mountTools: MountPage = (el: HTMLElement, ctx: PageContext) => createToolsPage(el, ctx);

export function createToolsPage(el: HTMLElement, ctx: PageContext, opts: ToolsOptions = {}): PageHandle {
  const base = siteBase();
  const hashBase = opts.hashBase ?? "#/tools";
  let lang = readLang();
  el.innerHTML = pageHtml(base, lang, opts);

  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const announceEl = $<HTMLElement>("[data-announce]");
  const announce = (t: string) => {
    announceEl.textContent = "";
    setTimeout(() => (announceEl.textContent = t), 30);
  };

  // ---- data (static files only) ----
  let candidates: CandidatesResponse | null = null;
  let suspects: SuspectsResponse | null = null;
  let kind: CandidateKind | "all" = "all";
  let loaded = false;
  const ctl = new AbortController();

  const dlBox = $<HTMLElement>(".tl-dl");
  const renderDownloads = () => (dlBox.innerHTML = downloadsHtml(candidates, kind, suspects));

  async function load() {
    if (loaded) return;
    loaded = true;
    const [c, v, s] = await Promise.all([
      loadCandidates(ctl.signal).catch(() => null),
      loadValidation(ctl.signal).catch(() => null),
      loadSuspects(ctl.signal).catch(() => null),
    ]);
    if (ctl.signal.aborted) return;
    candidates = c && Array.isArray(c.candidates) ? c : null;
    suspects = s && Array.isArray(s.suspects) ? s : null;
    renderDownloads();
    $<HTMLElement>(".tl-versions").innerHTML = versionsHtml(candidates, v && Array.isArray(v.checks) ? v : null, suspects);
  }

  dlBox.addEventListener("click", (ev) => {
    const t = ev.target as HTMLElement;
    const k = t.closest<HTMLButtonElement>("[data-kind]");
    if (k && !k.disabled) {
      kind = k.dataset.kind as CandidateKind | "all";
      dlBox.querySelectorAll<HTMLButtonElement>("[data-kind]").forEach((b) => b.setAttribute("aria-pressed", String(b === k)));
      return;
    }
    const d = t.closest<HTMLButtonElement>("[data-dl]");
    if (!d || !candidates) return;
    const sub = candidateSubset(candidates, kind);
    const stem = candidatesFileStem(candidates, kind);
    if (d.dataset.dl === "csv") saveFile(`${stem}.csv`, candidatesCSV(sub, base), "text/csv;charset=utf-8");
    if (d.dataset.dl === "json") saveFile(`${stem}.json`, candidatesJSON(sub), "application/json");
    if (d.dataset.dl === "vot") saveFile(`${stem}.vot`, candidatesVOTable(sub, base), "application/x-votable+xml");
    announce(`Downloading ${plural(sub.candidates.length, "object", "objects")}.`);
  });

  // ---- batch ----
  const input = $<HTMLTextAreaElement>("#tl-batch-in");
  const fileInput = $<HTMLInputElement>(".tl-file");
  const status = $<HTMLElement>(".tl-status");
  const result = $<HTMLElement>(".tl-result");
  const drop = $<HTMLElement>(".tl-drop");
  let batch: BatchResult | null = null;
  const coverage = new Map<string, Coverage>();
  let busy = false;

  function renderBatch() {
    if (!batch || batch.rows.length === 0) {
      result.hidden = true;
      result.innerHTML = "";
      return;
    }
    result.hidden = false;
    result.innerHTML = batchTable(batch, coverage, busy);
  }

  function readBatch() {
    const text = input.value;
    if (!text.trim()) {
      batch = null;
      renderBatch();
      status.textContent = "Paste positions first, one per line.";
      return;
    }
    batch = parseBatch(text);
    status.textContent = batch.rows.length ? "" : "No positions in that text.";
    renderBatch();
  }

  async function readFile(file: File) {
    const text = await file.slice(0, 2_000_000).text();
    input.value = text;
    readBatch();
    status.textContent = `Read ${file.name} (${Math.max(1, Math.round(file.size / 1000))} kB).`;
  }

  function updateCoverage(line: number) {
    // Refresh only the coverage cells, so focus and scroll stay put.
    if (!batch) return;
    for (const r of batch.rows) {
      const cell = result.querySelector<HTMLElement>(`tr[data-line="${r.line}"] .tl-cov`);
      if (cell) cell.innerHTML = coverageCell(r, coverage.get(posKey(r)), busy);
    }
    const done = result.querySelector<HTMLElement>(`tr[data-line="${line}"] .tl-cov-text, tr[data-line="${line}"] .tl-check`);
    done?.focus();
  }

  async function check(row: BatchRow) {
    if (busy || !row.pos) return;
    const key = posKey(row);
    busy = true;
    coverage.set(key, { state: "running", html: "" });
    updateCoverage(row.line);
    status.textContent = `Checking line ${row.line} on the live API. This can take about 45 seconds.`;
    const timer = new AbortController();
    const stop = setTimeout(() => timer.abort(), 75_000);
    try {
      const { frames } = await loadFrames(
        { ra: Number(row.pos.ra.toFixed(5)), dec: Number(row.pos.dec.toFixed(5)), clock: "months", fov: 5, det: 2 },
        timer.signal,
      );
      const eps = frames.epochs.filter((e) => e.det === 2);
      if ((frames as { synthetic?: boolean }).synthetic) {
        coverage.set(key, { state: "error", html: "The live engine sent example data, so coverage is unknown." });
      } else if (!eps.length) {
        coverage.set(key, { state: "done", html: "No D2 visits here yet." });
      } else {
        const first = Math.min(...eps.map((e) => e.mjd_start));
        const last = Math.max(...eps.map((e) => e.mjd_end));
        coverage.set(key, {
          state: "done",
          html: `${esc(plural(eps.length, "visit", "visits"))} in D2, ${esc(fmtDay(first))} to ${esc(fmtDay(last))}.<span class="pro-only tl-raw"> MJD ${first.toFixed(3)}–${last.toFixed(3)}, ${esc(frames.data_release)}, searched ${esc(frames.archive_checked)}.</span>`,
        });
      }
      status.textContent = `Line ${row.line} checked.`;
    } catch (e) {
      let msg = "The live service can't be reached. Try again later.";
      if (timer.signal.aborted) msg = "No answer within 75 seconds. Try again later.";
      else if (e instanceof ApiFailure) {
        if (e.code === "not_found") msg = "SPHEREx has no frames here yet.";
        else if (e.code === "upstream") msg = "NASA's archive didn't answer. Try again in a minute.";
        else if (e.status === 429 || e.status === 503) msg = "The service is busy. Try again in a minute.";
        else if (e.code === "bad_request") msg = e.message;
        else msg = `The live service failed (${e.status}).`;
      }
      coverage.set(key, { state: e instanceof ApiFailure && e.code === "not_found" ? "done" : "error", html: esc(msg) });
      status.textContent = `Line ${row.line}: ${msg}`;
    } finally {
      clearTimeout(stop);
      busy = false;
      updateCoverage(row.line);
    }
  }

  $<HTMLElement>(".tl-batch-actions").addEventListener("click", (ev) => {
    const act = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-act]")?.dataset.act;
    if (act === "read") readBatch();
    if (act === "file") fileInput.click();
    if (act === "example") {
      input.value = EXAMPLE_BATCH;
      readBatch();
    }
    if (act === "clear") {
      input.value = "";
      batch = null;
      status.textContent = "";
      renderBatch();
      input.focus();
    }
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
      ev.preventDefault();
      readBatch();
    }
  });
  fileInput.addEventListener("change", () => {
    const f = fileInput.files?.[0];
    if (f) void readFile(f);
    fileInput.value = "";
  });
  drop.addEventListener("dragover", (ev) => {
    if (!ev.dataTransfer?.types.includes("Files")) return;
    ev.preventDefault();
    drop.classList.add("is-over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (ev) => {
    drop.classList.remove("is-over");
    const f = ev.dataTransfer?.files?.[0];
    if (!f) return;
    ev.preventDefault();
    void readFile(f);
  });

  result.addEventListener("click", (ev) => {
    const t = ev.target as HTMLElement;
    const btn = t.closest<HTMLButtonElement>("[data-check]");
    if (btn && batch) {
      const row = batch.rows.find((r) => r.line === Number(btn.dataset.check));
      if (row) void check(row);
      return;
    }
    const a = t.closest<HTMLAnchorElement>("a[data-blink]");
    if (a && batch) {
      const me = ev as MouseEvent;
      if (me.button !== 0 || me.metaKey || me.ctrlKey || me.shiftKey || me.altKey) return;
      const row = batch.rows.find((r) => r.line === Number(a.dataset.blink));
      // Featured fields navigate to their static frames (?t=); anything else opens Explore in place.
      if (!row?.pos || featuredNear(row.pos)) return;
      ev.preventDefault();
      ctx.explore({ ra: Number(row.pos.ra.toFixed(5)), dec: Number(row.pos.dec.toFixed(5)), clock: "months", fov: 5, det: 2 });
      return;
    }
    if (t.closest("[data-act='batch-csv']") && batch) {
      const csv = batchCSV(batch.rows, base, (r) => {
        const c = coverage.get(posKey(r));
        if (!c || c.state === "running") return null;
        const div = document.createElement("div");
        div.innerHTML = c.html;
        return div.textContent;
      });
      saveFile("skyshift-batch.csv", csv, "text/csv;charset=utf-8");
      announce("Downloading the table.");
    }
  });

  // ---- API language, copy buttons, in-page links ----
  el.addEventListener("click", async (ev) => {
    const t = ev.target as HTMLElement;
    const langBtn = t.closest<HTMLButtonElement>("[data-lang]");
    if (langBtn) {
      lang = langBtn.dataset.lang === "python" ? "python" : "curl";
      try {
        localStorage.setItem(LANG_KEY, lang);
      } catch {
        /* storage blocked */
      }
      el.querySelectorAll<HTMLButtonElement>("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
      el.querySelectorAll<HTMLElement>("[data-lang-block]").forEach((b) => (b.hidden = b.dataset.langBlock !== lang));
      return;
    }
    const copyBtn = t.closest<HTMLButtonElement>("[data-copy], [data-copy-ack]");
    if (copyBtn) {
      const text =
        copyBtn.dataset.copyAck !== undefined
          ? [ACK_SPHEREX, ACK_IRSA, ACK_GAIA].join("\n\n")
          : (copyBtn.parentElement?.querySelector("code")?.textContent ?? "");
      const ok = await copyText(text);
      const label = copyBtn.textContent;
      copyBtn.textContent = ok ? "Copied" : "Select and copy";
      announce(ok ? "Copied." : "Couldn't copy: select the text and copy it.");
      setTimeout(() => (copyBtn.textContent = label), 1800);
      return;
    }
    const jump = t.closest<HTMLAnchorElement>("a[data-jump]");
    if (jump && location.hash === `${hashBase}/${jump.dataset.jump}`) {
      ev.preventDefault();
      jumpTo(jump.dataset.jump!);
    }
  });

  function jumpTo(id: string) {
    const h = el.querySelector<HTMLElement>(`#tl-${id}-h`);
    if (!h) return;
    const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    h.scrollIntoView?.({ behavior: smooth ? "smooth" : "auto", block: "start" });
    h.focus({ preventScroll: true });
  }

  return {
    show(param) {
      void load();
      if (param && SECTIONS.some((s) => s.id === param)) {
        // The shell scrolls to the top after show(): jump once it has.
        setTimeout(() => jumpTo(param), 0);
      }
    },
    destroy() {
      ctl.abort();
      el.innerHTML = "";
    },
  };
}
