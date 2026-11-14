// Review queue (#/review): one candidate at a time, big plate, vote with 1–4, S skips, ← goes
// back. A practice round on featured targets comes first, once per browser.
import type { Candidate } from "../api/types";
import type { MountPage } from "../app/modules";
import { emptyCounts, type Answer, type Counts } from "../vote/model";
import { Ballot } from "./ballot";
import { candidateSet, preload, votes, type CandidateSet, type Votes } from "./data";
import { keyAction, queueOrder } from "./model";
import { lastDiscoverHash, trackDiscover } from "./nav";
import { SAMPLE_NOTE, castVote, factsHtml, headHtml, motionHtml, visible } from "./parts";
import { Plate } from "./plate";
import { PRACTICE, judge, markPracticeDone, practiceDone, practiceSummary, type PracticeResult } from "./practice";
import { esc } from "./text";

const html = String.raw;
const SESSION_KEY = "skyshift.review.session";

/** Pencil-mark legend: what the marks on the plate mean (DESIGN.md). */
const LEGEND = html`<ul class="rq-legend" aria-label="Marks on the plate">
  <li><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5v3.5M8 11v3.5M1.5 8H5M11 8h3.5" stroke="var(--pencil-red)" stroke-width="1.6" stroke-linecap="round"/></svg>The spot to judge</li>
  <li><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5 13.5 8 8 13.5 2.5 8z" fill="none" stroke="var(--pencil-violet)" stroke-width="1.5"/></svg>Known asteroid, predicted</li>
  <li><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5" fill="none" stroke="var(--pencil-blue)" stroke-width="1.3"/></svg>Star Gaia already knows</li>
</ul>`;

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

export const mountReview: MountPage = (el) => {
  trackDiscover();
  el.innerHTML = html`
    <div class="rv rq">
      <p class="rv-sample" hidden></p>
      <header class="rq-head">
        <div class="rq-titles">
          <h1 class="rq-title">Review</h1>
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
    el.dataset.mode = m;
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
        waiting: !plateReady && !mine,
        error,
        local: v?.store.mode === "local",
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
      <h2 class="rq-card-title">What's in the red reticle?</h2>
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
      <h2 class="rq-card-title">You got ${s.right} of ${s.of}</h2>
      <p class="rv-why">${esc(s.line.replace(/^\d+ of \d+\. /, ""))}</p>
      <p class="rq-note">Real candidates have no answer key: the community's votes decide. When 5 people have voted and 70% agree, the candidate is settled.</p>
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
    titleEl.textContent = "Review";
    toolsEl.innerHTML = html`<button class="btn btn-ghost" type="button" data-act="practice">Practice again</button>`;
    if (!set || !set.candidates.length) return showEmpty();
    if (!order.length) {
      progressEl.textContent = "Loading votes…";
      try {
        countsAll = await v!.store.counts(set.candidates.map((c) => c.id));
      } catch {
        countsAll = new Map();
      }
      order = queueOrder(set.candidates, countsAll, (id) => v!.memory.votedFor(id));
      // Already-voted candidates stay reachable with ← only if seen this session.
      byId = new Map(set.candidates.map((c) => [c.id, c]));
    }
    if (pos >= 0 && history[pos]) showItem();
    else advance();
  }

  function fresh(): Candidate | null {
    const seen = new Set(history);
    return order.find((c) => !seen.has(c.id) && !v!.memory.votedFor(c.id)) ?? null;
  }

  function upcoming(): Candidate | null {
    if (pos < history.length - 1) return byId.get(history[pos + 1]) ?? null;
    return fresh();
  }

  function remaining(): number {
    return order.filter((c) => !v!.memory.votedFor(c.id)).length;
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
    progressEl.textContent = `${reviewed} reviewed this session. ${left} ${left === 1 ? "candidate needs" : "candidates need"} votes.`;
  }

  function showItem() {
    const c = byId.get(history[pos]);
    if (!c || !v) return showEnd();
    setMode("queue");
    current = c;
    mine = v.memory.votedFor(c.id);
    justNow = votedNow.has(c.id);
    saving = null;
    error = null;
    counts = countsAll.get(c.id) ?? emptyCounts();
    plateReady = false;
    renderProgress();
    const id = html`<a href="#/c/${encodeURIComponent(c.id)}">See every detail</a> of candidate <span class="rv-nowrap">${esc(c.id)}</span>.`;
    cardEl.innerHTML = html`${headHtml(c, "h2", "")}${motionHtml(c)}${factsHtml(c)}<p class="rv-id">${id}</p>`;
    ballot = new Ballot(ballotEl, {
      onVote: voteQueue,
      full: false,
      after: html`<div class="rq-after"><button class="btn btn-primary" type="button" data-act="next">Next candidate</button><span class="rv-soft">or press Enter</span></div>`,
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
    v.store
      .counts([c.id])
      .then((m) => {
        if (current !== c || saving || justNow) return;
        counts = m.get(c.id) ?? emptyCounts();
        countsAll.set(c.id, counts);
        renderBallot();
      })
      .catch(() => {
        /* keep the earlier counts */
      });
  }

  async function voteQueue(a: Answer) {
    const c = current;
    if (!c || !v || !ballot?.open() || mode !== "queue") return;
    saving = a;
    error = null;
    renderBallot();
    const r = await castVote(v, c, a, counts);
    if (current !== c) return;
    saving = null;
    if (r.ok) {
      counts = r.counts;
      countsAll.set(c.id, r.counts);
      mine = a;
      justNow = true;
      votedNow.add(c.id);
      reviewed++;
      writeSession(reviewed);
      renderProgress();
      const skip = movesEl.querySelector<HTMLButtonElement>("[data-act='skip']");
      if (skip) skip.innerHTML = "<kbd>S</kbd>Next";
    } else {
      error = r.already ? "This browser already voted on this one. Votes can't be changed." : r.message;
    }
    renderBallot();
    if (r.ok) ballotEl.querySelector<HTMLButtonElement>("[data-act='next']")?.focus({ preventScroll: true });
  }

  function skippedCount(): number {
    return history.filter((id) => !v?.memory.votedFor(id)).length;
  }

  function showEnd() {
    current = null;
    setMode("end");
    titleEl.textContent = "Review";
    renderProgressEnd();
    const skipped = skippedCount();
    endEl.innerHTML = html`
      <h2 class="rq-end-title">You've seen every candidate in this set.</h2>
      <p class="rv-why">The hunt zone adds more soon.</p>
      <p class="rq-note">${
        reviewed
          ? `You reviewed ${reviewed} this session. Thank you: every vote moves a candidate closer to a decision.`
          : "Votes you cast here count toward the list for astronomers."
      }</p>
      <div class="rv-actions">
        ${skipped ? html`<button class="btn btn-primary" type="button" data-act="revisit">Go back to the ${skipped} you skipped</button>` : ""}
        <a class="btn ${skipped ? "btn-ghost" : "btn-primary"}" href="#/community">See what the community decided</a>
        <a class="btn btn-ghost" href="${lastDiscoverHash()}">Browse all candidates</a>
      </div>
    `;
    endEl.querySelector<HTMLElement>(".rq-end-title")!.tabIndex = -1;
    endEl.querySelector<HTMLElement>(".rq-end-title")!.focus({ preventScroll: true });
  }

  function renderProgressEnd() {
    progressEl.textContent = `${reviewed} reviewed this session.`;
  }

  function revisit() {
    const keep = history.filter((id) => v?.memory.votedFor(id));
    history.length = 0;
    history.push(...keep);
    pos = history.length - 1;
    setMode("queue");
    advance();
  }

  function showEmpty() {
    setMode("empty");
    titleEl.textContent = "Review";
    progressEl.textContent = "";
    endEl.innerHTML = html`
      <h2 class="rq-end-title">No candidates are published yet.</h2>
      <p class="rv-why">The pipeline's first list is on its way.</p>
      <p class="rq-note">Meanwhile, warm up on three plates where the answer is known, or blink a featured target yourself.</p>
      <div class="rv-actions">
        <button class="btn btn-primary" type="button" data-act="practice">Try the practice round</button>
        <a class="btn btn-ghost" href="#/explore">Open Explore</a>
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

  return {
    show() {
      plate.mount();
      document.title = "Review: SkyShift";
      started ??= start();
    },
    hide() {
      plate.unmount();
      document.title = "SkyShift";
    },
    destroy() {
      window.removeEventListener("keydown", onKey, true);
      plate.unmount();
      el.innerHTML = "";
    },
  };
};
