// Results (#/results): a one-screen overview, and one page per section (web/DESIGN.md "Pages",
// "Two audiences"). The overview (#/results) has four cards, each one figure, one sentence and a
// link: What we found, How sure we are, Search progress, List for astronomers; then a line on the
// distant-planet search, and with Advanced on a card for Data and methods and Tools.
//
// Routes: #/results/<section>[/<part>] → show("<section>[/<part>]") shows that section's page alone
// (a "← Results" link, its title as h1, its full content) and scrolls to the part: a check id under
// "sure" (V5), "suspects", "distant" or "planet" under "found", a zone under "progress" (hunt), a
// Tools part under "tools" (batch). A section, or a part, that only Advanced shows turns it on:
// following a link to it asks for it.
//
// Advanced off (the default) each page says its point in a few plain sentences; the codes, tables,
// caveats and internals are "pro-only" (src/prefs/details.ts) and show with Advanced on.
//
// The live feed: the overview reads it once when shown (at most once a minute); polling runs only
// while #/results/progress is shown, and hide() stops it. Until the shell calls show() and hide(),
// the page follows #/results/<section> itself.
import { loadCandidates, loadDistant, loadSuspects, loadValidation } from "../api/client";
import type { CandidatesResponse, DistantResponse, SuspectsResponse, ValidationResponse } from "../api/types";
import type { PageContext, PageHandle } from "../app/modules";
import { mountCommunityWith, type CommunityOptions } from "../community";
import { astronomersFor, loadSnapshot, type Snapshot } from "../community/source";
import { astroFile, type AstroRow } from "../community/stats";
import { createLivePage, errorSentence, loadSkyrun, summarize, type LiveDeps, type LivePage, type SkyRun } from "../live";
import { getDetails, setDetails } from "../prefs/details";
import { createToolsPage } from "../tools";
import { methodsHtml, validationHtml } from "../tools/methods";
import { safeStorage, type KV } from "../vote/model";
import { VOTED_EVENT } from "../vote/store";
import { downloadText, suspectsCsv } from "./format";
import { astronomersCardHtml, foundCardHtml, planetLineHtml, progressCardHtml, sureCardHtml } from "./overview";
import { renderFound, renderSure, syntheticNote } from "./render";
import "./score.css";

export type SectionId = "found" | "sure" | "progress" | "astronomers" | "methods" | "tools";
/** What the page shows: the overview, or one section's page. */
export type ViewId = "overview" | SectionId;

export interface SectionInfo {
  id: SectionId;
  title: string;
  /** One line saying what the section's page holds. */
  gist: string;
  /** Shown only with Advanced on. */
  advanced?: boolean;
}

export const SECTIONS: readonly SectionInfo[] = [
  { id: "found", title: "What we found", gist: "The objects we put up to check, the strongest leads, and the search for a distant planet." },
  { id: "sure", title: "How sure we are", gist: "How the search did on things already known." },
  { id: "progress", title: "Search progress", gist: "How much of the sky along the planets' path has been searched so far." },
  { id: "astronomers", title: "List for astronomers", gist: "What people agreed on: leads for astronomers to check, not discoveries." },
  { id: "methods", title: "Data and methods", gist: "Where the pixels come from and what happens to them before a number reaches this page.", advanced: true },
  { id: "tools", title: "Tools", gist: "Downloads, batch coordinates, the API, an MCP server for AI assistants, and how to cite.", advanced: true },
];

/** The four cards on the overview, in order. */
const CARDS: readonly SectionId[] = ["found", "sure", "progress", "astronomers"];

const isSection = (x: string): x is SectionId => SECTIONS.some((s) => s.id === x);
const info = (id: SectionId) => SECTIONS.find((s) => s.id === id)!;

/** "sure/V5" → { section: "sure", more: "V5" }; anything else → null. */
export function parseResultsParam(param: string | null | undefined): { section: SectionId; more: string | null } | null {
  if (!param) return null;
  const [head, ...rest] = param.split("/");
  if (!isSection(head)) return null;
  let more: string | null = rest.join("/") || null;
  if (more) {
    try {
      more = decodeURIComponent(more);
    } catch {
      /* keep it as written */
    }
  }
  return { section: head, more };
}

/** The section part of a Results hash ("#/results/sure/V5" → "sure/V5"), or null. */
export function resultsParamOf(hash: string): string | null {
  const m = /^#\/(?:results|score)\/(.+)$/.exec(hash);
  return m ? m[1] : null;
}

export interface ResultsDeps {
  /** The four static files (default: /data/*.json through src/api). */
  loadValidation?: (signal: AbortSignal) => Promise<ValidationResponse | null>;
  loadSuspects?: (signal: AbortSignal) => Promise<SuspectsResponse | null>;
  loadDistant?: (signal: AbortSignal) => Promise<DistantResponse | null>;
  loadCandidates?: (signal: AbortSignal) => Promise<CandidatesResponse | null>;
  /** The live feed: its load also gives the overview's progress card. */
  live?: LiveDeps;
  /** The votes: its load also gives the overview's list-for-astronomers card. */
  community?: CommunityOptions;
}

export interface ResultsPage extends PageHandle {
  hide(): void;
  /** Loaded and drawn (tests). */
  ready(): Promise<void>;
}

/** Wire the suspects' "Download CSV" button inside el (null = file not published). */
export function wireSuspectsCsv(el: HTMLElement, sr: SuspectsResponse | null): void {
  el.querySelector<HTMLButtonElement>("[data-csv]")?.addEventListener("click", () => {
    if (!sr) return;
    const day = (sr.generated || "").slice(0, 10) || "latest";
    downloadText(suspectsCsv(sr.suspects), `skyshift-suspects-${day}.csv`);
  });
}

/** Without the shell's context (it mounts Results with the element only): plain navigation. */
function fallbackContext(): PageContext {
  return {
    navigate(hash) {
      location.hash = hash;
    },
    explore(at) {
      const p = new URLSearchParams({ ra: at.ra.toFixed(5), dec: at.dec.toFixed(5) });
      if (at.clock) p.set("clock", at.clock);
      if (at.fov) p.set("fov", String(at.fov));
      if (at.det) p.set("det", String(at.det));
      location.assign(`?${p}#/explore`);
    },
  };
}

const ARROW = `<span aria-hidden="true">→</span>`;

function cardHtml(id: SectionId): string {
  const s = info(id);
  return `<article class="rs-card" data-card="${id}" aria-labelledby="rs-card-${id}-h">
    <h2 class="rs-card-h" id="rs-card-${id}-h">${s.title}</h2>
    <div class="rs-card-body"><p class="rs-card-line">Loading…</p></div>
    <a class="rs-card-more" href="#/results/${id}">More<span class="visually-hidden"> on ${s.title.toLowerCase()}</span> ${ARROW}</a>
  </article>`;
}

function overviewHtml(): string {
  return `<section class="rs-view rs-overview" data-view="overview" aria-labelledby="rs-overview-h">
    <header class="rs-ov-head">
      <h1 class="sc-title rs-h1" id="rs-overview-h" tabindex="-1">Results</h1>
    </header>
    <div class="rs-cards">${CARDS.map(cardHtml).join("")}</div>
    <div class="rs-strips">
      <section class="rs-strip rs-planet-strip" aria-labelledby="rs-strip-h" hidden></section>
      <section class="rs-strip rs-adv-strip pro-only" aria-labelledby="rs-adv-h">
        <h2 class="rs-strip-h" id="rs-adv-h">Data, methods and tools</h2>
        <span class="rs-strip-links"><a class="rs-card-more" href="#/results/methods">Methods ${ARROW}</a>
          <a class="rs-card-more" href="#/results/tools">Tools ${ARROW}</a></span>
      </section>
      <p class="rs-strip rs-more plain-only">Methods and tools: <button type="button" class="rs-more-btn" data-advanced-on>turn on Advanced</button></p>
    </div>
  </section>`;
}

function detailHtml(s: SectionInfo): string {
  return `<section class="rs-view rs-page" data-view="${s.id}" id="rs-${s.id}" aria-labelledby="rs-${s.id}-h" hidden>
    <header class="rs-page-head">
      <a class="rs-back" href="#/results"><span aria-hidden="true">←</span> Results</a>
      <h1 class="sc-title rs-h1" id="rs-${s.id}-h" tabindex="-1">${s.title}</h1>
    </header>
    ${
      s.advanced
        ? `<p class="rs-needs plain-only">Part of Advanced. <button type="button" class="rs-more-btn" data-advanced-on>Turn it on</button></p>`
        : ""
    }
    <div class="rs-body${s.advanced ? " pro-only" : ""}" id="rs-${s.id}-body"></div>
  </section>`;
}

export function createResultsPage(el: HTMLElement, ctx: PageContext, deps: ResultsDeps = {}): ResultsPage {
  el.innerHTML = `<div class="sc rs">
    <div class="rs-synthetic"></div>
    ${overviewHtml()}
    ${SECTIONS.map(detailHtml).join("")}
  </div>`;
  const ctl = new AbortController();
  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const body = (id: SectionId) => $<HTMLElement>(`#rs-${id}-body`);
  const card = (id: SectionId) => $<HTMLElement>(`[data-card="${id}"] .rs-card-body`);

  let view: ViewId = "overview";
  let pageShown = true;
  /** The shell calls show(): it routes #/results/<section>, so the fallback below stands down. */
  let wired = false;
  /** Has a view been shown yet (focus moves to the new title only after the first). */
  let shownOnce = false;
  let v: ValidationResponse | null = null;

  // ---- the files: What we found, How sure we are, and their cards --------------------------------
  body("found").innerHTML = `<p class="sc-loading" role="status">Loading what we found…</p>`;
  body("sure").innerHTML = `<p class="sc-loading" role="status">Loading the checks…</p>`;
  const quiet = <T>(p: Promise<T | null>): Promise<T | null> => p.catch(() => null);
  const loaded = Promise.all([
    quiet((deps.loadValidation ?? loadValidation)(ctl.signal)),
    quiet((deps.loadSuspects ?? loadSuspects)(ctl.signal)),
    quiet((deps.loadDistant ?? loadDistant)(ctl.signal)),
    quiet((deps.loadCandidates ?? loadCandidates)(ctl.signal)),
  ]).then(([val, sr, dist, cands]) => {
    if (ctl.signal.aborted) return;
    v = val;
    $<HTMLElement>(".rs-synthetic").innerHTML = syntheticNote(val, sr, dist, cands);
    card("found").innerHTML = foundCardHtml(cands);
    card("sure").innerHTML = sureCardHtml(val);
    const strip = $<HTMLElement>(".rs-planet-strip");
    strip.innerHTML = planetLineHtml(dist);
    strip.hidden = !strip.innerHTML;
    body("found").innerHTML = renderFound(sr, dist, cands);
    wireSuspectsCsv(body("found"), sr);
    body("sure").innerHTML = renderSure(val, dist);
    const valid = el.querySelector<HTMLElement>(".tl-validation");
    if (valid) valid.innerHTML = validationHtml(val);
    // The part a route asked for may have just been drawn: go there again.
    reanchor();
  });

  // ---- Search progress: the Live page on its own page; one read for the overview's card -----------
  // Plain by default: the big number, the bar, one line and the map; machines, legends, pace and zone ids with Advanced.
  const live: LivePage = createLivePage(body("progress"), ctx, { ...deps.live, embedded: true, plain: true, hashBase: "#/results/progress" });
  let liveRunning = false;
  function updateLive() {
    const want = pageShown && view === "progress";
    if (want && !liveRunning) {
      liveRunning = true;
      live.resume();
    } else if (!want && liveRunning) {
      liveRunning = false;
      live.pause();
    }
  }
  const liveNow = deps.live?.now ?? Date.now;
  const loadRun = deps.live?.load ?? ((signal: AbortSignal) => loadSkyrun(signal));
  let run: SkyRun | null = null;
  let runAt = -Infinity;
  /** The overview's progress card: read the feed once, at most once a minute; redraw "Updated … ago" each time. */
  function refreshProgressCard() {
    if (run) card("progress").innerHTML = progressCardHtml(summarize(run, liveNow()));
    if (Date.now() - runAt < 60e3) return;
    runAt = Date.now();
    loadRun(ctl.signal).then(
      (r) => {
        if (ctl.signal.aborted) return;
        run = r;
        card("progress").innerHTML = progressCardHtml(summarize(r, liveNow()));
      },
      (e) => {
        if (ctl.signal.aborted || run) return;
        card("progress").innerHTML = progressCardHtml(null, errorSentence(e, null, liveNow()));
      },
    );
  }

  // ---- List for astronomers: the Community page's list on its own page; its count on a card -------
  const kv: KV = deps.community?.kv ?? safeStorage();
  const loadVotes = deps.community?.load ?? ((k: KV) => loadSnapshot({ kv: k }));
  /** One read of the votes shared by the card and the list's page; read again after a minute or a vote. */
  let votes: { p: Promise<Snapshot>; at: number } | null = null;
  const sharedVotes = (k: KV): Promise<Snapshot> => {
    if (!votes || Date.now() - votes.at > 60e3) votes = { p: loadVotes(k), at: Date.now() };
    return votes.p;
  };
  const onVoted = () => (votes = null);
  addEventListener(VOTED_EVENT, onVoted);
  const community = mountCommunityWith(body("astronomers"), ctx, { ...deps.community, kv, load: sharedVotes, variant: "astronomers" });
  let astro: { snap: Snapshot; rows: AstroRow[] } | null = null;
  let astroLoading: Promise<void> | null = null;
  function refreshAstronomersCard(): Promise<void> {
    astroLoading ??= sharedVotes(kv)
      .then(async (snap) => {
        const rows = await astronomersFor(snap);
        if (ctl.signal.aborted) return;
        astro = { snap, rows };
        card("astronomers").innerHTML = astronomersCardHtml(rows);
      })
      .catch(() => {
        if (!ctl.signal.aborted && !astro) card("astronomers").innerHTML = astronomersCardHtml(null, "Couldn't count the votes. Reload the page to try again.");
      })
      .finally(() => (astroLoading = null));
    return astroLoading;
  }
  function downloadAstronomers() {
    if (!astro) return;
    const meta = { generated: new Date().toISOString(), votes_from: astro.snap.mode, data_release: astro.snap.dataRelease };
    const file = astroFile("csv", astro.rows, meta, location.href);
    downloadText(file.text, file.name, file.type);
  }

  // ---- With Advanced on: Data and methods, Tools (drawn the first time their page opens) ------------
  let tools: PageHandle | null = null;
  function mountMethods() {
    if (body("methods").firstChild) return;
    body("methods").innerHTML = `<div class="tl-sec rs-methods">${methodsHtml()}</div>`;
    if (v) $<HTMLElement>(".tl-validation").innerHTML = validationHtml(v);
  }
  function mountTools() {
    if (tools) return;
    tools = createToolsPage(body("tools"), ctx, { embedded: true, hashBase: "#/results/tools" });
    // reads its static files
    tools.show(null);
  }

  // ---- views and parts ---------------------------------------------------------------------------
  /** One check or part of a section (V5 under sure, distant under found, batch under tools), if drawn. */
  function partOf(section: SectionId, more: string | null): HTMLElement | null {
    const id = (more ?? "").replace(/[^\w-]/g, "");
    if (!id) return null;
    if (section === "sure") return el.querySelector<HTMLElement>(`#sc-${id}`);
    if (section === "found") {
      // "planet": the plain line on the distant-planet search, or its full account with Advanced on.
      if (id === "planet") return el.querySelector<HTMLElement>(getDetails() ? "#sc-distant-h" : "#rs-planet-h");
      return el.querySelector<HTMLElement>(`#sc-${id}-h`);
    }
    if (section === "tools") return el.querySelector<HTMLElement>(`#tl-${id}-h`);
    return null;
  }

  /** A section, or a part of one, that only Advanced shows (the checks by code, the full distant-planet search). */
  function needsAdvanced(section: SectionId, more: string | null): boolean {
    if (info(section).advanced) return true;
    return !!partOf(section, more)?.closest(".pro-only");
  }

  /** Show one view alone; the shell's header keeps Results highlighted for all of them. */
  function setView(next: ViewId) {
    const changed = next !== view;
    view = next;
    el.querySelectorAll<HTMLElement>(".rs-view").forEach((n) => (n.hidden = n.dataset.view !== next));
    if (next === "overview") {
      refreshProgressCard();
      void refreshAstronomersCard();
    }
    // the list's page reads the votes when shown (the card's read is shared for a minute)
    if (next === "astronomers") community.show(null);
    if (next === "methods") mountMethods();
    if (next === "tools") mountTools();
    if (wired) document.title = `${next === "overview" ? "Results" : info(next).title}: SkyShift`;
    updateLive();
    return changed;
  }

  /** Bring one part of the shown page to the top of the screen, and focus it or the page's title. */
  function scrollToPart(section: SectionId, more: string | null, focus: boolean) {
    const target = partOf(section, more);
    if (target) target.scrollIntoView?.({ block: "start" });
    if (!focus) return;
    // A Tools part's heading takes focus itself (tabindex -1); otherwise the page's title does.
    (section === "tools" && target ? target : $<HTMLElement>(`#rs-${section}-h`)).focus({ preventScroll: true });
  }

  // A part's page fills in after the route (the files, the live feed, Tools): for a few seconds keep
  // the part in place as the content above it grows, unless the person scrolls or acts first.
  let settle: { section: SectionId; more: string; until: number } | null = null;
  function reanchor() {
    if (!settle) return;
    if (Date.now() >= settle.until || view !== settle.section) {
      settle = null;
      return;
    }
    // A route to a part that only Advanced shows, drawn after the route was followed: show it.
    if (!getDetails() && needsAdvanced(settle.section, settle.more)) setDetails(true);
    scrollToPart(settle.section, settle.more, false);
  }
  const stopSettling = () => (settle = null);
  const INTENT = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
  for (const t of INTENT) addEventListener(t, stopSettling, { capture: true, passive: true });
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => reanchor()) : null;
  ro?.observe(el.firstElementChild!);

  /** Show the overview, or a section's page (Advanced first, if it needs it) and one part of it. */
  function go(p: { section: SectionId; more: string | null } | null) {
    if (!p) {
      const changed = setView("overview");
      if (changed && shownOnce) $<HTMLElement>("#rs-overview-h").focus({ preventScroll: true });
      shownOnce = true;
      settle = null;
      return;
    }
    const { section, more } = p;
    if (!getDetails() && needsAdvanced(section, more)) setDetails(true);
    const changed = setView(section);
    if (section === "progress") live.show(more);
    scrollToPart(section, more, shownOnce && (changed || !!more));
    shownOnce = true;
    settle = more ? { section, more, until: Date.now() + 4000 } : null;
  }

  // ---- events ------------------------------------------------------------------------------------
  el.addEventListener("click", (ev) => {
    const t = ev.target as Element;
    if (t.closest("[data-astro-csv]")) return downloadAstronomers();
    const on = t.closest<HTMLElement>("[data-advanced-on]");
    if (on) {
      setDetails(true);
      // From inside a page ("See every check"): its content changes, so bring its top back into view.
      const page = on.closest<HTMLElement>(".rs-page");
      if (page) page.scrollIntoView?.({ block: "start" });
      return;
    }
    // A link to where the address already points: no hashchange, so go there here.
    const a = t.closest<HTMLAnchorElement>('a[href^="#/results"]');
    const me = ev as MouseEvent;
    if (a && !ev.defaultPrevented && location.hash === a.getAttribute("href") && me.button === 0 && !me.metaKey && !me.ctrlKey && !me.shiftKey && !me.altKey) {
      ev.preventDefault();
      go(parseResultsParam(resultsParamOf(a.getAttribute("href")!)));
    }
  });

  // Until the shell calls show(param): follow #/results/<section> here, after the shell's own scroll.
  const onHash = () => {
    if (wired) return;
    if (!/^#\/(?:results|score)(?:\/|$)/.test(location.hash) && location.hash) return;
    const p = parseResultsParam(resultsParamOf(location.hash));
    setTimeout(() => !wired && go(p), 0);
  };
  addEventListener("hashchange", onHash);
  onHash();

  return {
    show(param) {
      wired = true;
      pageShown = true;
      const p = parseResultsParam(param);
      // The page's view changes now; the shell scrolls to the top after show(), so a part waits for that.
      if (!p || !p.more) go(p);
      else {
        if (!getDetails() && needsAdvanced(p.section, p.more)) setDetails(true);
        setView(p.section);
        setTimeout(() => go(p), 0);
      }
    },
    hide() {
      pageShown = false;
      settle = null;
      updateLive();
    },
    destroy() {
      ctl.abort();
      ro?.disconnect();
      for (const t of INTENT) removeEventListener(t, stopSettling, { capture: true });
      removeEventListener("hashchange", onHash);
      removeEventListener(VOTED_EVENT, onVoted);
      live.destroy();
      community.destroy();
      tools?.destroy();
      el.innerHTML = "";
    },
    ready: () => loaded.then(() => Promise.all([community.ready(), astroLoading ?? Promise.resolve()])).then(() => undefined),
  };
}

/** What the shell mounts for #/results: today with the element only; pass ctx once it is wired. */
export const mountResults = (el: HTMLElement, ctx?: PageContext, deps?: ResultsDeps): ResultsPage =>
  createResultsPage(el, ctx ?? fallbackContext(), deps);
