// Small pieces both review pages share: the candidate's headline (kind, why, motion), copy to
// clipboard, the sample-data note and the casting of a vote.
import { GRADE_TEXT, NEAR_SOURCE_HINT, UNGRADED_TEXT, failedGates, gateText, gradeCSentence, gradeOf, nearSourceText } from "../api/grades";
import type { Candidate } from "../api/types";
import { formatRaDec } from "../app/astro";
import type { Answer, Counts } from "../vote/model";
import { VoteError } from "../vote/model";
import type { Votes } from "./data";
import { movedVote } from "./model";
import { KIND_ICON, abMag, esc, kindName, motion, ujy } from "./text";

const html = String.raw;

/** Kind line (icon + plain name) as the page heading, then the why sentence, large. */
export function headHtml(c: Candidate, level: "h1" | "h2" | "h3", idLine: string): string {
  return html`
    <header class="rv-head">
      <${level} class="rv-kind"><span class="rv-icon">${KIND_ICON[c.kind] ?? ""}</span>${esc(kindName(c.kind))}</${level}>
      <p class="rv-why">${esc(c.why || "The pipeline flagged this spot for a look.")}</p>
      ${idLine ? html`<p class="rv-id">${idLine}</p>` : ""}
    </header>
  `;
}

/** The measured motion, the largest number on the page (DESIGN.md), or nothing without one. */
export function motionHtml(c: Candidate): string {
  const m = motion(c);
  if (!m) return "";
  // σ (motion over its error) is a number for Advanced (pro-only); the ± stays for everyone.
  const err = m.plusMinus ? html`<span class="rv-pm">± ${m.plusMinus}${m.unit}${m.sigma != null ? html`<span class="pro-only">, ${sig(m.sigma)}σ</span>` : ""}</span>` : "";
  const toward = m.toward ? html`<span class="rv-toward">toward the ${m.toward}${c.pa_deg != null ? ` (${Math.round(c.pa_deg)}°)` : ""}</span>` : "";
  return html`
    <div class="rv-motion">
      <p class="rv-big"><span class="rv-big-n">${m.value}</span><span class="rv-big-u">${m.unit}</span></p>
      <p class="rv-motion-more">${err}${toward}</p>
    </div>
  `;
}

const sig = (s: number) => (s >= 100 ? Math.round(s).toLocaleString("en-US") : s.toFixed(1));

/**
 * The pipeline's vetting grade (src/api/grades.ts), for fast movers. "full" (the candidate page)
 * also says why a grade C failed, check by check, and calls an ungraded mover "not vetted yet";
 * the queue shows only a grade the file carries. A slot for the near-source warning follows
 * (filled by fillNearSource once suspects.json has been read).
 */
export function gradeHtml(c: Candidate, full: boolean): string {
  const g = gradeOf(c);
  if (c.kind !== "mover" && !g) return "";
  if (!g && !full) return nearSlot();
  const t = g ? GRADE_TEXT[g] : UNGRADED_TEXT;
  const gates = g === "C" ? failedGates(c) : [];
  const why =
    full && gates.length
      ? html`<ul class="rv-grade-why">${gates.map((code) => html`<li><b>${esc(gateText(code).label)}.</b> ${esc(gateText(code).plain)}</li>`).join("")}</ul>`
      : g === "C" && gates.length
        ? html`<p class="rv-grade-text">${esc(gradeCSentence(gates))}</p>`
        : "";
  return html`<section class="rv-grade" data-grade="${g ?? "none"}" aria-label="Vetting grade">
    <p class="rv-grade-head"><span class="rv-grade-badge">${esc(t.short)}</span>${g ? html` <span class="rv-grade-name">${esc(t.name)}</span>` : ""}</p>
    <p class="rv-grade-text">${esc(t.plain)}</p>
    ${why}
    ${nearSlot()}
  </section>`;
}

const nearSlot = () => html`<p class="rv-grade-near" data-near hidden></p>`;

/** Fill a page's near-source slot: "A catalogued source sits 4.1″ away." (a warning, not a gate). */
export function fillNearSource(root: ParentNode, arcsec: number | null): void {
  const el = root.querySelector<HTMLElement>("[data-near]");
  if (!el) return;
  el.hidden = arcsec == null;
  el.textContent = arcsec == null ? "" : `${nearSourceText(arcsec)} A warning only: the two may blend.`;
  el.title = arcsec == null ? "" : NEAR_SOURCE_HINT;
}

/** Two or three quick facts for the queue: S/N, brightness, detector. */
export function factsHtml(c: Candidate): string {
  // [term, value, Advanced only]: S/N is a pipeline number (pro-only); brightness has units.
  const rows: [string, string, boolean][] = [];
  if (c.snr != null) rows.push(["Signal to noise", sig(c.snr), true]);
  if (c.flux_ujy != null && c.flux_ujy > 0) rows.push(["Brightness", `${ujy(c.flux_ujy)} µJy <span class="rv-soft">AB ${abMag(c.flux_ujy).toFixed(1)}</span>`, false]);
  // shape: the sky direction of the stretch (an axis, 0–180° east of north).
  if (c.kind === "shape" && c.pa_deg != null) rows.push(["Stretched along", `${Math.round(c.pa_deg)}° east of north`, false]);
  // The detector is on the plate's tabs already.
  if (!rows.length) return "";
  const pro = rows.every(([, , p]) => p) ? " pro-only" : "";
  return html`<dl class="rv-facts${pro}">${rows.map(([k, v, p]) => html`<div${p ? ' class="pro-only"' : ""}><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
}

export function positionText(c: Candidate): string {
  return formatRaDec(c);
}

/** Copy text; true when it worked. Falls back to a hidden textarea where the API is blocked. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

/** Button feedback after a copy: "Copied" for two seconds, then the label comes back. */
export async function copyWithFeedback(btn: HTMLButtonElement, text: string): Promise<void> {
  const label = btn.dataset.label ?? btn.textContent ?? "Copy";
  btn.dataset.label = label;
  const ok = await copyText(text);
  btn.textContent = ok ? "Copied" : "Copy failed: select the text instead";
  window.setTimeout(() => {
    btn.textContent = label;
  }, 2000);
}

export const SAMPLE_NOTE =
  "Sample data: a real pilot sample from fixtures/api/candidates.json, shown in development because /data/candidates.json isn't published yet.";

/**
 * Cast or change this browser's vote (contracts/api.md v3.16: a new vote document; each voter's
 * latest one counts). `previous` is the vote it replaces, if any. Returns the new counts, or an
 * error in plain words.
 */
export async function castVote(
  v: Votes,
  c: Candidate,
  a: Answer,
  counts: Counts | null,
  previous: Answer | null = null,
): Promise<{ ok: true; counts: Counts } | { ok: false; message: string }> {
  try {
    await v.api.castVote(c.id, a);
    return { ok: true, counts: movedVote(counts ?? undefined, previous, a) };
  } catch (e) {
    const err = e instanceof VoteError ? e : new VoteError("Something went wrong saving the vote. Try again.");
    return { ok: false, message: err.message };
  }
}

/** Take this browser's vote back (an answer `withdrawn`, v3.16). Returns the new counts, or an error. */
export async function takeBackVote(
  v: Votes,
  c: Candidate,
  counts: Counts | null,
  previous: Answer | null,
): Promise<{ ok: true; counts: Counts } | { ok: false; message: string }> {
  try {
    await v.api.withdrawVote(c.id);
    return { ok: true, counts: movedVote(counts ?? undefined, previous, null) };
  } catch (e) {
    const err = e instanceof VoteError ? e : new VoteError("Something went wrong taking the vote back. Try again.");
    return { ok: false, message: err.message };
  }
}

/** Is the page element on screen (its page not hidden)? */
export const visible = (el: HTMLElement): boolean => el.isConnected && el.getClientRects().length > 0;
