// The time strip under the plate: epochs as ticks on real time. Months: each visit is a short bar
// spanning its weeks on a linear axis. Hours: each exposure a tick, long gaps compressed, broken
// and labelled. The current epoch is a red pencil mark with its date in the label row.
import type { Clock, Epoch } from "../api/types";
import { mjdToDate } from "../app/astro";
import { MONTHS, buildAxis, type Axis } from "./axis";

export interface StripParts {
  track: HTMLElement;
  labels: HTMLElement;
}

/** "26 Aug 2025", or "26 Aug 2025, 04:27 UTC" with the time. Fixed month names, so the plate label
 * and the strip's axis agree whatever the browser's locale data ("Sep", never "Sept"). */
export function plateDate(mjd: number, withTime = false): string {
  const d = mjdToDate(mjd);
  const day = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  if (!withTime) return day;
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${day}, ${hh}:${mm} UTC`;
}

/**
 * A visit's weeks: "12–28 Dec 2025", "28 Nov – 14 Dec 2025", "20 Dec 2025 – 6 Jan 2026", or one day.
 * The months clock shows a visit as one stack of photos taken over these weeks, so its date is a
 * span, never one day (an hours photo of the same visit has its own date and time).
 */
export function visitSpan(start: number, end: number): string {
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return plateDate(Number.isFinite(start) ? start : end);
  const a = mjdToDate(start);
  const b = mjdToDate(end);
  const day = (d: Date) => d.getUTCDate();
  const mon = (d: Date) => MONTHS[d.getUTCMonth()];
  const yr = (d: Date) => d.getUTCFullYear();
  if (yr(a) !== yr(b)) return `${day(a)} ${mon(a)} ${yr(a)} – ${day(b)} ${mon(b)} ${yr(b)}`;
  if (mon(a) !== mon(b)) return `${day(a)} ${mon(a)} – ${day(b)} ${mon(b)} ${yr(b)}`;
  if (day(a) !== day(b)) return `${day(a)}–${day(b)} ${mon(b)} ${yr(b)}`;
  return plateDate(start);
}

/** Date for the strip's red label: "26 Aug 2025" on months, "12 Dec, 04:27" on hours. */
export function stripDate(mjd: number, clock: Clock): string {
  if (clock === "months") return plateDate(mjd);
  return plateDate(mjd, true)
    .replace(/ \d{4},/, ",")
    .replace(" UTC", "");
}

/** Build the axis and draw the fixed parts of the strip. Returns the axis for later updates. */
export function drawStrip(parts: StripParts, epochs: Epoch[], clock: Clock, width: number): Axis {
  const axis = buildAxis(
    epochs.map((e) => e.mjd_mid),
    clock === "hours" ? "gaps" : "linear",
    clock === "months" ? epochs.map((e) => [e.mjd_start, e.mjd_end] as [number, number]) : [],
    Math.max(2, Math.min(7, Math.floor(width / 96))),
  );
  const pc = (x: number) => `${(x * 100).toFixed(3)}%`;
  const t: string[] = [`<span class="bk-base"></span>`];
  for (const k of axis.ticks) t.push(`<span class="bk-tick${k.major ? " is-major" : ""}" style="left:${pc(k.x)}"></span>`);
  epochs.forEach((e, i) => {
    if (clock === "months") {
      const a = axis.x(e.mjd_start);
      const b = axis.x(e.mjd_end);
      t.push(
        `<span class="bk-ep bk-span" data-i="${i}" style="left:${pc(a)};width:max(4px, ${((b - a) * 100).toFixed(3)}%)"></span>`,
      );
    } else t.push(`<span class="bk-ep bk-exp" data-i="${i}" style="left:${pc(axis.x(e.mjd_mid))}"></span>`);
  });
  for (const g of axis.gaps) if (g.days >= 0.5) t.push(`<span class="bk-break" style="left:${pc(g.x)}" aria-hidden="true"></span>`);
  t.push(
    `<span class="bk-ghost" hidden></span>`,
    `<span class="bk-all" hidden></span>`,
    `<span class="bk-refmark" hidden></span>`,
    `<span class="bk-now" hidden></span>`,
  );
  parts.track.innerHTML = t.join("");

  const l: string[] = [];
  for (const k of axis.ticks) if (k.label) l.push(`<span class="bk-tl" style="left:${pc(k.x)}">${k.label}</span>`);
  for (const g of axis.gaps) l.push(`<span class="bk-tl bk-gl" style="left:${pc(g.x)}">${g.label}</span>`);
  l.push(`<span class="bk-nowl" hidden></span>`);
  parts.labels.innerHTML = l.join("");
  return axis;
}

export interface StripState {
  axis: Axis;
  epochs: Epoch[];
  clock: Clock;
  /** Epoch shown (or null when every epoch is shown at once). */
  current: number | null;
  /** Reference epoch to mark in blue, or null. */
  ref: number | null;
  /** Text for the red label when every epoch is shown. */
  allLabel: string;
}

/** Move the red mark and its label; mark the reference; hide axis labels under the red one. */
export function updateStrip(parts: StripParts, s: StripState): void {
  const { axis, epochs } = s;
  if (!epochs.length) return;
  const pc = (x: number) => `${(x * 100).toFixed(3)}%`;
  const now = parts.track.querySelector<HTMLElement>(".bk-now");
  const all = parts.track.querySelector<HTMLElement>(".bk-all");
  const ref = parts.track.querySelector<HTMLElement>(".bk-refmark");
  const nowl = parts.labels.querySelector<HTMLElement>(".bk-nowl");
  parts.track.querySelectorAll<HTMLElement>(".bk-ep").forEach((m) => {
    const i = Number(m.dataset.i);
    m.classList.toggle("is-on", s.current === null || i === s.current);
    m.classList.toggle("is-ref", i === s.ref);
  });
  let x: number;
  if (s.current === null) {
    const a = axis.x(epochs[0].mjd_mid);
    const b = axis.x(epochs[epochs.length - 1].mjd_mid);
    x = (a + b) / 2;
    if (all) {
      all.hidden = false;
      all.style.left = pc(a);
      all.style.width = pc(b - a);
    }
    if (now) now.hidden = true;
    if (nowl) nowl.textContent = s.allLabel;
  } else {
    x = axis.x(epochs[s.current].mjd_mid);
    if (all) all.hidden = true;
    if (now) {
      now.hidden = false;
      now.style.left = pc(x);
    }
    const e = epochs[s.current];
    // Months: the visit's weeks (its bar), the same words as the plate's label.
    if (nowl) nowl.textContent = s.clock === "months" ? visitSpan(e.mjd_start, e.mjd_end) : stripDate(e.mjd_mid, s.clock);
  }
  if (ref) {
    ref.hidden = s.ref === null;
    if (s.ref !== null) ref.style.left = pc(axis.x(epochs[s.ref].mjd_mid));
  }
  if (nowl) {
    nowl.hidden = false;
    nowl.style.left = pc(x);
  }
  thin(parts.labels);
}

/** Keep the red label inside the strip, then hide axis labels that collide with it or each other. */
function thin(box: HTMLElement): void {
  const nowl = box.querySelector<HTMLElement>(".bk-nowl");
  if (!box.clientWidth) return;
  const place = (el: HTMLElement) => {
    el.style.transform = "translateX(-50%)";
    const r = el.getBoundingClientRect();
    const b = box.getBoundingClientRect();
    const over = r.right - b.right;
    const under = b.left - r.left;
    if (over > 0) el.style.transform = `translateX(calc(-50% - ${over}px))`;
    else if (under > 0) el.style.transform = `translateX(calc(-50% + ${under}px))`;
    return el.getBoundingClientRect();
  };
  // Priority: the red date, then gap lengths, then month or day names.
  const placed: DOMRect[] = [];
  if (nowl && !nowl.hidden) placed.push(place(nowl));
  for (const sel of [".bk-gl", ".bk-tl:not(.bk-gl)"]) {
    for (const it of box.querySelectorAll<HTMLElement>(sel)) {
      it.hidden = false;
      const r = place(it);
      if (placed.some((p) => r.right > p.left - 10 && r.left < p.right + 10)) it.hidden = true;
      else placed.push(r);
    }
  }
}
