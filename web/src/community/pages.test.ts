import { describe, expect, it, vi } from "vitest";
import type { CandidatesResponse, ValidationResponse } from "../api/types";
import type { PageContext } from "../app/modules";
import { KEYS, type Answer, type KV } from "../vote/model";
import { LocalStore, voteApi } from "../vote/store";
import { mountAboutWith } from "./about";
import { NICKNAME_KEY, mountCommunityWith } from "./community";
import { astronomersFor, loadSnapshot, type Snapshot } from "./source";

function memKV(): KV {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v) };
}

/** The vote API over a local store in this KV, as getVoteStore() gives in local mode. */
const localApi = (kv: KV) => async () => voteApi(new LocalStore(kv), kv);

const ctx = (): PageContext & { explore: ReturnType<typeof vi.fn> } => ({ navigate: vi.fn(), explore: vi.fn() });

const catalogue: CandidatesResponse = {
  generated: "2026-11-14T13:12:18Z",
  data_release: "DR1+QR3",
  zones: ["pilot"],
  counts: { mover: 2, high_pm: 1, spectral: 0 },
  candidates: [
    { id: "pilot-m1", kind: "mover", ra: 122.5, dec: 17, mjd: 60974.09, clock: "hours", det: 3, why: "Seen in two exposures." },
    { id: "pilot-m2", kind: "mover", ra: 121.3, dec: 17.9, mjd: 61141.2, clock: "hours", det: 4, why: "Seen in two detectors." },
    { id: "pilot-p1", kind: "high_pm", ra: 120.9, dec: 16.3, mjd: 61145.3, clock: "months", det: 6, why: "Moves; no Gaia star." },
  ].map((c) => ({
    ...c,
    zone: "pilot",
    score: null,
    snr: null,
    flux_ujy: null,
    rate_arcsec_h: null,
    pm_mas_yr: null,
    pm_err_mas_yr: null,
    pa_deg: null,
    nearest_known_arcsec: null,
    gaia_match: false,
    catwise_match: false,
    galactic_b: 24,
    labels: [],
    reasons: [],
    flags: [],
    frames_url: null,
  })) as CandidatesResponse["candidates"],
};

/** Seed votes through the vote store's local implementation, as the Review page would. */
async function seed(kv: KV, plan: [string, Answer[]][]) {
  const store = new LocalStore(kv);
  for (const [id, answers] of plan) {
    for (let i = 0; i < answers.length; i++) await store.vote(id, answers[i], `${id.replace(/\W/g, "")}_voter_${i}`);
  }
}

describe("loadSnapshot", () => {
  it("local mode: reads the local store's votes and this browser's answers; flags come from lookup", async () => {
    const kv = memKV();
    await seed(kv, [["pilot-m1", ["mover", "mover", "junk"]]]);
    const flagged = await new LocalStore(kv).flag({ ra: 10, dec: 20, mjd: 60800, clock: "hours", source: "user" });
    kv.set(KEYS.voted, JSON.stringify({ "pilot-m1": "mover", other: "nonsense" }));
    const s = await loadSnapshot({ kv, api: localApi(kv), catalogue: async () => catalogue });
    expect(s.mode).toBe("local");
    expect(s.note).toBeNull();
    expect(s.votes).toHaveLength(3);
    expect(s.listed).toBe(3);
    expect(s.mine).toEqual({ "pilot-m1": "mover" });
    expect(s.info.get("pilot-p1")?.kind).toBe("high_pm");
    const found = await s.lookup([flagged.id, "pilot-p1", "nowhere"]);
    expect([...found.keys()]).toEqual([flagged.id]);
    expect(found.get(flagged.id)).toMatchObject({ kind: "flag", ra: 10, dec: 20, clock: "hours" });
  });

  it("Firestore: every vote from the vote API, flagged candidates looked up once", async () => {
    const allCandidates = vi.fn(async () => [
      { id: "fs-1", ra: 5, dec: -5, mjd: 60900, clock: "months" as const, source: "user" as const, created: null },
      { id: "sus-1", ra: 6, dec: -6, mjd: 60901, clock: "hours" as const, source: "pipeline" as const, created: null },
    ]);
    const api = async () => ({
      mode: "firestore" as const,
      notice: null,
      allVotes: async () => [
        { candidate_id: "fs-1", answer: "mover" as const, voter: "a_voter_123", ts: 1_700_000_000_000 },
        { candidate_id: "pilot-m1", answer: "junk" as const, voter: "a_voter_123", ts: 1_700_000_100_000 },
        { candidate_id: "pilot-m1", answer: "mover" as const, voter: "a_voter_123", ts: 1_700_000_200_000 },
      ],
      allCandidates,
    });
    const s = await loadSnapshot({ kv: memKV(), api, catalogue: async () => catalogue });
    expect(s.mode).toBe("firestore");
    expect(s.votes.map((v) => [v.candidate_id, v.answer])).toEqual([["fs-1", "mover"], ["pilot-m1", "junk"]]); // one vote per voter
    const a = await s.lookup(["fs-1", "pilot-m1"]);
    const b = await s.lookup(["sus-1"]);
    expect(allCandidates).toHaveBeenCalledTimes(1);
    expect(a.get("fs-1")).toMatchObject({ kind: "flag", ra: 5, clock: "months" });
    expect(a.has("pilot-m1")).toBe(false); // already known from candidates.json
    expect(b.get("sus-1")).toMatchObject({ kind: "mover" });
  });

  it("astronomersFor: the Community page's list for astronomers, flagged dots placed by lookup", async () => {
    const kv = memKV();
    await seed(kv, [
      ["pilot-m1", ["mover", "mover", "mover", "mover", "mover"]],
      ["pilot-p1", ["junk", "junk", "junk", "junk", "junk"]],
      ["pilot-m2", ["mover", "mover", "mover"]],
    ]);
    const flagged = await new LocalStore(kv).flag({ ra: 10, dec: 20, mjd: 60800, clock: "hours", source: "user" });
    await seed(kv, [[flagged.id, ["changing", "changing", "changing", "changing", "changing", "unsure"]]]);
    const s = await loadSnapshot({ kv, api: localApi(kv), catalogue: async () => catalogue });
    const rows = await astronomersFor(s);
    expect(rows.map((r) => [r.id, r.answer, r.kind])).toEqual([
      ["pilot-m1", "mover", "mover"],
      [flagged.id, "changing", "flag"],
    ]);
    expect(rows[1]).toMatchObject({ ra: 10, dec: 20, next: "mpc" });
  });

  it("turns a failed read into a note, never an error", async () => {
    const s = await loadSnapshot({
      kv: memKV(),
      api: async () => ({
        mode: "firestore" as const,
        notice: null,
        allVotes: async () => {
          throw new Error("Can't reach the vote server.");
        },
        allCandidates: async () => [],
      }),
      catalogue: async () => null,
    });
    expect(s.votes).toEqual([]);
    expect(s.note).toMatch(/Couldn't read the votes: Can't reach the vote server\./);
    expect(s.listed).toBeNull();
  });
});

describe("Community page", () => {
  async function mounted(plan: [string, Answer[]][], mine: Record<string, Answer> = {}) {
    const kv = memKV();
    await seed(kv, plan);
    kv.set(KEYS.voted, JSON.stringify(mine));
    const el = document.createElement("div");
    document.body.append(el);
    const c = ctx();
    const page = mountCommunityWith(el, c, { kv, load: (k) => loadSnapshot({ kv: k, api: localApi(k), catalogue: async () => catalogue }) });
    page.show(null);
    await page.ready();
    return { el, kv, page, c };
  }

  it("states what people have done, with a local-mode note", async () => {
    const { el } = await mounted([
      ["pilot-m1", ["mover", "mover", "mover", "mover", "mover"]],
      ["pilot-p1", ["mover", "junk", "unsure"]],
    ]);
    const lead = el.querySelector(".cm-lead")!.textContent!.replace(/\s+/g, " ");
    expect(lead).toContain("cast 8 votes on 2 of the 3 objects the pipeline listed to check");
    expect(lead).toContain("1 reached consensus, and 1 of them is on the list for astronomers");
    expect(el.querySelector(".cm-source")!.textContent).toMatch(/Local mode/);
  });

  it("lists consensus candidates with links and enables the exports", async () => {
    const { el } = await mounted([
      ["pilot-m1", ["mover", "mover", "mover", "mover", "junk"]],
      ["pilot-m2", ["junk", "junk", "junk", "junk", "junk"]],
    ]);
    const rows = el.querySelectorAll(".cm-table tbody tr");
    expect(rows).toHaveLength(1);
    expect(rows[0].querySelector("a.cm-cand")!.getAttribute("href")).toBe("#/c/pilot-m1");
    expect(rows[0].textContent).toContain("4 of 5 agree (80%)");
    expect(rows[0].textContent).toContain("Minor Planet Center");
    expect(el.querySelector<HTMLButtonElement>("[data-act=csv]")!.disabled).toBe(false);
    expect(el.querySelector<HTMLButtonElement>("[data-act=json]")!.disabled).toBe(false);
  });

  it("explains an empty list and keeps the exports off", async () => {
    const { el } = await mounted([["pilot-m1", ["mover", "junk", "mover"]]]);
    expect(el.querySelector(".cm-empty")!.textContent).toMatch(/Nothing has reached consensus/);
    expect(el.querySelector<HTMLButtonElement>("[data-act=csv]")!.disabled).toBe(true);
    expect(el.querySelector(".cm-debate a")!.getAttribute("href")).toBe("#/c/pilot-m1");
  });

  it("shows this browser's agreement rate", async () => {
    const { el } = await mounted(
      [
        ["pilot-m1", ["mover", "mover", "mover", "mover", "mover"]],
        ["pilot-m2", ["junk", "junk", "junk", "junk", "junk"]],
      ],
      { "pilot-m1": "mover", "pilot-m2": "mover", "pilot-p1": "unsure" },
    );
    const you = el.querySelector(".cm-you-body")!.textContent!.replace(/\s+/g, " ");
    expect(you).toContain("You've voted on 3 objects");
    expect(you).toContain("2 of them have reached consensus, and your answer matched it on 1 (50%)");
  });

  it("keeps a nickname on this device only", async () => {
    const { el, kv } = await mounted([]);
    const input = el.querySelector<HTMLInputElement>("#cm-nick-input")!;
    input.value = "  Plate   reader <b> ";
    el.querySelector<HTMLFormElement>(".cm-nick")!.dispatchEvent(new Event("submit", { cancelable: true }));
    expect(kv.get(NICKNAME_KEY)).toBe("Plate reader <b>");
    expect(el.querySelector(".cm-hello")!.innerHTML).toBe("Hello, Plate reader &lt;b&gt;.");
    el.querySelector<HTMLButtonElement>("[data-act=nick-clear]")!.click();
    expect(kv.get(NICKNAME_KEY)).toBe("");
    expect(el.querySelector(".cm-hello")).toBeNull();
  });

  it("opens Explore for a dot a visitor flagged", async () => {
    const kv = memKV();
    const store = new LocalStore(kv);
    const f = await store.flag({ ra: 10, dec: 20, mjd: 60800, clock: "hours", source: "user" });
    for (let i = 0; i < 5; i++) await store.vote(f.id, "mover", `flag_voter_${i}`);
    const el = document.createElement("div");
    const c = ctx();
    const page = mountCommunityWith(el, c, { kv, load: (k) => loadSnapshot({ kv: k, api: localApi(k), catalogue: async () => catalogue }) });
    page.show(null);
    await page.ready();
    const a = el.querySelector<HTMLAnchorElement>(".cm-table a[data-explore]")!;
    expect(a.textContent).toBe("Flagged dot");
    a.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
    expect(c.explore).toHaveBeenCalledWith({ ra: 10, dec: 20, clock: "hours" });
  });

  it("says when nobody has voted", async () => {
    const el = document.createElement("div");
    const empty: Snapshot = {
      mode: "firestore",
      note: null,
      votes: [],
      info: new Map(),
      listed: 194,
      dataRelease: null,
      mine: {},
      loadedAt: Date.now(),
      lookup: async () => new Map(),
    };
    const page = mountCommunityWith(el, ctx(), { kv: memKV(), load: async () => empty });
    page.show(null);
    await page.ready();
    expect(el.querySelector(".cm-lead")!.textContent).toMatch(/No votes yet\. The pipeline has listed 194 objects to check/);
    expect(el.querySelector(".cm-source")!.textContent).toMatch(/Everyone's votes/);
  });
});

describe("About page", () => {
  it("renders every section, the challenge table and the README acknowledgements", async () => {
    const el = document.createElement("div");
    const v: ValidationResponse = {
      generated: "2026-11-14",
      data_release: "SPHEREx DR1 + QR3",
      checks: [
        { id: "V1", claim: "Five test objects recovered", status: "pass", headline: "5 of 5", value: 5, unit: null, expected: "", note: null, rows: [] },
        { id: "V6", claim: "Fast stars", status: "pending", headline: "Cut", value: null, unit: null, expected: "", note: null, rows: [] },
        { id: "V8", claim: "Spectra", status: "partial", headline: "Ks 1.010", value: 1.01, unit: null, expected: "", note: null, rows: [] },
      ],
    };
    const page = mountAboutWith(el, ctx(), { validation: async () => v });
    await page.ready();
    for (const id of ["ab-what", "ab-challenge", "ab-how", "ab-limits", "ab-faq", "ab-credits", "ab-team"]) {
      expect(el.querySelector(`#${id}`), id).not.toBeNull();
    }
    expect(el.querySelectorAll(".ab-table tbody tr").length).toBeGreaterThanOrEqual(9);
    expect(el.textContent).toContain(
      "This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by the National Aeronautics and Space Administration and operated by the California Institute of Technology.",
    );
    expect(el.querySelector(".ab-validation")!.textContent!.replace(/\s+/g, " ")).toContain("1 of 3 checks pass, 1 is partial and 1 is pending");
    const faq = [...el.querySelectorAll(".ab-faq h3")].map((h) => h.textContent);
    expect(faq).toEqual(["Do I need an account?", 'What does "mover" mean?', "Why are the images negative?", "How are votes used?", "Is my data stored?"]);
  });
});
