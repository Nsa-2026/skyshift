// Small pieces both review pages share: the candidate's headline (kind, why, motion), copy to
// clipboard, the sample-data note and the casting of a vote.
import type { Candidate } from "../api/types";
import { formatRaDec } from "../app/astro";
import type { Answer, Counts } from "../vote/model";
import { VoteError } from "../vote/model";
import type { Votes } from "./data";
import { withVote } from "./model";
import { KIND_ICON, abMag, esc, kindName, motion, ujy } from "./text";

const html = String.raw;

/** Kind line (icon + plain name) as the page heading, then the why sentence, large. */
export function headHtml(c: Candidate, level: "h1" | "h2", idLine: string): string {
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
  const err = m.plusMinus ? html`<span class="rv-pm">± ${m.plusMinus}${m.unit}${m.sigma != null ? html`, ${sig(m.sigma)}σ` : ""}</span>` : "";
  const toward = m.toward ? html`<span class="rv-toward">toward the ${m.toward}${c.pa_deg != null ? ` (${Math.round(c.pa_deg)}°)` : ""}</span>` : "";
  return html`
    <div class="rv-motion">
      <p class="rv-big"><span class="rv-big-n">${m.value}</span><span class="rv-big-u">${m.unit}</span></p>
      <p class="rv-motion-more">${err}${toward}</p>
    </div>
  `;
}

const sig = (s: number) => (s >= 100 ? Math.round(s).toLocaleString("en-US") : s.toFixed(1));

/** Two or three quick facts for the queue: S/N, brightness, detector. */
export function factsHtml(c: Candidate): string {
  const rows: [string, string][] = [];
  if (c.snr != null) rows.push(["Signal to noise", sig(c.snr)]);
  if (c.flux_ujy != null && c.flux_ujy > 0) rows.push(["Brightness", `${ujy(c.flux_ujy)} µJy <span class="rv-soft">AB ${abMag(c.flux_ujy).toFixed(1)}</span>`]);
  // The detector is on the plate's tabs already.
  if (!rows.length) return "";
  return html`<dl class="rv-facts">${rows.map(([k, v]) => html`<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
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

/** Cast a vote. Returns the new counts, or an error in plain words. */
export async function castVote(
  v: Votes,
  c: Candidate,
  a: Answer,
  counts: Counts | null,
): Promise<{ ok: true; counts: Counts } | { ok: false; message: string; already: boolean }> {
  try {
    await v.api.castVote(c.id, a);
    return { ok: true, counts: withVote(counts ?? undefined, a) };
  } catch (e) {
    const err = e instanceof VoteError ? e : new VoteError("Something went wrong saving the vote. Try again.");
    return { ok: false, message: err.message, already: err.kind === "already" };
  }
}

/** Is the page element on screen (its page not hidden)? */
export const visible = (el: HTMLElement): boolean => el.isConnected && el.getClientRects().length > 0;
