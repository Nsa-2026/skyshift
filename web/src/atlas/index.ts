// Sky, the home page (web/DESIGN.md "Sky: the home page"). The whole SPHEREx sky filling the page
// under the header: each searched zone one outline with its count, objects to check as dots when
// zoomed in, featured targets by name. One click opens the Viewer: a dot opens that object, a
// featured target its prepared frames, any other spot the Viewer there. The search box does the
// same for coordinates or a name. The view lives in the URL (#/sky/<ra>,<dec>,<fov>[,gal][,mol][,kinds]).
// Aladin Lite draws the globe; without WebGL2 a static Aitoff SVG of the same marks stands in.
import "./atlas.css";
import "./home.css";
import { ApiFailure, resolve } from "../api/client";
import type { Candidate, CandidateKind, SkyPos } from "../api/types";
import { formatRaDec } from "../app/astro";
import { parseCoords } from "../app/coords";
import type { MountPage, PageContext } from "../app/modules";
import { TARGETS, featuredNear, targetBySlug, type Target } from "../app/state";
import { browserHasWebGL2, loadAladinOnce, SkyUnavailable } from "../sky/loader";
import { ALLWISE, SPHEREX, TWO_MASS } from "../sky/layers";
import { createAladinMap } from "./aladin-map";
import { loadAtlasData, type AtlasData } from "./data";
import { kindIcon } from "./drawer";
import { createFallbackMap } from "./fallback";
import { parseState, sameState, stateHash, type AtlasState } from "./hash";
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
import type { Box } from "./labels";
import { inView, type BackgroundId, type Layers, type MapHandle, type MapModel, type MapView } from "./map";
import { meanPosition, sepDeg, wrap360 } from "./sphere";
import { WHOLE_FOV, defaultMol, fovToOpen, heatOrder, levelFor, showsDots, showsHeat, showsSignals, wholeFov, type Level } from "./zoom";
import { cellSideDeg } from "./healpix";
import { mountZoomFocus } from './focus';
import { liveLine, loadSearch, loadZoneSuspects, provisionalZone, type ProvisionalSet, type SearchLayer } from "./search";
import { areaFov, areaName, badgeTexts, zoneAreas, type ZoneArea } from "./zones";

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
    <div class="atl-stage" data-stage>
      <div class="atl-map" data-map tabindex="0" role="region" aria-roledescription="sky map"
        aria-label="Sky map" aria-describedby="atl-keys"></div>
      <p class="visually-hidden" id="atl-keys">Arrow keys move the map, plus and minus zoom. Enter opens the zone nearest the centre on the whole sky, and the object to check nearest the centre closer in. The list below the map holds what is in view.</p>
      <div class="atl-note" data-note role="status"><p>Loading the sky…</p></div>
      <div class="atl-tip" data-tip role="tooltip" hidden></div>

      <div class="atl-top" data-top data-map-chrome>
        <div class="atl-intro">
          <h1 class="atl-lede" id="atl-title">Blink any spot of NASA's SPHEREx sky and see what moved.</h1>
          <div class="atl-controls-side">
            <form class="atl-find" role="search">
              <label class="visually-hidden" for="atl-q">Coordinates or a name to blink</label>
              <input id="atl-q" class="input atl-find-input" name="q" autocomplete="off" spellcheck="false" placeholder="Coordinates or a name" />
              <button class="btn btn-primary atl-find-go" type="submit">Blink</button>
            </form>
            <button class="atl-adjust" type="button" aria-expanded="false" aria-controls="atl-more">Adjust</button>
          </div>
          <div class="atl-shape">
            <span class="atl-shape-label" id="atl-shape-label">Map shape</span>
            <div class="seg" role="group" aria-labelledby="atl-shape-label">
              <button type="button" data-proj="mol" aria-pressed="false" title="Oval: the whole sky at once (Mollweide projection)"><span class="atl-shape-icon is-oval" aria-hidden="true"></span>Oval</button>
              <button type="button" data-proj="globe" aria-pressed="true" title="Globe: half the sky at a time; drag to turn it"><span class="atl-shape-icon" aria-hidden="true"></span>Globe</button>
            </div>
          </div>
          <p class="atl-msg" data-msg role="status" aria-live="polite"></p>
        </div>
        <div class="atl-more" id="atl-more" hidden>
          <div class="atl-more-row">
            <div class="seg" role="group" aria-label="Coordinate grid">
              <button type="button" data-frame="eq" aria-pressed="true" title="Right ascension and declination">Equatorial</button>
              <button type="button" data-frame="gal" aria-pressed="false" title="Galactic longitude and latitude: the Milky Way runs along the equator">Galactic</button>
            </div>
          </div>
          <div class="atl-more-row atl-kinds" role="group" aria-label="Kinds of object shown">
            ${KINDS.map(
              (k) => html`<button class="atl-kind" type="button" data-kind="${k}" aria-pressed="true" title="${KIND_TEXT[k].meaning}">
                ${kindIcon(k, 18)}<span>${KIND_TEXT[k].plural}</span><span class="atl-kind-n" data-kind-n="${k}"></span><span class="visually-hidden" data-kind-hidden="${k}"></span>
              </button>`,
            ).join("")}
          </div>
          <div class="atl-more-row">
            <span class="atl-more-label" id="atl-bg-label">Background</span>
            <div class="seg" role="group" aria-labelledby="atl-bg-label">
              ${BACKGROUNDS.map((b) => html`<button type="button" data-bg="${b.id}" aria-pressed="${b.id === "2mass"}" title="${b.about}">${b.label}</button>`).join("")}
            </div>
          </div>
          <div class="atl-more-row">
            <span class="atl-more-label" id="atl-plate-label">Plate</span>
            <div class="seg" role="group" aria-labelledby="atl-plate-label">
              <button type="button" data-neg="1" aria-pressed="false" title="Dark stars on light glass, as astronomers blink plates">Negative</button>
              <button type="button" data-neg="0" aria-pressed="true" title="The survey's own colours, bright stars on dark sky">Positive</button>
            </div>
          </div>
          <div class="atl-more-row">
            <button class="atl-switch" type="button" data-layer="zones" aria-pressed="true">Zones</button>
            <button class="atl-switch" type="button" data-layer="signals" aria-pressed="true">Objects to check</button>
            <button class="atl-switch" type="button" data-layer="heat" aria-pressed="false"
              title="Objects to check counted per sky cell, a darker red for more">Heat map</button>
            <button class="atl-switch" type="button" data-mismatch aria-pressed="false"
              title="Moving stars faster than any star can move: almost certainly two different sources paired by mistake">Likely mismatches</button>
          </div>
          <div class="atl-more-row">
            <button class="atl-switch" type="button" data-layer="search" aria-pressed="false"
              title="The whole-sky run's zones along the ecliptic, shaded by how far tonight's search has got">Tonight's search</button>
            <button class="atl-switch" type="button" data-layer="provisional" aria-pressed="true"
              title="Zoomed into a searched zone: its suspects so far, as dashed rings. Not on the list to check yet: the WISE check still has to remove most of them">Provisional suspects</button>
          </div>
          <p class="atl-more-note" data-survey>Sky: 2MASS. Objects to check: SPHEREx.</p>
        </div>
      </div>

      <p class="atl-status" data-status data-map-chrome aria-live="polite"></p>
      <div class="atl-zoom" data-map-chrome role="group" aria-label="Zoom">
        <button type="button" data-zoom="in" aria-label="Zoom in">+</button>
        <button type="button" data-zoom="out" aria-label="Zoom out">−</button>
        <button type="button" data-zoom="all" title="Back to the whole sky">Whole sky</button>
      </div>
      <button class="atl-reveal" type="button" data-map-controls aria-label="Show controls" aria-expanded="false" title="Show controls (Escape)" hidden>Show controls</button>
    </div>

    <div class="atl-below">
      <div class="atl-count-box"><p class="atl-count" data-count aria-live="polite">Loading the list…</p><p class="atl-count-note">${LIST_NOTE}</p><a class="atl-live" href="#/live" data-live hidden></a></div>
      <p class="atl-key" data-key></p>
      <div class="atl-listbox">
        <button class="atl-list-toggle" type="button" aria-expanded="false" aria-controls="atl-list" data-list-toggle disabled>In view</button>
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
  const keyEl = q<HTMLParagraphElement>("[data-key]");
  const listEl = q<HTMLOListElement>("[data-list]");
  const listToggle = q<HTMLButtonElement>("[data-list-toggle]");
  const sourceEl = q<HTMLParagraphElement>("[data-source]");
  const input = q<HTMLInputElement>("#atl-q");
  const adjust = q<HTMLButtonElement>(".atl-adjust");
  const more = q<HTMLDivElement>("#atl-more");
  const zoomFocus = mountZoomFocus(el);
  // When the panels fade out or come back, the names on the map may use or must leave their room.
  const chromeObs =
    typeof MutationObserver === "function"
      ? new MutationObserver(() => {
          if (map && visible) map.update(model());
        })
      : null;
  for (const n of el.querySelectorAll("[data-top], [data-map-controls]")) chromeObs?.observe(n, { attributes: true, attributeFilter: ["inert", "hidden"] });
  /** How far the site header reaches down over the map (it floats there, and fades as the sky zooms in). */
  let headerH = 0;

  /**
   * The stage bleeds to the window's edges, whatever the page's own gutters, and ends at the
   * bottom of the first screen: its offset and width come from where the page actually sits.
   */
  function measure() {
    try {
      const vw = document.documentElement.clientWidth;
      const cs = getComputedStyle(el);
      const left = el.getBoundingClientRect().left + (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.borderLeftWidth) || 0);
      el.style.setProperty("--atl-vw", `${vw}px`);
      el.style.setProperty("--atl-left", `${-Math.round(left)}px`);
      // Panels start below the header, and the whole sky is fitted clear of it.
      const header = el.closest("#app")?.querySelector<HTMLElement>(":scope > .top");
      const over = header ? header.getBoundingClientRect().bottom - stage.getBoundingClientRect().top : 0;
      const before = headerH;
      headerH = Number.isFinite(over) ? Math.max(0, Math.round(over)) : 0;
      el.style.setProperty("--atl-header-height", `${headerH}px`);
      // The panels moved: refit the whole sky and place the names again.
      if (headerH !== before && map) map.update(model());
      const top = stage.getBoundingClientRect().top + scrollY;
      if (Number.isFinite(top)) el.style.setProperty("--atl-top", `${Math.max(0, Math.round(top))}px`);
    } catch {
      /* CSS defaults hold */
    }
  }

  const reduced = prefersReducedMotion();

  // ---- state ---------------------------------------------------------------------------------
  let state: AtlasState = { ra: 0, dec: 0, fov: WHOLE_FOV, gal: false, mol: false, kinds: [...KINDS] };
  // No heat map by default: each zone is one outline with its count, and objects show as dots closer in.
  let layers: Layers = { heat: false, zones: true, signals: true, search: false, provisional: true, background: "2mass", negative: false };
  stage.classList.add("is-positive");
  /** Tonight's search (the sky run's progress file), refreshed every minute while the page shows. */
  let search: SearchLayer | null = null;
  /** The provisional suspects of the combined zone in view, when zoomed in on one. */
  let provisional: ProvisionalSet | null = null;
  let data: AtlasData | null = null;
  let shown: Candidate[] = [];
  let zones: ZoneBox[] = [];
  let areas: ZoneArea[] = [];
  const cellCache = new Map<number, Cell[]>();
  let focusSky: Parameters<NonNullable<PageContext["skySelection"]>>[0] | null = null;
  let map: MapHandle | null = null;
  let mapStarting = false;
  let visible = false;
  let started = false;
  let destroyed = false;
  // Route changes can arrive while the candidates/CDN are still loading.
  let requestedParam: string | null = null;
  let level: Level = "whole";

  const cellsAt = (order: number): Cell[] => {
    let c = cellCache.get(order);
    if (!c) {
      c = binCandidates(shown, order);
      cellCache.set(order, c);
    }
    return c;
  };

  function model(): MapModel {
    return { candidates: shown, cellsAt, areas, targets: TARGETS, selectedId: null, layers, search, provisional };
  }

  /** Likely mismatches (implausible_pm) stay off the map unless Adjust turns them on. */
  let mismatches = false;
  const onMap = (c: Candidate) => state.kinds.includes(c.kind) && (mismatches || !isLikelyMismatch(c));

  function refilter() {
    const all = data?.candidates ?? [];
    shown = all.filter(onMap);
    cellCache.clear();
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
    if (map) renderStatus();
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

  // Run zones (ecl…) are internal names: say "this area" instead.
  const zoneLabel = (name: string) => (/^ecl/i.test(name) ? "this area" : zoneName(name));

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
    const positive = !layers.negative && !stage.classList.contains("is-fallback");
    keyEl.classList.toggle("is-positive", positive);
    const zonesKey = layers.zones ? `<span class="atl-key-zone" aria-hidden="true"></span>Outlined: the zones searched so far. Dots: the brightest named stars. ` : "";
    const heat = (() => {
      if (!layers.heat || !showsHeat(level)) return "";
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
      return `Red cells: objects to check per ${side >= 3 ? Math.round(side) : side.toFixed(1)}° cell, ${sw}. `;
    })();
    if (level === "whole") {
      keyEl.innerHTML = `${zonesKey}${heat}${kindIcon("target", 16)} Featured targets. Click a zone to zoom in, a target or any other spot to blink it.${searchKey()}`;
    } else if (level === "mid") {
      keyEl.innerHTML = `${zonesKey}${heat}Open rings: objects to check, ${positive ? "cyan" : "red"} for movers and moving stars; violet diamonds for odd spectra. Grey: grade C, fails a pipeline check. Zoom in for motion marks. ${kindIcon("target", 16)} Featured targets.${searchKey()}`;
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

  const inViewNow = () => (map ? inView(shown, map, { w: stage.clientWidth, h: stage.clientHeight }) : []);

  function renderStatus() {
    if (!data) return;
    if (!data.candidates.length) {
      statusEl.textContent = "Click any spot to blink it.";
      return;
    }
    if (level === "whole") {
      // Say why the map holds fewer than the total when likely mismatches are hidden.
      const hidden = mismatches ? 0 : data.candidates.filter((c) => state.kinds.includes(c.kind) && isLikelyMismatch(c)).length;
      statusEl.textContent = `${shownSentence(shown.length, hidden, zones.length)} Click a zone to zoom in, or any spot to blink it.`;
      return;
    }
    const n = layers.signals ? inViewNow().length : 0;
    const prov = provisionalSentence();
    statusEl.textContent = n
      ? `${plural(n, "object", "objects")} to check in view. Click ${level === "mid" ? "a dot" : "one"} to open it, or any other spot to blink it.${prov}`
      : `Nothing to check here. Click any spot to blink it.${prov}`;
  }

  /** The keyboard path: the zones on the whole sky, the objects in view closer in. */
  function renderList() {
    if (!map || !data) {
      listToggle.textContent = "In view";
      listToggle.disabled = true;
      return;
    }
    listToggle.disabled = false;
    if (showsDots(level) && layers.signals) {
      // Nearest the centre first, grade C after the rest (C never leads a list).
      const items = gradeCLast(inViewNow());
      listToggle.textContent = `Objects to check in view (${countText(items.length)})`;
      listEl.innerHTML = items.length
        ? items
            .slice(0, LIST_MAX)
            .map(
              (c) => html`<li><button type="button" class="atl-list-item${isGradeC(c) ? " is-gradec" : ""}" data-id="${esc(c.id)}">${kindIcon(c.kind, 16)}<span>${esc(oneLine(c))}</span><span class="atl-list-pos">${esc(formatRaDec(c))}</span></button></li>`,
            )
            .join("") + (items.length > LIST_MAX ? `<li class="atl-list-more">${countText(items.length - LIST_MAX)} more: zoom in to narrow the list.</li>` : "")
        : `<li class="atl-list-more">Nothing to check in view.</li>`;
    } else {
      listToggle.textContent = `Searched zones (${areas.length})`;
      listEl.innerHTML = areas.length
        ? areas
            .map(
              (a) => html`<li><button type="button" class="atl-list-item" data-area="${esc(a.id)}"><span class="atl-key-zone" aria-hidden="true"></span><span>${esc(badgeTexts(a)[0])}</span><span class="atl-list-pos">near ${esc(formatRaDec(a.center))}</span></button></li>`,
            )
            .join("")
        : `<li class="atl-list-more">No zones searched yet.</li>`;
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
    zoomFocus.update(v.fov, map?.kind === 'svg' ? false : state.mol);
    publishSky();
    writeHash();
    hideTip();
    if (changed) renderKey();
    // The list and the status are cheap but not free: once the view settles.
    if (viewRaf) clearTimeout(viewRaf);
    viewRaf = setTimeout(() => {
      viewRaf = null;
      if (!visible || destroyed) return;
      updateProvisional(v);
      renderStatus();
      renderList();
    }, 120);
  }

  // ---- one click to the Viewer -----------------------------------------------------------------
  /** An object to check opens on its own page, its spot in the Viewer. */
  function openCandidate(c: Candidate) {
    ctx.navigate(`#/c/${encodeURIComponent(c.id)}`);
  }

  /** Featured targets open their prepared frames (static first). */
  function openTarget(t: Target) {
    if (ctx.skySelection) {
      focusSky = { ra: t.ra, dec: t.dec, clock: t.clock, det: t.det, slug: t.slug };
      publishSky();
      ctx.navigate("#/explore");
      return;
    }
    // The shell's explore() finds a months target at its own spot.
    if (featuredNear(t)?.slug === t.slug) return ctx.explore({ ra: t.ra, dec: t.dec });
    // The moving ones (hours clock) only by name: the ?t= deep link, which the shell reads on popstate.
    history.pushState(null, "", `${location.pathname}?t=${encodeURIComponent(t.slug)}${location.hash}`);
    dispatchEvent(new PopStateEvent("popstate"));
    ctx.navigate("#/explore");
  }

  /** Any other spot: the Viewer there (a featured target's prepared frames when it is in one's field). */
  function openSpot(pos: SkyPos) {
    const p = { ra: wrap360(pos.ra), dec: pos.dec };
    const near = featuredNear(p);
    if (near) return openTarget(near);
    ctx.explore(p);
  }

  /** A zone: fly in until its objects show as dots. */
  function openArea(a: ZoneArea) {
    hideTip();
    const w = stage.clientWidth;
    const h = stage.clientHeight;
    map?.flyTo({ ...a.center, fov: areaFov(a, w > 0 && h > 0 ? w / h : 1) }, !reduced);
  }

  // ---- tooltip -------------------------------------------------------------------------------
  function hideTip() {
    tip.hidden = true;
  }

  function showTip(c: Candidate | null, x: number, y: number) {
    if (!c) return hideTip();
    tip.textContent = oneLine(c);
    tip.hidden = false;
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    const left = Math.max(6, Math.min(stage.clientWidth - w - 6, x + 14));
    const top = Math.max(6, Math.min(stage.clientHeight - h - 6, y - h - 12));
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }

  /** The controls floating over the map, in the map's coordinates: names and badges keep off them. */
  function reserved(): Box[] {
    const s = stage.getBoundingClientRect();
    const out: Box[] = [];
    // The header over the map, unless it has faded out.
    const header = el.closest("#app")?.querySelector<HTMLElement>(":scope > .top");
    if (headerH > 0 && !header?.hasAttribute("inert")) out.push({ x0: 0, y0: -1000, x1: s.width || 1e5, y1: headerH });
    for (const n of stage.querySelectorAll<HTMLElement>(".atl-intro, .atl-more, .atl-status, .atl-zoom, .atl-reveal")) {
      if (n.hidden || !n.offsetParent || n.closest("[inert]")) continue;
      const r = n.getBoundingClientRect();
      if (!(r.width > 0) || !(r.height > 0)) continue;
      out.push({ x0: r.left - s.left, y0: r.top - s.top, x1: r.right - s.left, y1: r.bottom - s.top });
    }
    return out;
  }

  // ---- the map -------------------------------------------------------------------------------
  const callbacks = {
    onView,
    onSignal: (c: Candidate) => openCandidate(c),
    onTarget: (t: Target) => openTarget(t),
    onCell: (cell: Cell) => {
      if (!map) return;
      const lvl = levelFor(map.view().fov);
      map.flyTo({ ...cell.center, fov: fovToOpen(cellSideDeg(cell.order), lvl) }, !reduced);
    },
    onZone: (a: ZoneArea) => openArea(a),
    onEmpty: (pos: SkyPos) => {
      hideTip();
      openSpot(pos);
    },
    onHover: (c: Candidate | null, x: number, y: number) => showTip(c, x, y),
    reserved,
    insetTop: () => headerH,
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
    msg(reason ? `The globe can't open here (${reason}), so this is a flat map of the same objects. Click a zone to look closer.` : null);
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
      mapEl.querySelectorAll(".atl-aladin, .atl-labelcanvas").forEach((n) => n.remove());
      map = null;
      startFallback(e instanceof SkyUnavailable ? e.reason : "the sky viewer couldn't start");
    } finally {
      mapStarting = false;
    }
  }

  function afterMapReady() {
    if (!map) return;
    const v = map.view();
    state = { ...state, ra: v.ra, dec: v.dec, fov: v.fov };
    publishSky();
    zoomFocus.update(v.fov, map.kind === "svg" ? false : state.mol);
    level = levelFor(v.fov);
    stage.classList.toggle("is-whole", level === "whole");
    renderKey();
    renderStatus();
    renderList();
    writeHash(true);
  }

  function publishSky() {
    // The whole-sky view's centre is not a choice: carry nothing, and the areas open on a
    // featured target (prepared frames) instead of building a live field at an arbitrary spot.
    const at = focusSky ?? (levelFor(state.fov) === "whole" ? null : { ra: state.ra, dec: state.dec });
    ctx.skySelection?.(at, stateHash(state));
  }

  function msg(text: string | null, kind: "info" | "error" = "info") {
    msgEl.textContent = text ?? "";
    msgEl.dataset.kind = kind;
    // The message moves the controls' edge: names on the map keep off it.
    map?.update(model());
  }

  /**
   * Default view: the whole sky turned to face the busiest zone. A wide map opens on the
   * whole-sky ellipse; a tall one (a phone) on the globe, which fills it.
   */
  function defaultState(): AtlasState {
    const c = areas[0]?.center ?? meanPosition(data?.candidates ?? []) ?? { ra: 0, dec: 0 };
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
    if (map) zoomFocus.update(map.view().fov, map.kind === 'svg' ? false : state.mol);
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
    areas = zoneAreas(zones);
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
      if (map) zoomFocus.update(map.view().fov, map.kind === 'svg' ? false : state.mol);
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
        renderStatus();
        renderList();
      }
      writeHash();
    }),
  );
  function setAdjust(open: boolean) {
    adjust.setAttribute("aria-expanded", String(open));
    more.hidden = !open;
    map?.update(model());
  }
  adjust.addEventListener("click", () => setAdjust(adjust.getAttribute("aria-expanded") !== "true"));
  el.querySelectorAll<HTMLButtonElement>("[data-bg]").forEach((b) =>
    b.addEventListener("click", () => {
      layers = { ...layers, background: b.dataset.bg as BackgroundId };
      syncSeg("[data-bg]", layers.background, "bg");
      q<HTMLElement>("[data-survey]").textContent = `Sky: ${BACKGROUNDS.find((x) => x.id === layers.background)!.label}. Objects to check: SPHEREx.`;
      map?.update(model());
    }),
  );
  el.querySelectorAll<HTMLButtonElement>("[data-neg]").forEach((b) =>
    b.addEventListener("click", () => {
      layers = { ...layers, negative: b.dataset.neg === "1" };
      syncSeg("[data-neg]", layers.negative ? "1" : "0", "neg");
      stage.classList.toggle("is-positive", !layers.negative);
      map?.update(model());
      renderKey();
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
      renderStatus();
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
      if (map) {
        if (key === "provisional") updateProvisional(map.view());
        renderStatus();
        renderList();
      }
    }),
  );

  // The search box opens the Viewer: coordinates or a featured name at once, an object to check
  // by its id, a zone by its name on the map, anything else through the name list (/api/resolve).
  let resolveCtl: AbortController | null = null;
  q<HTMLFormElement>(".atl-find").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    resolveCtl?.abort();
    resolveCtl = null;
    const text = input.value.trim();
    if (!text) return;
    msg(null);
    const pos = parseCoords(text);
    if (pos) return openSpot(pos);
    const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    const t = TARGETS.find((x) => [x.name, x.short, x.slug].some((n) => norm(n) === norm(text) || (norm(text).length >= 4 && norm(n).includes(norm(text)))));
    if (t) return openTarget(t);
    const c = data?.candidates.find((x) => x.id === text);
    if (c) return openCandidate(c);
    const a = areas.find((x) => x.zones.some((z) => norm(zoneName(z)).startsWith(norm(text)) || norm(z) === norm(text)) || norm(areaName(x)) === norm(text));
    if (a) return openArea(a);
    const ctl = new AbortController();
    resolveCtl = ctl;
    msg("Looking up the name…");
    try {
      const r = await resolve(text, ctl.signal);
      if (ctl.signal.aborted || destroyed) return;
      msg(null);
      const known = targetBySlug(r.slug);
      if (known) openTarget(known);
      else openSpot({ ra: r.ra, dec: r.dec });
    } catch (e) {
      if (ctl.signal.aborted || destroyed) return;
      msg(
        e instanceof ApiFailure && e.status === 404
          ? `“${text}” isn't a name we know. Paste coordinates instead, like 122.55 17.00 or 08h10m12s +17d00m13s.`
          : `Couldn't look up “${text}”. Paste coordinates like 122.55 17.00 or 08h10m12s +17d00m13s, or try Barnard's Star.`,
        "error",
      );
    } finally {
      if (resolveCtl === ctl) resolveCtl = null;
    }
  });
  input.addEventListener("input", () => {
    if (!resolveCtl && msgEl.dataset.kind !== "error") return;
    resolveCtl?.abort();
    resolveCtl = null;
    msg(null);
  });

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
      if (c) openCandidate(c);
    } else if (b.dataset.area) {
      const a = areas.find((x) => x.id === b.dataset.area);
      if (a) openArea(a);
    }
  });

  // Keyboard on the map: arrows pan, + and − zoom, Enter opens the zone or object nearest the centre.
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
      const first = showsDots(level) && layers.signals ? inViewNow()[0] : null;
      if (first) return openCandidate(first);
      const v = map.view();
      const a = [...areas].sort((x, y) => sepDeg(x.center, v) - sepDeg(y.center, v))[0];
      if (a && level === "whole") openArea(a);
    }
  });

  const onKey = (ev: KeyboardEvent) => {
    if (ev.key !== "Escape" || !visible || more.hidden) return;
    setAdjust(false);
    adjust.focus({ preventScroll: true });
  };
  document.addEventListener("keydown", onKey);

  // The fallback map redraws on resize itself; Aladin resizes its canvas, the status follows.
  const onResize = () => {
    measure();
    if (map) renderStatus();
  };
  addEventListener("resize", onResize);
  // Whatever sits above the map (the header) moves its top edge.
  let ro: ResizeObserver | null = null;
  if (typeof ResizeObserver === "function") {
    ro = new ResizeObserver(() => measure());
    for (const n of [document.querySelector("header.top"), document.querySelector(".top")]) if (n) ro.observe(n);
  }

  return {
    show(param) {
      requestedParam = param;
      const wasVisible = visible;
      visible = true;
      zoomFocus.show();
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
        applyState({ ...defaultState(), gal: state.gal, mol: state.mol, kinds: state.kinds, fov: wholeFov(state.mol) }, true);
      }
      if (data && !map) void startMap();
      writeHash(true);
    },
    hide() {
      visible = false;
      zoomFocus.hide();
      if (viewRaf) clearTimeout(viewRaf);
      viewRaf = null;
      hideTip();
      stopSearch();
      resolveCtl?.abort();
      resolveCtl = null;
      if (hashTimer) clearTimeout(hashTimer);
    },
    destroy() {
      destroyed = true;
      zoomFocus.destroy();
      chromeObs?.disconnect();
      if (viewRaf) clearTimeout(viewRaf);
      viewRaf = null;
      stopSearch();
      resolveCtl?.abort();
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
