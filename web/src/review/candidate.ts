// Candidate page (#/c/<id>): the plate at the candidate's spot with the candidate in the red
// reticle; beside it why it's listed, the measurements, the vote and what happens next.
import { failedGates, gatesPhrase, gradeOf, isGate, nearSourceOf, votable } from "../api/grades";
import type { Candidate } from "../api/types";
import type { MountPage } from "../app/modules";
import { emptyCounts, shiftCounts, type Answer, type Counts } from "../vote/model";
import { VOTED_EVENT, type VotedDetail } from "../vote/store";
import { AssistSlot, detailsOn, loadAssist, votesSummary } from "./assist";
import { Ballot } from "./ballot";
import { candidateSet, suspectFor, votes, type CandidateSet, type Votes } from "./data";
import { LIST_HASH, keyAction, neighbours, parseCandidateParam } from "./model";
import { listFor, queryNeedsCounts } from "./list";
import { lastDiscoverHash, lastDiscoverQuery, trackDiscover } from "./nav";
import { SAMPLE_NOTE, castVote, copyWithFeedback, fillNearSource, gradeHtml, headHtml, motionHtml, visible } from "./parts";
import { Plate } from "./plate";
import {
  KIND_TEXT,
  SITE,
  esc,
  flagText,
  kindName,
  nearestKnownText,
  reasonText,
} from "./text";
import { formatRaDec } from "../app/astro";

const html = String.raw;

const CHEVRON = {
  left: `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  right: `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};

/** The share link for a candidate. */
export function shareUrl(c: Pick<Candidate, "id">): string {
  return `${SITE}/#/c/${encodeURIComponent(c.id)}`;
}

/** Embed code: the Explore blink at the candidate's spot, without the page around it. */
export function embedCode(c: Candidate): string {
  const p = new URLSearchParams({
    ra: c.ra.toFixed(5),
    dec: c.dec.toFixed(5),
    clock: c.clock,
    fov: String(c.clock === "hours" ? 20 : 5),
    det: String(c.det),
    embed: "1",
  });
  return `<iframe src="${SITE}/?${p}" width="760" height="820" style="border:0" loading="lazy" title="${esc(`SkyShift: ${kindName(c.kind).toLowerCase()} ${c.id}`)}"></iframe>`;
}

function cataloguesHtml(c: Candidate): string {
  const pmDiffers = (code: string) => c.reasons.includes(code);
  return dl([
    ["Known asteroids and comets", esc(nearestKnownText(c))],
    [
      "Gaia DR3",
      c.gaia_match
        ? pmDiffers("gaia_pm_disagrees")
          ? "A star sits here, but Gaia lists a different motion."
          : "A star sits here."
        : "No star here.",
    ],
    [
      "CatWISE2020",
      c.catwise_match
        ? pmDiffers("catwise_pm_disagrees")
          ? "A source sits here, but CatWISE lists a different motion."
          : "A source sits here."
        : "No source here.",
    ],
  ]);
}

const dl = (rows: [string, string][]) =>
  html`<dl class="rv-dl">${rows.map(([k, v]) => html`<div${k ? "" : ` class="rv-dl-cont"`}><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;

/** Reasons as chips. Each chip is a button: it opens one plain sentence on what the reason means.
 *  A grade C candidate's failed checks are listed under its grade, so they aren't repeated here. */
export function reasonsHtml(c: Candidate): string {
  const reasons = gradeOf(c) === "C" ? c.reasons.filter((r) => !isGate(r)) : c.reasons;
  if (!reasons.length) return html`<p class="rv-soft">The pipeline gave no extra reasons.</p>`;
  const toneWord = { for: "supports it", doubt: "a reason for doubt", info: "context" } as const;
  return html`<ul class="rv-chips">
    ${reasons
      .map((code, i) => {
        const r = reasonText(code);
        return html`<li><button class="rv-chip" type="button" data-tone="${r.tone}" data-tip="${esc(r.tip)}"
          aria-expanded="false" aria-controls="rv-chip-tip" id="rv-chip-${i}">
          <span class="rv-chip-mark" aria-hidden="true">${r.tone === "for" ? "+" : r.tone === "doubt" ? "?" : "i"}</span>${esc(r.chip)}<span class="visually-hidden">, ${toneWord[r.tone]}</span>
        </button></li>`;
      })
      .join("")}
  </ul>
  <p class="rv-chip-tip" id="rv-chip-tip" aria-live="polite">Tap a reason to read what it means.</p>`;
}

/** Grade C: no ballot. Say why, and that the plate is still there to blink. */
export function noVoteHtml(c: Candidate): string {
  const gates = failedGates(c);
  const why = gates.length ? ` (${esc(gatesPhrase(gates))})` : "";
  return html`<section class="rv-ballot rv-novote" aria-labelledby="cd-novote-h" data-state="closed">
    <h2 class="rv-h" id="cd-novote-h">No vote on this one</h2>
    <p class="rv-novote-text">The pipeline graded it C: it fails a check${why}, so it is most likely an artefact or a catalogued source. Votes go to grade A and B objects.</p>
    <p class="rv-soft">You can still blink it and read every reason below.</p>
  </section>`;
}

function cautionsHtml(c: Candidate): string {
  if (!c.flags.length) return html`<p class="rv-soft">No artefact flags near it.</p>`;
  return html`<ol class="rv-notes">${c.flags.map((f) => html`<li>${esc(flagText(f))}</li>`).join("")}</ol>`;
}

export const mountCandidate: MountPage = (el, ctx) => {
  trackDiscover();
  el.innerHTML = html`
    <div class="rv cd">
      <p class="rv-sample" hidden></p>
      <nav class="cd-nav" aria-label="Candidate list">
        <a class="cd-back" href="${LIST_HASH}">${CHEVRON.left}All objects to check</a>
        <p class="cd-count" aria-live="polite"></p>
        <div class="cd-step">
          <a class="btn btn-ghost cd-prev" href="${LIST_HASH}" aria-disabled="true">${CHEVRON.left}Previous</a>
          <a class="btn btn-ghost cd-next" href="${LIST_HASH}" aria-disabled="true">Next${CHEVRON.right}</a>
        </div>
      </nav>
      <div class="cd-main">
        <div class="cd-left">
          <section class="cd-plate" aria-label="Frames at this spot"></section>
          <!-- Slot: src/assist mountChecklist (the guided check), mounted only if src/assist exists. -->
          <section class="cand-check" aria-label="Check this object" hidden></section>
        </div>
        <div class="cd-side"></div>
      </div>
      <div class="cd-empty" hidden></div>
    </div>
  `;
  const $ = <T extends Element>(s: string) => el.querySelector(s) as T;
  const sampleEl = $<HTMLElement>(".rv-sample");
  const mainEl = $<HTMLElement>(".cd-main");
  const sideEl = $<HTMLElement>(".cd-side");
  const emptyEl = $<HTMLElement>(".cd-empty");
  const navEl = $<HTMLElement>(".cd-nav");
  const backEl = $<HTMLAnchorElement>(".cd-back");
  const countEl = $<HTMLElement>(".cd-count");
  const prevEl = $<HTMLAnchorElement>(".cd-prev");
  const nextEl = $<HTMLAnchorElement>(".cd-next");
  const checkSlot = new AssistSlot($<HTMLElement>(".cand-check"));
  let nextSlot: AssistSlot | null = null;

  const plate = new Plate($(".cd-plate"), {
    onReady(ready) {
      plateReady = ready;
      renderBallot();
    },
  });
  let set: CandidateSet | null = null;
  let v: Votes | null = null;
  let cand: Candidate | null = null;
  let counts: Counts | null = null;
  let mine: Answer | null = null;
  let justNow = false;
  let saving: Answer | null = null;
  let resetting = false;
  let resetNow = false;
  let error: string | null = null;
  let plateReady = false;
  let ballot: Ballot | null = null;
  let token = 0;
  let visibleNow = false;

  function renderBallot() {
    if (!ballot || !cand) return;
    ballot.set({
      mine,
      justNow,
      counts,
      saving,
      resetting,
      reset: resetNow,
      waiting: !plateReady && !mine,
      error,
      local: v?.api.mode === "local",
      kind: cand.kind,
    });
    updateWhatNext();
  }

  // ---- the check assistant's slots (src/assist, optional) -------------------------------------
  async function mountAssist(c: Candidate) {
    const mod = await loadAssist();
    if (!mod || cand !== c) return;
    checkSlot.mount(mod.mountChecklist && ((slot) => mod.mountChecklist!(slot, c, { details: detailsOn() })));
    mountWhatNext(c);
  }
  function mountWhatNext(c: Candidate) {
    loadAssist().then((mod) => {
      if (!mod?.mountWhatNext || cand !== c || !nextSlot) return;
      const ok = nextSlot.mount((slot) => mod.mountWhatNext!(slot, c, votesSummary(counts ?? emptyCounts(), mine)));
      // The assistant writes the next steps: the ballot keeps only the short line.
      ballot?.setFull(!ok);
    });
  }
  let lastSummary = "";
  function updateWhatNext() {
    if (!cand || !nextSlot) return;
    const sum = votesSummary(counts ?? emptyCounts(), mine);
    const key = JSON.stringify(sum);
    if (key === lastSummary) return;
    lastSummary = key;
    if (!nextSlot.update(sum)) mountWhatNext(cand);
  }

  /** Vote, change the answer, or (null) reset it. One write at a time: the ballot is shut while saving. */
  async function vote(a: Answer | null) {
    if (!cand || !v || !ballot?.open() || a === mine) return;
    const c = cand;
    saving = a;
    resetting = !a;
    resetNow = false;
    error = null;
    renderBallot();
    const r = await castVote(v, c, a, counts);
    if (cand !== c) return;
    saving = null;
    resetting = false;
    if (r.ok) {
      counts = r.counts;
      mine = a;
      justNow = !!a;
      resetNow = !a;
    } else {
      error = r.message;
    }
    renderBallot();
    if (r.ok && !a) ballot?.focusChoices();
  }

  function renderSide(c: Candidate) {
    const id = html`Object <span class="rv-nowrap">${esc(c.id)}</span>${c.zone ? ` from the ${esc(c.zone)} zone` : ""}. ${esc(KIND_TEXT[c.kind]?.could ?? "")}`;
    const open = votable(c);
    sideEl.innerHTML = html`
      ${headHtml(c, "h1", id)}
      ${motionHtml(c)}
      ${gradeHtml(c, true)}
      <div class="cd-ballot">${open ? "" : noVoteHtml(c)}</div>
      <!-- Slot: src/assist mountWhatNext (report drafts, citation), mounted only if src/assist exists. -->
      <section class="cand-next" aria-label="What next" hidden></section>
      <section class="rv-sec" aria-labelledby="cd-why-h">
        <h2 class="rv-h" id="cd-why-h">Why it's listed</h2>
        ${reasonsHtml(c)}
      </section>
      <section class="rv-sec" aria-labelledby="cd-c-h">
        <h2 class="rv-h" id="cd-c-h">Catalogues at this spot</h2>
        ${cataloguesHtml(c)}
      </section>
      <section class="rv-sec" aria-labelledby="cd-f-h">
        <h2 class="rv-h" id="cd-f-h">Cautions</h2>
        ${cautionsHtml(c)}
      </section>
      <section class="rv-sec" aria-labelledby="cd-p-h">
        <h2 class="rv-h" id="cd-p-h">Position</h2>
        <p class="cd-pos"><span class="cd-radec">${formatRaDec(c)}</span>
          <span class="rv-soft">${c.ra.toFixed(5)}°, ${c.dec >= 0 ? "+" : "−"}${Math.abs(c.dec).toFixed(5)}°</span></p>
        <div class="rv-actions">
          <button class="btn" type="button" data-act="copy-pos">Copy coordinates</button>
          <button class="btn" type="button" data-act="explore">Open in Blink</button>
        </div>
      </section>
      <section class="rv-sec" aria-labelledby="cd-s-h">
        <h2 class="rv-h" id="cd-s-h">Share</h2>
        <div class="rv-actions">
          <button class="btn btn-ghost" type="button" data-act="copy-link">Copy link</button>
          <button class="btn btn-ghost" type="button" data-act="copy-embed">Copy embed code</button>
        </div>
        <details class="cd-embed">
          <summary>Show the embed code</summary>
          <label class="visually-hidden" for="cd-embed-code">Embed code</label>
          <textarea id="cd-embed-code" class="cd-code" readonly rows="4">${esc(embedCode(c))}</textarea>
        </details>
      </section>
    `;
    nextSlot?.clear();
    nextSlot = new AssistSlot(sideEl.querySelector<HTMLElement>(".cand-next")!);
    lastSummary = "";
    // Grade C is never put to a vote: no ballot, so keys 1–4 do nothing here either.
    ballot = open ? new Ballot(sideEl.querySelector(".cd-ballot")!, { onVote: vote, onReset: () => vote(null), full: true, kind: c.kind }) : null;
    renderBallot();
    if (c.kind === "mover")
      suspectFor(c.id).then((s) => {
        if (cand === c) fillNearSource(sideEl, nearSourceOf(s));
      });
  }

  sideEl.addEventListener("click", (ev) => {
    const chip = (ev.target as Element).closest<HTMLButtonElement>("button.rv-chip");
    if (chip) {
      const open = chip.getAttribute("aria-expanded") !== "true";
      sideEl.querySelectorAll<HTMLButtonElement>("button.rv-chip").forEach((b) => b.setAttribute("aria-expanded", String(b === chip && open)));
      const tip = sideEl.querySelector<HTMLElement>(".rv-chip-tip");
      if (tip) {
        tip.textContent = open ? chip.dataset.tip ?? "" : "Tap a reason to read what it means.";
        tip.dataset.open = String(open);
      }
      return;
    }
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-act]");
    if (!b || !cand) return;
    const c = cand;
    if (b.dataset.act === "copy-pos") copyWithFeedback(b, `${c.ra.toFixed(6)} ${c.dec.toFixed(6)}`);
    else if (b.dataset.act === "copy-link") copyWithFeedback(b, shareUrl(c));
    else if (b.dataset.act === "copy-embed") copyWithFeedback(b, embedCode(c));
    else if (b.dataset.act === "explore") ctx.explore({ ra: c.ra, dec: c.dec, clock: c.clock, det: c.det, fov: c.clock === "hours" ? 20 : 5 });
  });

  async function renderNav(c: Candidate, query: string) {
    const q = query || lastDiscoverQuery();
    let all = new Map<string, Counts>();
    if (queryNeedsCounts(q) && v && set) {
      try {
        all = await v.api.countsFor(set.candidates.map((x) => x.id));
      } catch {
        all = new Map();
      }
    }
    if (cand !== c || !set || !v) return;
    const api = v.api;
    // This candidate stays in its list after a vote, even when the list hides "mine".
    const votedFor = (id: string) => (id === c.id ? null : api.myVote(id));
    let items = await listFor(set.candidates, q, all, votedFor);
    let inList = true;
    if (!items.some((x) => x.id === c.id)) {
      items = await listFor(set.candidates, "", all, votedFor);
      inList = false;
    }
    if (cand !== c) return;
    const { prev, next, index } = neighbours(items, c.id);
    const suffix = query ? `?${query}` : "";
    const link = (a: HTMLAnchorElement, to: Candidate | null) => {
      a.href = to ? `#/c/${encodeURIComponent(to.id)}${suffix}` : lastDiscoverHash();
      a.setAttribute("aria-disabled", String(!to));
      a.tabIndex = to ? 0 : -1;
      a.title = to ? `${kindName(to.kind)} ${to.id}` : "";
    };
    link(prevEl, prev);
    link(nextEl, next);
    countEl.textContent =
      index >= 0 ? `${(index + 1).toLocaleString("en-GB")} of ${items.length.toLocaleString("en-GB")} ${inList && q ? "in your filtered list" : "by pipeline score"}` : "";
    backEl.href = lastDiscoverHash();
  }

  function showEmpty(title: string, body: string) {
    mainEl.hidden = true;
    navEl.hidden = false;
    emptyEl.hidden = false;
    countEl.textContent = "";
    for (const a of [prevEl, nextEl]) {
      a.setAttribute("aria-disabled", "true");
      a.tabIndex = -1;
    }
    emptyEl.innerHTML = html`<h1 class="rv-empty-title">${title}</h1>${body}`;
    plate.clear();
    checkSlot.clear();
    nextSlot?.clear();
  }

  async function show(param: string | null) {
    const { id, query } = parseCandidateParam(param);
    const t = ++token;
    if (cand && cand.id === id) {
      renderNav(cand, query);
      return;
    }
    cand = null;
    set = await candidateSet();
    v ??= await votes();
    if (t !== token) return;
    sampleEl.hidden = !set?.sample;
    sampleEl.textContent = set?.sample ? SAMPLE_NOTE : "";
    if (!set || !set.candidates.length) {
      showEmpty(
        "No objects to check are published yet",
        html`<p>The pipeline's first list is on its way. Meanwhile, the <a href="#/candidates/one=1">practice round</a> and the featured targets on <a href="#/explore">Blink</a> work now.</p>`,
      );
      return;
    }
    const c = set.candidates.find((x) => x.id === id);
    if (!c) {
      showEmpty(
        "This object isn't in the current list",
        html`<p>Nothing called “${esc(id)}” is in this set. It may come from an older list.</p>
          <p class="rv-actions"><a class="btn" href="${lastDiscoverHash()}">Browse the list</a> <a class="btn btn-ghost" href="#/candidates/one=1">Start reviewing</a></p>`,
      );
      return;
    }
    cand = c;
    mainEl.hidden = false;
    emptyEl.hidden = true;
    mine = v.api.myVote(c.id);
    justNow = false;
    saving = null;
    resetting = false;
    resetNow = false;
    error = null;
    counts = null;
    plateReady = false;
    document.title = `${kindName(c.kind)} ${c.id}: SkyShift`;
    checkSlot.clear();
    renderSide(c);
    renderNav(c, query);
    if (visibleNow) plate.showCandidate(c);
    mountAssist(c);
    try {
      const all = await v.api.countsFor([c.id]);
      if (cand !== c) return;
      counts = all.get(c.id) ?? emptyCounts();
    } catch {
      if (cand !== c) return;
      counts = null;
    }
    renderBallot();
  }

  const onKey = (ev: KeyboardEvent) => {
    if (!visible(el) || !cand) return;
    const a = keyAction(ev, { queue: false });
    if (a?.kind === "vote" && ballot?.open()) {
      ev.preventDefault();
      vote(a.answer);
    }
  };
  document.addEventListener("keydown", onKey);

  // A vote cast, changed or taken back elsewhere on this page's candidate (the review queue, the
  // assistant's step 6). The counts here include this browser's answer as it was: move it.
  const onVoted = (ev: Event) => {
    const d = (ev as CustomEvent<VotedDetail>).detail;
    if (!d || !cand || d.candidateId !== cand.id || saving || resetting || d.answer === mine) return;
    counts = shiftCounts(counts, mine, d.answer ?? null);
    mine = d.answer ?? null;
    justNow = !!mine;
    resetNow = !mine;
    renderBallot();
  };
  window.addEventListener(VOTED_EVENT, onVoted);

  // Disabled step links don't navigate.
  for (const a of [prevEl, nextEl])
    a.addEventListener("click", (ev) => {
      if (a.getAttribute("aria-disabled") === "true") ev.preventDefault();
    });

  return {
    show(param) {
      visibleNow = true;
      const { id } = parseCandidateParam(param);
      if (cand && cand.id !== id) plate.clear();
      plate.mount(); // re-shows the same candidate when coming back to it
      show(param);
    },
    hide() {
      visibleNow = false;
      plate.unmount();
      document.title = "SkyShift";
    },
    destroy() {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener(VOTED_EVENT, onVoted);
      checkSlot.clear();
      nextSlot?.clear();
      plate.unmount();
      el.innerHTML = "";
    },
  };
};
