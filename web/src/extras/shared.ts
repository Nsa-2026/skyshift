// Shared bits of the card's extra sections (web/src/extras): what one target is, how a failed
// service is said, and small text helpers. Units as contracts/api.md: degrees, arcsec, µm, MJD, µJy.
import { ApiFailure } from "../api/client";
import type { Det, FramesResponse, ObjectResponse } from "../api/types";
import { DETECTORS } from "../app/astro";
import type { View } from "../app/modules";

/** What the extras look at: the page's view and whatever of the object and frames has arrived. */
export interface Ctx {
  view: View;
  object: ObjectResponse | null;
  frames: FramesResponse | null;
  /** targetKey(view): a new key means a new target, so nothing from the old one may stay. */
  key: string;
}

/** One target's identity (slug and position). Changing clock or detector keeps it. */
export const targetKey = (v: Pick<View, "slug" | "ra" | "dec">): string => `${v.slug ?? ""}|${v.ra.toFixed(5)},${v.dec.toFixed(5)}`;

export const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export const isAbort = (e: unknown): boolean => (e as { name?: string } | null)?.name === "AbortError";

/**
 * A failed request as one plain sentence that names the service and says what to do next. It must
 * never read like an empty answer: a service that didn't answer is not a sky with nothing in it.
 */
export function failText(e: unknown, service: string): string {
  const notEmpty = "Nothing is shown, which is not the same as nothing there.";
  if (e instanceof ApiFailure) {
    if (e.code === "upstream" || e.status === 502 || e.status === 503 || e.status === 504)
      return `${service} didn't answer. ${notEmpty} Try again in a few minutes.`;
    if (e.code === "not_found" || e.status === 404) return `${service} has nothing prepared for this spot.`;
    if (e.code === "bad_request" || e.status === 400) return `SkyShift couldn't ask ${service} about this spot: ${e.message}`;
    return `${service} failed (${e.status}). ${notEmpty} Try again in a few minutes.`;
  }
  return `SkyShift's service can't be reached, so ${service} wasn't asked. ${notEmpty} Check your connection, then try again.`;
}

/** A detector's wavelengths as written in CLAUDE.md: "0.75–1.1", "4.4–5.0". */
export function umRange(det: Det): string {
  const f = (x: number) => (Number.isInteger(x) ? x.toFixed(1) : String(x));
  return `${f(DETECTORS[det].min)}–${f(DETECTORS[det].max)}`;
}

/** "1,234" */
export const grouped = (n: number): string => Math.round(n).toLocaleString("en-US");

/** "a, b and c" */
export function andList(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

export const plural = (n: number, one: string, many = `${one}s`): string => `${grouped(n)} ${n === 1 ? one : many}`;

/** Median MJD of the frames' epochs, or null without frames. */
export function framesMjd(frames: FramesResponse | null): number | null {
  const t = (frames?.epochs ?? []).map((e) => e.mjd_mid).filter(Number.isFinite).sort((a, b) => a - b);
  return t.length ? t[t.length >> 1] : null;
}

/** A collapsible section: <details> with a title, a one-line hint and a body filled on first open. */
export function sectionShell(id: string, title: string, hint: string): string {
  return `<details class="ex-sec" data-sec="${id}">
    <summary class="ex-sum"><span class="ex-title">${title}</span><span class="ex-hint">${hint}</span></summary>
    <div class="ex-body" data-body></div>
  </details>`;
}

/** Save text as a file now (a click, not a link: nothing to revoke later, no stale blob on screen). */
export function saveText(text: string, name: string, type: string): void {
  const blob = new Blob([text], { type });
  const url = typeof URL.createObjectURL === "function" ? URL.createObjectURL(blob) : `data:${type},${encodeURIComponent(text)}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.append(a);
  a.click();
  a.remove();
  if (url.startsWith("blob:")) setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
