// The sky page's data: candidates from /data/candidates.json (static, written by the pipeline
// export). In dev and tests only, when that file isn't there yet, the real pilot sample in
// fixtures/api/ stands in, so the page can be built before the export lands. Production never
// ships the fixture (the import sits behind import.meta.env.DEV and is dropped from the build).
import { loadCandidates } from "../api/client";
import type { Candidate, CandidatesResponse } from "../api/types";
import { isKind } from "./kinds";

export interface AtlasData {
  generated: string | null;
  dataRelease: string | null;
  zones: string[];
  candidates: Candidate[];
  /** "export": the real file; "sample": the dev fixture; "none": nothing to show yet. */
  origin: "export" | "sample" | "none";
}

/** Keep candidates the page can place and draw; drop the rest quietly. */
export function cleanCandidates(raw: unknown): Candidate[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: Candidate[] = [];
  for (const c of raw as Candidate[]) {
    if (!c || typeof c !== "object") continue;
    if (typeof c.id !== "string" || !c.id || seen.has(c.id)) continue;
    if (!isKind(c.kind) || !Number.isFinite(c.ra) || !Number.isFinite(c.dec) || Math.abs(c.dec) > 90) continue;
    seen.add(c.id);
    out.push({
      ...c,
      zone: typeof c.zone === "string" && c.zone ? c.zone : "other",
      labels: Array.isArray(c.labels) ? c.labels : [],
      reasons: Array.isArray(c.reasons) ? c.reasons : [],
      flags: Array.isArray(c.flags) ? c.flags : [],
      why: typeof c.why === "string" ? c.why : "",
      frames_url: typeof c.frames_url === "string" && c.frames_url ? c.frames_url : null,
    });
  }
  return out;
}

export function toAtlasData(r: CandidatesResponse | null, origin: AtlasData["origin"]): AtlasData {
  if (!r) return { generated: null, dataRelease: null, zones: [], candidates: [], origin: "none" };
  const candidates = cleanCandidates(r.candidates);
  const zones = [...new Set([...(Array.isArray(r.zones) ? r.zones : []), ...candidates.map((c) => c.zone)])].filter((z) =>
    candidates.some((c) => c.zone === z),
  );
  return { generated: r.generated ?? null, dataRelease: r.data_release ?? null, zones, candidates, origin };
}

export async function loadAtlasData(signal?: AbortSignal): Promise<AtlasData> {
  const r = await loadCandidates(signal);
  if (r) return toAtlasData(r, "export");
  if (import.meta.env.DEV) {
    try {
      const m = await import("../../../fixtures/api/candidates.json");
      return toAtlasData((m.default ?? m) as unknown as CandidatesResponse, "sample");
    } catch {
      /* no fixture either */
    }
  }
  return toAtlasData(null, "none");
}
