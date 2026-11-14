// The plate on the review pages: one blink player (src/blink, public mountBlink only) showing a
// candidate at its position, clock and detector, or a featured target for practice. The blink
// marks the frame centre with the red target reticle, which is the candidate's position.
import { ApiFailure, loadFrames } from "../api/client";
import type { Candidate, LoadedFrames } from "../api/types";
import type { BlinkHandle, View } from "../app/modules";
import { targetBySlug, viewForTarget } from "../app/state";
import { mountBlink } from "../blink";
import { candidateView, focusFrames, framesFor, hasFrames, isLive } from "./data";

const html = String.raw;

export function loadError(e: unknown): string {
  if (e instanceof ApiFailure) {
    if (e.code === "not_found") return "SPHEREx has no frames for this spot on this clock yet.";
    if (e.code === "upstream") return "NASA's archive didn't answer. Try again in a minute, or skip this one.";
    if (e.status === 404) return "These frames aren't published yet. Skip this one for now.";
    return `The live service failed (${e.status}). Skip this one, or try again in a minute.`;
  }
  return "The live service can't be reached. Check your connection, or skip this one.";
}

export interface PlateEvents {
  /** Frames are on screen (true) or not (false: loading or failed). */
  onReady?(ready: boolean): void;
}

export class Plate {
  private blink: BlinkHandle | null = null;
  private gen = 0;
  private current: { cand: Candidate | null; slug: string | null; view: View; all: boolean } | null = null;
  private timer = 0;
  readonly el: HTMLElement;
  private blinkEl: HTMLElement;
  private noteEl: HTMLElement;
  private waitEl: HTMLElement;

  constructor(host: HTMLElement, private events: PlateEvents = {}) {
    host.innerHTML = html`
      <div class="rv-plate">
        <div class="rv-blink"></div>
        <p class="rv-wait" role="status" aria-live="polite" hidden></p>
        <p class="rv-plate-note" hidden></p>
      </div>
    `;
    this.el = host.querySelector<HTMLElement>(".rv-plate")!;
    this.blinkEl = host.querySelector<HTMLElement>(".rv-blink")!;
    this.noteEl = host.querySelector<HTMLElement>(".rv-plate-note")!;
    this.waitEl = host.querySelector<HTMLElement>(".rv-wait")!;
    this.noteEl.addEventListener("click", (ev) => {
      const b = (ev.target as Element).closest<HTMLButtonElement>("button[data-all]");
      if (!b || !this.current) return;
      this.current.all = b.dataset.all === "1";
      this.reload();
    });
  }

  /** Mount the player (page shown). Re-shows what was on it. */
  mount(): void {
    if (this.blink) return;
    this.blink = mountBlink(this.blinkEl, {
      onViewChange: (change) => {
        if (!this.current) return;
        const v = { ...this.current.view, ...change };
        if (change.clock && change.clock !== this.current.view.clock && !change.fov) v.fov = change.clock === "hours" ? 20 : 5;
        this.current.view = v;
        this.reload();
      },
      onPick: () => {
        /* flagging dots lives on Explore */
      },
    });
    if (this.current) this.reload();
  }

  /** Remove the player (page hidden): no timers, no key handlers. */
  unmount(): void {
    this.gen++;
    clearInterval(this.timer);
    this.blink?.destroy();
    this.blink = null;
  }

  showCandidate(c: Candidate): Promise<boolean> {
    this.current = { cand: c, slug: null, view: candidateView(c), all: false };
    return this.reload();
  }

  showTarget(slug: string): Promise<boolean> {
    const t = targetBySlug(slug);
    if (!t) return Promise.resolve(false);
    this.current = { cand: null, slug, view: viewForTarget(t), all: true };
    return this.reload();
  }

  /** Clear the plate (end state). */
  clear(): void {
    this.gen++;
    this.current = null;
    clearInterval(this.timer);
    this.waitEl.hidden = true;
    this.noteEl.hidden = true;
  }

  private async reload(): Promise<boolean> {
    const cur = this.current;
    const blink = this.blink;
    if (!cur || !blink) return false;
    const gen = ++this.gen;
    clearInterval(this.timer);
    this.events.onReady?.(false);
    this.noteEl.hidden = true;
    const c = cur.cand;
    const live = c ? isLive(c, cur.view) && !hasFrames(c, cur.view) : false;
    blink.status("loading", live ? "Building frames from SPHEREx…" : "Loading frames…");
    this.waitEl.hidden = true;
    if (live) {
      const t0 = Date.now();
      const tick = () => {
        if (gen !== this.gen) return;
        const s = Math.round((Date.now() - t0) / 1000);
        this.waitEl.hidden = s < 3;
        this.waitEl.textContent =
          s < 50
            ? `Building this plate from NASA's archive: ${s} s so far. It usually takes 15 to 50 s.`
            : `Still building: ${s} s. NASA's archive is slow right now.`;
      };
      this.timer = window.setInterval(tick, 1000);
    }
    let loaded: LoadedFrames;
    try {
      loaded = c ? await framesFor(c, cur.view) : await loadFrames({ ...cur.view, slug: cur.slug });
    } catch (e) {
      if (gen !== this.gen) return false;
      clearInterval(this.timer);
      this.waitEl.hidden = true;
      blink.status("error", loadError(e));
      return false;
    }
    if (gen !== this.gen) return false;
    clearInterval(this.timer);
    this.waitEl.hidden = true;
    let frames = loaded.frames;
    if (c && !cur.all) {
      const f = focusFrames(loaded.frames, c, cur.view.det);
      frames = f.frames;
      if (f.focused) {
        this.noteEl.hidden = false;
        this.noteEl.innerHTML = html`Showing the ${f.shown} exposures within a day of the find.
          <button class="rv-link" type="button" data-all="1">Show all ${f.all}</button>`;
      }
    } else if (c && cur.all && cur.view.clock === "hours") {
      if (focusFrames(loaded.frames, c, cur.view.det).focused) {
        this.noteEl.hidden = false;
        this.noteEl.innerHTML = html`Showing every exposure.
          <button class="rv-link" type="button" data-all="0">Only those near the find</button>`;
      }
    }
    blink.status("idle");
    await blink.show(cur.view, frames, loaded.jsonUrl);
    if (gen !== this.gen) return false;
    this.events.onReady?.(true);
    return true;
  }
}
