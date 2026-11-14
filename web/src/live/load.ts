// Where the Live search page gets the run's progress: one public JSON file in Cloud Storage, rewritten
// every 2 min by the run (CORS * for GET, no-cache). VITE_SKYRUN_URL overrides it. Cross-origin, so a
// plain GET without credentials. The source is pluggable: a Firestore document or a test can stand in.
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

/**
 * One zone's suspects (suspects_url; the shape of SuspectsResponse in src/api/types.ts), brightest S/N first.
 * Rows without a finite position are dropped. Throws FeedError when the file is missing or failing.
 */
export async function loadZoneSuspects(url: string, signal?: AbortSignal, fetchFn?: typeof fetch): Promise<Suspect[]> {
  const raw = (await httpSource(url, fetchFn)(signal)) as Partial<SuspectsResponse> | null;
  const rows = Array.isArray(raw?.suspects) ? raw!.suspects : [];
  return rows
    .filter((s): s is Suspect => !!s && Number.isFinite(s.ra) && Number.isFinite(s.dec))
    .sort((a, b) => (Number(b.snr) || 0) - (Number(a.snr) || 0));
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
