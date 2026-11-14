// Sky, the home page (web/DESIGN.md "Sky: the home page"). The whole SPHEREx sky as a globe you
// can zoom into: candidates as a heat map that resolves into single pencil marks, a preview drawer
// with a live mini blink, and the view in the URL (#/sky/<ra>,<dec>,<fov>[,gal][,mol][,kinds]).
// Aladin Lite draws the globe; without WebGL2 a static Aitoff SVG of the same marks stands in.
import "./atlas.css";
import './home.css';
import type { Candidate, CandidateKind, SkyPos } from "../api/types";
import { formatRaDec } from "../app/astro";
import { parseCoords } from "../app/coords";
import type { MountPage, PageContext } from "../app/modules";
import { TARGETS, featuredNear, type Target } from "../app/state";
import { browserHasWebGL2, loadAladinOnce, SkyUnavailable } from "../sky/loader";
import { ALLWISE, SPHEREX, TWO_MASS } from "../sky/layers";
import { createAladinMap, readInk } from "./aladin-map";
import { loadAtlasData, type AtlasData } from "./data";
import { kindIcon, renderCandidate, renderTarget, type DrawerHandle } from "./drawer";
import { createFallbackMap } from "./fallback";
import { parseState, preparedBlinkHash, sameState, stateHash, type AtlasState } from "./hash";
import { binCandidates, rampLabels, rampSteps, zoneBoxes, type Cell, type ZoneBox } from "./heat";
import {
  KINDS,
  KIND_TEXT,
  LIST_NOTE,
  countSentence,
  countText,
  gradeCLast,
  isGradeC,
  isLikelyMismatch,
  kindChipTitle,
  oneLine,
  plural,
  shownSentence,
  zoneName,
} from "./kinds";
import { inView, type BackgroundId, type Layers, type MapHandle, type MapModel, type MapView } from "./map";
import { meanPosition, sepDeg, wrap360 } from "./sphere";
import { LEVEL_FOV, SIGNAL_FOV, SPOT_FOV, WHOLE_FOV, defaultMol, fovToOpen, heatOrder, levelFor, showsSignals, wholeFov, type Level } from "./zoom";
import { cellSideDeg } from "./healpix";
import { liveLine, loadSearch, loadZoneSuspects, provisionalZone, type ProvisionalSet, type SearchLayer } from "./search";

const html = String.raw;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const LIST_MAX = 60;

function prefersReducedMotion(): boolean {
  try {
    return matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

const BACKGROUNDS: { id: BackgroundId; label: string; about: string }[] = [
  { id: "spherex", label: "SPHEREx", about: SPHEREX.about },
  { id: "2mass", label: "2MASS", about: TWO_MASS.about },
  { id: "allwise", label: "AllWISE", about: ALLWISE.about },
];

export const mountAtlas: MountPage = (el: HTMLElement, ctx: PageContext) => {
  el.classList.add("atl");
  el.innerHTML = html`
    <div class="atl-intro">
      <h1 class="atl-lede" id="atl-title">
        Start with the sky.
        <span class="atl-lede-2">Drag, zoom, or pick a point.</span>
      </h1>
      <p class="sky-survey" data-survey>Sky: 2MASS · Signals: SPHEREx</p>
    </div>

    <div class="atl-controls">
      <div class="atl-controls-main">
        <div class="seg" role="group" aria-label="Map shape">
          <button type="button" data-proj="globe" aria-pressed="true" title="A globe: the sky as a sphere, half at a time">Globe</button>
          <button type="button" data-proj="mol" aria-pressed="false" title="The whole sky at once (Mollweide projection)">Whole sky</button>
        </div>
        <div class="seg" role="group" aria-label="Coordinate grid">
          <button type="button" data-frame="eq" aria-pressed="true" title="Right ascension and declination">Equatorial</button>
          <button type="button" data-frame="gal" aria-pressed="false" title="Galactic longitude and latitude: the Milky Way runs along the equator">Galactic</button>
        </div>
        <div class="atl-kinds" role="group" aria-label="Kinds of object shown">
          ${KINDS.map(
            (k) => html`<button class="atl-kind" type="button" data-kind="${k}" aria-pressed="true" title="${KIND_TEXT[k].meaning}">
              ${kindIcon(k, 18)}<span>${KIND_TEXT[k].plural}</span><span class="atl-kind-n" data-kind-n="${k}"></span><span class="visually-hidden" data-kind-hidden="${k}"></span>
            </button>`,
          ).join("")}
        </div>
      </div>
      <div class="atl-controls-side">
        <form class="atl-find" role="search">
          <label class="visually-hidden" for="atl-q">Fly to coordinates or a featured name</label>
          <input id="atl-q" class="input atl-find-input" name="q" autocomplete="off" spellcheck="false" placeholder="Fly to coordinates or a name" />
          <button class="btn atl-find-go" type="submit">Fly</button>
        </form>
        <button class="atl-adjust" type="button" aria-expanded="false" aria-controls="atl-more">Adjust</button>
      </div>
    </div>
    <div class="atl-more" id="atl-more" hidden>
      <div class="atl-more-row">
        <span class="atl-more-label" id="atl-bg-label">Background</span>
        <div class="seg" role="group" aria-labelledby="atl-bg-label">
          ${BACKGROUNDS.map((b) => html`<button type="button" data-bg="${b.id}" aria-pressed="${b.id === "2mass"}" title="${b.about}">${b.label}</button>`).join("")}
        </div>
      </div>
      <div class="atl-more-row">
        <span class="atl-more-label" id="atl-plate-label">Plate</span>
        <div class="seg" role="group" aria-labelledby="atl-plate-label">
          <button type="button" data-neg="1" aria-pressed="true" title="Dark stars on light glass, as astronomers blink plates">Negative</button>
          <button type="button" data-neg="0" aria-pressed="false" title="The survey's own colours, bright stars on dark sky">Positive</button>
        </div>
      </div>
      <div class="atl-more-row">
        <button class="atl-switch" type="button" data-layer="heat" aria-pressed="true">Heat map</button>
        <button class="atl-switch" type="button" data-layer="zones" aria-pressed="true">Zones</button>
        <button class="atl-switch" type="button" data-layer="signals" aria-pressed="true">Signals</button>
        <button class="atl-switch" type="button" data-mismatch aria-pressed="false"
          title="Moving stars faster than any star can move: almost certainly two different sources paired by mistake">Likely mismatches</button>
      </div>
      <div class="atl-more-row">
        <button class="atl-switch" type="button" data-layer="search" aria-pressed="false"
          title="The whole-sky run's zones along the ecliptic, shaded by how far tonight's search has got">Tonight's search</button>
        <button class="atl-switch" type="button" data-layer="provisional" aria-pressed="true"
          title="Zoomed into a searched zone: its suspects so far, as dashed rings. Not on the list to check yet: the WISE check still has to remove most of them">Provisional suspects</button>
      </div>
    </div>
    <p class="atl-msg" data-msg role="status" aria-live="polite"></p>

    <div class="atl-stage" data-stage>
      <div class="atl-map" data-map tabindex="0" role="region" aria-roledescription="sky map"
        aria-label="Sky map" aria-describedby="atl-keys"></div>
      <p class="visually-hidden" id="atl-keys">Arrow keys move the map, plus and minus zoom, Enter opens the signal nearest the centre. The list below the map holds every signal in view.</p>
      <div class="atl-note" data-note role="status"><p>Loading the sky…</p></div>
      <div class="atl-tip" data-tip role="tooltip" hidden></div>
      <div class="atl-pin" data-pin hidden>
        <p class="atl-pin-coords" data-pin-coords></p>
        <div class="atl-pin-actions">
          <button class="btn btn-primary" type="button" data-pin-blink>Blink here</button>
          <button class="btn btn-ghost" type="button" data-pin-copy>Copy</button>
          <button class="atl-pin-x" type="button" data-pin-x aria-label="Close">×</button>
        </div>
      </div>
      <p class="atl-status" data-status aria-live="polite"></p>
      <div class="atl-zoom" role="group" aria-label="Zoom">
        <button type="button" data-zoom="in" aria-label="Zoom in">+</button>
        <button type="button" data-zoom="out" aria-label="Zoom out">−</button>
        <button type="button" data-zoom="all" title="Back to the whole sky">Whole sky</button>
      </div>
      <button class="btn atl-continue" type="button" data-show-paths>Choose your next step ↓</button>
      <aside class="atl-drawer" data-drawer aria-labelledby="atl-dr-title" hidden></aside>
    </div>

    <section class="sky-paths" data-paths aria-labelledby="sky-paths-title">
      <div class="sky-paths-head"><h2 id="sky-paths-title">Continue with this sky</h2><p data-current-sky>Pick a point, or use the centre of the map.</p></div>
      <div class="sky-paths-grid">
        <a href="#/explore" data-enter-area="explore"><strong>Explore <span aria-hidden="true">↗</span></strong><span>Watch the sky change. Try a guided observation.</span></a>
        <a href="#/analyze" data-enter-area="analyze"><strong>Analyze <span aria-hidden="true">↗</span></strong><span>Measure a change and check the evidence.</span></a>
        <a href="#/research" data-enter-area="research"><strong>Research <span aria-hidden="true">↗</span></strong><span>Trace the data, methods and uncertainties.</span></a>
      </div>
      <p class="sky-paths-note">Choose any path, switch whenever you like, and return to this sky.</p>
    </section>

    <div class="atl-below">
      <div class="atl-count-box"><p class="atl-count" data-count aria-live="polite">Loading the list…</p><p class="atl-count-note">${LIST_NOTE}</p><a class="atl-live" href="#/research/live" data-live hidden></a></div>
      <p class="atl-key" data-key></p>
      <div class="atl-listbox">
        <button class="atl-list-toggle" type="button" aria-expanded="false" aria-controls="atl-list" data-list-toggle disabled>Signals in view</button>
        <ol class="atl-list" id="atl-list" data-list hidden></ol>
      </div>
      <p class="atl-source" data-source></p>
    </div>
  `;

  const q = <T extends Element>(s: string) => el.querySelector(s) as T;
  const stage = q<HTMLDivElement>("[data-stage]");
  const mapEl = q<HTMLDivElement>("[data-map]");
  const note = q<HTMLDivElement>("[data-note]");
  const countEl = q<HTMLParagraphElement>("[data-count]");
  const liveEl = q<HTMLAnchorElement>("[data-live]");
  const statusEl = q<HTMLParagraphElement>("[data-status]");
  const msgEl = q<HTMLParagraphElement>("[data-msg]");
  const tip = q<HTMLDivElement>("[data-tip]");
  const pin = q<HTMLDivElement>("[data-pin]");
  const drawerEl = q<HTMLElement>("[data-drawer]");
  const keyEl = q<HTMLParagraphElement>("[data-key]");
  const listEl = q<HTMLOListElement>("[data-list]");
  const listToggle = q<HTMLButtonElement>("[data-list-toggle]");
  const sourceEl = q<HTMLParagraphElement>("[data-source]");
  const input = q<HTMLInputElement>("#atl-q");
  const adjust = q<HTMLButtonElement>(".atl-adjust");
  const more = q<HTMLDivElement>("#atl-more");
  more.prepend(q<HTMLElement>('.atl-controls-main'));

  /** The stage bleeds to the window's edges and ends at the bottom of the first screen. */
  function measure() {
    try {
      el.style.setProperty("--atl-vw", `${document.documentElement.clientWidth}px`);
      const top = stage.getBoundingClientRect().top + scrollY;
      if (top > 0) el.style.setProperty("--atl-top", `${Math.round(top)}px`);
    } catch {
      /* CSS defaults hold */
    }
  }

  const reduced = prefersReducedMotion();
  const ink = readInk();

  // ---- state ---------------------------------------------------------------------------------
  let state: AtlasState = { ra: 0, dec: 0, fov: WHOLE_FOV, gal: false, mol: false, kinds: [...KINDS] };
  let layers: Layers = { heat: true, zones: true, signals: true, search: false, provisional: true, background: "2mass", negative: false };
  stage.classList.add('is-positive');
  el.querySelectorAll<HTMLButtonElement>('[data-neg]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.neg==='0')));
  /** Tonight's search (the sky run's progress file), refreshed every minute while the page shows. */
  let search: SearchLayer | null = null;
  /** The provisional suspects of the combined zone in view, when zoomed in on one. */
  let provisional: ProvisionalSet | null = null;
  let data: AtlasData | null = null;
  let shown: Candidate[] = [];
  let zones: ZoneBox[] = [];
  const cellCache = new Map<number, Cell[]>();
  let selected: Candidate | null = null;
  let focusSky:Parameters<NonNullable<PageContext['skySelection']>>[0]|null=null;
  let drawer: DrawerHandle | null = null;
  let map: MapHandle | null = null;
  let mapStarting = false;
  let visible = false;
  let started = false;
  let destroyed = false;
  // Route changes can arrive while the candidates/CDN are still loading.
  let requestedParam: string | null = null;
  let pinPos: SkyPos | null = null;
  let pinAt: { x: number; y: number } | null = null;
  let level: Level = "whole";
  /** "Next nearby" walks outward without revisiting: ids seen in this chain. */
  let chain = new Set<string>();
  let lastFocus: HTMLElement | null = null;

  const cellsAt = (order: number): Cell[] => {
    let c = cellCache.get(order);
    if (!c) {
      c = binCandidates(shown, order);
      cellCache.set(order, c);
    }
    return c;
  };

  function model(): MapModel {
    return { candidates: shown, cellsAt, zones, targets: TARGETS, selectedId: selected?.id ?? null, layers, search, provisional };
  }

  /** Likely mismatches (implausible_pm) stay off the map unless Adjust turns them on. */
  let mismatches = false;
  const onMap = (c: Candidate) => state.kinds.includes(c.kind) && (mismatches || !isLikelyMismatch(c));

  function refilter() {
    const all = data?.candidates ?? [];
    shown = all.filter(onMap);
    cellCache.clear();
    if (selected && !onMap(selected)) closeDrawer();
  }

  // ---- tonight's search --------------------------------------------------------------------
  const SEARCH_EVERY_MS = 60_000;
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  let searchAbort: AbortController | null = null;
  let searchAt = 0;
  /** Suspects per suspects_url and check, so panning about a zone fetches its list once. */
  const provCache = new Map<string, Promise<ProvisionalSet["suspects"]>>();
  let provWant: string | null = null;

  const live = () => !destroyed && visible && !(typeof document !== "undefined" && document.hidden);

  function renderLive() {
    liveEl.hidden = !search;
    if (!search) return;
    liveEl.textContent = liveLine(search);
    const when = search.updated != null ? new Date(search.updated) : null;
    const at = when && !Number.isNaN(when.getTime()) ? when.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : null;
    liveEl.title = `${at ? `As of ${at} UTC. ` : ""}Open the live search.`;
  }

  function stopSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = null;
    searchAbort?.abort();
    searchAbort = null;
  }

  function scheduleSearch(ms = SEARCH_EVERY_MS) {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = live() ? setTimeout(() => void refreshSearch(), ms) : null;
  }

  /** Load the progress file now; on success redraw, on failure keep the last good layer. */
  async function refreshSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = null;
    if (!live()) return;
    searchAbort?.abort();
    const ac = new AbortController();
    searchAbort = ac;
    try {
      const next = await loadSearch(ac.signal);
      if (ac.signal.aborted || destroyed) return;
      search = next;
      searchAt = Date.now();
      renderLive();
      map?.update(model());
      renderKey();
      if (map) updateProvisional(map.view());
    } catch (e) {
      if (ac.signal.aborted || destroyed) return;
      if (import.meta.env.DEV) console.warn("Sky: tonight's search didn't load.", e);
    } finally {
      if (searchAbort === ac) {
        searchAbort = null;
        scheduleSearch();
      }
    }
  }

  /** Back on the page (or the tab): refresh if the last load is a minute old, else wait out the rest. */
  function resumeSearch() {
    if (!live()) return;
    const age = Date.now() - searchAt;
    if (age >= SEARCH_EVERY_MS) void refreshSearch();
    else if (!searchAbort) scheduleSearch(SEARCH_EVERY_MS - age);
  }

  const onVisibility = () => (document.hidden ? stopSearch() : resumeSearch());
  document.addEventListener("visibilitychange", onVisibility);

  function setProvisional(next: ProvisionalSet | null) {
    if (next === provisional) return;
    provisional = next;
    map?.update(model());
    renderKey();
    if (map) renderStatus(map.view());
  }

  /** Zoomed in (< 8°) on a combined zone of tonight's run: load and show its provisional suspects. */
  function updateProvisional(v: MapView) {
    const z = layers.provisional && showsSignals(levelFor(v.fov)) ? provisionalZone(search, v, data?.zones ?? []) : null;
    if (!z || !z.suspectsUrl) {
      provWant = null;
      setProvisional(null);
      return;
    }
    // The list is rewritten when the WISE check runs: a new key then, so it's fetched again.
    const url = z.suspectsUrl;
    const key = `${url}#${z.provisional ? "gaia" : "wise"}`;
    if (provWant === key) return;
    provWant = key;
    let p = provCache.get(key);
    if (!p) {
      p = loadZoneSuspects(url);
      provCache.set(key, p);
      p.catch(() => provCache.delete(key));
    }
    p.then(
      (suspects) => {
        if (destroyed || provWant !== key) return;
        setProvisional({ zone: z.name, suspects, provisional: z.provisional });
      },
      () => {
        if (destroyed || provWant !== key) return;
        setProvisional(null);
      },
    );
  }

  /** "41 provisional suspects (dashed), before the WISE check." for the status line. */
  function provisionalSentence(): string {
    if (!provisional || !layers.provisional || !showsSignals(level) || !provisional.suspects.length) return "";
    const n = plural(provisional.suspects.length, "provisional suspect", "provisional suspects");
    return ` Dashed: ${n} in ${zoneLabel(provisional.zone)}, ${provisional.provisional ? "before the WISE check" : "after the WISE check, not yet reviewed"}.`;
  }

  const zoneLabel = (name: string) => (/^ecl/i.test(name) ? `zone ${name}` : zoneName(name));

  // ---- URL -----------------------------------------------------------------------------------
  let hashTimer: ReturnType<typeof setTimeout> | null = null;
  let lastWritten = "";
  function writeHash(now = false) {
    if (hashTimer) clearTimeout(hashTimer);
    const write = () => {
      hashTimer = null;
      if (!visible || destroyed) return;
      const h = stateHash(state);
      if (h === lastWritten && location.hash === h) return;
      lastWritten = h;
      // replaceState never fires hashchange: panning doesn't re-route the app.
      history.replaceState(history.state, "", `${location.pathname}${location.search}${h}`);
    };
    if (now) write();
    else hashTimer = setTimeout(write, 350);
  }

  // ---- counts, key, status, list --------------------------------------------------------------
  function renderCounts() {
    const all = data?.candidates ?? [];
    for (const k of KINDS) {
      const ofKind = all.filter((c) => c.kind === k);
      // Likely mismatches stay off the map: say so on the chip, so its count doesn't look wrong.
      const hidden = mismatches ? 0 : ofKind.filter(isLikelyMismatch).length;
      const n = ofKind.length - hidden;
      const span = el.querySelector<HTMLSpanElement>(`[data-kind-n="${k}"]`);
      if (span) span.textContent = countText(n);
      const note = el.querySelector<HTMLSpanElement>(`[data-kind-hidden="${k}"]`);
      if (note) note.textContent = hidden ? `, ${plural(hidden, "likely mismatch", "likely mismatches")} hidden` : "";
      const chip = el.querySelector<HTMLButtonElement>(`.atl-kind[data-kind="${k}"]`);
      if (chip) chip.title = kindChipTitle(k, n, hidden);
    }
    if (!data) return;
    if (data.origin === "none") {
      countEl.textContent = "Nothing to check yet: the pipeline's list hasn't been published.";
      return;
    }
    countEl.textContent = `${countSentence(all.length, data.zones.length)}.`;
    const when = data.generated ? new Date(data.generated) : null;
    const date =
      when && !Number.isNaN(when.getTime())
        ? when.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC"
        : null;
    sourceEl.textContent =
      data.origin === "sample"
        ? "Development preview: showing the pilot sample from fixtures, because /data/candidates.json isn't there yet. Background images: NASA/IPAC IRSA."
        : `SPHEREx ${data.dataRelease ?? ""} objects to check${date ? `, exported ${date}` : ""}. Background images: NASA/IPAC IRSA.`;
  }

  function renderKey() {
    const sig = showsSignals(level);
    if (!sig) {
      const order = heatOrder(state.fov, stage.clientWidth || 800);
      const cells = cellsAt(order);
      const steps = rampSteps(cells.reduce((m, c) => Math.max(m, c.count), 0));
      const labels = rampLabels(steps);
      const side = cellSideDeg(order);
      const sw = steps
        .map((_, i) => {
          const a = steps.length <= 1 ? 0.55 : 0.16 + (0.62 * i) / (steps.length - 1);
          return `<span class="atl-swatch" style="--a:${a.toFixed(3)}" aria-hidden="true"></span>${esc(labels[i])}`;
        })
        .join(" ");
      keyEl.innerHTML = `Red cells: objects to check per ${side >= 3 ? Math.round(side) : side.toFixed(1)}° cell, ${sw}. ${
        layers.zones ? `<span class="atl-key-zone" aria-hidden="true"></span>Outlined: the zones with objects to check. ` : ""
      }${kindIcon("target", 16)} Featured targets.${searchKey()}`;
    } else {
      const gradeC = shown.some(isGradeC)
        ? ` <span class="atl-key-group is-gradec">${kindIcon("mover", 16)} Grey: grade C, fails a pipeline check (not put to a vote).</span>`
        : "";
      keyEl.innerHTML = `${KINDS.map((k) => `${kindIcon(k, 16)} ${KIND_TEXT[k].name}`).join(", ")}, ${kindIcon("target", 16)} featured target. A moving star's arrow shows where it will be in 100 years.${gradeC}${provisionalKey()}`;
    }
  }

  /** The faint zones along the ecliptic, when tonight's search is on the map. */
  function searchKey(): string {
    if (!search || !layers.search) return "";
    const sw = (state: string) => `<span class="atl-key-search is-${state}" aria-hidden="true"></span>`;
    const deferred = search.deferred ? `, ${sw("deferred")}crowded, left for last` : "";
    const failed = search.failed ? `, ${sw("failed")}failed` : "";
    return ` <span class="atl-key-group">Tonight's search along the ecliptic: ${sw("queued")}waiting, ${sw("extracting")}being searched, ${sw("combined")}searched (redder: more suspects)${deferred}${failed}. <a href="#/live">Live search</a></span>`;
  }

  /** Close in on a zone of tonight's run: what the dashed rings are, and aren't. */
  function provisionalKey(): string {
    if (!provisional || !layers.provisional || !provisional.suspects.length) return "";
    const note = !provisional.provisional ? "" : (search?.provisionalNote ?? "Checked against Gaia only; the WISE check in the final pass removes most of them.");
    return ` <span class="atl-key-group"><span class="atl-key-prov" aria-hidden="true"></span>Dashed rings: tonight's suspects in ${esc(zoneLabel(provisional.zone))}, ${
      provisional.provisional ? "provisional, before the WISE check" : "after the WISE check, not yet reviewed"
    }. Not on the list to check yet. ${esc(note)}</span>`;
  }

  function renderStatus(v: MapView) {
    if (!data) return;
    const all = data.candidates.length;
    if (!all) {
      statusEl.textContent = "Nothing to show yet.";
      return;
    }
    if (level === "whole") {
      // Say why the map holds fewer than the intro's total when likely mismatches are hidden.
      const hidden = mismatches ? 0 : data.candidates.filter((c) => state.kinds.includes(c.kind) && isLikelyMismatch(c)).length;
      statusEl.textContent = `${shownSentence(shown.length, hidden, zones.length)} Zoom into a red patch.`;
    } else if (level === "mid") {
      const n = map ? inView(shown, map, { w: stage.clientWidth, h: stage.clientHeight }).length : 0;
      statusEl.textContent = n ? `${plural(n, "object", "objects")} to check in view. Zoom in to see each one.` : "Nothing to check here. Zoom out to find a red patch.";
    } else {
      const n = map ? inView(shown, map, { w: stage.clientWidth, h: stage.clientHeight }).length : 0;
      const prov = provisionalSentence();
      statusEl.textContent = n
        ? `${plural(n, "signal", "signals")} in view. Click one to preview its blink.${prov}`
        : prov
          ? `Nothing to check here.${prov}`
          : "No signals here. Zoom out to find a red patch.";
    }
    void v;
  }

  /** The keyboard path: signals in view at close range, the busiest places otherwise. */
  function renderList() {
    if (!map || !data) {
      listToggle.textContent = "Signals in view";
      listToggle.disabled = true;
      return;
    }
    listToggle.disabled = false;
    if (showsSignals(level)) {
      // Nearest the centre first, grade C after the rest (C never leads a list).
      const items = gradeCLast(inView(shown, map, { w: stage.clientWidth, h: stage.clientHeight }));
      listToggle.textContent = `Signals in view (${countText(items.length)})`;
      listEl.innerHTML = items.length
        ? items
            .slice(0, LIST_MAX)
            .map(
              (c) => html`<li><button type="button" class="atl-list-item${isGradeC(c) ? " is-gradec" : ""}" data-id="${esc(c.id)}">${kindIcon(c.kind, 16)}<span>${esc(oneLine(c))}</span><span class="atl-list-pos">${esc(formatRaDec(c))}</span></button></li>`,
            )
            .join("") + (items.length > LIST_MAX ? `<li class="atl-list-more">${countText(items.length - LIST_MAX)} more: zoom in to narrow the list.</li>` : "")
        : `<li class="atl-list-more">No signals in view.</li>`;
    } else {
      const order = heatOrder(state.fov, stage.clientWidth || 800);
      const cells = cellsAt(order).slice(0, 12);
      listToggle.textContent = `Busiest places (${cells.length})`;
      listEl.innerHTML = cells.length
        ? cells
            .map(
              (c) => html`<li><button type="button" class="atl-list-item" data-cell="${c.pix}" data-order="${order}"><span class="atl-swatch" style="--a:0.6" aria-hidden="true"></span><span>${esc(plural(c.count, "object", "objects"))}</span><span class="atl-list-pos">near ${esc(formatRaDec(c.center))}</span></button></li>`,
            )
            .join("")
        : `<li class="atl-list-more">Nothing of these kinds.</li>`;
    }
  }

  let viewRaf: ReturnType<typeof setTimeout> | null = null;
  function onView(v: MapView) {
    if (destroyed || !visible) return;
    const nextLevel = levelFor(v.fov);
    const changed = nextLevel !== level;
    level = nextLevel;
    stage.classList.toggle("is-whole", level === "whole");
    state = { ...state, ra: wrap360(v.ra), dec: v.dec, fov: v.fov };
    publishSky();
    writeHash();
    hideTip();
    placePin();
    if (changed) renderKey();
    // The list and the status are cheap but not free: once the view settles.
    if (viewRaf) clearTimeout(viewRaf);
    viewRaf = setTimeout(() => {
      viewRaf = null;
      if (!visible || destroyed) return;
      updateProvisional(v);
      renderStatus(v);
      renderList();
    }, 120);
  }

  // ---- drawer ------------------------------------------------------------------------------
  function openDrawer(fill: (el: HTMLElement) => DrawerHandle) {
    drawer?.destroy();
    if (!drawerEl.contains(document.activeElement)) lastFocus = document.activeElement as HTMLElement | null;
    drawerEl.hidden = false;
    stage.classList.add("has-drawer");
    drawer = fill(drawerEl);
    drawerEl.scrollTop = 0;
    q<HTMLElement>("#atl-dr-title")?.focus({ preventScroll: true });
    // On a phone the drawer is a sheet over the lower half: bring the map to the top so the
    // chosen mark, at the map's centre, stays in sight above it.
    if (phone()) {
      const top = stage.getBoundingClientRect().top;
      if (Math.abs(top) > 4) window.scrollBy({ top, behavior: reduced ? "auto" : "smooth" });
    }
  }

  function phone(): boolean {
    try {
      return matchMedia("(max-width: 720px)").matches;
    } catch {
      return false;
    }
  }

  function closeDrawer() {
    drawer?.destroy();
    drawer = null;
    drawerEl.hidden = true;
    stage.classList.remove("has-drawer");
    const had = selected;
    selected = null;
    focusSky=null;publishSky();
    chain = new Set();
    if (had) map?.update(model());
    // The list re-renders when the map flies to a signal, so the button that opened the drawer may
    // be gone: fall back to its new copy, then the map, never to the page's top.
    const back =
      lastFocus && document.contains(lastFocus)
        ? lastFocus
        : ([...listEl.querySelectorAll<HTMLElement>("button[data-id]")].find((b) => had && b.dataset.id === had.id) ?? mapEl);
    back.focus({ preventScroll: true });
    lastFocus = null;
  }

  function nextNearby(from: Candidate): Candidate | null {
    let best: Candidate | null = null;
    let bestD = Infinity;
    for (const c of shown) {
      if (c.id === from.id || chain.has(c.id)) continue;
      const d = sepDeg(c, from);
      if (d < bestD) {
        bestD = d;
        best = c;
      }
    }
    return best;
  }

  function selectCandidate(c: Candidate, opts: { fly?: boolean; keepChain?: boolean } = {}) {
    hidePin();
    hideTip();
    if (!opts.keepChain) chain = new Set();
    chain.add(c.id);
    selected = c;
    focusSky={ra:c.ra,dec:c.dec,clock:c.clock,det:c.det};publishSky();
    map?.update(model());
    if (opts.fly && map) {
      const v = map.view();
      const fov = showsSignals(levelFor(v.fov)) ? v.fov : SIGNAL_FOV;
      map.flyTo({ ra: c.ra, dec: c.dec, fov }, !reduced);
    }
    openDrawer((d) =>
      renderCandidate(d, c, {
        reducedMotion: reduced,
        red: ink.red,
        hasNext: shown.length > chain.size,
        handlers: {
          review: (k) => ctx.navigate(`#/c/${encodeURIComponent(k.id)}`),
          blink: (k) => {
            // Static first: prepared frames open on the object's own page; the live API only without them.
            const prepared = preparedBlinkHash(k);
            if (prepared) ctx.navigate(prepared);
            else ctx.explore({ ra: k.ra, dec: k.dec, clock: k.clock, det: k.det });
          },
          next: (k) => {
            const n = nextNearby(k);
            if (n) selectCandidate(n, { fly: true, keepChain: true });
          },
          close: closeDrawer,
        },
      }),
    );
  }

  /** Featured targets open their prepared frames (static first). */
  function openTarget(t: Target) {
    if(ctx.skySelection){
      focusSky={ra:t.ra,dec:t.dec,clock:t.clock,det:t.det,slug:t.slug};publishSky();
      ctx.navigate('#/explore');return;
    }
    // The shell's explore() finds a months target at its own spot.
    if (featuredNear(t)?.slug === t.slug) return ctx.explore({ ra: t.ra, dec: t.dec });
    // The moving ones (hours clock) only by name: the ?t= deep link, which the shell reads on popstate.
    history.pushState(null, "", `${location.pathname}?t=${encodeURIComponent(t.slug)}${location.hash}`);
    dispatchEvent(new PopStateEvent("popstate"));
    ctx.navigate("#/explore");
  }

  function selectTarget(t: Target) {
    hidePin();
    selected = null;
    focusSky={ra:t.ra,dec:t.dec,clock:t.clock,det:t.det,slug:t.slug};publishSky();
    map?.update(model());
    openDrawer((d) => renderTarget(d, t, { open: openTarget, close: closeDrawer }));
  }

  // ---- pin, tooltip --------------------------------------------------------------------------
  function placePin() {
    if (!pinPos || !map) return;
    const p = map.screenOf(pinPos);
    if (!p || p.x < 0 || p.y < 0 || p.x > stage.clientWidth || p.y > stage.clientHeight) {
      pin.style.visibility = "hidden";
      return;
    }
    pin.style.visibility = "";
    pinAt = p;
    const w = pin.offsetWidth || 240;
    const x = Math.max(8, Math.min(stage.clientWidth - w - 8, p.x - w / 2));
    const y = p.y + 14 + (pin.offsetHeight || 80) > stage.clientHeight - 8 ? p.y - (pin.offsetHeight || 80) - 14 : p.y + 14;
    pin.style.transform = `translate(${Math.round(x)}px, ${Math.round(Math.max(8, y))}px)`;
  }

  function showPin(pos: SkyPos) {
    if(!drawerEl.hidden)closeDrawer();
    pinPos = { ra: wrap360(pos.ra), dec: pos.dec };
    focusSky=pinPos;publishSky();
    const near = featuredNear(pinPos);
    q<HTMLParagraphElement>("[data-pin-coords]").textContent = near ? `${formatRaDec(pinPos)}, by ${near.name}` : formatRaDec(pinPos);
    pin.hidden = false;
    placePin();
  }

  function hidePin() {
    if(pinPos){focusSky=null;publishSky();}
    pinPos = null;
    pinAt = null;
    pin.hidden = true;
  }
  void pinAt;

  function hideTip() {
    tip.hidden = true;
  }

  function showTip(c: Candidate | null, x: number, y: number) {
    if (!c) return hideTip();
    tip.textContent = oneLine(c);
    tip.hidden = false;
    const w = tip.offsetWidth;
    const left = Math.max(6, Math.min(stage.clientWidth - w - 6, x + 14));
    const top = Math.max(6, y - 34);
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }

  // ---- the map -------------------------------------------------------------------------------
  const callbacks = {
    onView,
    onSignal: (c: Candidate) => selectCandidate(c),
    onTarget: (t: Target) => selectTarget(t),
    onCell: (cell: Cell) => {
      hidePin();
      if (!map) return;
      const lvl = levelFor(map.view().fov);
      map.flyTo({ ...cell.center, fov: fovToOpen(cellSideDeg(cell.order), lvl) }, !reduced);
    },
    onEmpty: (pos: SkyPos) => {
      hideTip();
      if (pinPos && sepDeg(pinPos, pos) < 1e-6) return;
      showPin(pos);
    },
    onHover: (c: Candidate | null, x: number, y: number) => showTip(c, x, y),
  };

  function showNote(text: string | null) {
    note.hidden = text == null;
    if (text != null) note.innerHTML = `<p>${esc(text)}</p>`;
  }

  function startFallback(reason: string | null) {
    if (destroyed) return;
    map?.destroy();
    stage.classList.add("is-fallback");
    stage.classList.toggle("is-whole", levelFor(state.fov) === "whole");
    map = createFallbackMap(mapEl, { view: { ra: state.ra, dec: state.dec, fov: state.fov }, gal: state.gal }, callbacks);
    map.update(model());
    showNote(null);
    msg(
      reason
        ? `The globe can't open here (${reason}), so this is a flat map of the same objects. Click a red cell to look closer.`
        : null,
    );
    el.querySelectorAll<HTMLButtonElement>("[data-proj], [data-bg], [data-neg]").forEach((b) => (b.disabled = true));
    afterMapReady();
  }

  async function startMap() {
    if (destroyed || !visible || map || mapStarting) return;
    mapStarting = true;
    try {
      if (!browserHasWebGL2()) return startFallback("this browser has no WebGL2");
      const A = await loadAladinOnce();
      if (destroyed || !visible) return;
      map = createAladinMap(
        mapEl,
        A,
        { view: { ra: state.ra, dec: state.dec, fov: state.fov }, gal: state.gal, mol: state.mol, layers, reducedMotion: reduced },
        callbacks,
      );
      map.update(model());
      showNote(null);
      afterMapReady();
    } catch (e) {
      if (destroyed || !visible) return;
      if (import.meta.env.DEV) console.error("Sky: the globe failed, using the flat map.", e);
      // Clear whatever Aladin left behind before the flat map takes the stage.
      map?.destroy();
      mapEl.querySelectorAll(".atl-aladin").forEach((n) => n.remove());
      map = null;
      startFallback(e instanceof SkyUnavailable ? e.reason : "the sky viewer couldn't start");
    } finally {
      mapStarting = false;
    }
  }

  function afterMapReady() {
    if (!map) return;
    const v = map.view();
    state={...state,ra:v.ra,dec:v.dec,fov:v.fov};publishSky();
    level = levelFor(v.fov);
    renderKey();
    renderStatus(v);
    renderList();
    writeHash(true);
  }

  function publishSky() {
    // The whole-sky view's centre is not a choice: carry nothing, and the areas open on a
    // featured target (prepared frames) instead of building a live field at an arbitrary spot.
    const at=focusSky??(levelFor(state.fov)==='whole'?null:{ra:state.ra,dec:state.dec});
    q<HTMLElement>('[data-current-sky]').textContent=at?`Your sky: ${formatRaDec(at)}`:
      'Pick a point or zoom in to take this sky with you. From the whole sky, the areas open on a featured target.';
    ctx.skySelection?.(at,stateHash(state));
  }

  function msg(text: string | null, kind: "info" | "error" = "info") {
    msgEl.textContent = text ?? "";
    msgEl.dataset.kind = kind;
    measure();
  }

  /** Default view: the globe turned to face the most candidates. */
  /**
   * Default view: the whole sky turned to face the most candidates. A wide map opens on the
   * whole-sky ellipse (all of it fits); a tall one (a phone) on the globe, which fills it.
   */
  function defaultState(): AtlasState {
    const c = zones[0]?.center ?? meanPosition(data?.candidates ?? []) ?? { ra: 0, dec: 0 };
    const mol = defaultMol(stage.clientWidth, stage.clientHeight);
    return { ra: c.ra, dec: c.dec, fov: wholeFov(mol), gal: false, mol, kinds: [...KINDS] };
  }

  function applyState(next: AtlasState, animate: boolean) {
    const prev = state;
    state = next;
    if (prev.kinds.join() !== next.kinds.join()) {
      refilter();
      syncKindButtons();
      map?.update(model());
      renderKey();
    }
    if (prev.gal !== next.gal) {
      map?.setFrame(next.gal);
      syncSeg("[data-frame]", next.gal ? "gal" : "eq", "frame");
    }
    if (prev.mol !== next.mol) {
      map?.setProjection(next.mol);
      syncSeg("[data-proj]", next.mol ? "mol" : "globe", "proj");
    }
    map?.flyTo({ ra: next.ra, dec: next.dec, fov: next.fov }, animate && !reduced);
  }

  async function start() {
    started = true;
    // Tonight's search loads beside the candidates; the map takes it whenever it comes.
    void refreshSearch();
    try {
      data = await loadAtlasData();
    } catch {
      data = { generated: null, dataRelease: null, zones: [], candidates: [], origin: "none" };
    }
    if (destroyed) return;
    zones = zoneBoxes(data.candidates);
    const fromUrl = parseState(requestedParam);
    state = fromUrl ?? defaultState();
    refilter();
    renderCounts();
    measure();
    syncKindButtons();
    syncSeg("[data-frame]", state.gal ? "gal" : "eq", "frame");
    syncSeg("[data-proj]", state.mol ? "mol" : "globe", "proj");
    if (state.mol && state.fov === WHOLE_FOV) state.fov = 360;
    await startMap();
  }

  // ---- controls ------------------------------------------------------------------------------
  function syncSeg(sel: string, value: string, attr: string) {
    el.querySelectorAll<HTMLButtonElement>(sel).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[attr] === value)));
  }

  function syncKindButtons() {
    el.querySelectorAll<HTMLButtonElement>("[data-kind]").forEach((b) =>
      b.setAttribute("aria-pressed", String(state.kinds.includes(b.dataset.kind as CandidateKind))),
    );
  }

  el.querySelectorAll<HTMLButtonElement>("[data-proj]").forEach((b) =>
    b.addEventListener("click", () => {
      const mol = b.dataset.proj === "mol";
      if (mol === state.mol) return;
      state = { ...state, mol };
      syncSeg("[data-proj]", mol ? "mol" : "globe", "proj");
      map?.setProjection(mol);
      if (map && levelFor(map.view().fov) === "whole") map.flyTo({ ...map.view(), fov: wholeFov(mol) }, false);
      writeHash();
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-frame]").forEach((b) =>
    b.addEventListener("click", () => {
      const gal = b.dataset.frame === "gal";
      if (gal === state.gal) return;
      state = { ...state, gal };
      syncSeg("[data-frame]", gal ? "gal" : "eq", "frame");
      map?.setFrame(gal);
      writeHash();
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-kind]").forEach((b) =>
    b.addEventListener("click", () => {
      const k = b.dataset.kind as CandidateKind;
      const kinds = state.kinds.includes(k) ? state.kinds.filter((x) => x !== k) : KINDS.filter((x) => x === k || state.kinds.includes(x));
      state = { ...state, kinds };
      refilter();
      syncKindButtons();
      map?.update(model());
      renderKey();
      if (map) {
        renderStatus(map.view());
        renderList();
      }
      writeHash();
    }),
  );
  adjust.addEventListener("click", () => {
    const open = adjust.getAttribute("aria-expanded") !== "true";
    adjust.setAttribute("aria-expanded", String(open));
    more.hidden = !open;
    measure();
  });
  q<HTMLButtonElement>('[data-show-paths]').addEventListener('click',()=>q<HTMLElement>('[data-paths]').scrollIntoView({block:'start',behavior:reduced?'auto':'smooth'}));
  el.querySelectorAll<HTMLButtonElement>("[data-bg]").forEach((b) =>
    b.addEventListener("click", () => {
      layers = { ...layers, background: b.dataset.bg as BackgroundId };
      syncSeg("[data-bg]", layers.background, "bg");
      q<HTMLElement>('[data-survey]').textContent=`Sky: ${BACKGROUNDS.find(b=>b.id===layers.background)!.label} · Signals: SPHEREx`;
      map?.update(model());
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-neg]").forEach((b) =>
    b.addEventListener("click", () => {
      layers = { ...layers, negative: b.dataset.neg === "1" };
      syncSeg("[data-neg]", layers.negative ? "1" : "0", "neg");
      stage.classList.toggle("is-positive", !layers.negative);
      map?.update(model());
    }),
  );
  q<HTMLButtonElement>("[data-mismatch]").addEventListener("click", (ev) => {
    mismatches = !mismatches;
    (ev.currentTarget as HTMLButtonElement).setAttribute("aria-pressed", String(mismatches));
    refilter();
    renderCounts();
    map?.update(model());
    renderKey();
    if (map) {
      renderStatus(map.view());
      renderList();
    }
  });
  el.querySelectorAll<HTMLButtonElement>("[data-layer]").forEach((b) =>
    b.addEventListener("click", () => {
      const key = b.dataset.layer as "heat" | "zones" | "signals" | "search" | "provisional";
      layers = { ...layers, [key]: !layers[key] };
      b.setAttribute("aria-pressed", String(layers[key]));
      map?.update(model());
      renderKey();
      if (map && key === "provisional") {
        updateProvisional(map.view());
        renderStatus(map.view());
      }
    }),
  );

  q<HTMLFormElement>(".atl-find").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    msg(null);
    const pos = parseCoords(text);
    const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    if (pos) {
      map?.flyTo({ ...pos, fov: SPOT_FOV }, !reduced);
      showPin(pos);
      return;
    }
    const t = TARGETS.find((x) => [x.name, x.short, x.slug].some((n) => norm(n) === norm(text) || (norm(text).length >= 4 && norm(n).includes(norm(text)))));
    if (t) {
      map?.flyTo({ ra: t.ra, dec: t.dec, fov: SPOT_FOV }, !reduced);
      selectTarget(t);
      return;
    }
    const c = data?.candidates.find((x) => x.id === text);
    if (c) {
      if (!onMap(c)) {
        state = { ...state, kinds: KINDS.filter((k) => k === c.kind || state.kinds.includes(k)) };
        if (isLikelyMismatch(c)) {
          mismatches = true;
          q<HTMLButtonElement>("[data-mismatch]").setAttribute("aria-pressed", "true");
          renderCounts();
        }
        refilter();
        syncKindButtons();
      }
      selectCandidate(c, { fly: true });
      return;
    }
    const z = zones.find((x) => norm(zoneName(x.zone)).startsWith(norm(text)) || norm(x.zone) === norm(text));
    if (z) {
      map?.flyTo({ ...z.center, fov: Math.max(2, Math.min(LEVEL_FOV.whole * 0.75, (z.dec1 - z.dec0) * 2.2)) }, !reduced);
      return;
    }
    msg(`“${text}” isn't coordinates or a featured name. Try 122.55 17.00, 08h10m12s +17d00m13s or Barnard's Star.`, "error");
  });

  q<HTMLButtonElement>("[data-pin-blink]").addEventListener("click", () => {
    if (!pinPos) return;
    const p = pinPos;
    hidePin();
    const near = featuredNear(p);
    if (near) openTarget(near);
    else ctx.explore({ ra: p.ra, dec: p.dec });
  });
  q<HTMLButtonElement>("[data-pin-copy]").addEventListener("click", (ev) => {
    if (!pinPos) return;
    const btn = ev.currentTarget as HTMLButtonElement;
    const text = `${pinPos.ra.toFixed(6)} ${pinPos.dec.toFixed(6)}`;
    try {
      navigator.clipboard.writeText(text).then(
        () => {
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 1400);
        },
        () => (btn.textContent = "Copy failed"),
      );
    } catch {
      btn.textContent = "Copy failed";
    }
  });
  q<HTMLButtonElement>("[data-pin-x]").addEventListener("click", hidePin);

  el.querySelectorAll<HTMLButtonElement>("[data-zoom]").forEach((b) =>
    b.addEventListener("click", () => {
      if (!map) return;
      if (b.dataset.zoom === "in") map.zoomBy(1 / 1.6);
      else if (b.dataset.zoom === "out") map.zoomBy(1.6);
      else map.flyTo({ ...map.view(), fov: wholeFov(state.mol) }, !reduced);
    }),
  );

  listToggle.addEventListener("click", () => {
    const open = listToggle.getAttribute("aria-expanded") !== "true";
    listToggle.setAttribute("aria-expanded", String(open));
    listEl.hidden = !open;
    if (open) renderList();
  });
  listEl.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button.atl-list-item");
    if (!b || !map) return;
    if (b.dataset.id) {
      const c = shown.find((x) => x.id === b.dataset.id);
      if (c) selectCandidate(c, { fly: true });
    } else if (b.dataset.cell) {
      const order = Number(b.dataset.order);
      const cell = cellsAt(order).find((c) => c.pix === Number(b.dataset.cell));
      if (cell) map.flyTo({ ...cell.center, fov: fovToOpen(cellSideDeg(order), levelFor(map.view().fov)) }, !reduced);
    }
  });

  // Keyboard on the map: arrows pan, + and − zoom, Enter opens the signal nearest the centre.
  mapEl.addEventListener("keydown", (ev) => {
    if (!map) return;
    const k = ev.key;
    const pan: Record<string, [number, number]> = { ArrowLeft: [-0.15, 0], ArrowRight: [0.15, 0], ArrowUp: [0, -0.15], ArrowDown: [0, 0.15] };
    if (pan[k]) {
      ev.preventDefault();
      map.panBy(pan[k][0], pan[k][1]);
    } else if (k === "+" || k === "=") {
      ev.preventDefault();
      map.zoomBy(1 / 1.6);
    } else if (k === "-" || k === "_") {
      ev.preventDefault();
      map.zoomBy(1.6);
    } else if (k === "Enter") {
      ev.preventDefault();
      if (showsSignals(level)) {
        const first = inView(shown, map, { w: stage.clientWidth, h: stage.clientHeight })[0];
        if (first) selectCandidate(first);
      } else {
        const order = heatOrder(state.fov, stage.clientWidth || 800);
        const v = map.view();
        const cell = [...cellsAt(order)].sort((a, b) => sepDeg(a.center, v) - sepDeg(b.center, v))[0];
        if (cell) callbacks.onCell(cell);
      }
    }
  });

  const onKey = (ev: KeyboardEvent) => {
    if (ev.key !== "Escape" || !visible) return;
    if (!pin.hidden) hidePin();
    else if (!drawerEl.hidden) closeDrawer();
  };
  document.addEventListener("keydown", onKey);

  // The fallback map redraws on resize itself; Aladin resizes its canvas, the status follows.
  const onResize = () => {
    measure();
    if (map) {
      placePin();
      renderStatus(map.view());
    }
  };
  addEventListener("resize", onResize);
  // Whatever sits above the map (the header, the lede, a message, Adjust) moves its top edge.
  let ro: ResizeObserver | null = null;
  if (typeof ResizeObserver === "function") {
    ro = new ResizeObserver(() => measure());
    for (const n of [document.querySelector("header.top"), q(".atl-intro"), q(".atl-controls"), more, msgEl]) if (n) ro.observe(n);
  }

  return {
    show(param) {
      requestedParam = param;
      const wasVisible = visible;
      visible = true;
      measure();
      if (!started) {
        void start();
        return;
      }
      resumeSearch();
      const next = parseState(param);
      if (next) {
        if (!sameState(next, state)) applyState(next, wasVisible);
      } else if (wasVisible && data) {
        // The Sky link while already here: back to the whole sky.
        closeDrawer();
        hidePin();
        applyState({ ...defaultState(), gal: state.gal, mol: state.mol, kinds: state.kinds, fov: wholeFov(state.mol) }, true);
      }
      if (data && !map) void startMap();
      writeHash(true);
    },
    hide() {
      visible = false;
      if (viewRaf) clearTimeout(viewRaf);
      viewRaf = null;
      drawer?.pause();
      hideTip();
      stopSearch();
      if (hashTimer) clearTimeout(hashTimer);
    },
    destroy() {
      destroyed = true;
      if (viewRaf) clearTimeout(viewRaf);
      viewRaf = null;
      stopSearch();
      drawer?.destroy();
      map?.destroy();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("visibilitychange", onVisibility);
      removeEventListener("resize", onResize);
      ro?.disconnect();
      if (hashTimer) clearTimeout(hashTimer);
      el.innerHTML = "";
      el.classList.remove("atl");
    },
  };
};
