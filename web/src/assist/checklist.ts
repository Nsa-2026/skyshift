// The check assistant on the page: a quiet vertical checklist (web/DESIGN.md, "The check
// assistant"). Each step shows its status twice, as a grease-pencil glyph and as a word, so colour
// is never the only cue. "Your check" steps take a yes / no / can't tell reply, kept per candidate
// in this browser. Every step has a closed "How to check this yourself" disclosure.
import "./assist.css";
import { ANSWER_TEXT, type Answer, type KV } from "../vote/model";
import { blinkHref, canReply, checklist, summarize, withReplies } from "./checks";
import { raw } from "./format";
import { REPLY_EVENT, loadReplies, saveReply } from "./replies";
import type { AnsweredStep, AssistCandidate, Reply, StepId, StepStatus } from "./types";
import { REPLY_TEXT, STATUS_TEXT } from "./words";

export interface ChecklistOptions {
  /** Professional mode: show field names and raw values under each finding. */
  details?: boolean;
  /** Called by the "Vote on it" button of the last step. Without it the step only explains. */
  onVote?: () => void;
  /** This browser's vote on the candidate, if any (src/vote store myVote). */
  myVote?: Answer | null;
  /** Link for "Open it in Blink"; null hides it. Default: the Explore page at the candidate. */
  blinkHref?: string | null;
  /** Heading text; "" leaves the heading out (when the page has its own). */
  title?: string;
  /** Storage for replies (tests). Default: this browser's localStorage, safely. */
  kv?: KV;
}

export interface ChecklistHandle {
  /** Show another candidate, or the same one with new data; options merge into the current ones. */
  update(c: AssistCandidate, opts?: Partial<ChecklistOptions>): void;
  destroy(): void;
}

const html = String.raw;
export const esc = (s: string) =>
  s.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);

/** Grease-pencil glyphs (DESIGN.md: 1.5 px strokes, round caps, slightly translucent). */
export const GLYPH: Record<StepStatus | "voted", string> = {
  pass: '<path d="M4.5 10.6 8.4 14.4 15.6 5.4"/>',
  caution: '<path d="M10 3.4 17.2 16H2.8z"/><path d="M10 8.2v3.6"/><path d="M10 13.9v.2"/>',
  fail: '<path d="M5.4 5.4l9.2 9.2M14.6 5.4l-9.2 9.2"/>',
  unknown: '<circle cx="10" cy="10" r="6.2" stroke-dasharray="2.1 2.5"/>',
  ask: '<circle cx="10" cy="10" r="6.2"/><path d="M10 10h.01"/>',
  voted: '<path d="M4.5 10.6 8.4 14.4 15.6 5.4"/>',
};
export const glyph = (k: keyof typeof GLYPH) =>
  `<svg class="as-glyph" viewBox="0 0 20 20" aria-hidden="true" focusable="false">${GLYPH[k]}</svg>`;

let uid = 0;

/** Mount the checklist for one candidate. See ChecklistOptions; returns update() and destroy(). */
export function mountChecklist(el: HTMLElement, c: AssistCandidate, opts: ChecklistOptions = {}): ChecklistHandle {
  const id = `as${++uid}`;
  let cand = c;
  let o: ChecklistOptions = { ...opts };
  const open = new Set<StepId>();
  const root = document.createElement("section");
  root.className = "as";
  el.append(root);

  function stepHtml(s: AnsweredStep, i: number): string {
    const voted = s.id === "call" && o.myVote ? o.myVote : null;
    const word = voted ? "Voted" : STATUS_TEXT[s.status];
    const howId = `${id}-how-${s.id}`;
    const isOpen = open.has(s.id);
    const blink = o.blinkHref === undefined ? blinkHref(cand) : o.blinkHref;
    const replyable = canReply(s);
    const finding =
      s.id === "call" && voted
        ? `You voted ${ANSWER_TEXT[voted].label.toLowerCase()}. ${s.finding}`
        : s.finding;
    const ask = replyable
      ? html`<div class="as-ask">
          <p class="as-prompt" id="${id}-q-${s.id}">${esc(s.prompt!)}</p>
          <div class="seg as-seg" role="group" aria-labelledby="${id}-q-${s.id}">
            ${(["yes", "no", "unsure"] as Reply[])
              .map(
                (r) =>
                  html`<button type="button" data-step="${s.id}" data-reply="${r}" aria-pressed="${s.reply === r}">${REPLY_TEXT[r]}</button>`,
              )
              .join("")}
          </div>
          ${s.reply ? html`<p class="as-note">Your answer, saved in this browser. Press it again to clear it.</p>` : ""}
        </div>`
      : "";
    const vote =
      s.id === "call" && o.onVote && !voted
        ? html`<p class="as-act"><button type="button" class="btn" data-vote>Vote on it</button></p>`
        : "";
    return html`<li class="as-step" data-step="${s.id}" data-status="${voted ? "voted" : s.status}">
      <div class="as-state" aria-hidden="true">${glyph(voted ? "voted" : s.status)}<span class="as-word">${esc(word)}</span></div>
      <div class="as-body">
        <h3 class="as-q"><span class="visually-hidden">Step ${i + 1}: </span>${esc(s.question)}</h3>
        <p class="as-find"><span class="visually-hidden">${esc(word)}. </span>${esc(finding)}</p>
        ${o.details ? html`<p class="as-details">${esc(s.details)}${s.reply ? esc(`; reply=${s.reply}, data status=${s.dataStatus}`) : ""}</p>` : ""}
        ${ask}${vote}
        <button type="button" class="as-how" data-how="${s.id}" aria-expanded="${isOpen}" aria-controls="${howId}">
          <svg class="as-caret" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path d="M4 2.5 7.5 6 4 9.5"/></svg>How to check this yourself
        </button>
        <div class="as-howto" id="${howId}"${isOpen ? "" : " hidden"}>
          <ol>${s.howTo.map((h) => `<li>${esc(h)}</li>`).join("")}</ol>
          ${blink && s.id !== "known" && s.id !== "call" ? html`<p><a href="${esc(blink)}">Open it in Blink</a></p>` : ""}
        </div>
      </div>
    </li>`;
  }

  function render(focus?: { step: string; attr: "reply" | "how" | "vote"; value?: string }) {
    const steps = withReplies(checklist(cand), loadReplies(cand.id, o.kv));
    const title = o.title ?? "Check it yourself";
    const meta = o.details
      ? html`<p class="as-details">Candidate ${esc(cand.id)}, kind ${esc(cand.kind)}, zone ${esc(cand.zone)}, pipeline score ${esc(raw(cand.score, 3))} (0–1, higher is more promising).</p>`
      : "";
    root.classList.toggle("is-details", !!o.details);
    root.setAttribute("aria-label", title || "Check it yourself");
    root.innerHTML = html`<div class="as-head">
        ${title ? html`<h2 class="as-title">${esc(title)}</h2>` : ""}
        <p class="as-sum">${esc(summarize(steps))}</p>
        ${meta}
      </div>
      <ol class="as-list">${steps.map(stepHtml).join("")}</ol>
      <p class="visually-hidden" aria-live="polite" data-live></p>`;
    if (focus) {
      const sel =
        focus.attr === "reply"
          ? `[data-step="${focus.step}"][data-reply="${focus.value}"]`
          : focus.attr === "how"
            ? `[data-how="${focus.step}"]`
            : "[data-vote]";
      (root.querySelector(sel) as HTMLElement | null)?.focus();
    }
  }

  function say(msg: string) {
    const live = root.querySelector("[data-live]");
    if (live) live.textContent = msg;
  }

  root.addEventListener("click", (ev) => {
    const t = (ev.target as Element).closest("button");
    if (!t || !root.contains(t)) return;
    if (t.hasAttribute("data-reply")) {
      const step = t.getAttribute("data-step") as StepId;
      const r = t.getAttribute("data-reply") as Reply;
      const was = loadReplies(cand.id, o.kv)[step] ?? null;
      const next = was === r ? null : r;
      saveReply(cand.id, step, next, o.kv);
      render({ step, attr: "reply", value: r });
      say(next ? `Saved your answer: ${REPLY_TEXT[next].toLowerCase()}.` : "Cleared your answer.");
    } else if (t.hasAttribute("data-how")) {
      const step = t.getAttribute("data-how") as StepId;
      const box = root.querySelector<HTMLElement>(`[id="${t.getAttribute("aria-controls") ?? ""}"]`);
      const nowOpen = !open.has(step);
      if (nowOpen) open.add(step);
      else open.delete(step);
      t.setAttribute("aria-expanded", String(nowOpen));
      if (box) box.hidden = !nowOpen;
    } else if (t.hasAttribute("data-vote")) {
      o.onVote?.();
    }
  });

  const onReply = (ev: Event) => {
    const d = (ev as CustomEvent<{ candidateId: string }>).detail;
    if (d?.candidateId !== cand.id) return;
    const active = document.activeElement as HTMLElement | null;
    if (active && root.contains(active)) return; // our own click already re-rendered
    render();
  };
  window.addEventListener(REPLY_EVENT, onReply);

  render();
  return {
    update(next, more) {
      if (next.id !== cand.id) open.clear();
      cand = next;
      if (more) o = { ...o, ...more };
      render();
    },
    destroy() {
      window.removeEventListener(REPLY_EVENT, onReply);
      root.remove();
    },
  };
}
