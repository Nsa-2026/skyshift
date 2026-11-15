import { beforeEach, describe, expect, it, vi } from "vitest";
import { KEYS, VoteError, type KV } from "./model";
import { LocalStore, VOTED_EVENT, firebaseConfig, openStore, toVoteError, voteApi, withTimeout } from "./store";

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
            { id: "near", data: () => ({ ra: 10, dec: 20.001, mjd: 60800, clock: "months", source: "user", created: { toMillis: () => 1700000000000 } }) },
            { id: "pipe", data: () => ({ ra: 10.002, dec: 20, mjd: 60801, clock: "hours", source: "pipeline" }) },
            { id: "far-in-ra", data: () => ({ ra: 11, dec: 20, mjd: 60800, clock: "months", source: "user" }) },
            { id: "broken", data: () => ({ ra: "x" }) },
          ]
        : [
            { id: "v1", data: () => ({ candidate_id: "near", answer: "mover", voter: "voter_a1", ts: { toMillis: () => 1 } }) },
            { id: "v2", data: () => ({ candidate_id: "near", answer: "junk", voter: "voter_b1", ts: { toMillis: () => 1 } }) },
            // voter_b1 takes the vote back; voter_c1 changes junk → changing (still pending: ts null)
            { id: "v3", data: () => ({ candidate_id: "near", answer: "withdrawn", voter: "voter_b1", ts: { toMillis: () => 5 } }) },
            { id: "v4", data: () => ({ candidate_id: "pipe", answer: "junk", voter: "voter_c1", ts: { toMillis: () => 2 } }) },
            { id: "v5", data: () => ({ candidate_id: "pipe", answer: "changing", voter: "voter_c1", ts: null }) },
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
    const before = Date.now();
    const c = await s.flag(doc);
    expect(c.id).toBeTruthy();
    expect(s.written.candidates).toEqual([{ ...doc, created: expect.any(Date) }]);
    expect(Object.keys(s.written.candidates[0]).sort()).toEqual(["clock", "created", "dec", "mjd", "ra", "source"]);
    expect(c.created).toBeGreaterThanOrEqual(before);
    await s.vote(c.id, "changing", "voter_12345");
    expect(Object.keys(s.written.votes[0]).sort()).toEqual(["answer", "candidate_id", "ts", "voter"]);
    expect(await s.near({ ra: 10, dec: 20 }, 10)).toEqual([c]);
    expect(await s.near({ ra: 10, dec: 21 }, 10)).toEqual([]);
    expect((await s.counts([c.id])).get(c.id)).toEqual({ mover: 0, changing: 1, junk: 0, unsure: 0 });
  });

  it("keeps a change and a take back as new documents; only each voter's latest counts (v3.16)", async () => {
    const s = new LocalStore(memKV());
    const c = await s.flag(doc);
    await s.vote(c.id, "mover", "voter_12345");
    await s.vote(c.id, "junk", "voter_12345"); // a change of mind, not refused
    await s.vote(c.id, "junk", "voter_67890");
    expect(s.written.votes.map((v) => v.answer)).toEqual(["mover", "junk", "junk"]);
    expect((await s.counts([c.id])).get(c.id)).toEqual({ mover: 0, changing: 0, junk: 2, unsure: 0 });
    await s.vote(c.id, "withdrawn", "voter_67890");
    expect(s.written.votes[3]).toMatchObject({ candidate_id: c.id, answer: "withdrawn", voter: "voter_67890" });
    expect((await s.counts([c.id])).get(c.id)).toEqual({ mover: 0, changing: 0, junk: 1, unsure: 0 });
    const all = await s.allVotes();
    expect(all.map((v) => [v.voter, v.answer])).toEqual([["voter_12345", "junk"]]);
    await expect(s.vote(c.id, "maybe" as never, "voter_12345")).rejects.toMatchObject({ kind: "invalid" });
  });

  it("only lets the browser create source user", async () => {
    const s = new LocalStore(memKV());
    const c = await s.flag({ ...doc, source: "pipeline" });
    expect(c.source).toBe("user");
  });

  it("adds pipeline suspects and survives a failing source or broken storage", async () => {
    const kv = memKV();
    kv.set(KEYS.localCandidates, "not json");
    const pipe = { id: "pipeline-s1", ra: 10, dec: 20, mjd: 60800, clock: "hours" as const, source: "pipeline" as const, created: null };
    expect(await new LocalStore(kv, async () => [pipe]).near({ ra: 10, dec: 20 }, 5)).toEqual([pipe]);
    expect(await new LocalStore(kv, async () => Promise.reject(new Error("404"))).near({ ra: 10, dec: 20 }, 5)).toEqual([]);
  });
});

describe("FirestoreStore", () => {
  beforeEach(() => void (sent.length = 0));

  it("sends exactly the contract's fields, with serverTimestamp() for created and ts", async () => {
    const { FirestoreStore } = await import("./firestore");
    const s = new FirestoreStore({ apiKey: "k", projectId: "nasa-glazing" });
    const c = await s.flag(doc);
    await s.vote(c.id, "unsure", "voter_12345");
    await s.vote(c.id, "withdrawn", "voter_12345");
    expect(sent).toEqual([
      { path: "candidates", data: { ra: 10, dec: 20, mjd: 60800.5, clock: "hours", source: "user", created: SERVER_TS } },
      { path: "votes", data: { candidate_id: c.id, answer: "unsure", ts: SERVER_TS, voter: "voter_12345" } },
      { path: "votes", data: { candidate_id: c.id, answer: "withdrawn", ts: SERVER_TS, voter: "voter_12345" } },
    ]);
  });

  it("cuts the declination band to a circle and drops broken docs", async () => {
    const { FirestoreStore } = await import("./firestore");
    const s = new FirestoreStore({ apiKey: "k", projectId: "p" });
    const near = await s.near({ ra: 10, dec: 20 }, 60);
    expect(near.map((c) => c.id)).toEqual(["near", "pipe"]);
    expect(near.map((c) => c.created)).toEqual([1700000000000, null]);
    expect((await s.allCandidates()).map((c) => c.id)).toEqual(["near", "pipe", "far-in-ra"]);
  });

  it("reads voter and ts, so counts and allVotes keep each voter's latest and never withdrawn", async () => {
    const { FirestoreStore } = await import("./firestore");
    const s = new FirestoreStore({ apiKey: "k", projectId: "p" });
    const all = await s.allVotes();
    expect(all.map((v) => [v.candidate_id, v.voter, v.answer, v.ts])).toEqual([
      ["near", "voter_a1", "mover", 1],
      ["pipe", "voter_c1", "changing", null],
    ]);
    const counts = await s.counts(["near", "pipe"]);
    expect(counts.get("near")).toEqual({ mover: 1, changing: 0, junk: 0, unsure: 0 });
    expect(counts.get("pipe")).toEqual({ mover: 0, changing: 1, junk: 0, unsure: 0 });
  });
});

describe("vote API for other pages (voteApi, getVoteStore)", () => {
  it("counts every id, zeros included, and casts this browser's one vote", async () => {
    const kv = memKV();
    const s = new LocalStore(kv);
    const api = voteApi(s, kv);
    expect(api.mode).toBe("local");
    expect(api.voterId()).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    const seen = vi.fn();
    window.addEventListener(VOTED_EVENT, (e) => seen((e as CustomEvent).detail), { once: true });

    // Pipeline candidates from /data/candidates.json vote by their own id.
    await api.castVote("pilot-pm-0003", "mover");
    expect(seen).toHaveBeenCalledWith({ candidateId: "pilot-pm-0003", answer: "mover", previous: null });
    expect(api.myVote("pilot-pm-0003")).toBe("mover");
    expect(api.myVote("pilot-pm-0004")).toBeNull();
    expect(s.written.votes[0]).toMatchObject({ candidate_id: "pilot-pm-0003", answer: "mover", voter: api.voterId() });

    await expect(api.castVote("pilot-pm-0005", "maybe" as never)).rejects.toMatchObject({ kind: "invalid" });
    await expect(api.castVote("pilot-pm-0005", "withdrawn" as never)).rejects.toMatchObject({ kind: "invalid" });
    expect(s.written.votes).toHaveLength(1);

    const counts = await api.countsFor(["pilot-pm-0003", "pilot-pm-0004", "pilot-pm-0003"]);
    expect([...counts.keys()]).toEqual(["pilot-pm-0003", "pilot-pm-0004"]);
    expect(counts.get("pilot-pm-0003")).toEqual({ mover: 1, changing: 0, junk: 0, unsure: 0 });
    expect(counts.get("pilot-pm-0004")).toEqual({ mover: 0, changing: 0, junk: 0, unsure: 0 });
    expect((await api.countsFor([])).size).toBe(0);
  });

  it("changes a vote: a new document, the memory follows, the event says what it was", async () => {
    const kv = memKV();
    const s = new LocalStore(kv);
    const api = voteApi(s, kv);
    const seen = vi.fn();
    const on = (e: Event) => seen((e as CustomEvent).detail);
    window.addEventListener(VOTED_EVENT, on);
    try {
      await api.castVote("c1", "mover");
      await api.castVote("c1", "mover"); // the same answer: nothing written, no event
      expect(s.written.votes).toHaveLength(1);
      expect(seen).toHaveBeenCalledTimes(1);

      await api.castVote("c1", "junk");
      expect(s.written.votes.map((v) => v.answer)).toEqual(["mover", "junk"]);
      expect(seen).toHaveBeenLastCalledWith({ candidateId: "c1", answer: "junk", previous: "mover" });
      expect(api.myVote("c1")).toBe("junk");
      expect(voteApi(s, kv).myVote("c1")).toBe("junk"); // remembered in this browser
      expect((await api.countsFor(["c1"])).get("c1")).toEqual({ mover: 0, changing: 0, junk: 1, unsure: 0 });
    } finally {
      window.removeEventListener(VOTED_EVENT, on);
    }
  });

  it("takes a vote back: a withdrawn document, forgotten here, event answer null", async () => {
    const kv = memKV();
    const s = new LocalStore(kv);
    const api = voteApi(s, kv);
    const seen = vi.fn();
    const on = (e: Event) => seen((e as CustomEvent).detail);
    window.addEventListener(VOTED_EVENT, on);
    try {
      await api.withdrawVote("c1"); // no vote here: nothing to take back
      expect(s.written.votes).toHaveLength(0);
      expect(seen).not.toHaveBeenCalled();

      await api.castVote("c1", "changing");
      await s.vote("c1", "mover", "someone_else_1");
      await api.withdrawVote("c1");
      expect(s.written.votes.map((v) => v.answer)).toEqual(["changing", "mover", "withdrawn"]);
      expect(s.written.votes[2]).toMatchObject({ candidate_id: "c1", voter: api.voterId() });
      expect(seen).toHaveBeenLastCalledWith({ candidateId: "c1", answer: null, previous: "changing" });
      expect(api.myVote("c1")).toBeNull();
      expect(voteApi(s, kv).myVote("c1")).toBeNull();
      expect((await api.countsFor(["c1"])).get("c1")).toEqual({ mover: 1, changing: 0, junk: 0, unsure: 0 });
      expect((await api.allVotes()).map((v) => [v.voter, v.answer])).toEqual([["someone_else_1", "mover"]]);

      await api.withdrawVote("c1"); // already taken back: nothing more
      expect(s.written.votes).toHaveLength(3);

      // Voting again after a take back is a first vote again.
      await api.castVote("c1", "unsure");
      expect(seen).toHaveBeenLastCalledWith({ candidateId: "c1", answer: "unsure", previous: null });
      expect((await api.countsFor(["c1"])).get("c1")).toEqual({ mover: 1, changing: 0, junk: 0, unsure: 1 });
    } finally {
      window.removeEventListener(VOTED_EVENT, on);
    }
  });

  it("keeps the vote in memory when taking it back fails", async () => {
    const kv = memKV();
    const s = new LocalStore(kv);
    const api = voteApi(s, kv);
    await api.castVote("c1", "mover");
    s.vote = () => Promise.reject({ code: "unavailable" });
    await expect(api.withdrawVote("c1")).rejects.toMatchObject({ kind: "offline" });
    await expect(api.castVote("c1", "junk")).rejects.toMatchObject({ kind: "offline" });
    expect(api.myVote("c1")).toBe("mover");
  });

  it("lists all votes and all flagged candidates", async () => {
    const kv = memKV();
    const s = new LocalStore(kv);
    const api = voteApi(s, kv);
    const c = await api.flag(doc);
    expect(api.flaggedAt(c.id)).toEqual(expect.any(Number));
    await api.castVote(c.id, "junk");
    await s.vote(c.id, "mover", "someone_else_1");
    const votes = await api.allVotes();
    expect(votes.map((v) => [v.candidate_id, v.answer])).toEqual([
      [c.id, "junk"],
      [c.id, "mover"],
    ]);
    expect(votes[0].ts).toEqual(expect.any(Number));
    expect((await api.allCandidates()).map((x) => x.id)).toEqual([c.id]);
  });

  it("turns store failures into plain words", async () => {
    const kv = memKV();
    const s = new LocalStore(kv);
    s.vote = () => Promise.reject({ code: "permission-denied" });
    s.allVotes = () => Promise.reject({ code: "unavailable" });
    const api = voteApi(s, kv);
    await expect(api.castVote("x1", "mover")).rejects.toMatchObject({ kind: "denied" });
    expect(api.myVote("x1")).toBeNull();
    await expect(api.allVotes()).rejects.toMatchObject({ kind: "offline" });
  });

  it("getVoteStore is one shared, local-mode API without a Firebase config", async () => {
    // A developer's web/.env may hold the real config: blank it so the test never opens Firestore.
    vi.stubEnv("VITE_FIREBASE_API_KEY", "");
    vi.stubEnv("VITE_FIREBASE_PROJECT_ID", "");
    vi.resetModules();
    const { getVoteStore } = await import("./store");
    const a = await getVoteStore();
    expect(await getVoteStore()).toBe(a);
    expect(a.mode).toBe("local");
    expect(a.notice).toBeNull();
    vi.unstubAllEnvs();
  });

  it("openStore picks Firestore when configured", async () => {
    const { store } = await openStore(memKV(), { VITE_FIREBASE_API_KEY: "k", VITE_FIREBASE_PROJECT_ID: "p" });
    expect(store.mode).toBe("firestore");
  });
});

describe("errors in plain words", () => {
  it("maps Firestore codes", () => {
    expect(toVoteError({ code: "permission-denied" }).kind).toBe("denied");
    expect(toVoteError({ code: "permission-denied" }).message).not.toMatch(/can't be changed/);
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
