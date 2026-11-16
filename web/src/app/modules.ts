// The seams between areas. src/app (P3) owns the page and calls these; each area implements
// its own module behind one of these interfaces. Keep them small: change one only with both owners.
import type { Clock, Det, Epoch, FramesResponse, ObjectResponse, SkyPos } from "../api/types";

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
/** The image actually displayed, including only source frames that loaded successfully. */
export interface BlinkPresentation {
  mode: 'single-detector' | 'all-wavelengths';
  detectors: Det[];
  units: 'MJy/sr' | 'sky-noise';
  members: { id: string; det: Det; mjd_mid: number; data_url: string }[];
}
/** Where a candidate should be in one frame (degrees, ICRS). Built by src/review/track.ts. */
export interface TrackSpot extends SkyPos {
  /** The frame: `epochs[].id` of the response handed to show(). */
  epoch_id: string;
  /** The frame's time (its `mjd_mid`). */
  mjd: number;
  /** true: SkyShift measured it in this frame (solid circle). false: predicted only (dashed). */
  measured: boolean;
}
/** The object the red circles follow: one spot per frame it should be in. */
export interface TrackMarks {
  /** What it is, for the caption: "the fast mover", "the moving star", … */
  label: string;
  spots: TrackSpot[];
}
export interface BlinkOptions {
  /** The blink calls this when the person picks the clock, field or detector inside the player. */
  onViewChange(change: Partial<Pick<View, "clock" | "fov" | "det">>): void;
  /** A click on the image: hand the position to votes. */
  onPick(pick: DotPick): void;
  /** Current epoch changed (for the sky view and the card). */
  onEpoch?(epoch: { id: string; mjd_mid: number; det: Det; presentation?: BlinkPresentation }): void;
  /** Hours (show() with `pages`): more exposures of the visit were loaded; the response handed to
   *  show() with all of them (contracts/api.md v3.23 `page`), before show()'s filter. */
  onFrames?(frames: FramesResponse): void;
  /**
   * The Viewer tab's plate (Alexandra's layout): SkyShift's measured circles start off and their
   * list is folded to one line that turns them on; Close-up/Wide field buttons; double-click zooms.
   * Other plates (To check, Learn) leave it out and keep the usual behaviour.
   */
  viewer?: boolean;
  /**
   * Leave SkyShift's artefacts (camera glitches) out of the plate, the legend, the summary and the
   * list. The To check plates (src/review/plate.ts) set it; the Viewer always does.
   */
  hideArtefacts?: boolean;
}

export interface BlinkHandle {
  /** Show frames for this view. jsonUrl resolves epoch data_url. Preloads every epoch before playing.
   *  pages (hours): the frames show at once, then the rest of their visit loads from the live API, a
   *  few pages at a time (contracts/api.md v3.23 `page`), and joins the blink without stopping it.
   *  visitMjd: a time inside the visit to load (an object's find); else one of the frames' own.
   *  filter: what of the loaded visit the plate shows (an object to check: the day of its find);
   *  onFrames gets the whole visit. */
  show(
    view: View,
    frames: FramesResponse,
    jsonUrl: string,
    options?: {
      singleDetector?: boolean;
      pages?: boolean;
      visitMjd?: number | null;
      filter?: ((frames: FramesResponse) => FramesResponse) | null;
    },
  ): Promise<void>;
  /** Hours clock: has the shown photo a no-data part (past the detector's edge) worth filling? */
  needsFill?(): boolean;
  /** Exposures used only to fill the plate's no-data edge on the hours clock, never blinked
   *  (data_url resolved against url). Replaces earlier ones; show() clears them. Resolves once they
   *  are painted (or could not be loaded). */
  fill?(sources: { epoch: Epoch; url: string }[]): Promise<void>;
  /** Loading or error state while app fetches frames. message null clears it. */
  status(kind: "loading" | "error" | "idle", message?: string): void;
  highlight?(pos:SkyPos|null):void;
  /** Red circles where a candidate should be, in every frame: solid where measured, dashed where
   *  predicted. Matched to frames by epoch id, so call it before or after show(); show() drops marks
   *  that match none of its epochs. null clears. */
  track?(marks: TrackMarks | null): void;
  gaia?(stars: FramesResponse['gaia']): void;
  active?(visible: boolean): void;
  /** Guided observation controls use the same player and calibrated image data. */
  display?(mode: 'blink' | 'difference'): void;
  step?(): void;
  canCompare?(): boolean;
  /** Keep practice and derived images within one detector; preserve the normal viewing preference. */
  singleDetector?(locked: boolean): void;
  /** The person's All-wavelengths preference, as if they pressed All (true) or a detector (false);
   *  applied at the next show() or singleDetector(). Returns the preference before. An object to
   *  check opens on its own detector this way and can still go to All. */
  preferAllWave?(on: boolean): boolean;
  destroy(): void;
}
export type MountBlink = (el: HTMLElement, opts: BlinkOptions) => BlinkHandle;

// ---- P3: src/card/index.ts exports mountCard --------------------------------------------
export interface CardHandle {
  section?(value: 'all' | 'summary' | 'motion' | 'spectrum' | 'lightcurve' | ('motion' | 'spectrum')[]): void;
  reference?(points: {um:number; flux_ujy:number}[]): void;
  show(view: View, object: ObjectResponse | null, frames: FramesResponse | null): void;
  /** An element shown in place of the name and the measurement (in the Motion dashboard when the
   *  Advanced dashboards are on): a candidate's own panel, its facts and its vote. null: none. */
  lead?(el: HTMLElement | null): void;
  status(kind: "loading" | "error" | "idle", message?: string): void;
}
export interface CardOptions {
  plots?: boolean;
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
  /** focus false: pick without moving keyboard focus to Flag it (a click on the plate). */
  pick(p: DotPick, focus?: boolean): void;
  /** The view changed: show candidates near it. */
  view(v: View): void;
}
export type MountVote = (el: HTMLElement) => VoteHandle;

// ---- P3: src/score/index.ts exports mountScore (the scoreboard page) -----------------------
export type MountScore = (el: HTMLElement) => { destroy(): void };

// ---- Pages (DESIGN.md "Pages"). The shell routes by hash and mounts each page on first visit.
export interface PageContext {
  currentView?(): View | null;
  /** Selected sky point, or map centre when nothing is selected and the map is zoomed in; null on
   *  the whole-sky view, where the centre is not a choice. Map FOV is never a blink FOV. */
  skySelection?(at: (SkyPos & Partial<Pick<View,'clock'|'det'|'slug'>>) | null, returnHash:string): void;
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
