// The seams between areas. src/app (P3) owns the page and calls these; each area implements
// its own module behind one of these interfaces. Keep them small: change one only with both owners.
import type { Clock, Det, FramesResponse, ObjectResponse, SkyPos } from "../api/types";

/** A dot someone clicked in the blink: the frame's sky position and the clicked epoch's time. */
export interface DotPick extends SkyPos {
  mjd: number;
  clock: Clock;
  det: Det;
  epoch_id: string;
}

/** What the page is looking at. Lives in the URL (src/app/state.ts). */
export interface View extends SkyPos {
  clock: Clock;
  fov: number;
  det: Det;
  /** Featured target, or null for any coordinates. */
  slug: string | null;
  name: string | null;
}

// ---- P4: src/blink/index.ts exports mountBlink -------------------------------------------
export interface BlinkOptions {
  /** The blink calls this when the person picks the clock, field or detector inside the player. */
  onViewChange(change: Partial<Pick<View, "clock" | "fov" | "det">>): void;
  /** A click on the image: hand the position to votes. */
  onPick(pick: DotPick): void;
  /** Current epoch changed (for the sky view and the card). */
  onEpoch?(epoch: { id: string; mjd_mid: number; det: Det }): void;
}

export interface BlinkHandle {
  /** Show frames for this view. jsonUrl resolves epoch data_url. Preloads every epoch before playing. */
  show(view: View, frames: FramesResponse, jsonUrl: string): Promise<void>;
  /** Loading or error state while app fetches frames. message null clears it. */
  status(kind: "loading" | "error" | "idle", message?: string): void;
  destroy(): void;
}
export type MountBlink = (el: HTMLElement, opts: BlinkOptions) => BlinkHandle;

// ---- P3: src/card/index.ts exports mountCard --------------------------------------------
export interface CardHandle {
  show(view: View, object: ObjectResponse | null, frames: FramesResponse | null): void;
  status(kind: "loading" | "error" | "idle", message?: string): void;
}
export interface CardOptions {
  /** Open the blink at another position (the card's "open it where it is now" for a star that moved away). */
  onExplore?(at: SkyPos): void;
}
export type MountCard = (el: HTMLElement, opts?: CardOptions) => CardHandle;

// ---- P3: src/sky/index.ts exports mountSky ----------------------------------------------
export interface SkyHandle {
  goto(view: View, frames: FramesResponse | null): void;
  /** Highlight the current epoch (moves Gaia dots to its time). */
  epoch(mjd: number): void;
  destroy(): void;
}
export type MountSky = (el: HTMLElement, opts: { onClick?(pos: SkyPos): void }) => SkyHandle;

// ---- P4: src/vote/index.ts exports mountVote --------------------------------------------
export interface VoteHandle {
  /** A dot was flagged in the blink. */
  pick(p: DotPick): void;
  /** The view changed: show candidates near it. */
  view(v: View): void;
}
export type MountVote = (el: HTMLElement) => VoteHandle;

// ---- P3: src/score/index.ts exports mountScore (the scoreboard page) -----------------------
export type MountScore = (el: HTMLElement) => { destroy(): void };

// ---- Pages (DESIGN.md "Pages"). The shell routes by hash and mounts each page on first visit.
export interface PageContext {
  /** Go to a route, e.g. "#/c/pilot-pm-0003" or "#/review". */
  navigate(hash: string): void;
  /** Open the Explore page (blink) at a position. clock/det/fov default from the current view. */
  explore(at: SkyPos & Partial<Pick<View, "clock" | "det" | "fov">>): void;
}
export interface PageHandle {
  /** The page became visible. param is the route's argument (the candidate id for #/c/<id>), or null. */
  show(param: string | null): void;
  /** The page was hidden (stop timers, pause blinks). */
  hide?(): void;
  destroy(): void;
}
export type MountPage = (el: HTMLElement, ctx: PageContext) => PageHandle;
