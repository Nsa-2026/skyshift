// "Tonight's search": the sky run's zones along the ecliptic, drawn faintly on the whole sky by
// state, and a combined zone's provisional suspects when zoomed in on it. Data from src/live (read
// only): loadSkyrun() for the progress file, zonesToPolygons() for the zone outlines. The file's
// `notes` and each zone's `deferred` flag aren't in src/live's model yet, so the raw JSON is kept
// beside the parsed run and read here.
import type { SkyPos, Suspect } from "../api/types";
import {
  httpSource,
  loadSkyrun,
  loadZoneSuspects,
  zoneCentre,
  zoneContains,
  zonesToPolygons,
  type Ring,
  type SkyRun,
  type ZoneState,
} from "../live";

/** How a zone is drawn: its run state, or "deferred" for a crowded bulge zone left for last. */
export type SearchState = ZoneState | "deferred";

export interface SearchZone {
  name: string;
  state: SearchState;
  /** Closed rings of [RA, Dec], degrees. */
  rings: Ring[];
  /** Box, as in the progress file: ra[0] > ra[1] wraps through RA 0. */
  ra: [number, number];
  dec: [number, number];
  center: SkyPos;
  suspects: number | null;
  suspectsUrl: string | null;
  /** Edges merged and the WISE check done: its suspects are final. */
  final: boolean;
}

export interface SearchLayer {
  zones: SearchZone[];
  /** Zones in the run (totals.zones). */
  total: number;
  /** Zones searched to the end (combined). */
  searched: number;
  /** Zones a machine is reading or combining right now. */
  atWork: number;
  failed: number;
  deferred: number;
  /** Most suspects in one combined zone, for the red wash. */
  maxSuspects: number;
  /** The file's own words on provisional results, or null. */
  provisionalNote: string | null;
  updated: number | null;
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

/** Build the layer from a parsed run and the raw file it came from. Pure. */
export function toSearchLayer(run: SkyRun, raw: unknown): SearchLayer {
  const rawZones = isObj(raw) && Array.isArray(raw.zones) ? (raw.zones as unknown[]) : [];
  const deferredNames = new Set(rawZones.filter((z): z is Obj => isObj(z) && z.deferred === true).map((z) => String(z.name)));
  const notes = isObj(raw) && isObj(raw.notes) ? raw.notes : {};
  const polys = zonesToPolygons(run.zones, { stepDeg: 2 });
  const zones: SearchZone[] = polys.map((p) => {
    const z = p.zone;
    const state: SearchState = z.state === "queued" && deferredNames.has(z.name) ? "deferred" : z.state;
    return {
      name: z.name,
      state,
      rings: p.rings,
      ra: z.ra,
      dec: z.dec,
      center: zoneCentre(z),
      suspects: z.suspects,
      suspectsUrl: z.suspects_url,
      final: z.final,
    };
  });
  const count = (s: SearchState) => zones.filter((z) => z.state === s).length;
  return {
    zones,
    total: Math.max(run.totals.zones ?? 0, zones.length),
    searched: count("combined"),
    atWork: count("extracting") + count("combining"),
    failed: count("failed"),
    deferred: count("deferred"),
    maxSuspects: zones.reduce((m, z) => (z.state === "combined" ? Math.max(m, z.suspects ?? 0) : m), 0),
    provisionalNote: typeof notes.provisional === "string" && notes.provisional.trim() ? notes.provisional.trim() : null,
    updated: run.updated,
  };
}

/** The intro band's one line: "Live: 12 of 333 zones searched tonight, 10 at work." */
export function liveLine(l: Pick<SearchLayer, "searched" | "total" | "atWork">): string {
  const nf = new Intl.NumberFormat("en-GB");
  const work = l.atWork > 0 ? `, ${nf.format(l.atWork)} at work` : "";
  return `Live: ${nf.format(l.searched)} of ${nf.format(l.total)} zones searched tonight${work}.`;
}

/** Opacity of a combined zone's red wash: more suspects, a little redder; always faint. */
export function suspectAlpha(suspects: number | null, max: number): number {
  if (!(max > 0) || suspects == null || !(suspects > 0)) return 0.06;
  return 0.06 + 0.22 * Math.min(1, Math.log1p(suspects) / Math.log1p(max));
}

/**
 * The combined zone whose provisional suspects to show for a view: the zone holding the view's
 * centre, combined, with a suspects file, and not one whose objects are already candidates (the
 * hunt zone's suspects are in candidates.json). Null otherwise.
 */
export function provisionalZone(l: SearchLayer | null, at: SkyPos, candidateZones: readonly string[]): SearchZone | null {
  if (!l) return null;
  const skip = new Set(candidateZones);
  return (
    l.zones.find((z) => z.state === "combined" && !!z.suspectsUrl && !skip.has(z.name) && zoneContains({ ra: z.ra, dec: z.dec }, at.ra, at.dec)) ??
    null
  );
}

/** What the map shows of one zone's provisional suspects. */
export interface ProvisionalSet {
  zone: string;
  suspects: readonly Suspect[];
  /** True when the run has merged the zone's edges and run the WISE check. */
  final: boolean;
}

/** The progress file, parsed, with its notes and deferred flags. */
export async function loadSearch(signal?: AbortSignal): Promise<SearchLayer> {
  let raw: unknown = null;
  const source = httpSource();
  const run = await loadSkyrun(signal, async (s) => (raw = await source(s)));
  return toSearchLayer(run, raw);
}

export { loadZoneSuspects };
