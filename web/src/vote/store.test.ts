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
            { id: "v1", data: () => ({ candidate_id: "near", answer: "mover", voter: "a", ts: 1 }) },
            { id: "v2", data: () => ({ candidate_id: "near", answer: "junk", voter: "b", ts: 1 }) },
            // Voter c changed unsure → changing, then took it back (v3.16): counts as nothing.
            { id: "v3", data: () => ({ candidate_id: "near", answer: "unsure", voter: "c", ts: { toMillis: () => 2 } }) },
            { id: "v4", data: () => ({ candidate_id: "near", answer: "changing", voter: "c", ts: { toMillis: () => 3 } }) },
            { id: "v5", data: () => ({ candidate_id: "near", answer: "withdrawn", voter: "c", ts: null }) },
            // Voter d changed mover → junk: counts once, as junk.
            { id: "v6", data: () => ({ candidate_id: "pipe", answer: "junk", voter: "d", ts: { toMillis: () => 9 } }) },
            { id: "v7", data: () => ({ candidate_id: "pipe", answer: "mover", voter: "d", ts: { toMillis: () => 4 } }) },
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

  it("writes a new document for a change or a take-back, and counts each voter's latest (v3.16)", async () => {
    const s = new LocalStore(memKV());
    const c = await s.flag(doc);
    await s.vote(c.id, "mover", "voter_12345");
    await s.vote(c.id, "junk", "voter_12345"); // a change
    await s.vote(c.id, "junk", "voter_67890");
    expect((await s.counts([c.id])).get(c.id)).toEqual({ mover: 0, changing: 0, junk: 2, unsure: 0 });
    await s.vote(c.id, "withdrawn", "voter_67890"); // a take-back
    expect((await s.counts([c.id])).get(c.id)).toEqual({ mover: 0, changing: 0, junk: 1, unsure: 0 });
    // Nothing was edited or deleted: four documents, the history in order.
    expect(s.written.votes.map((v) => [v.voter, v.answer])).toEqual([
      ["voter_12345", "mover"],
      ["voter_12345", "junk"],
      ["voter_67890", "junk"],
      ["voter_67890", "withdrawn"],
    ]);
    expect((await s.allVotes()).map((v) => v.answer)).toEqual(["mover", "junk", "junk", "withdrawn"]);
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
      // A take-back is one more new document, never an update (the rules are create-only).
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
    // Every document, the take-back included: readers apply the latest-vote rule when they count.
    expect((await s.allVotes()).map((v) => v.answer)).toEqual(["mover", "junk", "unsure", "changing", "withdrawn", "junk", "mover"]);
    const counts = await s.counts(["near", "pipe"]);
    expect(counts.get("near")).toEqual({ mover: 1, changing: 0, junk: 1, unsure: 0 });
    expect(counts.get("pipe")).toEqual({ mover: 0, changing: 0, junk: 1, unsure: 0 });
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

    const counts = await api.countsFor(["pilot-pm-0003", "pilot-pm-0004", "pilot-pm-0003"]);
    expect([...counts.keys()]).toEqual(["pilot-pm-0003", "pilot-pm-0004"]);
    expect(counts.get("pilot-pm-0003")).toEqual({ mover: 1, changing: 0, junk: 0, unsure: 0 });
    expect(counts.get("pilot-pm-0004")).toEqual({ mover: 0, changing: 0, junk: 0, unsure: 0 });
    expect((await api.countsFor([])).size).toBe(0);
  });

  it("changes and takes back this browser's vote: new documents, memory follows the latest", async () => {
    const kv = memKV();
    const s = new LocalStore(kv);
    const api = voteApi(s, kv);
    const seen: unknown[] = [];
    const h = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(VOTED_EVENT, h);
    const id = "pilot-pm-0007";
    await api.castVote(id, "mover");
    await s.vote(id, "mover", "someone_else_1");
    expect((await api.countsFor([id])).get(id)).toEqual({ mover: 2, changing: 0, junk: 0, unsure: 0 });

    await api.castVote(id, "junk"); // change
    expect(api.myVote(id)).toBe("junk");
    expect((await api.countsFor([id])).get(id)).toEqual({ mover: 1, changing: 0, junk: 1, unsure: 0 });

    await api.castVote(id, "junk"); // the same answer again: nothing written, nothing fired
    expect(s.written.votes).toHaveLength(3);

    await api.withdrawVote(id); // reset
    expect(api.myVote(id)).toBeNull();
    expect((await api.countsFor([id])).get(id)).toEqual({ mover: 1, changing: 0, junk: 0, unsure: 0 });
    await api.withdrawVote(id); // nothing left to take back: nothing written
    expect(s.written.votes.map((v) => v.answer)).toEqual(["mover", "mover", "junk", "withdrawn"]);
    expect(JSON.parse(kv.get(KEYS.voted)!)).toEqual({});

    await api.castVote(id, "unsure"); // voting again after a reset counts again
    expect((await api.countsFor([id])).get(id)).toEqual({ mover: 1, changing: 0, junk: 0, unsure: 1 });
    window.removeEventListener(VOTED_EVENT, h);
    expect(seen).toEqual([
      { candidateId: id, answer: "mover", previous: null },
      { candidateId: id, answer: "junk", previous: "mover" },
      { candidateId: id, answer: null, previous: "junk" },
      { candidateId: id, answer: "unsure", previous: null },
    ]);
  });

  it("allows one write at a time per candidate, and keeps the memory when a write fails", async () => {
    const kv = memKV();
    const s = new LocalStore(kv);
    const api = voteApi(s, kv);
    await api.castVote("c1", "mover");
    let release: () => void = () => {};
    const real = s.vote.bind(s);
    s.vote = (id, a, voter) => new Promise<void>((r) => (release = () => r(real(id, a, voter))));
    const first = api.castVote("c1", "junk");
    await expect(api.castVote("c1", "changing")).rejects.toMatchObject({ kind: "busy" });
    await expect(api.withdrawVote("c1")).rejects.toMatchObject({ kind: "busy" });
    release();
    await first;
    expect(api.myVote("c1")).toBe("junk");
    s.vote = () => Promise.reject({ code: "unavailable" });
    await expect(api.withdrawVote("c1")).rejects.toMatchObject({ kind: "offline" });
    expect(api.myVote("c1")).toBe("junk");
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
