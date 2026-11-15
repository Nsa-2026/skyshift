// Where the Community page's numbers come from: the vote API (src/vote/store getVoteStore), which
// reads Firestore when it is configured (everyone's votes) and this browser's local store otherwise.
// Candidate kinds and positions come from /data/candidates.json; dots that visitors flagged come
// from the vote API's candidates.
import { loadCandidates } from "../api/client";
import type { CandidatesResponse } from "../api/types";
import { ANSWERS, KEYS, readJson, type Answer, type Candidate, type KV } from "../vote/model";
import { getVoteStore, type VoteApi } from "../vote/store";
import { astronomersList, cleanVotes, tally, type AstroRow, type CandInfo, type VoteRow } from "./stats";

export interface Snapshot {
  mode: "firestore" | "local";
  /** A plain sentence when something went wrong (fallback to local mode, or the votes didn't load), else null. */
  note: string | null;
  votes: VoteRow[];
  /** Candidates by id, from candidates.json. */
  info: Map<string, CandInfo>;
  /** Candidates the pipeline listed (candidates.json), or null if the file isn't there. */
  listed: number | null;
  dataRelease: string | null;
  /** This browser's answers by candidate id. */
  mine: Record<string, Answer>;
  loadedAt: number;
  /** Positions for ids that aren't in candidates.json (dots visitors flagged, pipeline suspects). */
  lookup(ids: string[]): Promise<Map<string, CandInfo>>;
}

/** candidates.json → CandInfo by id. */
export function infoFromCatalogue(r: CandidatesResponse | null): Map<string, CandInfo> {
  const out = new Map<string, CandInfo>();
  for (const c of r?.candidates ?? []) {
    out.set(c.id, { id: c.id, kind: c.kind, ra: c.ra, dec: c.dec, mjd: c.mjd, clock: c.clock, det: c.det, why: c.why });
  }
  return out;
}

/** A flagged candidate from the vote store → CandInfo. Pipeline suspects are fast movers. */
export function infoFromFlag(c: Candidate): CandInfo {
  const suspect = c.source === "pipeline" && c.clock === "hours";
  return {
    id: c.id,
    kind: suspect ? "mover" : "flag",
    ra: c.ra,
    dec: c.dec,
    mjd: c.mjd,
    clock: c.clock,
    det: null,
    why: suspect ? "Pipeline suspect: a mover with no known asteroid nearby." : c.source === "pipeline" ? "Pipeline suspect." : "Flagged by a visitor on the Blink page.",
  };
}

/** This browser's answers, as the vote API remembers them. */
export function myAnswers(kv: KV): Record<string, Answer> {
  const raw = readJson<Record<string, unknown>>(kv, KEYS.voted, {});
  const out: Record<string, Answer> = {};
  for (const [id, a] of Object.entries(raw)) if ((ANSWERS as readonly unknown[]).includes(a)) out[id] = a as Answer;
  return out;
}

export interface LoadOptions {
  kv: KV;
  /** Default: the page's shared vote API. */
  api?: () => Promise<Pick<VoteApi, "mode" | "notice" | "allVotes" | "allCandidates">>;
  /** Default: /data/candidates.json. */
  catalogue?: () => Promise<CandidatesResponse | null>;
  now?: () => number;
}

/** Read everything the Community page shows. Never throws: failures become a note. */
export async function loadSnapshot(opts: LoadOptions): Promise<Snapshot> {
  const now = opts.now ?? (() => Date.now());
  const catP = (opts.catalogue ?? (() => loadCandidates()))().catch(() => null);
  const api = await (opts.api ?? getVoteStore)();
  let note = api.notice;
  const [cat, raw] = await Promise.all([
    catP,
    api.allVotes().catch((e: unknown) => {
      const msg = e instanceof Error && e.message ? e.message : "The vote server didn't answer.";
      note = `Couldn't read the votes: ${msg} The numbers below may be missing votes.`;
      return [] as unknown[];
    }),
  ]);
  const info = infoFromCatalogue(cat);
  let flags: Promise<Map<string, CandInfo>> | null = null;
  return {
    mode: api.mode,
    note,
    votes: cleanVotes(raw),
    info,
    listed: cat ? cat.candidates.length : null,
    dataRelease: cat?.data_release ?? null,
    mine: myAnswers(opts.kv),
    loadedAt: now(),
    async lookup(ids) {
      const want = ids.filter((id) => !info.has(id));
      if (!want.length) return new Map();
      flags ??= api
        .allCandidates()
        .then((all) => new Map(all.map((c) => [c.id, infoFromFlag(c)])))
        .catch(() => new Map<string, CandInfo>());
      const all = await flags;
      return new Map(want.filter((id) => all.has(id)).map((id) => [id, all.get(id)!]));
    },
  };
}

/**
 * The list for astronomers, as the Community page shows it: everyone's votes (each browser's latest
 * per object), consensus movers and changing sources, strongest agreement first, with positions looked
 * up for dots that visitors flagged. To check downloads it from here.
 */
export async function astronomersFor(s: Snapshot): Promise<AstroRow[]> {
  const counts = tally(s.votes);
  const info = new Map(s.info);
  const rows = astronomersList(counts, (id) => info.get(id));
  const missing = rows.map((r) => r.id).filter((id) => !info.has(id));
  if (!missing.length) return rows;
  const found = await s.lookup(missing).catch(() => new Map<string, CandInfo>());
  for (const [id, i] of found) info.set(id, i);
  return found.size ? astronomersList(counts, (id) => info.get(id)) : rows;
}
