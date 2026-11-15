// To check (#/candidates, was "Discover"): what the pipeline found, filtered and sorted by anyone,
// on a list. Every filter lives in the URL (#/candidates/kind=…&sort=…); the filter model itself
// is pure, in ./filters.ts.
// "Check them individually" (and a one=1 link, e.g. #/candidates/kind=mover&one=1, or the old #/review)
// opens the Viewer on the object in this list that needs votes most; its Previous and Next walk the list. The map view (map=1, e.g.
// #/candidates/kind=mover&map=1, opened from the toolbar's "See them on the sky") is a page of its
// own: the same interactive sky map (./skymap.ts) shown beside the intro, with more room.
// Downloads: this list, and the list for astronomers (src/community, the Community page's own list and rule).
// What's worth checking: the list sets aside moving stars Gaia already knows, fast movers whose motion
// isn't significant and grade C fast movers (./filters.ts setAsideOf). A line under the toolbar names
// each group with its count, one click from the list (show=… in the URL), and "Show all" adds them all.
// DESIGN.md: "Product", "Pages", "Candidates", "Smart filters", "Votes and consensus".
import "./discover.css";
import { loadCandidates } from "../api/client";
import { GRADE_TEXT, failedGates, gatesPhrase, gradeLabel, gradeOf, isGate, votable } from "../api/grades";
import type { Candidate, CandidatesResponse } from "../api/types";
import { DETECTORS, formatMjd, formatRaDec } from "../app/astro";
import type { MountPage } from "../app/modules";
import { queueOrder } from "../review/model";
import {rememberDiscover,trackDiscover} from '../review/nav';
import { isEmbed } from "../app/state";
import { getDetails, onDetails } from "../prefs/details";
import { astronomersFor, loadSnapshot } from "../community/source";
import { CONSENSUS_PCT, astroFile } from "../community/stats";
import { ANSWER_TEXT, CONSENSUS, safeStorage, type Answer, type Counts } from "../vote/model";
import { download, toCsv, toJson } from "./export";
import { arcsecPerHour, arcsecPerYr, flux, fromSlider, num, plural, rangeText, rangeValue, roundNice, SLIDER_MAX, toSlider } from "./format";
import {
  PLANE_B,
  PRESETS,
  RANGES,
  SET_ASIDE,
  SORTS,
  activeCount,
  activePreset,
  anyGraded,
  bestRelaxation,
  countBy,
  defaultState,
  domainOf,
  filterRows,
  FACET_VALUES,
  candidatesHash,
  isOneAtATime,
  isMapView,
  leading,
  routeQuery,
  isActiveRange,
  isCautionCode,
  isCautioned,
  isDoubtCode,
  isDoubted,
  presetState,
  queryToState,
  sameState,
  setAsideValues,
  setsAside,
  significance,
  sortRows,
  toRows,
  withoutTest,
  type FilterState,
  type Filtered,
  type ListKey,
  type Range,
  type RangeKey,
  type Row,
  type SetAside,
  type SortKey,
  type TestKey,
  type Tri,
} from "./filters";
import { glyphSvg, mapGlyph, mountSkyMap, type SkyMapHandle } from "./skymap";
import { GRADE_PLAIN, GRADE_VALUES, KIND_TEXT, KINDS, REVIEW_TEXT, SET_ASIDE_TEXT, flagText, gradeFacetText, labelText, reasonText, type ReviewState } from "./text";
import { thumbLoader } from "./thumb";
import { onVoted, openVoteReader, type VoteReader } from "./votes";

const html = String.raw;
const PAGE = 50;
const esc = (s: string) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const ANSWERS: Answer[] = ["mover", "changing", "junk", "unsure"];
const TONE_ORDER = { support: 0, doubt: 1, caution: 2 } as const;
const TONE_MARK = {
  support: "",
  doubt: '<span class="doubt-ring" aria-hidden="true"></span>',
  caution: '<span class="caution-mark" aria-hidden="true"></span>',
} as const;

type VoteStatus = "idle" | "loading" | "ok" | "local" | "error";

export const mountDiscover: MountPage = (el, ctx) => {
  trackDiscover();
  const embed = isEmbed();
  el.innerHTML = html`
    <div class="dv">
      <section class="dv-intro" aria-labelledby="dv-title" ${embed ? "hidden" : ""}>
        <h1 id="dv-title" class="dv-title">Objects to check in NASA's SPHEREx sky.</h1>
        <p class="dv-sub">Spots in NASA's SPHEREx pictures that known stars and asteroids don't explain. Many are just camera glitches, so people need to look: blink each one, say what you see, and the best go to astronomers.</p>
        <p class="dv-note" data-note hidden></p>
        <div class="dv-actions">
          <button type="button" class="btn btn-primary" data-one-on
            title="Open the Viewer on the object in this list that needs votes most, then step through the list with Previous and Next">Check them individually</button>
          <a class="btn btn-ghost" href="#/about">How it works</a>
        </div>
      </section>

      <aside class="dv-map" aria-label="Objects on the sky" hidden></aside>

      <div class="dv-presets" role="group" aria-label="Presets" hidden>
        <div class="seg dv-seg">
          ${PRESETS.map((p) => html`<button type="button" data-preset="${p.id}" aria-pressed="false" title="${esc(p.hint)}">${esc(p.label)}</button>`).join("")}
        </div>
      </div>

      <section class="dv-sky" aria-labelledby="dv-sky-title" hidden>
        <a class="dv-sky-back" href="#/candidates" data-sky-list><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>Back to the list</a>
        <h1 id="dv-sky-title" class="dv-title" tabindex="-1">Objects to check on the sky</h1>
        <div class="dv-sky-what">
          <p class="dv-sky-count" aria-live="polite">Loading the list…</p>
          <ul class="dv-sky-chips" aria-label="Filters in use" hidden></ul>
          <a class="btn-link" href="#/candidates" data-sky-list data-sky-change hidden>Change filters</a>
        </div>
        <div data-skymap></div>
      </section>

      <section class="dv-filters" aria-labelledby="dv-filters-title" hidden>
        <div class="fx-head">
          <h2 id="dv-filters-title" class="dv-h2">Filters</h2>
          <button type="button" class="btn btn-ghost btn-sm" data-clear>Clear all</button>
          <button type="button" class="btn btn-sm fx-close" data-close-sheet>Done</button>
        </div>
        <div class="fx-body"></div>
        <div class="fx-sheet-foot"><button type="button" class="btn btn-primary" data-close-sheet data-show-n>Show the list</button></div>
      </section>

      <section class="dv-results" aria-labelledby="dv-count">
        <div class="dv-toolbar" hidden>
          <h2 id="dv-count" class="dv-count" aria-live="polite" tabindex="-1"></h2>
          <button type="button" class="btn btn-sm dv-filters-btn" aria-haspopup="dialog">Filters</button>
          <label class="dv-sort"><span>Sort by</span>
            <select class="dv-select">${SORTS.map((s) => html`<option value="${s.key}"${s.hint ? ` title="${esc(s.hint)}"` : ""}>${s.label}</option>`).join("")}</select>
          </label>
          <details class="dv-dl dv-export-top">
            <summary class="btn btn-sm">Download</summary>
            <div class="dv-dl-panel">${exportHtml("top")}</div>
          </details>
          <a class="btn btn-sm dv-skylink" href="#/candidates/map=1" title="These objects on a large sky map">${mapGlyph()}See them on the sky</a>
        </div>
        <div class="dv-scope" hidden></div>
        <p class="dv-real" hidden>These are the objects the community agreed move or change: at least ${CONSENSUS.minVotes} votes with ${CONSENSUS_PCT}% agreeing.
          They go to astronomers as leads to check, not discoveries.
          <button type="button" class="btn-link" data-astro="csv">Download the list for astronomers (CSV)</button>
          <span class="dv-dl-status" data-dl-status role="status" aria-live="polite"></span></p>
        <ul class="dv-active" aria-label="Filters in use" hidden></ul>
        <div class="dv-cols" aria-hidden="true" hidden><span>Object</span><span>Measured</span><span>Votes</span></div>
        <ol class="dv-list" aria-labelledby="dv-count"></ol>
        <div class="dv-more" hidden><p class="dv-more-n"></p><button type="button" class="btn" data-more>Show more</button></div>
        <div class="dv-dl-foot dv-export-foot" hidden>${exportHtml("foot")}</div>
        <div class="dv-state" role="status"><p class="dv-state-title">Loading the list…</p></div>
      </section>
    </div>
  `;

  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const noteEl = $<HTMLParagraphElement>("[data-note]");
  const presetsEl = $<HTMLDivElement>(".dv-presets");
  const skyEl = $<HTMLElement>(".dv-sky");
  const mapPreview = $<HTMLElement>(".dv-map");
  const skyLink = $<HTMLAnchorElement>(".dv-skylink");
  const filtersEl = $<HTMLElement>(".dv-filters");
  const fxBody = $<HTMLDivElement>(".fx-body");
  const toolbar = $<HTMLDivElement>(".dv-toolbar");
  const countEl = $<HTMLHeadingElement>(".dv-count");
  const scopeEl = $<HTMLDivElement>(".dv-scope");
  const filtersBtn = $<HTMLButtonElement>(".dv-filters-btn");
  const sortSel = $<HTMLSelectElement>(".dv-select");
  const activeEl = $<HTMLUListElement>(".dv-active");
  const colsEl = $<HTMLDivElement>(".dv-cols");
  const list = $<HTMLOListElement>(".dv-list");
  const moreEl = $<HTMLDivElement>(".dv-more");
  const stateEl = $<HTMLDivElement>(".dv-state");
  const rootEl = $<HTMLDivElement>(".dv");
  const oneOnBtn = $<HTMLButtonElement>("[data-one-on]");
  const realEl = $<HTMLParagraphElement>(".dv-real");
  const dlEl = $<HTMLDetailsElement>(".dv-dl");

  let data: CandidatesResponse | null = null;
  let sample = false;
  let loading = true;
  let byId = new Map<string, Candidate>();
  let counts = new Map<string, Counts>();
  let reader: VoteReader | null = null;
  let voteStatus: VoteStatus = "idle";
  let votesAt = 0;
  let rows: Row[] = [];
  let f: Filtered = filterRows([], defaultState());
  let sorted: Row[] = [];
  let state: FilterState = queryToState(routeQuery(location.hash));
  /** A one=1 link: open the Viewer on the first object to check once the list and its votes are in. */
  let one = isOneAtATime(location.hash);
  /** The map view: the filtered set on a large sky map, a page of its own (map=1 in the URL; it wins over one=1). */
  let mapView = isMapView(location.hash);
  if (mapView) one = false;
  /** The page is on screen (between the shell's show and hide). */
  let onScreen = false;
  let shown = PAGE;
  const updates: (() => void)[] = [];
  const open = (id: string) => ctx.navigate(`#/c/${encodeURIComponent(id)}`);

  /** One map shared by the list's preview and the expanded view; keep its pan, zoom and selection. */
  let map: SkyMapHandle | null = null;
  const mapEl = document.createElement("div");
  let mapHost: HTMLElement | null = null;
  let drawn: Row[] | null = null;
  const thumbs = thumbLoader((id) => byId.get(id));
  // "More filters" shows with Advanced (the switch in the page header).
  const offDetails = onDetails((on) => {
    const more = fxBody.querySelector<HTMLElement>(".fx-more");
    if (more) more.hidden = !on;
  });

  // ---- data -------------------------------------------------------------------------------------

  async function load() {
    let resp: CandidatesResponse | null = null;
    try {
      resp = await loadCandidates();
    } catch {
      resp = null;
    }
    if (!resp && import.meta.env.DEV) {
      // Dev only, until the exporter writes /data/candidates.json: a real pilot sample, labelled.
      try {
        const mod = await import("../../../fixtures/api/candidates.json");
        resp = mod.default as unknown as CandidatesResponse;
        sample = true;
      } catch {
        resp = null;
      }
    }
    loading = false;
    data = resp && Array.isArray(resp.candidates) ? resp : null;
    if (!data) {
      renderNoData();
      renderOne();
      return;
    }
    byId = new Map(data.candidates.map((c) => [c.id, c]));
    rows = toRows(data.candidates, counts);
    for (const n of [presetsEl, filtersEl, toolbar]) n.hidden = false;
    buildFilters();
    apply();
    renderOne();
    loadVotes();
  }

  async function loadVotes() {
    if (!data) return;
    voteStatus = "loading";
    renderIntro();
    try {
      reader ??= await openVoteReader();
      counts = await reader.counts(data.candidates.map((c) => c.id));
      voteStatus = reader.mode === "local" ? "local" : "ok";
    } catch {
      voteStatus = "error";
    }
    votesAt = Date.now();
    rows = toRows(data.candidates, counts, (id) => reader?.votedOn(id) ?? false);
    apply();
  }

  // ---- state ------------------------------------------------------------------------------------

  /** Keep the address in step without a hashchange (which would re-route and scroll to the top).
   *  push: a new history entry (turning "Check them individually" on), so the browser's Back
   *  returns to the list. */
  function writeUrl(push = false) {
    if (!/^#\/?(?:(?:analyze\/)?candidates|discover)\b/.test(location.hash)) return;
    const hash = candidatesHash(state, { one, map: mapView });
    const url = `${location.pathname}${location.search}${hash}`;
    if (push && location.hash !== hash) history.pushState(null, "", url);
    else if (location.hash !== hash) history.replaceState(history.state, "", url);
    rememberDiscover();
  }

  /** "Clear all": no filters, the same order, and the same set-aside groups shown (not a filter). */
  const cleared = (): FilterState => ({ ...defaultState(), sort: state.sort, show: [...state.show] });

  function setState(next: FilterState, opts: { keepPage?: boolean } = {}) {
    state = next;
    if (!opts.keepPage) shown = PAGE;
    writeUrl();
    apply();
  }

  function apply() {
    f = filterRows(rows, state);
    sorted = sortRows(f.rows, state.sort);
    renderIntro();
    if (!data) return;
    for (const u of updates) u();
    renderPresets();
    renderToolbar();
    renderScope();
    renderActive();
    renderReal();
    renderList();
    renderSky();
    syncOne();
  }

  // ---- check them individually ------------------------------------------------------------------
  // The Viewer is where people check and vote. "Check them individually" opens it on the object in
  // this list that needs votes most (what has no consensus, grade A first, fewest votes); the
  // Viewer's Previous and Next walk the same list, and its Back returns here. A one=1 link (shared,
  // or the old #/review) does the same once the votes are in, and leaves the plain list behind it.

  /** The object to start on: the first that still needs your vote, else the first that takes votes, else the first. */
  function firstToCheck(): Candidate | null {
    const cands = sorted.map((r) => r.c);
    const fresh = cands.filter((c) => !(reader?.votedOn(c.id) ?? false));
    return queueOrder(fresh, counts)[0] ?? cands.find((c) => votable(c)) ?? cands[0] ?? null;
  }

  function startChecking() {
    const c = firstToCheck();
    if (one) {
      one = false;
      writeUrl(); // the list's own URL, without one=1, is what the Viewer's Back returns to
    }
    if (c) open(c.id);
  }

  /** A one=1 link: wait for the list and its vote counts, then go. */
  function syncOne() {
    if (!one || !onScreen || loading || !data || voteStatus === "idle" || voteStatus === "loading") return;
    startChecking();
  }

  function renderOne() {
    rootEl.dataset.view = mapView ? "map" : "list";
    renderSky();
  }

  oneOnBtn.addEventListener("click", () => startChecking());

  // ---- the community's list ---------------------------------------------------------------------

  /** "Community says real" on: say what the list is and offer the list for astronomers. */
  function renderReal() {
    realEl.hidden = activePreset(state)?.id !== "community-real" || sorted.length === 0;
  }

  // ---- intro ------------------------------------------------------------------------------------

  /** Only what warns of a real problem: sample data, the vote server's notice, vote counts out of reach.
   *  The list's size is in the toolbar; grades are on each row and in the filters. */
  function renderIntro() {
    const notes: string[] = [];
    if (sample) notes.push("Sample data from the pilot zone, until the full list is published.");
    if (reader?.notice) notes.push(reader.notice);
    if (voteStatus === "error") notes.push("Vote counts can't be reached right now; everything else works.");
    noteEl.hidden = !notes.length;
    noteEl.textContent = notes.join(" ");
  }

  // ---- presets ----------------------------------------------------------------------------------

  presetsEl.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("[data-preset]");
    if (!b) return;
    const p = PRESETS.find((x) => x.id === b.dataset.preset);
    if (!p) return;
    const on = activePreset(state)?.id === p.id;
    setState(on ? defaultState() : presetState(p));
  });

  function renderPresets() {
    const on = activePreset(state)?.id ?? null;
    presetsEl.querySelectorAll<HTMLButtonElement>("[data-preset]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.preset === on)));
  }

  // ---- filters panel ----------------------------------------------------------------------------

  function buildFilters() {
    if (!data) return;
    updates.length = 0;
    fxBody.innerHTML = "";
    const cands = data.candidates;

    // In view for everyone: what it is, its grade, votes, possible camera glitches.
    const main = document.createElement("div");
    main.className = "fx-group fx-main";
    main.innerHTML = html`<div class="fx-group-body"></div>`;
    fxBody.append(main);
    const g1 = main.firstElementChild as HTMLElement;
    checkList(g1, "kind", "Kind", () =>
      KINDS.map((k) => ({ v: k, label: KIND_TEXT[k].name, title: `${k}: ${KIND_TEXT[k].gist}`, glyph: glyphSvg(k, 12) })),
    );
    const graded = anyGraded(cands);
    // Only when the list carries grades (or a link asks for one): an older export has none.
    checkList(g1, "grade", "Discovery type", () => (graded ? [...GRADE_VALUES] : state.grade).map((v) => ({ v, ...gradeFacetText(v) })), { hideWhenEmpty: true });
    const votes = document.createElement("div");
    votes.className = "fx-set";
    g1.append(votes);
    checkList(votes, "review", "Votes", () => (["none", "some", "consensus"] as ReviewState[]).map((v) => ({ v, label: REVIEW_TEXT[v], title: reviewTitle(v) })));
    toggle(votes, "hideMine", "mine", "Hide ones I voted on", (r) => r.mine, "From this browser's memory; nothing leaves your device.");
    tri(g1, "cautions", "Possible camera glitches", "Odd pixels, ghost reflections or halos near it, or the same spot seen days apart. Many of these are junk.", isCautioned, ["With", "Without"]);

    // The rest, for Advanced only, in one closed section. A link's filter still applies with it
    // hidden: its chip above the list shows it and removes it. Left out: the verdict (the preset
    // "Community says real" and "Community verdict" above cover it) and the |b| slider (the plane
    // switch sets the same range).
    const more = group("More filters", ["label", "reason", "doubts", "gaia", "catwise", "known", "score", "sig", "pm", "rate", "snr", "flux", "b", "det", "zone"], false);
    const moreBox = more.parentElement!;
    moreBox.classList.add("fx-more", "pro-only");
    moreBox.hidden = !getDetails();
    sub(more, "Evidence");
    chipList(more, "label", "Spectral labels", () => uniq([...cands.flatMap((c) => c.labels), ...state.label]).map((v) => ({ v, label: labelText(v), title: v })), "No spectral labels in this set yet.");
    chipList(
      more,
      "reason",
      "Why it's listed",
      () =>
        uniq([...cands.flatMap((c) => c.reasons), ...state.reason])
          .sort((a, b) => TONE_ORDER[reasonText(a).tone] - TONE_ORDER[reasonText(b).tone])
          .map((v) => ({ v, label: reasonText(v).label, title: `${v}: ${reasonText(v).detail}`, tone: reasonText(v).tone })),
      "No reason codes in this set.",
    );
    toggle(more, "hideDoubts", "doubts", "Hide doubtful ones", (r) => isDoubted(r.c), "Any reason marked with a ring: a catalogue disagrees, a poor fit, few detections, or chance pairs.");
    tri(more, "gaia", "In Gaia", "gaia_match", (c) => c.gaia_match);
    tri(more, "catwise", "In CatWISE", "catwise_match", (c) => c.catwise_match);
    rangeCtl(more, "known");
    sub(more, "Measurements");
    for (const k of ["score", "sig", "pm", "rate", "snr", "flux"] as RangeKey[]) rangeCtl(more, k);
    sub(more, "Sky and data");
    planeToggle(more);
    checkList(more, "det", "Detector", () =>
      ([1, 2, 3, 4, 5, 6] as const).map((d) => ({
        v: String(d),
        label: `D${d}, ${DETECTORS[d].min}–${DETECTORS[d].max} µm`,
        title: `det ${d}: detector ${d} sees ${DETECTORS[d].min}–${DETECTORS[d].max} µm`,
        glyph: `<span class="det-dot det-${d}" aria-hidden="true"></span>`,
      })),
    );
    checkList(more, "zone", "Zone", () => uniq([...data!.zones, ...cands.map((c) => c.zone), ...state.zone]).map((v) => ({ v, label: `${v[0]?.toUpperCase()}${v.slice(1)} zone`, title: `zone: ${v}` })));
  }

  /** A small heading inside "More filters". */
  function sub(parent: HTMLElement, title: string) {
    const h = document.createElement("h3");
    h.className = "fx-sub";
    h.textContent = title;
    parent.append(h);
  }

  function group(title: string, keys: TestKey[], openByDefault = true): HTMLElement {
    const d = document.createElement("details");
    d.className = "fx-group";
    d.open = openByDefault || keys.some((k) => isOn(k));
    d.innerHTML = html`<summary><span class="fx-group-title">${esc(title)}</span><span class="fx-badge" hidden></span></summary><div class="fx-group-body"></div>`;
    fxBody.append(d);
    const badge = d.querySelector<HTMLSpanElement>(".fx-badge")!;
    updates.push(() => {
      const n = keys.filter(isOn).length;
      badge.hidden = n === 0;
      badge.textContent = `${n} on`;
      if (n && !d.open) d.open = true;
    });
    return d.querySelector<HTMLElement>(".fx-group-body")!;
  }

  function isOn(k: TestKey): boolean {
    if (k === "gaia" || k === "catwise") return state[k] !== "any";
    if (k === "doubts") return state.hideDoubts;
    if (k === "cautions") return state.cautions !== "any";
    if (k === "mine") return state.hideMine;
    if (k in RANGES) return isActiveRange(state.ranges[k as RangeKey]);
    return state[k as ListKey].length > 0;
  }

  interface Opt {
    v: string;
    label: string;
    title?: string;
    glyph?: string;
    tone?: "support" | "doubt" | "caution";
  }

  function checkList(parent: HTMLElement, key: ListKey, title: string, options: () => Opt[], o: { hideWhenEmpty?: boolean } = {}) {
    const fs = document.createElement("fieldset");
    fs.className = "fx";
    fs.dataset.facet = key;
    parent.append(fs);
    let sig = "";
    fs.addEventListener("change", () => {
      const vals = [...fs.querySelectorAll<HTMLInputElement>("input:checked")].map((i) => i.value);
      setState({ ...state, [key]: vals });
    });
    updates.push(() => {
      const opts = options();
      const cnt = countBy(f.without(key), FACET_VALUES[key]);
      const anyCount = opts.some((x) => (cnt.get(x.v) ?? 0) > 0 || state[key].includes(x.v));
      const allCount = countBy(rows, FACET_VALUES[key]);
      const noneInData = opts.every((x) => !allCount.get(x.v)) && !state[key].length;
      fs.hidden = noneInData && !!o.hideWhenEmpty;
      const next = opts.map((x) => x.v).join("|");
      if (next !== sig) {
        sig = next;
        fs.innerHTML =
          html`<legend class="fx-title">${esc(title)}</legend>` +
          opts
            .map(
              (x) => html`<label class="fx-opt" title="${esc(x.title ?? x.v)}"><input type="checkbox" value="${esc(x.v)}" />${x.glyph ?? ""}<span class="fx-name">${esc(x.label)}</span><span class="fx-n"></span></label>`,
            )
            .join("");
      }
      fs.querySelectorAll<HTMLLabelElement>(".fx-opt").forEach((lab) => {
        const input = lab.querySelector("input")!;
        const n = cnt.get(input.value) ?? 0;
        input.checked = state[key].includes(input.value);
        lab.querySelector(".fx-n")!.textContent = n.toLocaleString("en-GB");
        lab.classList.toggle("is-zero", n === 0 && !input.checked);
      });
      void anyCount;
    });
  }

  function chipList(parent: HTMLElement, key: "label" | "reason", title: string, options: () => Opt[], emptyText: string) {
    const box = document.createElement("div");
    box.className = "fx";
    parent.append(box);
    let sig = "";
    box.addEventListener("click", (ev) => {
      const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-v]");
      if (!b) return;
      const v = b.dataset.v!;
      const cur = state[key];
      setState({ ...state, [key]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] });
      box.querySelector<HTMLButtonElement>(`button[data-v="${cssEsc(v)}"]`)?.focus();
    });
    updates.push(() => {
      const opts = options();
      const next = opts.map((x) => x.v).join("|");
      if (next !== sig) {
        sig = next;
        const titleId = `fx-${key}-t`;
        box.innerHTML =
          html`<p class="fx-title" id="${titleId}">${esc(title)}</p>` +
          (opts.length
            ? html`<div class="fx-chips" role="group" aria-labelledby="${titleId}">${opts
                .map(
                  (x) =>
                    html`<button type="button" class="fchip${x.tone && x.tone !== "support" ? ` is-${x.tone}` : ""}" data-v="${esc(x.v)}" aria-pressed="false" title="${esc(x.title ?? x.v)}">${TONE_MARK[x.tone ?? "support"]}<span>${esc(x.label)}</span><span class="fx-n"></span></button>`,
                )
                .join("")}</div>`
            : html`<p class="fx-empty">${esc(emptyText)}</p>`);
      }
      const cnt = countBy(f.without(key), FACET_VALUES[key]);
      box.querySelectorAll<HTMLButtonElement>("button[data-v]").forEach((b) => {
        const v = b.dataset.v!;
        const n = cnt.get(v) ?? 0;
        const on = state[key].includes(v);
        b.setAttribute("aria-pressed", String(on));
        b.querySelector(".fx-n")!.textContent = n.toLocaleString("en-GB");
        b.classList.toggle("is-zero", n === 0 && !on);
      });
    });
  }

  function toggle(parent: HTMLElement, field: "hideDoubts" | "hideMine", key: TestKey, label: string, hides: (r: Row) => boolean, hint: string) {
    const lab = document.createElement("label");
    lab.className = "fx-toggle";
    lab.title = hint;
    lab.innerHTML = html`<input type="checkbox" /><span class="fx-name">${esc(label)}</span><span class="fx-n"></span>`;
    parent.append(lab);
    const input = lab.querySelector("input")!;
    input.addEventListener("change", () => setState({ ...state, [field]: input.checked }));
    updates.push(() => {
      input.checked = state[field];
      const n = f.without(key).filter(hides).length;
      lab.querySelector(".fx-n")!.textContent = hiddenText(n, input.checked);
      lab.classList.toggle("is-zero", n === 0 && !input.checked);
    });
  }

  function planeToggle(parent: HTMLElement) {
    const lab = document.createElement("label");
    lab.className = "fx-toggle";
    lab.title = "Stars crowd in the Milky Way plane (galactic latitude |b| below 10°), where false matches are more likely.";
    lab.innerHTML = html`<input type="checkbox" /><span class="fx-name">Hide the Milky Way plane (|b| &lt; ${PLANE_B}°)</span><span class="fx-n"></span>`;
    parent.append(lab);
    const input = lab.querySelector("input")!;
    input.addEventListener("change", () => {
      const ranges = { ...state.ranges };
      const cur = ranges.b;
      if (input.checked) ranges.b = { min: Math.max(PLANE_B, cur?.min ?? 0), max: cur?.max ?? null };
      else if (cur && cur.max != null) ranges.b = { min: null, max: cur.max };
      else delete ranges.b;
      setState({ ...state, ranges });
    });
    updates.push(() => {
      const b = state.ranges.b;
      input.checked = !!b && b.min != null && b.min >= PLANE_B;
      const n = f.without("b").filter((r) => Math.abs(r.c.galactic_b) < PLANE_B).length;
      lab.querySelector(".fx-n")!.textContent = hiddenText(n, input.checked);
      lab.classList.toggle("is-zero", n === 0 && !input.checked);
    });
  }

  function tri(parent: HTMLElement, key: "gaia" | "catwise" | "cautions", label: string, hint: string, val: (c: Candidate) => boolean, [yesText, noText] = ["Yes", "No"]) {
    const box = document.createElement("div");
    box.className = "fx-tri";
    const id = `fx-${key}-t`;
    box.innerHTML = html`<span class="fx-name" id="${id}" title="${esc(hint)}">${esc(label)}</span>
      <div class="seg seg-sm" role="group" aria-labelledby="${id}">
        <button type="button" data-v="any" aria-pressed="false">Any</button>
        <button type="button" data-v="yes" aria-pressed="false">${esc(yesText)} <span class="fx-n"></span></button>
        <button type="button" data-v="no" aria-pressed="false">${esc(noText)} <span class="fx-n"></span></button>
      </div>`;
    parent.append(box);
    box.addEventListener("click", (ev) => {
      const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-v]");
      if (b) setState({ ...state, [key]: b.dataset.v as Tri });
    });
    updates.push(() => {
      const base = f.without(key);
      const yes = base.filter((r) => val(r.c)).length;
      box.querySelectorAll<HTMLButtonElement>("button[data-v]").forEach((b) => {
        b.setAttribute("aria-pressed", String(b.dataset.v === state[key]));
        const n = b.querySelector(".fx-n");
        if (n) n.textContent = (b.dataset.v === "yes" ? yes : base.length - yes).toLocaleString("en-GB");
      });
    });
  }

  function rangeCtl(parent: HTMLElement, key: RangeKey) {
    if (!data) return;
    const spec = RANGES[key];
    const dom = domainOf(spec, data.candidates);
    if (!dom) return;
    const id = `fx-r-${key}`;
    const box = document.createElement("div");
    box.className = "fx-range";
    const unit = spec.unit ? html`<span class="fx-unit">${esc(spec.unit)}</span>` : "";
    box.innerHTML = html`
      <div class="fx-range-head"><span class="fx-name" id="${id}" title="${esc(spec.code)}">${esc(spec.label)}</span><span class="fx-cover"></span></div>
      <svg class="fx-hist" viewBox="0 0 200 28" preserveAspectRatio="none" aria-hidden="true"></svg>
      <div class="fx-slider">
        <div class="fx-track"><div class="fx-fill"></div></div>
        <input type="range" class="fx-lo" min="0" max="${SLIDER_MAX}" step="1" aria-label="${esc(spec.label)}, lowest" />
        <input type="range" class="fx-hi" min="0" max="${SLIDER_MAX}" step="1" aria-label="${esc(spec.label)}, highest" />
      </div>
      <div class="fx-nums">
        <input class="input fx-num" data-side="min" inputmode="decimal" aria-label="${esc(spec.label)}, from" />
        <span class="fx-to">to</span>
        <input class="input fx-num" data-side="max" inputmode="decimal" aria-label="${esc(spec.label)}, to" />
        ${unit}
      </div>`;
    parent.append(box);
    const lo = box.querySelector<HTMLInputElement>(".fx-lo")!;
    const hi = box.querySelector<HTMLInputElement>(".fx-hi")!;
    const fill = box.querySelector<HTMLDivElement>(".fx-fill")!;
    const nMin = box.querySelector<HTMLInputElement>('[data-side="min"]')!;
    const nMax = box.querySelector<HTMLInputElement>('[data-side="max"]')!;
    const hist = box.querySelector<SVGSVGElement>(".fx-hist")!;
    const cover = box.querySelector<HTMLSpanElement>(".fx-cover")!;

    const cur = (): Range => state.ranges[key] ?? { min: null, max: null };
    const commit = (r: Range) => {
      const ranges = { ...state.ranges };
      const clean: Range = {
        min: r.min != null && r.min > dom[0] + 1e-9 ? r.min : null,
        max: r.max != null && r.max < dom[1] - 1e-9 ? r.max : null,
      };
      if (clean.min == null && clean.max == null) delete ranges[key];
      else ranges[key] = clean;
      setState({ ...state, ranges });
    };
    const paintFill = (a: number, b: number) => {
      fill.style.left = `${(a / SLIDER_MAX) * 100}%`;
      fill.style.right = `${100 - (b / SLIDER_MAX) * 100}%`;
    };

    let raf = 0;
    const onSlide = (which: "lo" | "hi") => {
      let a = Number(lo.value);
      let b = Number(hi.value);
      if (a > b) {
        if (which === "lo") a = b;
        else b = a;
        lo.value = String(a);
        hi.value = String(b);
      }
      paintFill(a, b);
      const vmin = a <= 0 ? null : roundNice(fromSlider(a, dom, spec.log), spec);
      const vmax = b >= SLIDER_MAX ? null : roundNice(fromSlider(b, dom, spec.log), spec);
      nMin.value = vmin == null ? rangeValue(spec, dom[0]) : rangeValue(spec, vmin);
      nMax.value = vmax == null ? rangeValue(spec, dom[1]) : rangeValue(spec, vmax);
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => commit({ min: vmin, max: vmax }));
    };
    lo.addEventListener("input", () => onSlide("lo"));
    hi.addEventListener("input", () => onSlide("hi"));
    const onNum = () => {
      const p = (s: string) => {
        const v = Number(s.replace(",", ".").replace(/[^0-9.eE+-]/g, ""));
        return s.trim() === "" || !Number.isFinite(v) ? null : v;
      };
      let a = p(nMin.value);
      let b = p(nMax.value);
      if (a != null && b != null && a > b) [a, b] = [b, a];
      commit({ min: a, max: b });
    };
    nMin.addEventListener("change", onNum);
    nMax.addEventListener("change", onNum);

    updates.push(() => {
      const r = cur();
      const a = r.min == null ? 0 : toSlider(r.min, dom, spec.log);
      const b = r.max == null ? SLIDER_MAX : toSlider(r.max, dom, spec.log);
      const active = document.activeElement;
      if (active !== lo && active !== hi) {
        lo.value = String(a);
        hi.value = String(b);
        paintFill(a, b);
      }
      if (active !== nMin) nMin.value = rangeValue(spec, r.min ?? dom[0]);
      if (active !== nMax) nMax.value = rangeValue(spec, r.max ?? dom[1]);
      box.classList.toggle("is-on", isActiveRange(r));

      // Coverage and the histogram of what the other filters leave.
      const base = f.without(key);
      const vals = base.map((x) => spec.value(x.c)).filter((v): v is number => v != null && Number.isFinite(v));
      const measured = rows.filter((x) => spec.value(x.c) != null).length;
      cover.textContent = measured === rows.length ? "" : `${measured.toLocaleString("en-GB")} of ${rows.length.toLocaleString("en-GB")} measured`;
      cover.title = spec.missing === "far" ? "No known object nearby counts as far away." : "Narrowing this hides objects without a value.";
      hist.innerHTML = histogram(vals, dom, spec.log, r);
    });
  }

  // ---- toolbar, active chips, list --------------------------------------------------------------

  sortSel.addEventListener("change", () => setState({ ...state, sort: sortSel.value as SortKey }, { keepPage: true }));
  filtersBtn.addEventListener("click", () => openSheet(true));
  el.querySelectorAll<HTMLButtonElement>("[data-close-sheet]").forEach((b) => b.addEventListener("click", () => openSheet(false)));
  $<HTMLButtonElement>("[data-clear]").addEventListener("click", () => setState(cleared()));
  filtersEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && filtersEl.dataset.open === "true") openSheet(false);
  });

  function openSheet(on: boolean) {
    if (on) {
      filtersEl.dataset.open = "true";
      filtersEl.setAttribute("role", "dialog");
      filtersEl.setAttribute("aria-modal", "true");
      document.body.classList.add("dv-sheet-open");
      filtersEl.querySelector<HTMLElement>(".fx-close")?.focus();
    } else {
      delete filtersEl.dataset.open;
      filtersEl.removeAttribute("role");
      filtersEl.removeAttribute("aria-modal");
      document.body.classList.remove("dv-sheet-open");
      if (getComputedStyle(filtersBtn).display !== "none") filtersBtn.focus();
    }
  }

  el.querySelectorAll<HTMLButtonElement>("[data-export]").forEach((b) =>
    b.addEventListener("click", () => {
      const origin = `${location.origin}${location.pathname}`.replace(/\/$/, "");
      const stamp = new Date().toISOString().slice(0, 10);
      if (b.dataset.export === "csv") download(`skyshift-candidates-${stamp}.csv`, toCsv(sorted, origin), "text/csv;charset=utf-8");
      else
        download(
          `skyshift-candidates-${stamp}.json`,
          toJson(sorted, { generated: data?.generated ?? null, data_release: data?.data_release ?? null, query_url: location.href, exported: new Date().toISOString() }),
          "application/json",
        );
    }),
  );

  // The list for astronomers: the Community page's list (src/community), read when asked for.
  let astroBusy = false;
  const sayAstro = (text: string) => el.querySelectorAll<HTMLElement>("[data-dl-status]").forEach((s) => (s.textContent = text));
  async function exportAstro(format: "csv" | "json") {
    if (astroBusy) return;
    astroBusy = true;
    sayAstro("Reading everyone's votes…");
    try {
      const snap = await loadSnapshot({ kv: safeStorage() });
      const rows = await astronomersFor(snap);
      if (!rows.length) {
        sayAstro(
          snap.note && !snap.votes.length
            ? snap.note
            : `Nothing has reached consensus as a mover or changing source yet, so the list is empty. An object joins it with ${CONSENSUS.minVotes} votes and ${CONSENSUS_PCT}% agreeing.`,
        );
        return;
      }
      const file = astroFile(format, rows, { generated: new Date().toISOString(), votes_from: snap.mode, data_release: snap.dataRelease }, location.href);
      download(file.name, file.text, file.type);
      sayAstro(`Saved ${file.name}: ${plural(rows.length, "object", "objects")}${snap.mode === "local" ? ", from this browser's votes only" : ""}.`);
    } catch {
      sayAstro("The list for astronomers couldn't be made just now. Try again in a minute.");
    } finally {
      astroBusy = false;
    }
  }
  el.querySelectorAll<HTMLButtonElement>("[data-astro]").forEach((b) =>
    b.addEventListener("click", () => void exportAstro(b.dataset.astro === "json" ? "json" : "csv")),
  );

  // The Download menu closes on a click outside it and on Escape (back to its button).
  const onDocClick = (ev: MouseEvent) => {
    if (dlEl.open && !dlEl.contains(ev.target as Node)) dlEl.open = false;
  };
  document.addEventListener("click", onDocClick);
  dlEl.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || !dlEl.open) return;
    dlEl.open = false;
    dlEl.querySelector<HTMLElement>("summary")?.focus();
  });

  function renderToolbar() {
    const n = sorted.length;
    const all = rows.length;
    countEl.textContent = n === all ? plural(all, "object", "objects") : `${n.toLocaleString("en-GB")} of ${plural(all, "object", "objects")}`;
    const on = activeCount(state);
    filtersBtn.textContent = on ? `Filters (${on})` : "Filters";
    sortSel.value = state.sort;
    const clear = $<HTMLButtonElement>("[data-clear]");
    clear.disabled = on === 0;
    const showN = $<HTMLButtonElement>("[data-show-n]");
    showN.textContent = n ? `Show ${plural(n, "object", "objects")}` : "No matches: change a filter";
    el.querySelectorAll<HTMLButtonElement>("[data-export]").forEach((b) => {
      b.disabled = n === 0;
      b.title = `Download these ${n.toLocaleString("en-GB")} as ${b.dataset.export?.toUpperCase()}`;
      b.setAttribute("aria-label", `Download ${n.toLocaleString("en-GB")} as ${b.dataset.export?.toUpperCase()}`);
    });
    el.querySelectorAll<HTMLElement>("[data-dl-n]").forEach((p) => {
      p.textContent = n ? `The ${plural(n, "object", "objects")} shown, with their measurements and votes.` : "Nothing matches these filters.";
    });
  }

  // ---- what's worth checking: the groups set aside, each one click away -------------------------
  // Counts are under the other filters (f.without("scope")), like the facet counts, so they say
  // what a click would add to this list.

  scopeEl.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-scope]");
    if (!b) return;
    const v = b.dataset.scope!;
    const g = v as SetAside;
    const show = v === "all" ? [...SET_ASIDE] : v === "none" ? [] : state.show.includes(g) ? state.show.filter((x) => x !== g) : [...state.show, g];
    setState({ ...state, show });
    (scopeEl.querySelector<HTMLButtonElement>(`button[data-scope="${cssEsc(v)}"]`) ?? scopeEl.querySelector<HTMLButtonElement>("button[data-scope]") ?? countEl).focus();
  });

  /** "8,108 moving stars Gaia already knows". */
  const asideText = (g: SetAside, n: number) => plural(n, SET_ASIDE_TEXT[g].one, SET_ASIDE_TEXT[g].many);

  function renderScope() {
    const base = f.without("scope");
    const n = countBy(base, setAsideValues);
    const aside = SET_ASIDE.reduce((a, g) => a + (n.get(g) ?? 0), 0);
    if (state.verdict.length) {
      // "Community says real": a verdict outranks our checks, so nothing is set aside.
      scopeEl.hidden = aside === 0;
      scopeEl.innerHTML = html`<p class="dv-scope-text">Nothing is set aside here: what the community agreed on counts more than our checks.</p>`;
      return;
    }
    const groups = SET_ASIDE.filter((g) => (n.get(g) ?? 0) > 0 || state.show.includes(g));
    scopeEl.hidden = groups.length === 0;
    if (!groups.length) {
      scopeEl.innerHTML = "";
      return;
    }
    const hiding = groups.some((g) => !state.show.includes(g) && (n.get(g) ?? 0) > 0);
    const lead = !state.show.length
      ? "Only what's worth checking is listed."
      : hiding
        ? "What's worth checking is listed, plus what you added."
        : `Everything is listed, including ${plural(aside, "object", "objects")} usually set aside.`;
    const action = hiding
      ? html`<button type="button" class="btn-link" data-scope="all" title="List everything, set-aside ones too">Show all ${base.length.toLocaleString("en-GB")}</button>`
      : html`<button type="button" class="btn-link" data-scope="none" title="Set these groups aside again">Only what's worth checking</button>`;
    scopeEl.innerHTML = html`<p class="dv-scope-text">${esc(lead)}</p>
      <span class="dv-scope-label" id="dv-scope-l">Also show:</span>
      <span class="dv-scope-groups" role="group" aria-labelledby="dv-scope-l">${groups
        .map((g) => {
          const on = state.show.includes(g);
          return html`<button type="button" class="fchip" data-scope="${g}" aria-pressed="${on}" title="${esc(SET_ASIDE_TEXT[g].title)}">${esc(asideText(g, n.get(g) ?? 0))}</button>`;
        })
        .join("")}</span>
      ${action}`;
  }

  /** The set-aside rule in a few words, for the map page's list of what's filtered (null: nothing set aside). */
  function scopeChip(): string | null {
    if (!setsAside(state) || f.without("scope").length === sorted.length) return null;
    if (!state.show.length) return "Only what's worth checking";
    return `Worth checking, plus ${SET_ASIDE.filter((g) => state.show.includes(g)).map((g) => SET_ASIDE_TEXT[g].short).join(" and ")}`;
  }

  function renderActive() {
    const chips = activeChips(state);
    activeEl.hidden = chips.length === 0;
    activeEl.innerHTML =
      chips.map((c, i) => html`<li><button type="button" class="achip" data-i="${i}" aria-label="Remove filter: ${esc(c.label)}"><span>${esc(c.label)}</span><span class="achip-x" aria-hidden="true"></span></button></li>`).join("") +
      (chips.length > 1 ? html`<li><button type="button" class="btn-link" data-i="all">Clear all</button></li>` : "");
    activeEl.querySelectorAll<HTMLButtonElement>("button[data-i]").forEach((b) =>
      b.addEventListener("click", () => {
        if (b.dataset.i === "all") setState(cleared());
        else setState(chips[Number(b.dataset.i)].remove());
        activeEl.querySelector<HTMLButtonElement>("button")?.focus() ?? countEl.focus();
      }),
    );
  }

  function activeChips(s: FilterState): { label: string; remove(): FilterState }[] {
    const out: { label: string; remove(): FilterState }[] = [];
    const drop = (key: ListKey, v: string) => () => ({ ...s, [key]: s[key].filter((x) => x !== v) });
    for (const v of s.kind) out.push({ label: KIND_TEXT[v as keyof typeof KIND_TEXT]?.name ?? v, remove: drop("kind", v) });
    for (const v of s.review) out.push({ label: REVIEW_TEXT[v as ReviewState] ?? v, remove: drop("review", v) });
    for (const v of s.verdict) out.push({ label: `Verdict: ${ANSWER_TEXT[v as Answer]?.label ?? v}`, remove: drop("verdict", v) });
    for (const v of s.label) out.push({ label: labelText(v), remove: drop("label", v) });
    for (const v of s.reason) out.push({ label: reasonText(v).label, remove: drop("reason", v) });
    for (const v of s.det) out.push({ label: `D${v}`, remove: drop("det", v) });
    for (const v of s.zone) out.push({ label: `${v} zone`, remove: drop("zone", v) });
    for (const v of s.grade) out.push({ label: gradeFacetText(v).label, remove: drop("grade", v) });
    if (s.gaia !== "any") out.push({ label: s.gaia === "yes" ? "In Gaia" : "Not in Gaia", remove: () => ({ ...s, gaia: "any" }) });
    if (s.catwise !== "any") out.push({ label: s.catwise === "yes" ? "In CatWISE" : "Not in CatWISE", remove: () => ({ ...s, catwise: "any" }) });
    if (s.hideDoubts) out.push({ label: "No doubtful ones", remove: () => ({ ...s, hideDoubts: false }) });
    if (s.cautions !== "any") out.push({ label: s.cautions === "yes" ? "Only possible glitches" : "No possible glitches", remove: () => ({ ...s, cautions: "any" }) });
    if (s.hideMine) out.push({ label: "Not voted on by me", remove: () => ({ ...s, hideMine: false }) });
    for (const k of Object.keys(s.ranges) as RangeKey[]) {
      const r = s.ranges[k];
      if (!isActiveRange(r)) continue;
      const spec = RANGES[k];
      const label =
        k === "b" && r.min === PLANE_B && r.max == null
          ? "Away from the Milky Way plane"
          : k === "known" && r.max == null && r.min != null
            ? `No known object within ${num(r.min, 0)}″`
            : `${spec.label} ${rangeText(spec, r)}`;
      out.push({ label, remove: () => withoutTest(s, k) });
    }
    return out;
  }

  $<HTMLButtonElement>("[data-more]").addEventListener("click", () => {
    const first = shown;
    shown += PAGE;
    renderList();
    list.querySelectorAll<HTMLAnchorElement>(".dv-link")[first]?.focus();
  });

  function renderList() {
    const items = sorted.slice(0, shown);
    list.innerHTML = items.map(rowHtml).join("");
    list.querySelectorAll<HTMLElement>("[data-thumb]").forEach((b) => {
      if (byId.get(b.dataset.thumb ?? "")?.frames_url) thumbs.observe(b);
    });
    colsEl.hidden = items.length === 0;
    // Phones: the downloads sit under the list (the list for astronomers even when it's empty).
    $<HTMLDivElement>(".dv-export-foot").hidden = false;
    const rest = sorted.length - items.length;
    moreEl.hidden = rest <= 0;
    if (rest > 0) {
      moreEl.querySelector(".dv-more-n")!.textContent = `Showing ${items.length.toLocaleString("en-GB")} of ${sorted.length.toLocaleString("en-GB")}.`;
      moreEl.querySelector("[data-more]")!.textContent = `Show ${Math.min(PAGE, rest)} more`;
    }
    if (!sorted.length) renderNoMatch();
    else stateEl.hidden = true;
  }

  function renderNoMatch() {
    stateEl.hidden = false;
    const hint = bestRelaxation(f);
    const chip = hint ? activeChips(state).find((c) => sameState(c.remove(), withoutTest(state, hint.key))) : undefined;
    if (activePreset(state)?.id === "check-first") {
      stateEl.innerHTML = html`
        <p class="dv-state-title">Nothing to check first.</p>
        <p class="dv-state-text">Nothing unsettled here looks like a camera glitch: no odd pixels, ghosts or halos near it, and no spot seen again days apart.</p>
        <div class="dv-state-actions"><button type="button" class="btn" data-clear-all>Back to the list</button></div>`;
      stateEl.querySelector("[data-clear-all]")?.addEventListener("click", () => setState(defaultState()));
      return;
    }
    if (activePreset(state)?.id === "community-real") {
      stateEl.innerHTML = html`
        <p class="dv-state-title">Nothing the community agreed on yet.</p>
        <p class="dv-state-text">An object joins this list when ${CONSENSUS.minVotes} people have voted and ${CONSENSUS_PCT}% of them say it moves or changes. Those go to astronomers. Your votes get it there.</p>
        <div class="dv-state-actions"><button type="button" class="btn btn-primary" data-vote-now>Check the ones that need votes</button><button type="button" class="btn btn-ghost" data-clear-all>Back to the list</button></div>`;
      stateEl.querySelector("[data-vote-now]")?.addEventListener("click", () => {
        setState(presetState(PRESETS.find((p) => p.id === "needs-vote")!));
        startChecking();
      });
      stateEl.querySelector("[data-clear-all]")?.addEventListener("click", () => setState(defaultState()));
      return;
    }
    // Only set-aside ones match (a link to grade C, say): name them and show them in one click.
    const asideRows = f.activeKeys.includes("scope") ? f.without("scope") : [];
    if (asideRows.length) {
      const n = countBy(asideRows, setAsideValues);
      const groups = SET_ASIDE.filter((g) => n.get(g)).map((g) => asideText(g, n.get(g)!));
      const named = groups.length > 1 ? `${groups.slice(0, -1).join(", ")} and ${groups.at(-1)}` : groups[0];
      const one = asideRows.length === 1;
      stateEl.innerHTML = html`
        <p class="dv-state-title">Nothing worth checking matches these filters.</p>
        <p class="dv-state-text">${esc(`${named} ${one ? "matches" : "match"} them, but ${one ? "it's" : "they're"} set aside.`)}</p>
        <div class="dv-state-actions">
          <button type="button" class="btn" data-show-aside>Show ${asideRows.length === 1 ? "it" : "them"}</button>
          <button type="button" class="btn btn-ghost" data-clear-all>Clear all filters</button>
        </div>`;
      stateEl.querySelector("[data-show-aside]")?.addEventListener("click", () => setState(withoutTest(state, "scope")));
      stateEl.querySelector("[data-clear-all]")?.addEventListener("click", () => setState(cleared()));
      return;
    }
    stateEl.innerHTML = html`
      <p class="dv-state-title">Nothing matches these filters.</p>
      <p class="dv-state-text">${
        hint && chip
          ? `Removing “${esc(chip.label)}” would bring back ${plural(hint.n, "object", "objects")}.`
          : "Loosen a range or switch off a filter on the left."
      }</p>
      <div class="dv-state-actions">
        ${hint ? html`<button type="button" class="btn" data-relax>Remove that filter</button>` : ""}
        <button type="button" class="btn btn-ghost" data-clear-all>Clear all filters</button>
      </div>`;
    stateEl.querySelector("[data-relax]")?.addEventListener("click", () => hint && setState(withoutTest(state, hint.key)));
    stateEl.querySelector("[data-clear-all]")?.addEventListener("click", () => setState(cleared()));
  }

  function renderNoData() {
    renderIntro();
    stateEl.hidden = false;
    stateEl.innerHTML = html`
      <p class="dv-state-title">The list of objects to check isn't published yet.</p>
      <p class="dv-state-text">The list appears here as soon as it's published. Meanwhile, blink any spot of the SPHEREx sky yourself: paste coordinates above, or start from a featured target.</p>
      <div class="dv-state-actions"><a class="btn btn-primary" href="#/explore">Open Explore</a><a class="btn btn-ghost" href="#/results">See how we checked</a></div>`;
    el.querySelector<HTMLButtonElement>("[data-one-on]")?.classList.replace("btn-primary", "btn-ghost");
  }

  // ---- one row ----------------------------------------------------------------------------------

  function rowHtml(r: Row): string {
    const c = r.c;
    const kind = KIND_TEXT[c.kind] ?? { name: c.kind, plural: c.kind, gist: "" };
    const pos = formatRaDec(c);
    const href = `#/c/${encodeURIComponent(c.id)}`;
    const chips: string[] = [];
    // The vetting grade leads (movers only; an ungraded row shows none, as before the vetting).
    const grade = gradeOf(c);
    const gates = grade === "C" ? failedGates(c) : [];
    if (grade)
      chips.push(
        html`<li class="tag tag-grade is-${grade}" title="${esc(`${GRADE_TEXT[grade].short}: ${GRADE_PLAIN[grade]}`)}">${esc(grade === "C" ? GRADE_TEXT.C.short : gradeLabel(grade))}</li>`,
      );
    for (const l of c.labels) chips.push(html`<li class="tag tag-label" title="${esc(l)}">${esc(labelText(l))}</li>`);
    // A grade C row says its failed checks in its own line: keep them out of the doubts chip.
    const doubts = c.reasons.filter((x) => isDoubtCode(x) && !(grade === "C" && isGate(x)));
    for (const code of c.reasons.filter((x) => reasonText(x).tone === "support")) {
      const t = reasonText(code);
      chips.push(html`<li class="tag tag-support" title="${esc(code)}: ${esc(t.detail)}">${esc(t.label)}</li>`);
    }
    if (doubts.length) {
      // One chip for the pipeline's doubts: the first in words, the rest counted (all in the title).
      const [first, ...rest] = doubts.map(reasonText);
      const all = doubts.map((d) => `${reasonText(d).label} (${d})`).join("; ");
      chips.push(
        html`<li class="tag tag-doubt" title="Why we're unsure: ${esc(all)}"><span class="doubt-ring" aria-hidden="true"></span>${esc(first.label)}${
          rest.length
            ? html`<span class="tag-more" aria-hidden="true">+${rest.length}</span><span class="visually-hidden">, and ${esc(rest.map((r) => r.label.toLowerCase()).join(", "))}</span>`
            : ""
        }</li>`,
      );
    }
    // Cautions: artefact flags and caution reasons (outlier pixels, the same spot days apart), once each.
    const cautions = new Map<string, string>();
    for (const fl of c.flags) cautions.set(flagText(fl), fl);
    // A grade C row names its failed checks in its own line: no caution chip for those.
    for (const code of c.reasons.filter((x) => isCautionCode(x) && !(grade === "C" && isGate(x)))) {
      const t = reasonText(code);
      cautions.set(t.label, cautions.has(t.label) ? `${cautions.get(t.label)}, ${code}` : `${code}: ${t.detail}`);
    }
    for (const [label, title] of cautions)
      chips.push(html`<li class="tag tag-caution" title="${esc(title)}"><span class="caution-mark" aria-hidden="true"></span>${esc(label)}<span class="visually-hidden"> (caution)</span></li>`);
    const det = DETECTORS[c.det];
    const failed =
      grade === "C"
        ? html`<p class="dv-gradec">${esc(gates.length ? `Fails a check: ${gatesPhrase(gates)}.` : "Fails one of our checks.")} Open it to blink; it isn't put to a vote.</p>`
        : "";
    return html`<li class="dv-row${grade === "C" ? " is-gradec" : ""}" data-id="${esc(c.id)}">
      <div class="dv-thumb" data-thumb="${esc(c.id)}" aria-hidden="true">${emptyPlate()}</div>
      <div class="dv-main">
        <h3 class="dv-name"><a class="dv-link" href="${href}" title="${esc(c.id)}">${glyphSvg(c.kind, 12)}<span class="dv-kind">${esc(kind.name)}</span><span class="dv-pos">${esc(pos)}</span></a></h3>
        <p class="dv-why">${esc(c.why)}</p>
        ${failed}
        ${chips.length ? html`<ul class="dv-tags" aria-label="Labels and reasons">${chips.join("")}</ul>` : ""}
      </div>
      <div class="dv-meas">${measured(c)}<p class="m3 det-${c.det}" title="Detector ${c.det}, ${det?.min}–${det?.max} µm. Seen best on ${esc(formatMjd(c.mjd))}."><span class="det-dot" aria-hidden="true"></span>D${c.det} <span class="m-um">${det?.min}–${det?.max} µm</span></p>${scoreHtml(c.score)}</div>
      <div class="dv-votes">${votesHtml(r)}</div>
    </li>`;
  }

  /** What was measured, in units. Pipeline numbers (S/N, σ, the score) are for Advanced: pro-only. */
  function measured(c: Candidate): string {
    const lines: string[] = [];
    if (c.kind === "high_pm" && c.pm_mas_yr != null) {
      const sig = significance(c);
      lines.push(html`<p class="m1"><span class="visually-hidden">Motion </span>${arcsecPerYr(c.pm_mas_yr)}</p>`);
      if (c.pm_err_mas_yr != null)
        lines.push(
          html`<p class="m2">± ${num(c.pm_err_mas_yr / 1000, c.pm_err_mas_yr >= 10000 ? 1 : 2)}″/yr${sig != null ? html`<span class="pro-only">, <span title="Motion over its error">${num(sig, 1)}σ</span></span>` : ""}</p>`,
        );
    } else if (c.rate_arcsec_h != null) {
      lines.push(html`<p class="m1"><span class="visually-hidden">Motion </span>${arcsecPerHour(c.rate_arcsec_h)}</p>`);
      const snr = c.snr != null ? html`<span class="pro-only">S/N ${num(c.snr, 1)}${c.flux_ujy != null ? ", " : ""}</span>` : "";
      if (c.snr != null || c.flux_ujy != null) lines.push(html`<p class="m2">${snr}${c.flux_ujy != null ? flux(c.flux_ujy) : ""}</p>`);
    } else {
      if (c.snr != null) lines.push(html`<p class="m1 pro-only"><abbr title="Signal to noise">S/N</abbr> ${num(c.snr, 1)}</p>`);
      if (c.flux_ujy != null) lines.push(html`<p class="m2">${flux(c.flux_ujy)}</p>`);
    }
    if (c.kind === "mover" && c.nearest_known_arcsec != null)
      lines.push(html`<p class="m2" title="Distance to the nearest known asteroid or comet at that time">Known object ${num(c.nearest_known_arcsec, 0)}″ away</p>`);
    else if (c.kind === "mover") lines.push(html`<p class="m2" title="Nothing known within the search radius">No known object near</p>`);
    return lines.join("");
  }

  /** The pipeline's score, 0 to 1, for Advanced only (pro-only); the list's order already uses it. */
  function scoreHtml(score: number | null): string {
    if (score == null) return html`<p class="m2 dv-score pro-only" title="No score for this kind yet">No score</p>`;
    const pct = Math.round(Math.min(1, Math.max(0, score)) * 100);
    return html`<p class="m2 dv-score pro-only" title="Score, 0 to 1: how promising we rate it">Score <span class="dv-score-n">${num(score, 2)}</span><span class="dv-bar" aria-hidden="true"><span style="width:${pct}%"></span></span></p>`;
  }

  function votesHtml(r: Row): string {
    const mine = r.mine ? html`<span class="dv-mine">You voted</span>` : "";
    if (voteStatus === "error") return html`<span class="dv-none">Votes unavailable</span>${mine}`;
    if (r.total === 0 && gradeOf(r.c) === "C") return html`<span class="dv-none" title="Grade C objects failed one of our checks: they aren't put to a vote">Not put to a vote</span>${mine}`;
    if (r.total === 0) return html`<span class="dv-none">No votes yet</span>${mine}`;
    const segs = ANSWERS.filter((a) => r.votes[a] > 0)
      .map((a) => html`<span class="vseg vseg-${a}" style="flex:${r.votes[a]}" title="${ANSWER_TEXT[a].label}: ${r.votes[a]}"></span>`)
      .join("");
    const lead = r.verdict ? { answer: r.verdict, n: r.votes[r.verdict] } : leading(r.votes) ?? { answer: "mover" as Answer, n: 0 };
    const text = r.verdict
      ? html`<span class="dv-verdict">Verdict: ${esc(ANSWER_TEXT[r.verdict].label.toLowerCase())}</span><span class="dv-vn">${lead.n} of ${r.total}</span>`
      : html`<span class="dv-vn">${plural(r.total, "vote", "votes")}, ${lead.n} ${esc(ANSWER_TEXT[lead.answer].label.toLowerCase())}</span>`;
    return html`<span class="dv-vbar" aria-hidden="true">${segs}</span>${text}${mine}`;
  }

  // ---- the map view -----------------------------------------------------------------------------
  // Show the filtered set beside the intro; the optional map page adds context and a way back.

  function renderSky() {
    skyLink.href = candidatesHash(state, { map: true });
    // Wait for renderOne to expose the expanded view before measuring it.
    const on = mapView && rootEl.dataset.view === "map";
    skyEl.hidden = !on;
    mapPreview.hidden = mapView || one || !data;
    if (!on && mapPreview.hidden) {
      map?.closeFullscreen();
      return;
    }
    const listHash = candidatesHash(state);
    skyEl.querySelectorAll<HTMLAnchorElement>("[data-sky-list]").forEach((a) => (a.href = listHash));
    const count = skyEl.querySelector<HTMLParagraphElement>(".dv-sky-count")!;
    const chipsEl = skyEl.querySelector<HTMLUListElement>(".dv-sky-chips")!;
    const change = skyEl.querySelector<HTMLAnchorElement>("[data-sky-change]")!;
    if (!data) {
      count.textContent = loading ? "Loading the list…" : "No objects to check are published yet.";
      chipsEl.hidden = change.hidden = true;
      return;
    }
    // What's set aside counts as filtered here, so the count never claims "All" while some are missing.
    const scope = scopeChip();
    const chips = [...(scope ? [scope] : []), ...activeChips(state).map((c) => c.label)];
    count.textContent = chips.length
      ? `${sorted.length.toLocaleString("en-GB")} of ${plural(rows.length, "object", "objects")}, filtered:`
      : `All ${plural(rows.length, "object", "objects")}, no filters.`;
    chipsEl.hidden = !chips.length;
    chipsEl.innerHTML = chips.map((c) => html`<li>${esc(c)}</li>`).join("");
    change.hidden = false;
    change.textContent = chips.length ? "Change filters" : "Filter them";
    const host = on ? skyEl.querySelector<HTMLElement>("[data-skymap]")! : mapPreview;
    if (mapHost !== host) {
      map?.closeFullscreen();
      host.append(mapEl);
      mapHost = host;
    }
    if (!map) {
      map = mountSkyMap(mapEl, { onOpen: open });
      map.setData(data.candidates);
    }
    if (drawn !== sorted) map.setFiltered((drawn = sorted).map((r) => r.c.id));
  }

  // ---- page handle ------------------------------------------------------------------------------

  load();
  // A vote cast on another page of this tab: refresh the counts next time this page shows.
  const offVoted = onVoted(() => {
    votesAt = 0;
  });

  return {
    show(param) {
      onScreen = true;
      const q = routeQuery(param);
      const fromUrl = queryToState(q);
      // #/candidates/one=1 (and the old #/review, mapped to it): the Viewer on this list's first object to check.
      one = isOneAtATime(q);
      // #/candidates/…&map=1: the same filter on the sky map, a page of its own.
      const wasMap = mapView;
      mapView = isMapView(q);
      if (mapView) one = false;
      if (!sameState(fromUrl, state)) {
        state = fromUrl;
        shown = PAGE;
        apply();
      }
      renderOne();
      syncOne();
      if (mapView && !wasMap) $<HTMLElement>("#dv-sky-title").focus({ preventScroll: true });
      // Votes may have changed on the Review page: refresh at most every 20 s.
      if (data && voteStatus !== "loading" && Date.now() - votesAt > 20_000) loadVotes();
    },
    hide() {
      onScreen = false;
      map?.closeFullscreen();
      map?.highlight(null);
      openSheet(false);
    },
    destroy() {
      offVoted();
      offDetails();
      thumbs.destroy();
      map?.destroy();
      document.removeEventListener("click", onDocClick);
      document.body.classList.remove("dv-sheet-open");
      el.innerHTML = "";
    },
  };
};

/** The downloads: this filtered list, and the list for astronomers (the Community page's list:
 *  consensus movers and changing sources). Top: a menu in the toolbar; foot: under the list on phones. */
function exportHtml(where: "top" | "foot"): string {
  const id = (s: string) => `dv-dl-${s}-${where}`;
  const btn = (attr: string, v: string) => html`<button type="button" class="btn btn-ghost btn-sm" ${attr}="${v}">${v.toUpperCase()}</button>`;
  return html`
    <div class="dv-dl-group" role="group" aria-labelledby="${id("list")}">
      <p class="dv-dl-title" id="${id("list")}">This list</p>
      <p class="dv-dl-text" data-dl-n></p>
      <div class="dv-dl-btns">${btn("data-export", "csv")}${btn("data-export", "json")}</div>
    </div>
    <div class="dv-dl-group" role="group" aria-labelledby="${id("astro")}">
      <p class="dv-dl-title" id="${id("astro")}">List for astronomers</p>
      <p class="dv-dl-text">What the community agreed moves or changes: at least ${CONSENSUS.minVotes} votes with ${CONSENSUS_PCT}% agreeing. Leads to check, not discoveries.</p>
      <div class="dv-dl-btns">${btn("data-astro", "csv")}${btn("data-astro", "json")}</div>
      <p class="dv-dl-status" data-dl-status role="status" aria-live="polite"></p>
    </div>`;
}

// ---- helpers ------------------------------------------------------------------------------------

/** What a hide-toggle does, in a few words: "hides 12" before, "12 hidden" after. */
function hiddenText(n: number, on: boolean): string {
  const v = n.toLocaleString("en-GB");
  return on ? `${v} hidden` : n ? `hides ${v}` : "none here";
}

function uniq(xs: string[]): string[] {
  return [...new Set(xs)];
}

function cssEsc(s: string): string {
  return typeof globalThis.CSS?.escape === "function" ? CSS.escape(s) : s.replace(/["\\\]]/g, "\\$&");
}

function reviewTitle(v: ReviewState): string {
  if (v === "none") return "none: nobody has voted yet";
  if (v === "some") return "some: has votes, no agreement yet";
  return "consensus: at least 5 votes with 70 % agreeing";
}

/** The empty plate: glass with the target reticle (DESIGN.md "Pencil marks"). */
function emptyPlate(): string {
  return html`<svg class="dv-thumb-empty" viewBox="0 0 56 56" aria-hidden="true" focusable="false"><g class="reticle"><path d="M17 28h7M32 28h7M28 17v7M28 32v7"/></g></svg>`;
}

/** A small histogram (20 bins) of the values, bars inside the selected range in ink. */
function histogram(vals: number[], dom: [number, number], log: boolean, r: Range): string {
  const nb = 20;
  const bins = new Array<number>(nb).fill(0);
  const t = (v: number) => (log ? (Math.log10(Math.max(v, dom[0])) - Math.log10(dom[0])) / (Math.log10(dom[1]) - Math.log10(dom[0])) : (v - dom[0]) / (dom[1] - dom[0]));
  for (const v of vals) {
    const i = Math.min(nb - 1, Math.max(0, Math.floor(t(v) * nb)));
    bins[i]++;
  }
  const max = Math.max(1, ...bins);
  const w = 200 / nb;
  const lo = r.min == null ? 0 : t(r.min);
  const hi = r.max == null ? 1 : t(r.max);
  return bins
    .map((n, i) => {
      if (!n) return "";
      const h = Math.max(2, (n / max) * 26);
      const mid = (i + 0.5) / nb;
      const inside = mid >= lo - 1e-9 && mid <= hi + 1e-9;
      return `<rect class="${inside ? "h-in" : "h-out"}" x="${(i * w + 1).toFixed(1)}" y="${(28 - h).toFixed(1)}" width="${(w - 2).toFixed(1)}" height="${h.toFixed(1)}" rx="1"/>`;
    })
    .join("");
}
