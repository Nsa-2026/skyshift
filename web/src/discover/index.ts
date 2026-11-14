// Candidates (#/candidates, was "Discover"): what the pipeline found, filtered and sorted by anyone,
// on a list and a small sky map that share one filtered set. Every filter lives in the URL
// (#/candidates/kind=…&sort=…); the filter model itself is pure, in ./filters.ts.
// DESIGN.md: "Product", "Pages", "Candidates", "Smart filters", "Votes and consensus".
import "./discover.css";
import { loadCandidates } from "../api/client";
import type { Candidate, CandidatesResponse } from "../api/types";
import { DETECTORS, formatMjd, formatRaDec } from "../app/astro";
import type { MountPage } from "../app/modules";
import { isEmbed } from "../app/state";
import { ANSWER_TEXT, type Answer, type Counts } from "../vote/model";
import { download, toCsv, toJson } from "./export";
import { arcsecPerHour, arcsecPerYr, flux, fromSlider, num, plural, rangeText, rangeValue, roundNice, SLIDER_MAX, toSlider } from "./format";
import {
  PLANE_B,
  PRESETS,
  RANGES,
  SORTS,
  activeCount,
  activePreset,
  bestRelaxation,
  countBy,
  defaultState,
  domainOf,
  filterRows,
  FACET_VALUES,
  candidatesHash,
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
  type SortKey,
  type TestKey,
  type Tri,
} from "./filters";
import { glyphSvg, mountSkyMap } from "./skymap";
import { KIND_TEXT, KINDS, REVIEW_TEXT, flagText, labelText, reasonText, zonesPhrase, type ReviewState } from "./text";
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
  const embed = isEmbed();
  el.innerHTML = html`
    <div class="dv">
      <section class="dv-intro" aria-labelledby="dv-title" ${embed ? "hidden" : ""}>
        <h1 id="dv-title" class="dv-title">The pipeline found these in NASA's SPHEREx sky.</h1>
        <p class="dv-sub">Each needs human eyes: blink it, say what you see, and the best go to astronomers.</p>
        <p class="dv-counts" aria-live="polite">Loading candidates…</p>
        <p class="dv-note" data-note hidden></p>
        <div class="dv-actions">
          <a class="btn btn-primary" href="#/review">Start reviewing</a>
          <a class="btn btn-ghost" href="#/about">How it works</a>
        </div>
      </section>

      <div class="dv-presets" role="group" aria-label="Presets" hidden>
        <div class="seg dv-seg">
          ${PRESETS.map((p) => html`<button type="button" data-preset="${p.id}" aria-pressed="false" title="${esc(p.hint)}">${esc(p.label)}</button>`).join("")}
        </div>
      </div>

      <aside class="dv-map" aria-labelledby="dv-map-title" hidden>
        <div class="dv-map-inner">
          <h2 id="dv-map-title" class="dv-h2">On the sky</h2>
          <div data-skymap></div>
        </div>
      </aside>

      <section class="dv-filters" aria-labelledby="dv-filters-title" hidden>
        <div class="fx-head">
          <h2 id="dv-filters-title" class="dv-h2">Filters</h2>
          <button type="button" class="btn btn-ghost btn-sm" data-clear>Clear all</button>
          <button type="button" class="btn btn-sm fx-close" data-close-sheet>Done</button>
        </div>
        <div class="fx-body"></div>
        <div class="fx-sheet-foot"><button type="button" class="btn btn-primary" data-close-sheet data-show-n>Show candidates</button></div>
      </section>

      <section class="dv-results" aria-labelledby="dv-count">
        <div class="dv-toolbar" hidden>
          <h2 id="dv-count" class="dv-count" aria-live="polite"></h2>
          <button type="button" class="btn btn-sm dv-filters-btn" aria-haspopup="dialog">Filters</button>
          <label class="dv-sort"><span>Sort by</span>
            <select class="dv-select">${SORTS.map((s) => html`<option value="${s.key}">${s.label}</option>`).join("")}</select>
          </label>
          <div class="dv-export dv-export-top" role="group" aria-label="Download the filtered list">
            <span class="dv-export-label">Download</span>
            <button type="button" class="btn btn-ghost btn-sm" data-export="csv">CSV</button>
            <button type="button" class="btn btn-ghost btn-sm" data-export="json">JSON</button>
          </div>
        </div>
        <ul class="dv-active" aria-label="Filters in use" hidden></ul>
        <div class="dv-cols" aria-hidden="true" hidden><span>Candidate</span><span>Measured</span><span>Score</span><span>Votes</span></div>
        <ol class="dv-list" aria-labelledby="dv-count"></ol>
        <div class="dv-more" hidden><p class="dv-more-n"></p><button type="button" class="btn" data-more>Show more</button></div>
        <div class="dv-export dv-export-foot" role="group" aria-label="Download the filtered list" hidden>
          <span class="dv-export-label">Download this list as</span>
          <button type="button" class="btn btn-ghost btn-sm" data-export="csv">CSV</button>
          <button type="button" class="btn btn-ghost btn-sm" data-export="json">JSON</button>
        </div>
        <div class="dv-state" role="status"><p class="dv-state-title">Loading candidates…</p></div>
      </section>
    </div>
  `;

  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const countsEl = $<HTMLParagraphElement>(".dv-counts");
  const noteEl = $<HTMLParagraphElement>("[data-note]");
  const presetsEl = $<HTMLDivElement>(".dv-presets");
  const mapAside = $<HTMLElement>(".dv-map");
  const filtersEl = $<HTMLElement>(".dv-filters");
  const fxBody = $<HTMLDivElement>(".fx-body");
  const toolbar = $<HTMLDivElement>(".dv-toolbar");
  const countEl = $<HTMLHeadingElement>(".dv-count");
  const filtersBtn = $<HTMLButtonElement>(".dv-filters-btn");
  const sortSel = $<HTMLSelectElement>(".dv-select");
  const activeEl = $<HTMLUListElement>(".dv-active");
  const colsEl = $<HTMLDivElement>(".dv-cols");
  const list = $<HTMLOListElement>(".dv-list");
  const moreEl = $<HTMLDivElement>(".dv-more");
  const stateEl = $<HTMLDivElement>(".dv-state");

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
  let shown = PAGE;
  const updates: (() => void)[] = [];
  const open = (id: string) => ctx.navigate(`#/c/${encodeURIComponent(id)}`);

  const map = mountSkyMap($("[data-skymap]"), {
    onOpen: open,
    onHover(id) {
      list.querySelectorAll(".dv-row.is-hot").forEach((r) => r.classList.remove("is-hot"));
      if (id) list.querySelector(`.dv-row[data-id="${cssEsc(id)}"]`)?.classList.add("is-hot");
    },
  });
  const thumbs = thumbLoader((id) => byId.get(id));

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
      return;
    }
    byId = new Map(data.candidates.map((c) => [c.id, c]));
    rows = toRows(data.candidates, counts);
    for (const n of [presetsEl, mapAside, filtersEl, toolbar]) n.hidden = false;
    map.setData(data.candidates);
    buildFilters();
    apply();
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

  /** Keep the address in step without a hashchange (which would re-route and scroll to the top). */
  function writeUrl() {
    if (!/^#\/?(candidates|discover)\b/.test(location.hash)) return;
    const hash = candidatesHash(state);
    if (location.hash !== hash) history.replaceState(history.state, "", `${location.pathname}${location.search}${hash}`);
  }

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
    renderActive();
    renderList();
    map.setFiltered(sorted.map((r) => r.c.id));
  }

  // ---- intro ------------------------------------------------------------------------------------

  function renderIntro() {
    if (loading) {
      countsEl.textContent = "Loading candidates…";
      return;
    }
    if (!data) {
      countsEl.textContent = "No candidates are published yet.";
      return;
    }
    const n = data.candidates.length;
    const reviewed = rows.filter((r) => r.total > 0).length;
    const votes = rows.reduce((a, r) => a + r.total, 0);
    const lead = html`<strong>${n.toLocaleString("en-GB")}</strong> ${n === 1 ? "candidate" : "candidates"} from ${esc(zonesPhrase(data.zones))}.`;
    let tail = "";
    if (voteStatus === "loading" || voteStatus === "idle") tail = " Counting votes…";
    else if (voteStatus === "error") tail = " Vote counts can't be reached right now; everything else works.";
    else if (reviewed === 0) tail = " None reviewed yet: yours could be the first vote.";
    else
      tail = html` <strong>${reviewed.toLocaleString("en-GB")}</strong> reviewed so far, with <strong>${votes.toLocaleString("en-GB")}</strong> ${votes === 1 ? "vote" : "votes"} cast.`;
    countsEl.innerHTML = lead + tail;
    const notes: string[] = [];
    if (sample) notes.push("Sample data from the pilot zone, until the full list is published.");
    if (reader?.notice) notes.push(reader.notice);
    else if (voteStatus === "local") notes.push("Votes stay on this device for now.");
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

    const g1 = group("What it is", ["kind", "label"]);
    checkList(g1, "kind", "Kind", () =>
      KINDS.map((k) => ({ v: k, label: KIND_TEXT[k].name, title: `${k}: ${KIND_TEXT[k].gist}`, glyph: glyphSvg(k, 12) })),
    );
    chipList(g1, "label", "Spectral labels", () => uniq([...cands.flatMap((c) => c.labels), ...state.label]).map((v) => ({ v, label: labelText(v), title: v })), "No spectral labels in this set yet.");

    const g2 = group("Community", ["review", "verdict", "mine"]);
    checkList(g2, "review", "Review state", () => (["none", "some", "consensus"] as ReviewState[]).map((v) => ({ v, label: REVIEW_TEXT[v], title: reviewTitle(v) })));
    checkList(
      g2,
      "verdict",
      "Verdict",
      () => ANSWERS.map((a) => ({ v: a, label: ANSWER_TEXT[a].label, title: `${a}: ${ANSWER_TEXT[a].meaning}` })),
      { emptyText: "No verdicts yet: one needs at least 5 votes with 70 % agreeing.", hideWhenEmpty: true },
    );
    toggle(g2, "hideMine", "mine", "Hide ones I voted on", (r) => r.mine, "From this browser's memory; nothing leaves your device.");

    const g3 = group("Evidence", ["reason", "doubts", "cautions", "gaia", "catwise", "known"]);
    chipList(
      g3,
      "reason",
      "Why it's listed",
      () =>
        uniq([...cands.flatMap((c) => c.reasons), ...state.reason])
          .sort((a, b) => TONE_ORDER[reasonText(a).tone] - TONE_ORDER[reasonText(b).tone])
          .map((v) => ({ v, label: reasonText(v).label, title: `${v}: ${reasonText(v).detail}`, tone: reasonText(v).tone })),
      "No reason codes in this set.",
    );
    toggle(g3, "hideDoubts", "doubts", "Hide ones the pipeline doubts", (r) => isDoubted(r.c), "Any reason marked with a ring: a catalogue disagrees, a poor fit, few detections, or chance pairs.");
    tri(g3, "cautions", "Artefact cautions", "Flags near it (outlier pixels, ghosts, halos, crosstalk, bloom) or the same spot seen days apart. Many of these are junk.", isCautioned, ["With", "Without"]);
    tri(g3, "gaia", "In Gaia", "gaia_match", (c) => c.gaia_match);
    tri(g3, "catwise", "In CatWISE", "catwise_match", (c) => c.catwise_match);
    rangeCtl(g3, "known");

    const g4 = group("Measurements", ["score", "sig", "pm", "rate", "snr", "flux"]);
    for (const k of ["score", "sig", "pm", "rate", "snr", "flux"] as RangeKey[]) rangeCtl(g4, k);

    const g5 = group("Sky and data", ["b", "det", "zone"], false);
    planeToggle(g5);
    rangeCtl(g5, "b");
    checkList(g5, "det", "Detector", () =>
      ([1, 2, 3, 4, 5, 6] as const).map((d) => ({
        v: String(d),
        label: `D${d}, ${DETECTORS[d].min}–${DETECTORS[d].max} µm`,
        title: `det ${d}: detector ${d} sees ${DETECTORS[d].min}–${DETECTORS[d].max} µm`,
        glyph: `<span class="det-dot det-${d}" aria-hidden="true"></span>`,
      })),
    );
    checkList(g5, "zone", "Zone", () => uniq([...data!.zones, ...cands.map((c) => c.zone), ...state.zone]).map((v) => ({ v, label: `${v[0]?.toUpperCase()}${v.slice(1)} zone`, title: `zone: ${v}` })));
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

  function checkList(parent: HTMLElement, key: ListKey, title: string, options: () => Opt[], o: { emptyText?: string; hideWhenEmpty?: boolean } = {}) {
    const fs = document.createElement("fieldset");
    fs.className = "fx";
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
      const next = opts.map((x) => x.v).join("|") + (noneInData && o.hideWhenEmpty ? "#empty" : "");
      if (next !== sig) {
        sig = next;
        fs.innerHTML =
          html`<legend class="fx-title">${esc(title)}</legend>` +
          (noneInData && o.hideWhenEmpty
            ? html`<p class="fx-empty">${esc(o.emptyText ?? "")}</p>`
            : opts
                .map(
                  (x) => html`<label class="fx-opt" title="${esc(x.title ?? x.v)}"><input type="checkbox" value="${esc(x.v)}" />${x.glyph ?? ""}<span class="fx-name">${esc(x.label)}</span><span class="fx-n"></span></label>`,
                )
                .join(""));
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
      cover.title = spec.missing === "far" ? "No known object nearby counts as far away." : "Narrowing this hides candidates without a value.";
      hist.innerHTML = histogram(vals, dom, spec.log, r);
    });
  }

  // ---- toolbar, active chips, list --------------------------------------------------------------

  sortSel.addEventListener("change", () => setState({ ...state, sort: sortSel.value as SortKey }, { keepPage: true }));
  filtersBtn.addEventListener("click", () => openSheet(true));
  el.querySelectorAll<HTMLButtonElement>("[data-close-sheet]").forEach((b) => b.addEventListener("click", () => openSheet(false)));
  $<HTMLButtonElement>("[data-clear]").addEventListener("click", () => setState({ ...defaultState(), sort: state.sort }));
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

  function renderToolbar() {
    const n = sorted.length;
    const all = rows.length;
    countEl.textContent = n === all ? plural(all, "candidate", "candidates") : `${n.toLocaleString("en-GB")} of ${plural(all, "candidate", "candidates")}`;
    const on = activeCount(state);
    filtersBtn.textContent = on ? `Filters (${on})` : "Filters";
    sortSel.value = state.sort;
    const clear = $<HTMLButtonElement>("[data-clear]");
    clear.disabled = on === 0;
    const showN = $<HTMLButtonElement>("[data-show-n]");
    showN.textContent = n ? `Show ${plural(n, "candidate", "candidates")}` : "No matches: change a filter";
    el.querySelectorAll<HTMLButtonElement>("[data-export]").forEach((b) => {
      b.disabled = n === 0;
      b.title = `Download these ${n} as ${b.dataset.export?.toUpperCase()}`;
      b.setAttribute("aria-label", `Download ${n} as ${b.dataset.export?.toUpperCase()}`);
    });
  }

  function renderActive() {
    const chips = activeChips(state);
    activeEl.hidden = chips.length === 0;
    activeEl.innerHTML =
      chips.map((c, i) => html`<li><button type="button" class="achip" data-i="${i}" aria-label="Remove filter: ${esc(c.label)}"><span>${esc(c.label)}</span><span class="achip-x" aria-hidden="true"></span></button></li>`).join("") +
      (chips.length > 1 ? html`<li><button type="button" class="btn-link" data-i="all">Clear all</button></li>` : "");
    activeEl.querySelectorAll<HTMLButtonElement>("button[data-i]").forEach((b) =>
      b.addEventListener("click", () => {
        if (b.dataset.i === "all") setState({ ...defaultState(), sort: state.sort });
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
    if (s.gaia !== "any") out.push({ label: s.gaia === "yes" ? "In Gaia" : "Not in Gaia", remove: () => ({ ...s, gaia: "any" }) });
    if (s.catwise !== "any") out.push({ label: s.catwise === "yes" ? "In CatWISE" : "Not in CatWISE", remove: () => ({ ...s, catwise: "any" }) });
    if (s.hideDoubts) out.push({ label: "No pipeline doubts", remove: () => ({ ...s, hideDoubts: false }) });
    if (s.cautions !== "any") out.push({ label: s.cautions === "yes" ? "Only artefact cautions" : "No artefact cautions", remove: () => ({ ...s, cautions: "any" }) });
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

  list.addEventListener("pointerover", (ev) => {
    const row = (ev.target as Element).closest<HTMLElement>(".dv-row");
    if (row) map.highlight(row.dataset.id ?? null);
  });
  list.addEventListener("pointerleave", () => map.highlight(null));
  list.addEventListener("focusin", (ev) => {
    const row = (ev.target as Element).closest<HTMLElement>(".dv-row");
    if (row) map.highlight(row.dataset.id ?? null);
  });
  list.addEventListener("focusout", () => map.highlight(null));
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
    $<HTMLDivElement>(".dv-export-foot").hidden = items.length === 0;
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
        <p class="dv-state-text">No unsettled candidate here has an artefact caution: no outlier pixels, ghosts or halos near it, and no spot seen again days apart.</p>
        <div class="dv-state-actions"><button type="button" class="btn" data-clear-all>Show all candidates</button></div>`;
      stateEl.querySelector("[data-clear-all]")?.addEventListener("click", () => setState(defaultState()));
      return;
    }
    stateEl.innerHTML = html`
      <p class="dv-state-title">No candidates match these filters.</p>
      <p class="dv-state-text">${
        hint && chip
          ? `Removing “${esc(chip.label)}” would bring back ${plural(hint.n, "candidate", "candidates")}.`
          : "Loosen a range or switch off a filter on the left."
      }</p>
      <div class="dv-state-actions">
        ${hint ? html`<button type="button" class="btn" data-relax>Remove that filter</button>` : ""}
        <button type="button" class="btn btn-ghost" data-clear-all>Clear all filters</button>
      </div>`;
    stateEl.querySelector("[data-relax]")?.addEventListener("click", () => hint && setState(withoutTest(state, hint.key)));
    stateEl.querySelector("[data-clear-all]")?.addEventListener("click", () => setState({ ...defaultState(), sort: state.sort }));
  }

  function renderNoData() {
    renderIntro();
    stateEl.hidden = false;
    stateEl.innerHTML = html`
      <p class="dv-state-title">The candidate list isn't published yet.</p>
      <p class="dv-state-text">The pipeline's list appears here as soon as it's exported. Meanwhile, blink any spot of the SPHEREx sky yourself: paste coordinates above, or start from a featured target.</p>
      <div class="dv-state-actions"><a class="btn btn-primary" href="#/explore">Open Explore</a><a class="btn btn-ghost" href="#/results">See what the pipeline checked</a></div>`;
    el.querySelector<HTMLAnchorElement>('.dv-actions a[href="#/review"]')?.classList.replace("btn-primary", "btn-ghost");
  }

  // ---- one row ----------------------------------------------------------------------------------

  function rowHtml(r: Row): string {
    const c = r.c;
    const kind = KIND_TEXT[c.kind] ?? { name: c.kind, plural: c.kind, gist: "" };
    const pos = formatRaDec(c);
    const href = `#/c/${encodeURIComponent(c.id)}`;
    const chips: string[] = [];
    for (const l of c.labels) chips.push(html`<li class="tag tag-label" title="${esc(l)}">${esc(labelText(l))}</li>`);
    const doubts = c.reasons.filter(isDoubtCode);
    for (const code of c.reasons.filter((x) => reasonText(x).tone === "support")) {
      const t = reasonText(code);
      chips.push(html`<li class="tag tag-support" title="${esc(code)}: ${esc(t.detail)}">${esc(t.label)}</li>`);
    }
    if (doubts.length) {
      // One chip for the pipeline's doubts: the first in words, the rest counted (all in the title).
      const [first, ...rest] = doubts.map(reasonText);
      const all = doubts.map((d) => `${reasonText(d).label} (${d})`).join("; ");
      chips.push(
        html`<li class="tag tag-doubt" title="Pipeline doubts: ${esc(all)}"><span class="doubt-ring" aria-hidden="true"></span>${esc(first.label)}${
          rest.length
            ? html`<span class="tag-more" aria-hidden="true">+${rest.length}</span><span class="visually-hidden">, and ${esc(rest.map((r) => r.label.toLowerCase()).join(", "))}</span>`
            : ""
        }</li>`,
      );
    }
    // Cautions: artefact flags and caution reasons (outlier pixels, the same spot days apart), once each.
    const cautions = new Map<string, string>();
    for (const fl of c.flags) cautions.set(flagText(fl), fl);
    for (const code of c.reasons.filter(isCautionCode)) {
      const t = reasonText(code);
      cautions.set(t.label, cautions.has(t.label) ? `${cautions.get(t.label)}, ${code}` : `${code}: ${t.detail}`);
    }
    for (const [label, title] of cautions)
      chips.push(html`<li class="tag tag-caution" title="${esc(title)}"><span class="caution-mark" aria-hidden="true"></span>${esc(label)}<span class="visually-hidden"> (caution)</span></li>`);
    const det = DETECTORS[c.det];
    return html`<li class="dv-row" data-id="${esc(c.id)}">
      <div class="dv-thumb" data-thumb="${esc(c.id)}" aria-hidden="true">${emptyPlate()}</div>
      <div class="dv-main">
        <h3 class="dv-name"><a class="dv-link" href="${href}" title="${esc(c.id)}">${glyphSvg(c.kind, 12)}<span class="dv-kind">${esc(kind.name)}</span><span class="dv-pos">${esc(pos)}</span></a></h3>
        <p class="dv-why">${esc(c.why)}</p>
        ${chips.length ? html`<ul class="dv-tags" aria-label="Labels and reasons">${chips.join("")}</ul>` : ""}
      </div>
      <div class="dv-meas">${measured(c)}<p class="m3 det-${c.det}" title="Detector ${c.det}, ${det?.min}–${det?.max} µm. Seen best on ${esc(formatMjd(c.mjd))}."><span class="det-dot" aria-hidden="true"></span>D${c.det} <span class="m-um">${det?.min}–${det?.max} µm</span></p></div>
      <div class="dv-score">${scoreHtml(c.score)}</div>
      <div class="dv-votes">${votesHtml(r)}</div>
    </li>`;
  }

  function measured(c: Candidate): string {
    const lines: string[] = [];
    if (c.kind === "high_pm" && c.pm_mas_yr != null) {
      const sig = significance(c);
      lines.push(html`<p class="m1"><span class="visually-hidden">Motion </span>${arcsecPerYr(c.pm_mas_yr)}</p>`);
      if (c.pm_err_mas_yr != null)
        lines.push(html`<p class="m2">± ${num(c.pm_err_mas_yr / 1000, c.pm_err_mas_yr >= 10000 ? 1 : 2)}″/yr${sig != null ? html`, <span title="Motion over its error">${num(sig, 1)}σ</span>` : ""}</p>`);
    } else if (c.rate_arcsec_h != null) {
      lines.push(html`<p class="m1"><span class="visually-hidden">Motion </span>${arcsecPerHour(c.rate_arcsec_h)}</p>`);
      if (c.snr != null) lines.push(html`<p class="m2">S/N ${num(c.snr, 1)}${c.flux_ujy != null ? `, ${flux(c.flux_ujy)}` : ""}</p>`);
    } else {
      if (c.snr != null) lines.push(html`<p class="m1"><abbr title="Signal to noise">S/N</abbr> ${num(c.snr, 1)}</p>`);
      if (c.flux_ujy != null) lines.push(html`<p class="m2">${flux(c.flux_ujy)}</p>`);
    }
    if (c.kind === "mover" && c.nearest_known_arcsec != null)
      lines.push(html`<p class="m2" title="Distance to the nearest known asteroid or comet at that time">Known object ${num(c.nearest_known_arcsec, 0)}″ away</p>`);
    else if (c.kind === "mover") lines.push(html`<p class="m2" title="Nothing known within the search radius">No known object near</p>`);
    return lines.join("");
  }

  function scoreHtml(score: number | null): string {
    if (score == null) return html`<span class="dv-none" title="The pipeline gives this kind no score yet">No score</span>`;
    const pct = Math.round(Math.min(1, Math.max(0, score)) * 100);
    return html`<span class="dv-score-n"><span class="visually-hidden">Score </span>${num(score, 2)}</span><span class="dv-bar" aria-hidden="true"><span style="width:${pct}%"></span></span>`;
  }

  function votesHtml(r: Row): string {
    const mine = r.mine ? html`<span class="dv-mine">You voted</span>` : "";
    if (voteStatus === "error") return html`<span class="dv-none">Votes unavailable</span>${mine}`;
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

  // ---- page handle ------------------------------------------------------------------------------

  load();
  // A vote cast on another page of this tab: refresh the counts next time this page shows.
  const offVoted = onVoted(() => {
    votesAt = 0;
  });

  return {
    show(param) {
      const fromUrl = queryToState(routeQuery(param));
      if (!sameState(fromUrl, state)) {
        state = fromUrl;
        shown = PAGE;
        apply();
      }
      // Votes may have changed on the Review page: refresh at most every 20 s.
      if (data && voteStatus !== "loading" && Date.now() - votesAt > 20_000) loadVotes();
    },
    hide() {
      map.highlight(null);
      openSheet(false);
    },
    destroy() {
      offVoted();
      thumbs.destroy();
      map.destroy();
      document.body.classList.remove("dv-sheet-open");
      el.innerHTML = "";
    },
  };
};

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
