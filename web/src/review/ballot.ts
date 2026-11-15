// The ballot: four large choices (keys 1–4), then where the votes stand and what happens next.
// Votes go through src/vote's store with candidate_id = the candidate's id. A vote can be changed
// (pick another answer) or taken back (contracts/api.md v3.16: each voter's latest vote counts).
import type { CandidateKind } from "../api/types";
import { ANSWERS, emptyCounts, type Answer, type Counts } from "../vote/model";
import { consensus, forAstronomers } from "./model";
import { FORUM_URL, MPC_URL, answerLabel, answerText, ballotHeading, consensusText, countsText, esc } from "./text";

const html = String.raw;

export interface BallotState {
  /** This browser's answer, if it voted. */
  mine: Answer | null;
  /** Whether mine was cast just now (not on an earlier visit). */
  justNow: boolean;
  counts: Counts | null;
  /** An answer being saved. */
  saving: Answer | null;
  /** The vote is being taken back. */
  withdrawing?: boolean;
  /** A plain line after taking a vote back ("Your vote is taken back."). */
  notice?: string | null;
  /** Choices are off (frames not on screen yet). */
  waiting: boolean;
  error: string | null;
  local: boolean;
  kind: CandidateKind;
  /** Practice: the choice the person made, and the feedback (no counts, nothing saved). */
  practice?: { answer: Answer; correct: boolean; text: string } | null;
}

export interface BallotOptions {
  onVote(a: Answer): void;
  /** Take this browser's vote back. Without it, no "Take back" button. */
  onWithdraw?(): void;
  /** Short "what happens next" (review queue) or the full one (candidate page). */
  full: boolean;
  /** Extra content after the result, e.g. the queue's Next button. */
  after?: string;
  heading?: string;
  /** The candidate's kind: "shape" asks "One object or two?" with its own answer labels. */
  kind?: CandidateKind;
}

export class Ballot {
  private state: BallotState | null = null;
  private choicesEl: HTMLElement;
  private statusEl: HTMLElement;
  private resultEl: HTMLElement;
  private localEl: HTMLElement;
  private section: HTMLElement;

  constructor(
    readonly el: HTMLElement,
    private opts: BallotOptions,
  ) {
    const id = `rv-q-${Math.random().toString(36).slice(2, 8)}`;
    const words = answerText(opts.kind);
    el.innerHTML = html`
      <section class="rv-ballot" aria-labelledby="${id}">
        <h2 class="rv-h" id="${id}">${esc(opts.heading ?? ballotHeading(opts.kind))}</h2>
        <p class="rv-local" hidden>Local mode: your votes stay in this browser. Nobody else sees them.</p>
        <div class="rv-choices" role="group" aria-labelledby="${id}">
          ${ANSWERS.map(
            (a, i) => html`<button class="rv-choice" type="button" data-answer="${a}" aria-pressed="false"
              aria-keyshortcuts="${i + 1}">
              <kbd class="rv-key" aria-hidden="true">${i + 1}</kbd>
              <span class="rv-choice-label">${words[a].label}</span>
              <span class="rv-choice-meaning">${words[a].meaning}</span>
            </button>`,
          ).join("")}
        </div>
        <p class="rv-ballot-status" role="status" aria-live="polite"></p>
        <div class="rv-result" hidden></div>
      </section>
    `;
    this.choicesEl = el.querySelector(".rv-choices")!;
    this.statusEl = el.querySelector(".rv-ballot-status")!;
    this.resultEl = el.querySelector(".rv-result")!;
    this.localEl = el.querySelector(".rv-local")!;
    this.section = el.querySelector(".rv-ballot")!;
    this.choicesEl.addEventListener("click", (ev) => {
      const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-answer]");
      if (!b || b.disabled) return;
      this.opts.onVote(b.dataset.answer as Answer);
    });
    this.resultEl.addEventListener("click", (ev) => {
      const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-act='withdraw']");
      if (!b || b.disabled) return;
      this.opts.onWithdraw?.();
    });
  }

  /** Full "what happens next" (false when another part of the page writes it). */
  setFull(full: boolean): void {
    if (this.opts.full === full) return;
    this.opts = { ...this.opts, full };
    if (this.state) this.set(this.state);
  }

  /** Can a vote be cast (or changed) right now? */
  open(): boolean {
    const s = this.state;
    return !!s && !s.saving && !s.withdrawing && !(s.waiting && !s.mine) && !s.practice;
  }

  set(s: BallotState): void {
    this.state = s;
    this.localEl.hidden = !s.local || !!s.practice;
    const chosen = s.practice?.answer ?? s.mine ?? s.saving;
    this.section.dataset.state = s.practice ? "practice" : s.saving || s.withdrawing ? "saving" : s.mine ? "voted" : s.waiting ? "waiting" : "open";
    this.choicesEl.querySelectorAll<HTMLButtonElement>("button[data-answer]").forEach((b) => {
      const a = b.dataset.answer as Answer;
      b.setAttribute("aria-pressed", String(a === chosen));
      // After a vote the other answers stay open: picking one changes the vote.
      b.disabled = !!(s.saving || s.withdrawing || s.practice || (s.waiting && !s.mine));
      b.classList.toggle("is-right", !!s.practice && a === chosen && s.practice.correct);
      b.classList.toggle("is-wrong", !!s.practice && a === chosen && !s.practice.correct);
    });

    if (s.error) {
      this.statusEl.dataset.kind = "error";
      this.statusEl.textContent = s.error;
    } else if (s.saving) {
      this.statusEl.dataset.kind = "info";
      this.statusEl.textContent = `Saving your ${answerLabel(s.saving, s.kind)} vote…`;
    } else if (s.withdrawing) {
      this.statusEl.dataset.kind = "info";
      this.statusEl.textContent = "Taking your vote back…";
    } else if (s.waiting && !s.mine && !s.practice) {
      this.statusEl.dataset.kind = "info";
      this.statusEl.textContent = "The choices open when the plate is ready.";
    } else {
      this.statusEl.dataset.kind = "info";
      this.statusEl.textContent = !s.mine && s.notice ? s.notice : "";
    }

    if (s.practice) {
      this.resultEl.hidden = false;
      this.resultEl.innerHTML = html`<p class="rv-feedback" data-correct="${s.practice.correct}">${esc(s.practice.text)}</p>${this.opts.after ?? ""}`;
      return;
    }
    if (!s.mine) {
      this.resultEl.hidden = true;
      this.resultEl.innerHTML = "";
      return;
    }
    this.resultEl.hidden = false;
    this.resultEl.innerHTML = resultHtml(s, this.opts.full, !!this.opts.onWithdraw) + (this.opts.after ?? "");
  }
}

/** Counts bar with a written tally (colour is never the only cue). */
export function countsBar(k: Counts, kind?: CandidateKind | null): string {
  const n = k.mover + k.changing + k.junk + k.unsure;
  const words = answerText(kind);
  const seg = (a: Answer) => (k[a] ? html`<i class="rv-seg rv-seg-${a}" style="flex-grow:${k[a]}"></i>` : "");
  return html`
    <div class="rv-bar" role="img" aria-label="${countsText(k, kind)}">${n ? ANSWERS.map(seg).join("") : `<i class="rv-seg rv-seg-empty"></i>`}</div>
    <dl class="rv-tally">
      ${ANSWERS.map((a) => html`<div><dt><i class="rv-sw rv-seg-${a}"></i>${words[a].label}</dt><dd>${k[a]}</dd></div>`).join("")}
    </dl>
  `;
}

function resultHtml(s: BallotState, full: boolean, canWithdraw: boolean): string {
  const k = s.counts ?? emptyCounts();
  const c = consensus(k);
  const yours = s.justNow
    ? `Saved: you said ${answerLabel(s.mine!, s.kind)}.`
    : `You said ${answerLabel(s.mine!, s.kind)} on an earlier visit.`;
  const change = canWithdraw
    ? html`<p class="rv-change rv-soft">Changed your mind? Pick another answer above, or
        <button class="rv-link" type="button" data-act="withdraw" ${s.saving || s.withdrawing ? "disabled" : ""}>take your vote back</button>.</p>`
    : html`<p class="rv-change rv-soft">Changed your mind? Pick another answer above.</p>`;
  return html`
    <p class="rv-yours">${esc(yours)}</p>
    ${change}
    ${countsBar(k, s.kind)}
    <p class="rv-consensus" data-state="${c.state}">${esc(consensusText(c, s.kind))}</p>
    ${full ? nextStepsHtml(c, s.kind) : shortNextHtml(c)}
  `;
}

function shortNextHtml(c: ReturnType<typeof consensus>): string {
  if (forAstronomers(c)) return html`<p class="rv-next">It's on the <a href="#/community">list for astronomers</a>.</p>`;
  return "";
}

/** What happens next, in plain words, with the places it leads (Community, forum, MPC). */
export function nextStepsHtml(c: ReturnType<typeof consensus>, kind: CandidateKind): string {
  const forum = html`<a href="${FORUM_URL}" target="_blank" rel="noopener">Backyard Worlds forum</a>`;
  if (forAstronomers(c)) {
    const mover =
      kind === "mover"
        ? html`<p>A possible new asteroid or comet: an astronomer checks its positions against the
            <a href="${MPC_URL}" target="_blank" rel="noopener">Minor Planet Center</a>, which records every known one,
            and reports it there if it's new.</p>`
        : kind === "high_pm"
          ? html`<p>Nearby stars and brown dwarfs are followed up with the ${forum} community, who
              check other surveys and ask for telescope time.</p>`
          : kind === "shape"
            ? html`<p>Two objects where the catalogues list one: an astronomer checks other surveys (and its
                motion) to see whether the second one is a passing asteroid, a companion, or a background source.</p>`
            : "";
    return html`<div class="rv-next">
      <p>It's on the <a href="#/community">list for astronomers</a>, with its votes, position and frames.</p>
      ${mover}
    </div>`;
  }
  if (c.state === "reached") {
    return html`<div class="rv-next"><p>The community set it aside. It stays listed with its votes, so anyone can see why.</p></div>`;
  }
  return html`<div class="rv-next">
    <p>When at least 5 people have voted and 70% agree it's ${kind === "shape" ? "two objects" : "a mover"} or changing, it joins the
      <a href="#/community">list for astronomers</a>.</p>
    <p>Want to talk it through? Share the link on the ${forum}.</p>
  </div>`;
}
