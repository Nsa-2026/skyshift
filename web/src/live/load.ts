// Where the Live search page gets the run's progress: one public JSON file in Cloud Storage, rewritten
// every 2 min by the run (CORS * for GET, no-cache). VITE_SKYRUN_URL overrides it. Cross-origin, so a
// plain GET without credentials. The source is pluggable: a Firestore document or a test can stand in.
import { countGrades, gateTotals, gradeCounts, rankByGrade, type GradeCounts } from "../api/grades";
import type { Suspect, SuspectsResponse } from "../api/types";
import { parseSkyrun, type SkyRun } from "./model";

/** Fetch the raw progress JSON. */
export type SkyrunSource = (signal?: AbortSignal) => Promise<unknown>;

export const DEFAULT_SKYRUN_URL = "https://storage.googleapis.com/nasa-glazing-live/skyrun/progress.json";
export const SKYRUN_URL: string = (import.meta.env?.VITE_SKYRUN_URL as string | undefined) ?? DEFAULT_SKYRUN_URL;

/**
 * Why the feed failed. "not_connected": the URL isn't there yet (404, an HTML page instead of JSON, a proxy
 * with nothing behind it); "unreachable": no answer (offline, CORS, DNS); "server": any other error status.
 */
export class FeedError extends Error {
  constructor(
    message: string,
    readonly kind: "not_connected" | "unreachable" | "server",
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "FeedError";
  }
}

export function httpSource(url: string = SKYRUN_URL, fetchFn: typeof fetch = (...a) => fetch(...a)): SkyrunSource {
  return async (signal) => {
    let res: Response;
    try {
      // no-store: never a browser-cached copy; the file changes every 2 min.
      res = await fetchFn(url, { signal, cache: "no-store", credentials: "omit" });
    } catch (e) {
      if ((e as Error)?.name === "AbortError") throw e;
      throw new FeedError(`No answer from ${url}`, "unreachable");
    }
    if (!res.ok) {
      const kind = res.status === 404 || res.status === 502 || res.status === 503 || res.status === 504 ? "not_connected" : "server";
      throw new FeedError(`${res.status} from ${url}`, kind, res.status);
    }
    const type = res.headers.get("content-type") ?? "";
    if (/text\/html/i.test(type)) throw new FeedError(`${url} answered with a web page, not the progress file`, "not_connected", res.status);
    try {
      return await res.json();
    } catch (e) {
      if ((e as Error)?.name === "AbortError") throw e;
      throw new FeedError(`${url} didn't answer with JSON`, "not_connected", res.status);
    }
  };
}

/** A zone's suspects file, read: its rows ranked, and the vetting's zone-level fields when it has them. */
export interface ZoneSuspects {
  /** Grade A, B, C, then ungraded; then S/N, highest first. An ungraded file: S/N only, as before. */
  rows: Suspect[];
  /** Grades counted by the file (`grades`), else from its rows; null for a file without grades. */
  grades: GradeCounts | null;
  /** The zone-level checks every listed suspect passed (`vetoes`), or null. */
  vetoes: string[] | null;
  /** true: Gaia or every WISE catalogue couldn't be checked (`provisional`). */
  provisional: boolean;
  /** Gates that failed suspects, with counts, most first. */
  gates: [string, number][];
}

/** Read a parsed suspects file (contracts/api.md suspects.json). Pure. */
export function toZoneSuspects(raw: unknown): ZoneSuspects {
  const o = (raw && typeof raw === "object" ? raw : {}) as Partial<SuspectsResponse>;
  const rows = rankByGrade((Array.isArray(o.suspects) ? o.suspects : []).filter((s): s is Suspect => !!s && Number.isFinite(s.ra) && Number.isFinite(s.dec)));
  const vetoes = Array.isArray(o.vetoes) ? o.vetoes.filter((v): v is string => typeof v === "string") : null;
  return { rows, grades: gradeCounts(o.grades) ?? countGrades(rows), vetoes, provisional: o.provisional === true, gates: gateTotals(o.gates) };
}

/**
 * One zone's suspects file (suspects_url; the shape of SuspectsResponse in src/api/types.ts). Rows
 * without a finite position are dropped. Throws FeedError when the file is missing or failing.
 */
export async function loadZoneSuspectsFile(url: string, signal?: AbortSignal, fetchFn?: typeof fetch): Promise<ZoneSuspects> {
  return toZoneSuspects(await httpSource(url, fetchFn)(signal));
}

/** One zone's suspects, ranked: grade (A, B, C, ungraded), then S/N. An ungraded file is S/N order. */
export async function loadZoneSuspects(url: string, signal?: AbortSignal, fetchFn?: typeof fetch): Promise<Suspect[]> {
  return (await loadZoneSuspectsFile(url, signal, fetchFn)).rows;
}

let defaultSource: SkyrunSource | null = null;

/**
 * The run's progress, parsed (schema 1). Throws FeedError when the feed is missing or failing and
 * SkyrunInvalid when the file can't be read. Used by the Live search page and the Sky page's layer.
 */
export async function loadSkyrun(signal?: AbortSignal, source?: SkyrunSource): Promise<SkyRun> {
  const raw = await (source ?? (defaultSource ??= httpSource()))(signal);
  const run = parseSkyrun(raw);
  if (run.warnings.length && typeof console !== "undefined") console.warn("skyrun:", run.warnings.slice(0, 10).join("; "));
  return run;
}
