// Optional: the check assistant (src/assist, another team) on the Candidate page. Loaded only if
// src/assist/index.ts exists: import.meta.glob finds nothing when it doesn't, so this page builds
// and works without it, and a failing assistant never breaks the page.
import type { Candidate } from "../api/types";
import { consensus, total, type Answer, type Counts } from "../vote/model";

/** What we hand mountWhatNext: the counts themselves, plus totals, consensus and this browser's vote. */
export interface VotesSummary extends Counts {
  counts: Counts;
  total: number;
  /** src/vote's consensus: { answer, share, votes } at ≥ 5 votes and ≥ 70 %, else null. */
  consensus: ReturnType<typeof consensus>;
  mine: Answer | null;
}

export function votesSummary(counts: Counts, mine: Answer | null): VotesSummary {
  return { ...counts, counts, total: total(counts), consensus: consensus(counts), mine };
}

type Handle = { update?(...args: unknown[]): void; destroy?(): void } | void | null | undefined;

export interface AssistModule {
  mountChecklist?(el: HTMLElement, candidate: Candidate, opts: { details: boolean }): Handle;
  mountWhatNext?(el: HTMLElement, candidate: Candidate, votes: VotesSummary): Handle;
}

const modules = import.meta.glob("../assist/index.ts");

let loading: Promise<AssistModule | null> | null = null;

export function loadAssist(): Promise<AssistModule | null> {
  loading ??= (async () => {
    const load = modules["../assist/index.ts"];
    if (!load) return null;
    try {
      return (await load()) as AssistModule;
    } catch {
      return null;
    }
  })();
  return loading;
}

/** The professional "Details" switch (DESIGN.md "Two audiences"), however the header keeps it. */
export function detailsOn(): boolean {
  const yes = (v: string | null | undefined) => v === "1" || v === "on" || v === "true";
  try {
    if (yes(document.documentElement.dataset.details)) return true;
    return yes(localStorage.getItem("skyshift.details"));
  } catch {
    return false;
  }
}

/** One assistant slot: mounts, updates and tears down safely. */
export class AssistSlot {
  private handle: Handle = null;

  constructor(private el: HTMLElement) {}

  mount(fn: ((el: HTMLElement) => Handle) | undefined): boolean {
    this.clear();
    if (!fn) return false;
    try {
      this.handle = fn(this.el);
      this.el.hidden = false;
      return true;
    } catch {
      this.clear();
      return false;
    }
  }

  /** Update in place when the assistant supports it; otherwise false (the caller re-mounts). */
  update(...args: unknown[]): boolean {
    const h = this.handle;
    if (!h || typeof h.update !== "function") return false;
    try {
      h.update(...args);
      return true;
    } catch {
      return false;
    }
  }

  clear(): void {
    try {
      if (this.handle && typeof this.handle.destroy === "function") this.handle.destroy();
    } catch {
      /* its problem, not the page's */
    }
    this.handle = null;
    this.el.innerHTML = "";
    this.el.hidden = true;
  }
}
