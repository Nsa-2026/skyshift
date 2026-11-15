// Review queue: one candidate at a time, big plate, vote with 1–4, S skips, ← goes back (and an
// answer can be changed or reset there, or on the candidate's page). A practice
// round on featured targets comes first, once per browser. It runs on its own page (#/review, the
// old link) and inside To check as "One at a time" (#/candidates/one=1), where scope() limits it
// to the filtered list.
import { nearSourceOf } from "../api/grades";
import type { Candidate } from "../api/types";
import type { MountPage, PageHandle } from "../app/modules";
import { SPHEREX_V_LIMIT } from "../blink/known";
import { emptyCounts, type Answer, type Counts } from "../vote/model";
import { Ballot } from "./ballot";
import { candidateSet, preload, suspectFor, votes, type CandidateSet, type Votes } from "./data";
import { gradeCCount, inScope, keyAction, queueOrder } from "./model";
import { lastDiscoverHash, trackDiscover } from "./nav";
import { SAMPLE_NOTE, castVote, factsHtml, fillNearSource, gradeHtml, headHtml, motionHtml, visible } from "./parts";
import { Plate } from "./plate";
import { PRACTICE, judge, markPracticeDone, practiceDone, practiceSummary, type PracticeResult } from "./practice";
import { MOVER_CAUTION, answerLabel, esc } from "./text";

const html = String.raw;
const SESSION_KEY = "skyshift.review.session";

/** Pencil-mark legend: what the marks on the plate mean (DESIGN.md). */
const LEGEND = html`<ul class="rq-legend" aria-label="Marks on the plate">
  <li><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5v3.5M8 11v3.5M1.5 8H5M11 8h3.5" stroke="var(--pencil-red)" stroke-width="1.6" stroke-linecap="round"/></svg>The spot to judge</li>
  <li><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5 13.5 8 8 13.5 2.5 8z" fill="none" stroke="var(--pencil-violet)" stroke-width="1.5"/></svg>Known asteroid, predicted (V ${SPHEREX_V_LIMIT} or brighter)</li>
  <li><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5" fill="none" stroke="var(--pencil-blue)" stroke-width="1.3"/></svg>Star Gaia already knows</li>
</ul>`;

/** The three practice plates: what each was and what the person said. */
function recapHtml(results: PracticeResult[]): string {
  if (!results.length) return "";
  const rows = results.map((r) => {
    const item = PRACTICE.find((p) => p.slug === r.slug);
    if (!item) return "";
    const said = r.correct ? `You said ${answerLabel(r.answer)}.` : `You said ${answerLabel(r.answer)}; it was ${answerLabel(item.answer)}.`;
    return html`<li class="${r.correct ? "is-right" : "is-off"}"><b>${esc(item.name)}</b><span>${esc(said)}</span></li>`;
  });
  return html`<ul class="rq-recap" aria-label="Your answers">${rows.join("")}</ul>`;
}

function readSession(): number {
  try {
    const n = Number(JSON.parse(sessionStorage.getItem(SESSION_KEY) ?? "{}")?.reviewed);
    return Number.isFinite(n) && n > 0 ? n : 0;
  } catch {
    return 0;
  }
}
function writeSession(reviewed: number): void {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ reviewed }));
  } catch {
    /* blocked: the count lives in memory */
  }
}

type Mode = "loading" | "practice" | "summary" | "queue" | "end" | "empty";

export interface QueueOptions {
  /** Inside the To check page: an h2 heading, no page title of its own, and its end links go back
   *  to the list (listHref) and to what the community says is real (realHref). */
  embedded?: { listHref(): string; realHref(): string };
}

export interface QueueHandle extends PageHandle {
  /** Only these candidates (null: every one). The queue keeps its own order inside them: what needs
   *  votes first. Call it before show() so the first object comes from the list. */
  scope(ids: readonly string[] | null): void;
}

/** The review queue's own page (#/review). */
export const mountReview: MountPage = (el) => mountQueue(el);

export function mountQueue(el: HTMLElement, opts: QueueOptions = {}): QueueHandle {
  trackDiscover();
  const embedded = opts.embedded ?? null;
  const QUEUE_TITLE = embedded ? "Check them individually" : "Review";
  const h = embedded ? "h2" : "h1";
  /** Headings inside the queue sit one level under its title. */
  const sub = embedded ? "h3" : "h2";
  el.innerHTML = html`
    <div class="rv rq${embedded ? " rq-embedded" : ""}">
      <p class="rv-sample" hidden></p>
      <header class="rq-head">
        <div class="rq-titles">
          <${h} class="rq-title">${QUEUE_TITLE}</${h}>
          <p class="rq-progress" role="status" aria-live="polite"></p>
        </div>
        <div class="rq-tools"></div>
      </header>
      <div class="rq-main">
        <section class="rq-plate" aria-label="Frames"></section>
        <div class="rq-side">
          <div class="rq-card"></div>
          <div class="rq-ballot"></div>
          <div class="rq-moves"></div>
        </div>
      </div>
      <section class="rq-end" hidden aria-live="polite"></section>
    </div>
  `;
  const $ = <T extends Element>(s: string) => el.querySelector(s) as T;
  const rootEl = $<HTMLElement>(".rq");
  const sampleEl = $<HTMLElement>(".rv-sample");
  const titleEl = $<HTMLElement>(".rq-title");
  const progressEl = $<HTMLElement>(".rq-progress");
  const toolsEl = $<HTMLElement>(".rq-tools");
  const mainEl = $<HTMLElement>(".rq-main");
  const cardEl = $<HTMLElement>(".rq-card");
  const ballotEl = $<HTMLElement>(".rq-ballot");
  const movesEl = $<HTMLElement>(".rq-moves");
  const endEl = $<HTMLElement>(".rq-end");

  let mode: Mode = "loading";
  let set: CandidateSet | null = null;
  let v: Votes | null = null;
  let started: Promise<void> | null = null;
  let plateReady = false;
  let ballot: Ballot | null = null;
  /** The candidates the queue may show (null: all); see scope(). */
  let scope: Set<string> | null = null;
  /** Has the order been built for the current scope? (An empty order counts as built.) */
  let built = false;
  /** Bumped on every new scope, so a slow vote count for an old list is dropped. */
  let scopeGen = 0;

  // practice
  let pi = 0;
  let results: PracticeResult[] = [];
  let practiceAnswer: ReturnType<typeof judge> | null = null;

  // queue
  let order: Candidate[] = [];
  let byId = new Map<string, Candidate>();
  let countsAll = new Map<string, Counts>();
  const history: string[] = [];
  let pos = -1;
  let reviewed = readSession();
  /** Voted on this visit to the page, so going back says "you said" rather than "earlier". */
  const votedNow = new Set<string>();
  let current: Candidate | null = null;
  let counts: Counts | null = null;
  let mine: Answer | null = null;
  let justNow = false;
  let saving: Answer | null = null;
  /** Taking the vote back is being saved; resetNow: it was, just now. */
  let resetting = false;
  let resetNow = false;
  let error: string | null = null;

  const plate = new Plate($(".rq-plate"), {
    onReady(ready) {
      plateReady = ready;
      renderBallot();
    },
  });

  // ---- shared -------------------------------------------------------------------------------
  function setMode(m: Mode) {
    mode = m;
    rootEl.dataset.mode = m;
    const onPlate = m === "practice" || m === "queue";
    mainEl.hidden = !onPlate && m !== "summary";
    endEl.hidden = m !== "end" && m !== "empty";
    if (!onPlate && m !== "summary") plate.clear();
  }

  function focusCard() {
    const h = cardEl.querySelector<HTMLElement>(".rv-kind, .rq-card-title");
    if (h) {
      h.tabIndex = -1;
      h.focus({ preventScroll: true });
    }
    const top = mainEl.getBoundingClientRect().top;
    if (top < 0) mainEl.scrollIntoView({ block: "start" });
  }

  function renderBallot() {
    if (!ballot) return;
    if (mode === "practice") {
      ballot.set({
        mine: null,
        justNow: false,
        counts: null,
        saving: null,
        waiting: !plateReady && !practiceAnswer,
        error: null,
        local: false,
        kind: "mover",
        practice: practiceAnswer ? { answer: practiceAnswer.answer, correct: practiceAnswer.correct, text: practiceAnswer.text } : null,
      });
    } else if (mode === "queue" && current) {
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
        kind: current.kind,
      });
    }
  }

  // ---- practice -----------------------------------------------------------------------------
  function startPractice() {
    pi = 0;
    results = [];
    setMode("practice");
    showPractice();
  }

  function showPractice() {
    const item = PRACTICE[pi];
    practiceAnswer = null;
    plateReady = false;
    titleEl.textContent = "Practice round";
    progressEl.textContent = `Plate ${pi + 1} of ${PRACTICE.length}. We know the answer to these three, so you'll see how you did.`;
    toolsEl.innerHTML = html`<button class="btn btn-ghost" type="button" data-act="skip-practice">Skip practice</button>`;
    cardEl.innerHTML = html`
      <${sub} class="rq-card-title">${esc(item.title)}</${sub}>
      <p class="rv-why">${esc(item.prompt)}</p>
      ${LEGEND}
    `;
    const last = pi === PRACTICE.length - 1;
    ballot = new Ballot(ballotEl, {
      onVote: answerPractice,
      full: false,
      after: html`<div class="rq-after"><button class="btn btn-primary" type="button" data-act="practice-next">${last ? "See how you did" : "Next plate"}</button><span class="rv-soft">or press Enter</span></div>`,
    });
    movesEl.innerHTML = "";
    renderBallot();
    plate.showTarget(item.slug);
  }

  function answerPractice(a: Answer) {
    if (mode !== "practice" || practiceAnswer || !plateReady) return;
    practiceAnswer = judge(PRACTICE[pi], a);
    results.push(practiceAnswer);
    renderBallot();
    ballotEl.querySelector<HTMLButtonElement>("[data-act='practice-next']")?.focus({ preventScroll: true });
  }

  function nextPractice() {
    if (!practiceAnswer) return;
    if (pi < PRACTICE.length - 1) {
      pi++;
      showPractice();
      focusCard();
    } else showSummary();
  }

  function showSummary() {
    if (v) markPracticeDone(v.kv);
    setMode("summary");
    plate.clear();
    const s = practiceSummary(results);
    titleEl.textContent = "Practice round";
    progressEl.textContent = "Done.";
    toolsEl.innerHTML = "";
    cardEl.innerHTML = html`
      <${sub} class="rq-card-title">You got ${s.right} of ${s.of}</${sub}>
      <p class="rv-why">${esc(s.line.replace(/^\d+ of \d+\. /, ""))}</p>
      ${recapHtml(results)}
      <p class="rq-note">Real objects have no answer key, and most turn out to be detector artefacts: the community's votes decide. When 5 people have voted and 70% agree, the object is settled. Short lessons with more examples are on <a href="#/learn">Learn</a>.</p>
      <div class="rq-after"><button class="btn btn-primary" type="button" data-act="start">Start reviewing</button></div>
    `;
    ballotEl.innerHTML = "";
    ballot = null;
    movesEl.innerHTML = "";
    focusCard();
  }

  function skipPractice() {
    if (v) markPracticeDone(v.kv);
    startQueue();
  }

  // ---- queue --------------------------------------------------------------------------------
  async function startQueue() {
    setMode("queue");
    titleEl.textContent = QUEUE_TITLE;
    toolsEl.innerHTML = html`<button class="btn btn-ghost" type="button" data-act="practice">Practice again</button>`;
    if (!set || !set.candidates.length) return showEmpty();
    while (!built) {
      progressEl.textContent = "Loading votes…";
      const gen = scopeGen;
      const next = await orderFor(scope);
      // A new scope arrived meanwhile: build again, for it.
      if (gen === scopeGen) {
        order = next;
        built = true;
      }
    }
    if (pos >= 0 && history[pos]) showItem();
    else advance();
  }

  /** The queue's order over the candidates in scope. Counts are read once per candidate. */
  async function orderFor(within: Set<string> | null): Promise<Candidate[]> {
    if (!set || !v) return [];
    const api = v.api;
    const pool = inScope(set.candidates, within);
    const need = pool.filter((c) => !countsAll.has(c.id)).map((c) => c.id);
    if (need.length) {
      try {
        for (const [id, k] of await api.countsFor(need)) countsAll.set(id, k);
      } catch {
        /* order without the missing counts */
      }
    }
    // Already-voted candidates stay reachable with ← only if seen this session.
    byId = new Map(set.candidates.map((c) => [c.id, c]));
    return queueOrder(pool, countsAll, (id) => api.myVote(id));
  }

  /** A new list from To check: start over inside it, keeping the object on screen if it's still in. */
  async function setScope(ids: readonly string[] | null) {
    const next = ids ? new Set(ids) : null;
    const same = next === scope || (!!next && !!scope && next.size === scope.size && [...next].every((id) => scope!.has(id)));
    if (same) return;
    scope = next;
    const gen = ++scopeGen;
    if (!built) return; // not started yet: startQueue reads the scope
    const nextOrder = await orderFor(next);
    if (gen !== scopeGen) return;
    order = nextOrder;
    history.length = 0;
    pos = -1;
    const keep = mode === "queue" && current && (!next || next.has(current.id)) ? current : null;
    if (keep) {
      // The object on screen stays: only the count, Back and the next preload change.
      history.push(keep.id);
      pos = 0;
      renderProgress();
      movesEl.querySelector<HTMLButtonElement>("[data-act='back']")?.setAttribute("disabled", "");
      const up = upcoming();
      if (up) preload(up);
    } else if (mode === "queue" || mode === "end") {
      advance();
    }
  }

  function fresh(): Candidate | null {
    const seen = new Set(history);
    return order.find((c) => !seen.has(c.id) && !v!.api.myVote(c.id)) ?? null;
  }

  function upcoming(): Candidate | null {
    if (pos < history.length - 1) return byId.get(history[pos + 1]) ?? null;
    return fresh();
  }

  function remaining(): number {
    return order.filter((c) => !v!.api.myVote(c.id)).length;
  }

  function advance() {
    if (pos < history.length - 1) {
      pos++;
    } else {
      const c = fresh();
      if (!c) return showEnd();
      history.push(c.id);
      pos = history.length - 1;
    }
    showItem();
  }

  function back() {
    if (pos <= 0) return;
    pos--;
    showItem();
  }

  function renderProgress() {
    const left = remaining();
    const n = left.toLocaleString("en-GB");
    const need = scope
      ? `${n} in this list ${left === 1 ? "still needs" : "still need"} your vote`
      : `${n} ${left === 1 ? "object to check needs" : "objects to check need"} votes`;
    progressEl.textContent = `${reviewed} reviewed this session. ${need}.${skippedCText()}`;
  }

  /** Grade C never enters the queue: say so, once, in the progress line. */
  function skippedCText(): string {
    const n = set ? gradeCCount(inScope(set.candidates, scope)) : 0;
    return n ? ` ${n.toLocaleString("en-GB")} grade C ${n === 1 ? "object isn't" : "objects aren't"} in the queue: ${n === 1 ? "it fails" : "they fail"} a pipeline check.` : "";
  }

  function showItem() {
    const c = byId.get(history[pos]);
    if (!c || !v) return showEnd();
    setMode("queue");
    current = c;
    mine = v.api.myVote(c.id);
    justNow = votedNow.has(c.id);
    saving = null;
    resetting = false;
    resetNow = false;
    error = null;
    counts = countsAll.get(c.id) ?? emptyCounts();
    plateReady = false;
    renderProgress();
    const caution = c.kind === "mover" ? `${MOVER_CAUTION} ` : "";
    // The raw id is for Advanced (pro-only); everyone gets the link.
    const id = html`${caution}<a href="#/c/${encodeURIComponent(c.id)}">See every detail</a><span class="pro-only"> of object <span class="rv-nowrap">${esc(c.id)}</span></span>.`;
    cardEl.innerHTML = html`${headHtml(c, sub, "")}${motionHtml(c)}${gradeHtml(c, false)}${factsHtml(c)}<p class="rv-id">${id}</p>`;
    if (c.kind === "mover")
      suspectFor(c.id).then((s) => {
        if (current === c) fillNearSource(cardEl, nearSourceOf(s));
      });
    ballot = new Ballot(ballotEl, {
      onVote: voteQueue,
      onReset: () => voteQueue(null),
      kind: c.kind,
      full: false,
      after: html`<div class="rq-after"><button class="btn btn-primary" type="button" data-act="next">Next object</button><span class="rv-soft">or press Enter</span></div>`,
    });
    movesEl.innerHTML = html`
      <div class="rq-buttons">
        <button class="btn btn-ghost" type="button" data-act="back" ${pos > 0 ? "" : "disabled"}><kbd>←</kbd>Back</button>
        <button class="btn btn-ghost" type="button" data-act="skip"><kbd>S</kbd>${mine ? "Next" : "Skip"}</button>
      </div>
      <p class="rq-keys">Keys: <kbd>1</kbd> to <kbd>4</kbd> vote, <kbd>S</kbd> skips, <kbd>←</kbd> goes back, <kbd>Space</kbd> pauses the blink.</p>
      ${LEGEND}
    `;
    renderBallot();
    plate.showCandidate(c);
    const up = upcoming();
    if (up) preload(up);
    // Others may have voted since the queue was built.
    v.api
      .countsFor([c.id])
      .then((m) => {
        if (current !== c || saving || resetting || justNow || resetNow) return;
        counts = m.get(c.id) ?? emptyCounts();
        countsAll.set(c.id, counts);
        renderBallot();
      })
      .catch(() => {
        /* keep the earlier counts */
      });
  }

  /** Vote, change the answer, or (null) reset it. One write at a time: the ballot is shut while saving. */
  async function voteQueue(a: Answer | null) {
    const c = current;
    if (!c || !v || !ballot?.open() || mode !== "queue" || a === mine) return;
    const first = !mine;
    saving = a;
    resetting = !a;
    resetNow = false;
    error = null;
    renderBallot();
    const r = await castVote(v, c, a, counts);
    if (current !== c) return;
    saving = null;
    resetting = false;
    if (r.ok) {
      counts = r.counts;
      countsAll.set(c.id, r.counts);
      mine = a;
      justNow = !!a;
      resetNow = !a;
      // Reviewed counts objects, not answers: a change or a second vote after a reset adds nothing.
      if (a && first && !votedNow.has(c.id)) {
        reviewed++;
        writeSession(reviewed);
      }
      if (a) votedNow.add(c.id);
      renderProgress();
      const skip = movesEl.querySelector<HTMLButtonElement>("[data-act='skip']");
      if (skip) skip.innerHTML = `<kbd>S</kbd>${mine ? "Next" : "Skip"}`;
    } else {
      error = r.message;
    }
    renderBallot();
    if (r.ok && a && first) ballotEl.querySelector<HTMLButtonElement>("[data-act='next']")?.focus({ preventScroll: true });
    else if (r.ok && !a) ballot?.focusChoices();
  }

  function skippedCount(): number {
    return history.filter((id) => !v?.api.myVote(id)).length;
  }

  function showEnd() {
    current = null;
    setMode("end");
    titleEl.textContent = QUEUE_TITLE;
    renderProgressEnd();
    const skipped = skippedCount();
    const thanks = reviewed
      ? `You reviewed ${reviewed} this session. Thank you: every vote moves an object closer to a decision.`
      : "Votes you cast here count toward the list for astronomers.";
    const revisitBtn = skipped ? html`<button class="btn btn-primary" type="button" data-act="revisit">Go back to the ${skipped} you skipped</button>` : "";
    const lead = skipped ? "btn-ghost" : "btn-primary";
    endEl.innerHTML = embedded
      ? html`
      <${sub} class="rq-end-title">${history.length ? "You've seen every object in this list." : "Nothing in this list needs your vote."}</${sub}>
      <p class="rv-why">${
        history.length
          ? "Change a filter to find more, or go back to the list."
          : "You've voted on every one of them, or the rest fail a pipeline check (grade C). Change a filter, or go back to the list."
      }</p>
      <p class="rq-note">${thanks}</p>
      <div class="rv-actions">
        ${revisitBtn}
        <a class="btn ${lead}" href="${esc(embedded.listHref())}">Back to the list</a>
        <a class="btn btn-ghost" href="${esc(embedded.realHref())}">What the community says is real</a>
      </div>`
      : html`
      <h2 class="rq-end-title">You've seen every object in this set.</h2>
      <p class="rv-why">The hunt zone adds more soon.</p>
      <p class="rq-note">${thanks}</p>
      <div class="rv-actions">
        ${revisitBtn}
        <a class="btn ${lead}" href="#/community">See what the community decided</a>
        <a class="btn btn-ghost" href="${lastDiscoverHash()}">Browse the whole list</a>
      </div>
    `;
    endEl.querySelector<HTMLElement>(".rq-end-title")!.tabIndex = -1;
    endEl.querySelector<HTMLElement>(".rq-end-title")!.focus({ preventScroll: true });
  }

  function renderProgressEnd() {
    progressEl.textContent = `${reviewed} reviewed this session.${skippedCText()}`;
  }

  function revisit() {
    const keep = history.filter((id) => v?.api.myVote(id));
    history.length = 0;
    history.push(...keep);
    pos = history.length - 1;
    setMode("queue");
    advance();
  }

  function showEmpty() {
    setMode("empty");
    titleEl.textContent = QUEUE_TITLE;
    progressEl.textContent = "";
    endEl.innerHTML = html`
      <${sub} class="rq-end-title">No objects to check are published yet.</${sub}>
      <p class="rv-why">The pipeline's first list is on its way.</p>
      <p class="rq-note">Meanwhile, warm up on three plates where the answer is known, or blink a featured target yourself.</p>
      <div class="rv-actions">
        <button class="btn btn-primary" type="button" data-act="practice">Try the practice round</button>
        <a class="btn btn-ghost" href="#/explore">Open Blink</a>
      </div>
    `;
  }

  // ---- events -------------------------------------------------------------------------------
  el.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-act]");
    if (!b || b.disabled) return;
    switch (b.dataset.act) {
      case "skip-practice":
        return skipPractice();
      case "practice":
        return startPractice();
      case "practice-next":
        return nextPractice();
      case "start":
        return startQueue().then(focusCard);
      case "next":
      case "skip":
        advance();
        return focusCard();
      case "back":
        back();
        return focusCard();
      case "revisit":
        return revisit();
    }
  });

  // Window capture: ← and → mean "previous / next candidate" here, before the blink's own
  // arrow keys (which step frames) see them. Space still plays and pauses the blink.
  const onKey = (ev: KeyboardEvent) => {
    if (!visible(el)) return;
    const a = keyAction(ev, { queue: true });
    if (!a) return;
    if (mode === "practice") {
      if (a.kind === "vote") {
        ev.preventDefault();
        answerPractice(a.answer);
      } else if (a.kind === "confirm" && practiceAnswer) {
        ev.preventDefault();
        nextPractice();
      }
      return;
    }
    if (mode !== "queue" || !current) return;
    if (a.kind === "vote") {
      if (!ballot?.open()) return;
      ev.preventDefault();
      voteQueue(a.answer);
      return;
    }
    if (a.kind === "confirm" && !mine) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (a.kind === "back") back();
    else advance();
    focusCard();
  };
  window.addEventListener("keydown", onKey, true);

  async function start() {
    [set, v] = await Promise.all([candidateSet(), votes()]);
    sampleEl.hidden = !set?.sample;
    sampleEl.textContent = set?.sample ? SAMPLE_NOTE : "";
    if (!practiceDone(v.kv)) startPractice();
    else await startQueue();
  }

  let shown = false;
  let pageTitle = "";
  return {
    show() {
      plate.mount();
      // Embedded, the page's own title comes back when the queue hides.
      if (!shown) pageTitle = document.title;
      shown = true;
      document.title = embedded ? "To check, individually: SkyShift" : "Review: SkyShift";
      started ??= start();
    },
    hide() {
      plate.unmount();
      if (!embedded) document.title = "SkyShift";
      else if (shown) document.title = pageTitle;
      shown = false;
    },
    destroy() {
      window.removeEventListener("keydown", onKey, true);
      plate.unmount();
      el.innerHTML = "";
    },
    scope(ids) {
      void setScope(ids);
    },
  };
}
