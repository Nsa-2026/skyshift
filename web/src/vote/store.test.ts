import { beforeEach, describe, expect, it, vi } from "vitest";
import { KEYS, VoteError, type KV } from "./model";
import { LocalStore, firebaseConfig, toVoteError, withTimeout } from "./store";

// FirestoreStore against a fake SDK: checks what it would send to Firestore.
const sent: { path: string; data: Record<string, unknown> }[] = [];
const SERVER_TS = { __serverTimestamp: true };
vi.mock("firebase/app", () => ({ getApps: () => [], initializeApp: () => ({}) }));
vi.mock("firebase/firestore", () => ({
  getFirestore: () => ({}),
  collection: (_db: unknown, path: string) => ({ path }),
  addDoc: async (col: { path: string }, data: Record<string, unknown>) => {
    sent.push({ path: col.path, data });
    return { id: `fs-${sent.length}` };
  },
  serverTimestamp: () => SERVER_TS,
  query: (col: { path: string }, ...parts: unknown[]) => ({ col, parts }),
  where: (field: string, op: string, value: unknown) => ({ field, op, value }),
  limit: (n: number) => ({ limit: n }),
  getDocs: async (q: { col: { path: string }; parts: { field?: string; value?: unknown }[] }) => {
    const docs =
      q.col.path === "candidates"
        ? [
            { id: "near", data: () => ({ ra: 10, dec: 20.001, mjd: 60800, clock: "months", source: "user" }) },
            { id: "pipe", data: () => ({ ra: 10.002, dec: 20, mjd: 60801, clock: "hours", source: "pipeline" }) },
            { id: "far-in-ra", data: () => ({ ra: 11, dec: 20, mjd: 60800, clock: "months", source: "user" }) },
            { id: "broken", data: () => ({ ra: "x" }) },
          ]
        : [
            { id: "v1", data: () => ({ candidate_id: "near", answer: "mover", voter: "a", ts: 1 }) },
            { id: "v2", data: () => ({ candidate_id: "near", answer: "junk", voter: "b", ts: 1 }) },
          ];
    return { docs, forEach: (f: (d: (typeof docs)[number]) => void) => docs.forEach(f) };
  },
}));

function memKV(): KV {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v) };
}
const doc = { ra: 10, dec: 20, mjd: 60800.5, clock: "hours" as const, source: "user" as const };

describe("LocalStore", () => {
  it("writes the contract's shapes and reads them back near a point", async () => {
    const s = new LocalStore(memKV());
    const c = await s.flag(doc);
    expect(c.id).toBeTruthy();
    expect(s.written.candidates).toEqual([doc]);
    await s.vote(c.id, "changing", "voter_12345");
    expect(Object.keys(s.written.votes[0]).sort()).toEqual(["answer", "candidate_id", "ts", "voter"]);
    expect(await s.near({ ra: 10, dec: 20 }, 10)).toEqual([c]);
    expect(await s.near({ ra: 10, dec: 21 }, 10)).toEqual([]);
    expect((await s.counts([c.id])).get(c.id)).toEqual({ mover: 0, changing: 1, junk: 0, unsure: 0 });
  });

  it("refuses a second vote from the same voter", async () => {
    const s = new LocalStore(memKV());
    const c = await s.flag(doc);
    await s.vote(c.id, "mover", "voter_12345");
    await expect(s.vote(c.id, "junk", "voter_12345")).rejects.toMatchObject({ kind: "already" });
    await s.vote(c.id, "junk", "voter_67890");
    expect((await s.counts([c.id])).get(c.id)).toEqual({ mover: 1, changing: 0, junk: 1, unsure: 0 });
  });

  it("only lets the browser create source user", async () => {
    const s = new LocalStore(memKV());
    const c = await s.flag({ ...doc, source: "pipeline" });
    expect(c.source).toBe("user");
  });

  it("adds pipeline suspects and survives a failing source or broken storage", async () => {
    const kv = memKV();
    kv.set(KEYS.localCandidates, "not json");
    const pipe = { id: "pipeline-s1", ra: 10, dec: 20, mjd: 60800, clock: "hours" as const, source: "pipeline" as const };
    expect(await new LocalStore(kv, async () => [pipe]).near({ ra: 10, dec: 20 }, 5)).toEqual([pipe]);
    expect(await new LocalStore(kv, async () => Promise.reject(new Error("404"))).near({ ra: 10, dec: 20 }, 5)).toEqual([]);
  });
});

describe("FirestoreStore", () => {
  beforeEach(() => void (sent.length = 0));

  it("sends exactly the contract's fields, with serverTimestamp() for ts", async () => {
    const { FirestoreStore } = await import("./firestore");
    const s = new FirestoreStore({ apiKey: "k", projectId: "nasa-glazing" });
    const c = await s.flag(doc);
    await s.vote(c.id, "unsure", "voter_12345");
    expect(sent).toEqual([
      { path: "candidates", data: { ra: 10, dec: 20, mjd: 60800.5, clock: "hours", source: "user" } },
      { path: "votes", data: { candidate_id: c.id, answer: "unsure", ts: SERVER_TS, voter: "voter_12345" } },
    ]);
  });

  it("cuts the declination band to a circle and drops broken docs", async () => {
    const { FirestoreStore } = await import("./firestore");
    const s = new FirestoreStore({ apiKey: "k", projectId: "p" });
    const near = await s.near({ ra: 10, dec: 20 }, 60);
    expect(near.map((c) => c.id)).toEqual(["near", "pipe"]);
    const counts = await s.counts(["near", "pipe"]);
    expect(counts.get("near")).toEqual({ mover: 1, changing: 0, junk: 1, unsure: 0 });
  });
});

describe("errors in plain words", () => {
  it("maps Firestore codes", () => {
    expect(toVoteError({ code: "permission-denied" }).kind).toBe("denied");
    expect(toVoteError({ code: "unavailable" }).kind).toBe("offline");
    expect(toVoteError({ code: "resource-exhausted" }).kind).toBe("busy");
    expect(toVoteError(new Error("boom")).message).not.toContain("boom");
    const own = new VoteError("x", "already");
    expect(toVoteError(own)).toBe(own);
  });
  it("times out a hanging write", async () => {
    vi.useFakeTimers();
    const p = withTimeout(new Promise(() => {}), 1000);
    vi.advanceTimersByTime(1001);
    await expect(p).rejects.toMatchObject({ kind: "offline" });
    vi.useRealTimers();
  });
});

describe("firebase config", () => {
  it("needs an api key and a project id", () => {
    expect(firebaseConfig({})).toBeNull();
    expect(firebaseConfig({ VITE_FIREBASE_API_KEY: "k", VITE_FIREBASE_PROJECT_ID: " " })).toBeNull();
    expect(firebaseConfig({ VITE_FIREBASE_API_KEY: "k", VITE_FIREBASE_PROJECT_ID: "nasa-glazing" })).toMatchObject({
      apiKey: "k",
      projectId: "nasa-glazing",
    });
  });
});
