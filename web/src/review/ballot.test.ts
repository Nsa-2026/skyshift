// Changing and resetting a vote (contracts/api.md v3.16) on the ballot, in the review queue
// (#/candidates/one=1) and on the candidate page (#/c/<id>). The plate and the candidate list are
// stand-ins; the votes go through the real vote API on a LocalStore.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Candidate } from "../api/types";
import { KEYS, type Answer, type KV } from "../vote/model";
import { LocalStore, voteApi, type VoteApi } from "../vote/store";
import { Ballot, type BallotState } from "./ballot";
import { markPracticeDone } from "./practice";
import { cand } from "./testkit";

// ---- stand-ins -----------------------------------------------------------------------------------
let mockCands: Candidate[] = [];
let mockVotes: { api: VoteApi; kv: KV } | null = null;
/** The plate: frames are "on screen" as soon as a candidate is shown. Any other method is a no-op. */
vi.mock("./plate", async (orig) => ({
  ...(await orig<typeof import("./plate")>()),
  Plate: class {
    constructor(_host: HTMLElement, events: { onReady?(ready: boolean): void } = {}) {
      const show = () => events.onReady?.(true);
      return new Proxy(this, { get: (_t, k) => (k === "showCandidate" || k === "showTarget" ? show : () => {}) });
    }
  },
}));
vi.mock("./data", async (orig) => ({
  ...(await orig<typeof import("./data")>()),
  candidateSet: async () => ({ data: { candidates: mockCands }, candidates: mockCands, sample: false }),
  votes: async () => mockVotes,
  preload: () => {},
  suspectFor: async () => null,
}));
// jsdom lays nothing out, so "is the page on screen?" is always yes here.
vi.mock("./parts", async (orig) => ({ ...(await orig<typeof import("./parts")>()), visible: () => true }));
vi.mock("./assist", async (orig) => ({ ...(await orig<typeof import("./assist")>()), loadAssist: async () => null }));

const { mountQueue } = await import("./review");
const { mountSuspect } = await import("./candidate");

function memKV(): KV {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v) };
}
const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle(n = 6) {
  for (let i = 0; i < n; i++) await tick();
}
/** A key press on the page (the queue listens on window, the candidate page on document). */
const key = (k: string) => document.body.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));

// ---- the ballot on its own -----------------------------------------------------------------------
const base: BallotState = { mine: null, justNow: false, counts: null, saving: null, waiting: false, error: null, local: false, kind: "mover" };

describe("ballot: change or reset an answer", () => {
  let el: HTMLElement;
  let picked: Answer[];
  let resets: number;
  let b: Ballot;
  beforeEach(() => {
    el = document.createElement("div");
    document.body.append(el);
    picked = [];
    resets = 0;
    b = new Ballot(el, { onVote: (a) => picked.push(a), onReset: () => resets++, full: false });
  });
  afterEach(() => (document.body.innerHTML = ""));
  const choice = (a: Answer) => el.querySelector<HTMLButtonElement>(`button[data-answer='${a}']`)!;
  const resetBtn = () => el.querySelector<HTMLButtonElement>("[data-act='reset-vote']");

  it("keeps all four choices usable after a vote, with this browser's answer selected", () => {
    b.set({ ...base, mine: "mover", justNow: true, counts: { mover: 3, changing: 0, junk: 1, unsure: 0 } });
    expect(el.querySelector<HTMLElement>(".rv-ballot")!.dataset.state).toBe("voted");
    expect(choice("mover").getAttribute("aria-pressed")).toBe("true");
    for (const a of ["changing", "junk", "unsure"] as const) {
      expect(choice(a).getAttribute("aria-pressed")).toBe("false");
      expect(choice(a).disabled).toBe(false);
    }
    expect(b.open()).toBe(true);
    expect(el.querySelector(".rv-yours")!.textContent).toBe("Saved: you said Mover. Pick another answer to change it, or reset it.");
    expect(resetBtn()?.textContent).toBe("Reset answer");
    expect(el.textContent).not.toContain("can't be changed");
  });

  it("an earlier visit's vote can be changed too", () => {
    b.set({ ...base, mine: "junk", counts: { mover: 0, changing: 0, junk: 1, unsure: 0 } });
    expect(el.querySelector(".rv-yours")!.textContent).toBe("You said Junk on an earlier visit. Pick another answer to change it, or reset it.");
    choice("changing").click();
    expect(picked).toEqual(["changing"]);
  });

  it("picking the answer it already has does nothing; Reset answer asks to take it back", () => {
    b.set({ ...base, mine: "mover", justNow: true });
    choice("mover").click();
    expect(picked).toEqual([]);
    resetBtn()!.click();
    expect(resets).toBe(1);
  });

  it("shows Reset answer only when this browser has a vote, and only with onReset", () => {
    b.set(base);
    expect(resetBtn()).toBeNull();
    expect(el.querySelector<HTMLElement>(".rv-result")!.hidden).toBe(true);
    const other = document.createElement("div");
    const plain = new Ballot(other, { onVote: () => {}, full: false });
    plain.set({ ...base, mine: "mover", justNow: true });
    expect(other.querySelector("[data-act='reset-vote']")).toBeNull();
    expect(other.querySelector(".rv-yours")!.textContent).toBe("Saved: you said Mover. Pick another answer to change it.");
  });

  it("shuts while a change or a reset is saving: one write at a time", () => {
    b.set({ ...base, mine: "mover", justNow: true, saving: "junk" });
    expect(b.open()).toBe(false);
    expect(el.querySelector<HTMLElement>(".rv-ballot")!.dataset.state).toBe("saving");
    expect([...el.querySelectorAll<HTMLButtonElement>("button[data-answer]")].every((x) => x.disabled)).toBe(true);
    expect(choice("junk").getAttribute("aria-pressed")).toBe("true");
    expect(el.querySelector(".rv-ballot-status")!.textContent).toBe("Changing your answer to Junk…");
    expect(resetBtn()!.disabled).toBe(true);

    b.set({ ...base, mine: "mover", justNow: true, resetting: true });
    expect(b.open()).toBe(false);
    expect(el.querySelector(".rv-ballot-status")!.textContent).toBe("Resetting your answer…");
    resetBtn()!.click();
    expect(resets).toBe(0);
  });

  it("says when the answer was reset, and shows the error when it fails", () => {
    b.set({ ...base, reset: true });
    expect(el.querySelector(".rv-ballot-status")!.textContent).toBe("Answer reset. Pick an answer to vote again.");
    expect([...el.querySelectorAll("button[aria-pressed='true']")]).toHaveLength(0);
    b.set({ ...base, mine: "mover", error: "Can't reach the vote server. Check your connection, then try again." });
    expect(el.querySelector<HTMLElement>(".rv-ballot-status")!.dataset.kind).toBe("error");
    expect(choice("mover").getAttribute("aria-pressed")).toBe("true");
  });

  it("keeps focus on Reset answer when the counts refresh", () => {
    b.set({ ...base, mine: "mover", justNow: true });
    resetBtn()!.focus();
    b.set({ ...base, mine: "mover", justNow: true, counts: { mover: 2, changing: 0, junk: 0, unsure: 0 } });
    expect(document.activeElement).toBe(resetBtn());
  });

  it("practice is unchanged: no reset, no saved answer", () => {
    const p = document.createElement("div");
    const practice = new Ballot(p, { onVote: () => {}, onReset: () => resets++, full: false });
    practice.set({ ...base, practice: { answer: "junk", correct: true, text: "Right: a cosmic ray." } });
    expect(practice.open()).toBe(false);
    expect(p.querySelector("[data-act='reset-vote']")).toBeNull();
    expect(p.querySelector(".rv-feedback")!.textContent).toBe("Right: a cosmic ray.");
  });
});

// ---- the review queue ----------------------------------------------------------------------------
describe("review queue: vote, go back, change, reset", () => {
  let el: HTMLElement;
  let kv: KV;
  let store: LocalStore;
  let handle: ReturnType<typeof mountQueue>;
  beforeEach(async () => {
    sessionStorage.clear();
    kv = memKV();
    markPracticeDone(kv);
    store = new LocalStore(kv);
    mockVotes = { api: voteApi(store, kv), kv };
    mockCands = [cand("q1", { score: 0.9 }), cand("q2", { score: 0.8 }), cand("q3", { score: 0.7 })];
    el = document.createElement("main");
    document.body.append(el);
    handle = mountQueue(el);
    handle.show(null);
    await settle();
  });
  afterEach(() => {
    handle.destroy();
    document.body.innerHTML = "";
  });
  const pressed = () => [...el.querySelectorAll<HTMLButtonElement>(".rq-ballot button[aria-pressed='true']")].map((b) => b.dataset.answer);
  const shown = () => el.querySelector(".rv-id a")?.getAttribute("href");
  const click = (sel: string) => el.querySelector<HTMLButtonElement>(sel)!.click();
  const answers = () => store.written.votes.map((v) => [v.candidate_id, v.answer]);

  it("keys 1–4 vote and change; Next and Back keep the flow; Reset answer takes the vote back", async () => {
    expect(shown()).toBe("#/c/q1");
    key("1");
    await settle();
    expect(answers()).toEqual([["q1", "mover"]]);
    expect(pressed()).toEqual(["mover"]);
    expect(el.querySelector(".rq-progress")!.textContent).toMatch(/^1 reviewed this session/);
    // After the first vote the queue still moves on with Next.
    expect(document.activeElement?.getAttribute("data-act")).toBe("next");

    key("2"); // change: mover → changing
    await settle();
    expect(answers()).toEqual([["q1", "mover"], ["q1", "changing"]]);
    expect(pressed()).toEqual(["changing"]);
    expect(el.querySelector(".rv-yours")!.textContent).toContain("Saved: you said Changing.");
    expect(el.querySelector(".rv-tally")!.textContent).toMatch(/Mover\s*0/);
    expect(el.querySelector(".rv-tally")!.textContent).toMatch(/Changing\s*1/);
    expect(el.querySelector(".rq-progress")!.textContent).toMatch(/^1 reviewed this session/); // a change isn't a new review

    key("2"); // the same answer again writes nothing
    await settle();
    expect(answers()).toHaveLength(2);

    click("[data-act='next']");
    await settle();
    expect(shown()).toBe("#/c/q2");
    click("[data-act='back']");
    await settle();
    expect(shown()).toBe("#/c/q1");
    expect(pressed()).toEqual(["changing"]);
    expect(el.querySelector<HTMLButtonElement>("button[data-answer='junk']")!.disabled).toBe(false);

    click("[data-act='reset-vote']");
    await settle();
    expect(answers()).toEqual([["q1", "mover"], ["q1", "changing"], ["q1", "withdrawn"]]);
    expect(pressed()).toEqual([]);
    expect(mockVotes!.api.myVote("q1")).toBeNull();
    expect(JSON.parse(kv.get(KEYS.voted)!)).toEqual({});
    expect(el.querySelector(".rv-ballot-status")!.textContent).toBe("Answer reset. Pick an answer to vote again.");
    expect(el.querySelector<HTMLElement>(".rq-ballot .rv-result")!.hidden).toBe(true);
    expect(el.querySelector("[data-act='skip']")!.textContent).toBe("SSkip");
    expect((await mockVotes!.api.countsFor(["q1"])).get("q1")).toEqual({ mover: 0, changing: 0, junk: 0, unsure: 0 });

    key("3"); // vote again after the reset
    await settle();
    expect(pressed()).toEqual(["junk"]);
    expect((await mockVotes!.api.countsFor(["q1"])).get("q1")).toEqual({ mover: 0, changing: 0, junk: 1, unsure: 0 });
    expect(el.querySelector(".rq-progress")!.textContent).toMatch(/^1 reviewed this session/);
  });

  it("guards double clicks: one write in flight", async () => {
    let release: () => void = () => {};
    const real = store.vote.bind(store);
    store.vote = (id, a, voter) => new Promise<void>((r) => (release = () => r(real(id, a, voter))));
    click("button[data-answer='mover']");
    click("button[data-answer='mover']");
    click("button[data-answer='junk']");
    key("4");
    expect(el.querySelector(".rv-ballot-status")!.textContent).toBe("Saving your Mover vote…");
    expect([...el.querySelectorAll<HTMLButtonElement>(".rq-ballot button[data-answer]")].every((b) => b.disabled)).toBe(true);
    release();
    await settle();
    expect(answers()).toEqual([["q1", "mover"]]);
    expect(pressed()).toEqual(["mover"]);
  });

  it("shows a failed change in plain words and keeps the earlier answer", async () => {
    key("1");
    await settle();
    store.vote = () => Promise.reject({ code: "unavailable" });
    key("3");
    await settle();
    expect(el.querySelector(".rv-ballot-status")!.textContent).toContain("Can't reach the vote server");
    expect(pressed()).toEqual(["mover"]);
    expect(mockVotes!.api.myVote("q1")).toBe("mover");
  });
});

// ---- the candidate in the Viewer (#/c/<id>) -----------------------------------------------------
describe("candidate in the Viewer: change or reset an earlier vote", () => {
  afterEach(() => (document.body.innerHTML = ""));

  it("opens with the earlier answer selected, changes it, and resets it", async () => {
    const kv = memKV();
    const store = new LocalStore(kv);
    const api = voteApi(store, kv);
    mockVotes = { api, kv };
    mockCands = [cand("c1"), cand("c2")];
    await api.castVote("c1", "unsure");
    await store.vote("c1", "mover", "someone_else_1");
    const el = document.createElement("main");
    document.body.append(el);
    const page = mountSuspect(el, { open: vi.fn(), showing: () => true, showAll: vi.fn() });
    page.show("c1");
    await settle(10);
    // The Viewer says so once the candidate's frames are on the plate.
    page.ready(true);
    const pressed = () => [...el.querySelectorAll<HTMLButtonElement>(".cd-ballot button[aria-pressed='true']")].map((b) => b.dataset.answer);
    expect(pressed()).toEqual(["unsure"]);
    expect(el.querySelector(".rv-yours")!.textContent).toBe("You said Not sure on an earlier visit. Pick another answer to change it, or reset it.");

    key("1");
    await settle();
    expect(pressed()).toEqual(["mover"]);
    expect(el.querySelector(".rv-tally")!.textContent).toMatch(/Mover\s*2/);
    expect(el.querySelector(".rv-tally")!.textContent).toMatch(/Not sure\s*0/);

    el.querySelector<HTMLButtonElement>("[data-act='reset-vote']")!.click();
    await settle();
    expect(pressed()).toEqual([]);
    expect(api.myVote("c1")).toBeNull();
    expect(store.written.votes.map((v) => v.answer)).toEqual(["unsure", "mover", "mover", "withdrawn"]);
    expect((await api.countsFor(["c1"])).get("c1")).toEqual({ mover: 1, changing: 0, junk: 0, unsure: 0 });
    // Focus goes back to the choices, so the keyboard can pick again.
    expect(document.activeElement?.classList.contains("rv-choice")).toBe(true);
    page.destroy();
  });
});
