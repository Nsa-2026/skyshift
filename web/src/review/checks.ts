// The checks a candidate carries (contracts/api.md v3.23 `checks`) and its status (`known`, `flaw`),
// as HTML for the To check list (src/discover) and the suspect panel in the Viewer (./candidate.ts).
// Each check is marked in words, strong first: "Sign against", "Can't rule out", "Sign for"; then
// what was measured, then what to look at. Colour is never the only cue.
import "./checks.css";
import { LEVEL_TEXT, STATUS_TEXT, checksOf, knownNameOf, statusOf } from "../api/checks";
import type { CandidateCheck, CheckLevel } from "../api/types";
import { esc } from "./text";

const html = String.raw;

/** Small marks in front of each level's words: a bar (against), an exclamation (can't rule out), a tick (for). */
const MARK: Record<CheckLevel, string> = {
  strong: html`<svg class="ck-mark" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><circle cx="6" cy="6" r="6"/><path d="M3 6h6"/></svg>`,
  warn: html`<svg class="ck-mark" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><circle cx="6" cy="6" r="5.25"/><path d="M6 3v3.6M6 8.6v.1"/></svg>`,
  ok: html`<svg class="ck-mark" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><circle cx="6" cy="6" r="6"/><path d="m3.4 6.2 1.8 1.8 3.4-3.6"/></svg>`,
};

const sentence = (s: string) => (/[.!?…]$/.test(s) ? s : `${s}.`);

/** One check: its level in words, what was measured, and what to look at (when the file says). */
export function checkHtml(c: CandidateCheck): string {
  const t = LEVEL_TEXT[c.level];
  return html`<li class="ck is-${c.level}" data-code="${esc(c.code)}">
    <span class="ck-level" title="${esc(t.hint)}">${MARK[c.level]}${esc(t.label)}<span class="visually-hidden">:</span></span>
    <span class="ck-text">${esc(sentence(c.text))}</span>
    ${c.look ? html`<span class="ck-look"><span class="ck-look-k">Look:</span> ${esc(sentence(c.look))}</span>` : ""}
  </li>`;
}

/** A candidate's checks as a list, strong first, or "" without any. */
export function checksHtml(x: { checks?: unknown }, opts: { label?: string; className?: string } = {}): string {
  const checks = checksOf(x);
  if (!checks.length) return "";
  return html`<ul class="ck-list${opts.className ? ` ${opts.className}` : ""}" aria-label="${esc(opts.label ?? "What to check")}">${checks.map(checkHtml).join("")}</ul>`;
}

/** Known body or instrument flaw: what it certainly is, in words ("" for open ones). */
export function statusHtml(x: { status?: unknown; known_name?: unknown }, opts: { short?: boolean } = {}): string {
  const s = statusOf(x);
  if (s === "open") return "";
  const name = knownNameOf(x);
  const head = s === "known" ? (name ? html`Known body: <span class="ck-name">${esc(name)}</span>` : "Known body") : "Instrument flaw";
  return html`<p class="ck-status is-${s}"><b class="ck-status-head">${head}</b>${opts.short ? "" : html` <span class="ck-status-text">${esc(STATUS_TEXT[s].plain)}</span>`}</p>`;
}
