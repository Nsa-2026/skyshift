// Loads Aladin Lite 3.8.2 at runtime: jsDelivr first, unpkg second. Never from npm, never
// unpinned (latest is a beta), never from CDS (down since 9 Nov).
// dist/aladin.js is an ES module (it ends in `export { P as default }`), so it is loaded with a
// runtime import() of the CDN URL; a classic <script> tag can't run it.
import type { AladinStatic } from "./aladin";

export const ALADIN_URLS = [
  "https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js",
  "https://unpkg.com/aladin-lite@3.8.2/dist/aladin.js",
] as const;

/** Why the sky view can't show. `reason` finishes the sentence "Sky view unavailable: …". */
export class SkyUnavailable extends Error {
  constructor(readonly reason: string) {
    super(`Sky view unavailable: ${reason}`);
    this.name = "SkyUnavailable";
  }
}

export type Importer = (url: string) => Promise<unknown>;

export interface LoadOptions {
  urls?: readonly string[];
  importer?: Importer;
  hasWebGL2?: () => boolean;
  /** Per source, and again for Aladin's own start-up. */
  timeoutMs?: number;
}

const runtimeImport: Importer = (url) => import(/* @vite-ignore */ url);

export function browserHasWebGL2(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!c.getContext("webgl2");
  } catch {
    return false;
  }
}

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`${what} timed out after ${ms} ms`)), ms);
    p.then(
      (v) => {
        clearTimeout(t);
        resolve(v);
      },
      (e) => {
        clearTimeout(t);
        reject(e);
      },
    );
  });
}

function asAladin(mod: unknown): AladinStatic | null {
  const m = mod as { default?: unknown } | null;
  const a = (m && typeof m === "object" && "default" in m ? m.default : m) as Partial<AladinStatic> | null;
  return a && typeof a.aladin === "function" && typeof a.HiPS === "function" ? (a as AladinStatic) : null;
}

/**
 * Try each source in order; the first that yields Aladin wins. Throws SkyUnavailable with a
 * plain reason when WebGL2 is missing, every source fails, or Aladin can't start.
 */
export async function loadAladin(opts: LoadOptions = {}): Promise<AladinStatic> {
  const { urls = ALADIN_URLS, importer = runtimeImport, hasWebGL2 = browserHasWebGL2, timeoutMs = 20000 } = opts;
  // Check first: no point downloading 2.4 MB the browser can't draw.
  if (!hasWebGL2()) throw new SkyUnavailable("this browser has no WebGL2");

  let A: AladinStatic | null = null;
  for (const url of urls) {
    try {
      A = asAladin(await withTimeout(importer(url), timeoutMs, url));
      if (A) break;
    } catch {
      /* next source */
    }
  }
  if (!A) throw new SkyUnavailable("the Aladin sky viewer couldn't be downloaded");

  try {
    await withTimeout(Promise.resolve(A.init), timeoutMs, "Aladin start-up");
  } catch {
    throw new SkyUnavailable("WebGL2 couldn't start in this browser");
  }
  return A;
}

let shared: Promise<AladinStatic> | null = null;

/** One download per page. A failure isn't cached, so a later mount can try again. */
export function loadAladinOnce(opts?: LoadOptions): Promise<AladinStatic> {
  if (!shared) {
    shared = loadAladin(opts);
    shared.catch(() => (shared = null));
  }
  return shared;
}
