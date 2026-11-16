// A candidate in the Viewer (#/c/<id>): the Viewer's own plate opens on the candidate's spot, clock
// and detector, with red circles where it should be in each frame, and the side column is the
// Viewer's usual one. This panel takes the card's place for the name and the measurement (in the
// Motion dashboard when Advanced is on): the list around the candidate (back, previous, next),
// what it is and how fast it moves, its grade, and its vote (change it or reset it).
import { failedGates, gatesPhrase, nearSourceOf, votable } from "../api/grades";
import { epochsFor } from "../api/client";
import type { Candidate, Det, Epoch, FramesResponse } from "../api/types";
import type { TrackMarks, View } from "../app/modules";
import { emptyCounts, shiftCounts, type Answer, type Counts } from "../vote/model";
import { VOTED_EVENT, type VotedDetail } from "../vote/store";
import { Ballot } from "./ballot";
import { OWN_GAP_DAYS, candidateSet, findGap, focusFrames, framesFor, gapText, suspectFor, votes, type CandidateSet, type Votes } from "./data";
import { LIST_HASH, keyAction, neighbours, parseCandidateParam } from "./model";
import { listFor, queryNeedsCounts } from "./list";
import { lastDiscoverHash, lastDiscoverQuery, trackDiscover } from "./nav";
import { SAMPLE_NOTE, castVote, fillNearSource, gradeHtml, headHtml, motionHtml, visible } from "./parts";
import { candidateMarks } from "./track";
import { aboutCandidate } from "./about";
import { KIND_TEXT, SITE, esc, kindName } from "./text";

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

/** Grade C: no ballot. Say why, and that the plate is still there to blink. */
export function noVoteHtml(c: Candidate): string {
  const gates = failedGates(c);
  const why = gates.length ? ` (${esc(gatesPhrase(gates))})` : "";
  return html`<section class="rv-ballot rv-novote" aria-labelledby="cd-novote-h" data-state="closed">
    <h2 class="rv-h" id="cd-novote-h">No vote on this one</h2>
    <p class="rv-novote-text">The pipeline graded it C: it fails a check${why}, so it is most likely an artefact or a catalogued source. Votes go to grade A and B objects.</p>
    <p class="rv-soft">You can still blink it.</p>
  </section>`;
}

/** What this object did, in which detector, and what could make it an artefact (src/review/about). */
export function aboutHtml(c: Candidate, frames?: FramesResponse | null): string {
  const a = aboutCandidate(c, frames);
  return html`<div class="sp-about">
    <p class="sp-about-said">${esc(a.said.join(" "))}</p>
    ${a.watch ? html`<p class="sp-about-watch">${esc(a.watch)}</p>` : ""}
  </div>`;
}

/** The part of the plate (BlinkHandle) the object's detector choice needs. */
export interface WavePlate {
  /** Set the plate's All-wavelengths preference (applied at its next show); returns the one before. */
  preferAllWave?(on: boolean): boolean;
}

/**
 * Each object to check opens on its own detector (candidateView: c.det) alone, not "All": open()
 * sets the plate's preference before the candidate's frames are shown. The person can still press
 * All. leave() puts back the preference the person had before the first object.
 */
export function suspectWave(plate: WavePlate): { open(): void; leave(): void } {
  let saved: boolean | null = null;
  return {
    open() {
      const before = plate.preferAllWave?.(false);
      if (saved === null && typeof before === "boolean") saved = before;
    },
    leave() {
      if (saved === null) return;
      plate.preferAllWave?.(saved);
      saved = null;
    },
  };
}

/** What the plate note says about the frames shown for a candidate. */
export interface SuspectFramesNote {
  /** Hours: only the exposures within a day of the find are shown (of `all`). */
  focused: boolean;
  shown: number;
  all: number;
  /** Showing every exposure though some are near the find (the person asked for all). */
  allShown: boolean;
  /** Days from the find to the nearest frame when the find's own exposures are missing, else null. */
  missingGap: number | null;
}

/**
 * Frames for the Viewer at a candidate's spot: its static frames first (src/review/data
 * framesFor), on the hours clock only the exposures within a day of the find unless `all`, and
 * the red track circles for them.
 */
export async function suspectFrames(
  c: Candidate,
  view: View,
  all: boolean,
): Promise<{ frames: FramesResponse; whole: FramesResponse; jsonUrl: string; marks: TrackMarks | null; note: SuspectFramesNote; fill: FillSource[] }> {
  const loaded = await framesFor(c, view);
  const s = suspectShown(c, loaded.frames, view.det, all);
  return {
    ...s,
    whole: loaded.frames,
    jsonUrl: loaded.jsonUrl,
    // A few exposures of one session share one edge: the visit's others fill it (never blinked),
    // and so does the candidate's saved fill.json (contracts/api.md v3.20).
    fill: [
      ...(s.note.focused ? epochsFor(loaded.frames, view.det).map((epoch) => ({ epoch, url: loaded.jsonUrl })) : []),
      ...(await fillFor(loaded.jsonUrl, view)),
    ],
  };
}

/**
 * What the plate shows of a candidate's frames (`whole`: every exposure loaded so far, which grows as
 * the rest of its visit loads): on the hours clock only the exposures within a day of the find unless
 * `all`, the red track circles for them, and what the plate note says.
 */
export function suspectShown(
  c: Candidate,
  whole: FramesResponse,
  det: Det,
  all: boolean,
): { frames: FramesResponse; marks: TrackMarks | null; note: SuspectFramesNote } {
  const f = focusFrames(whole, c, det);
  const gap = findGap(whole, c, det);
  const missingGap = gap !== null && gap > OWN_GAP_DAYS ? gap : null;
  const frames = all ? whole : f.frames;
  return {
    frames,
    // Always named, so the panel can say SkyShift didn't measure it here (and why) instead of "no movers".
    marks: candidateMarks(c, frames, missingGap),
    note: {
      focused: !all && f.focused,
      shown: f.shown,
      all: f.all,
      allShown: all && f.focused,
      missingGap,
    },
  };
}

/** An exposure that only fills the plate's empty edge, with the JSON its data_url resolves against. */
export interface FillSource {
  epoch: Epoch;
  url: string;
}

/** The saved fill exposures next to a candidate's static frames.json (optional: most have none). */
async function fillFor(jsonUrl: string, view: View): Promise<FillSource[]> {
  if (view.clock !== "hours" || !/\/frames\.json$/.test(new URL(jsonUrl).pathname)) return [];
  const url = new URL("fill.json", jsonUrl).href;
  try {
    const res = await fetch(url);
    if (!res.ok) return [];
    const doc = (await res.json()) as FramesResponse;
    if (doc.clock !== "hours") return [];
    return epochsFor(doc, view.det).map((epoch) => ({ epoch, url }));
  } catch {
    return [];
  }
}

export interface SuspectOptions {
  /** Put this candidate on the Viewer's plate (candidateView: its spot, clock and detector). */
  open(c: Candidate): void;
  /** Is the plate already on this candidate? */
  showing(c: Candidate): boolean;
  /** Show every exposure (true) or only those near the find (false). */
  showAll(all: boolean): void;
}

export interface SuspectHandle {
  /** #/c/<id>[?list query]: show this candidate (the id as the route gives it, decoded). */
  show(param: string): void;
  /** The plate shows the candidate's frames (true), or is loading or failed (false). */
  ready(ok: boolean): void;
  /** What the frames on the plate are (null: nothing to say). */
  note(n: SuspectFramesNote | null): void;
  /** The Viewer left the candidate. */
  hide(): void;
  /** Remove the panel and its key and vote listeners. */
  destroy(): void;
}

/** The panel at the top of the Viewer's side column while it shows a candidate. */
export function mountSuspect(el: HTMLElement, opts: SuspectOptions): SuspectHandle {
  trackDiscover();
  el.innerHTML = html`
    <div class="rv sp">
      <p class="rv-sample" hidden></p>
      <nav class="cd-nav sp-nav" aria-label="Objects to check">
        <a class="cd-back" href="${LIST_HASH}">${CHEVRON.left}All objects to check</a>
        <p class="cd-count" aria-live="polite"></p>
        <div class="cd-step">
          <a class="btn btn-ghost cd-prev" href="${LIST_HASH}" aria-disabled="true">${CHEVRON.left}Previous</a>
          <a class="btn btn-ghost cd-next" href="${LIST_HASH}" aria-disabled="true">Next${CHEVRON.right}</a>
        </div>
      </nav>
      <p class="rv-plate-note sp-note" hidden></p>
      <div class="sp-body"></div>
      <div class="cd-empty" hidden></div>
    </div>
  `;
  const $ = <T extends Element>(s: string) => el.querySelector(s) as T;
  const sampleEl = $<HTMLElement>(".rv-sample");
  const bodyEl = $<HTMLElement>(".sp-body");
  const emptyEl = $<HTMLElement>(".cd-empty");
  const noteEl = $<HTMLElement>(".sp-note");
  const backEl = $<HTMLAnchorElement>(".cd-back");
  const countEl = $<HTMLElement>(".cd-count");
  const prevEl = $<HTMLAnchorElement>(".cd-prev");
  const nextEl = $<HTMLAnchorElement>(".cd-next");

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
  let active = false;

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

  /** What it is, how fast it moves, its grade and its vote. */
  function renderBody(c: Candidate) {
    const id = html`Object <span class="rv-nowrap">${esc(c.id)}</span>${c.zone ? ` from the ${esc(c.zone)} zone` : ""}. ${esc(KIND_TEXT[c.kind]?.could ?? "")}`;
    const open = votable(c);
    bodyEl.innerHTML = html`
      ${headHtml(c, "h2", id, aboutHtml(c))}
      ${motionHtml(c)}
      ${gradeHtml(c, true)}
      <div class="cd-ballot">${open ? "" : noVoteHtml(c)}</div>
    `;
    // Grade C is never put to a vote: no ballot, so keys 1–4 do nothing here either.
    ballot = open ? new Ballot(bodyEl.querySelector(".cd-ballot")!, { onVote: vote, onReset: () => vote(null), full: true, kind: c.kind }) : null;
    renderBallot();
    // Its own frames (the plate's: framesFor shares them) make the description precise.
    framesFor(c)
      .then((loaded) => {
        const about = bodyEl.querySelector(".sp-about");
        if (cand === c && about) about.outerHTML = aboutHtml(c, loaded.frames);
      })
      .catch(() => {
        /* the description stays as the candidate's own fields give it */
      });
    if (c.kind === "mover")
      suspectFor(c.id).then((s) => {
        if (cand === c) fillNearSource(bodyEl, nearSourceOf(s));
      });
  }

  noteEl.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-all]");
    if (b) opts.showAll(b.dataset.all === "1");
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
      // Not in the list: step through everything, including the groups the list sets aside by default.
      items = await listFor(set.candidates, "show=all", all, votedFor);
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
    bodyEl.hidden = true;
    noteEl.hidden = true;
    emptyEl.hidden = false;
    countEl.textContent = "";
    for (const a of [prevEl, nextEl]) {
      a.setAttribute("aria-disabled", "true");
      a.tabIndex = -1;
    }
    emptyEl.innerHTML = html`<h2 class="rv-empty-title">${title}</h2>${body}`;
  }

  async function show(param: string) {
    const { id, query } = parseCandidateParam(param);
    const t = ++token;
    if (cand && cand.id === id) {
      renderNav(cand, query);
      document.title = `${kindName(cand.kind)} ${cand.id}: SkyShift`;
      if (!opts.showing(cand)) {
        plateReady = false;
        renderBallot();
        opts.open(cand);
      }
      return;
    }
    cand = null;
    set = await candidateSet();
    v ??= await votes();
    if (t !== token || !active) return;
    sampleEl.hidden = !set?.sample;
    sampleEl.textContent = set?.sample ? SAMPLE_NOTE : "";
    if (!set || !set.candidates.length) {
      showEmpty(
        "No objects to check are published yet",
        html`<p>The pipeline's first list is on its way. Meanwhile, the <a href="#/candidates/one=1">practice round</a> and the featured targets work now.</p>`,
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
    bodyEl.hidden = false;
    emptyEl.hidden = true;
    noteEl.hidden = true;
    mine = v.api.myVote(c.id);
    justNow = false;
    saving = null;
    resetting = false;
    resetNow = false;
    error = null;
    counts = null;
    plateReady = false;
    renderBody(c);
    renderNav(c, query);
    opts.open(c);
    document.title = `${kindName(c.kind)} ${c.id}: SkyShift`;
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
    if (!active || !visible(el) || !cand) return;
    const a = keyAction(ev, { queue: false });
    if (a?.kind === "vote" && ballot?.open()) {
      ev.preventDefault();
      vote(a.answer);
    }
  };
  document.addEventListener("keydown", onKey);

  // A vote cast, changed or taken back elsewhere on this candidate (the review queue, the
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
      active = true;
      void show(param);
    },
    ready(ok) {
      if (plateReady === ok) return;
      plateReady = ok;
      renderBallot();
    },
    note(n) {
      if (!n || (!n.focused && !n.allShown && n.missingGap === null)) {
        noteEl.hidden = true;
        noteEl.innerHTML = "";
        return;
      }
      const missing = n.missingGap !== null ? `The find's own exposures are not in these frames (nearest is ${gapText(n.missingGap)} away). ` : "";
      const toggle = n.focused
        ? html`Showing the ${n.shown} exposures within a day of the find. <button class="rv-link" type="button" data-all="1">Show all ${n.all}</button>`
        : n.allShown
          ? html`Showing every exposure. <button class="rv-link" type="button" data-all="0">Only those near the find</button>`
          : "";
      noteEl.hidden = false;
      noteEl.innerHTML = missing + toggle;
    },
    hide() {
      active = false;
      token++;
      noteEl.hidden = true;
    },
    destroy() {
      active = false;
      token++;
      document.removeEventListener("keydown", onKey);
      window.removeEventListener(VOTED_EVENT, onVoted);
      el.innerHTML = "";
    },
  };
}
