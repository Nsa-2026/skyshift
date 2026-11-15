// What the sky page asks of a map, whichever draws it: Aladin Lite (the globe) or the static
// Aitoff SVG (no WebGL2, or Aladin failed). index.ts talks only to this interface.
import type { Candidate, SkyPos } from "../api/types";
import type { Target } from "../app/state";
import type { Cell } from "./heat";
import type { Box } from "./labels";
import type { ProvisionalSet, SearchLayer } from "./search";
import type { ZoneArea } from "./zones";

export interface MapView extends SkyPos {
  /** Field of view across the map's long side, degrees. */
  fov: number;
}

export type BackgroundId = "spherex" | "2mass" | "allwise";

export interface Layers {
  heat: boolean;
  zones: boolean;
  signals: boolean;
  background: BackgroundId;
  /** True draws a plate negative; false shows the survey's own colours. */
  negative: boolean;
  /** Tonight's search: the run's zones by state, far out. */
  search: boolean;
  /** A combined zone's provisional suspects, close in. */
  provisional: boolean;
}

export interface MapModel {
  /** Candidates of the shown kinds. */
  candidates: readonly Candidate[];
  /** Heat cells of the shown kinds at a HEALPix order (memoised by the caller). */
  cellsAt(order: number): readonly Cell[];
  /** The searched zones, overlapping ones joined: one outline, one fill and one badge each. */
  areas: readonly ZoneArea[];
  targets: readonly Target[];
  selectedId: string | null;
  layers: Layers;
  /** Tonight's search (null until the progress file has loaded, or when it can't be read). */
  search: SearchLayer | null;
  /** Provisional suspects of the combined zone in view, or null. */
  provisional: ProvisionalSet | null;
}

export interface MapCallbacks {
  /** The view moved or zoomed (called at most once per animation frame). */
  onView(v: MapView): void;
  onSignal(c: Candidate): void;
  onTarget(t: Target): void;
  /** A heat cell was clicked: open it. */
  onCell(cell: Cell): void;
  /** A zone (on the whole sky) or its badge was clicked: fly in to it. */
  onZone(area: ZoneArea): void;
  /** Empty sky was clicked, at screen point (x, y) inside the map. */
  onEmpty(pos: SkyPos, x: number, y: number): void;
  /** Hovering a signal (null when leaving it), at screen point (x, y) inside the map. */
  onHover(c: Candidate | null, x: number, y: number): void;
  /** Boxes on the map covered by the page's own controls: labels keep off them. */
  reserved?(): Box[];
}

export interface MapHandle {
  readonly kind: "aladin" | "svg";
  update(model: MapModel): void;
  view(): MapView;
  flyTo(to: SkyPos & { fov?: number }, animate: boolean): void;
  setProjection(mol: boolean): void;
  setFrame(gal: boolean): void;
  zoomBy(factor: number): void;
  /** Pan by a fraction of the field (dx east-west on screen, dy north-south on screen). */
  panBy(dx: number, dy: number): void;
  /** Screen point of a sky position inside the map, or null when it isn't visible. */
  screenOf(pos: SkyPos): { x: number; y: number } | null;
  destroy(): void;
}

/** Candidates whose position falls inside the map, nearest the centre first. */
export function inView(cands: readonly Candidate[], map: Pick<MapHandle, "screenOf">, size: { w: number; h: number }): Candidate[] {
  const out: { c: Candidate; d: number }[] = [];
  for (const c of cands) {
    const p = map.screenOf(c);
    if (!p || p.x < 0 || p.y < 0 || p.x > size.w || p.y > size.h) continue;
    out.push({ c, d: (p.x - size.w / 2) ** 2 + (p.y - size.h / 2) ** 2 });
  }
  return out.sort((a, b) => a.d - b.d).map((o) => o.c);
}
