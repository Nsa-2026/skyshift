import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Answer, Counts } from "../vote/model";
import {
  ASTRO_CSV_COLUMNS,
  CONSENSUS,
  activity,
  agreement,
  astroFile,
  astronomersList,
  candidateLink,
  cleanVotes,
  floorTo,
  mostDebated,
  nextStep,
  summarize,
  tally,
  toAstroCsv,
  toAstroJson,
  verdict,
  type CandInfo,
  type VoteRow,
} from "./stats";

const c = (mover = 0, changing = 0, junk = 0, unsure = 0): Counts => ({ mover, changing, junk, unsure });
const HOUR = 3_600_000;
const T0 = Date.UTC(2026, 10, 14, 9, 0, 0); // Sat 14 Nov 2026, 09:00 UTC

/** n votes on one candidate, one voter each, an answer per vote. */
function votesFor(id: string, answers: Answer[], t = T0, prefix = "v"): VoteRow[] {
  return answers.map((answer, i) => ({ candidate_id: id, answer, ts: t + i * 60_000, voter: `${prefix}${i}_voter` }));
}

/** RFC 4180 rows → cells (quoted cells may hold commas). */
function parseCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let q = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (q) {
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') q = false;
      else cur += ch;
    } else if (ch === '"') q = true;
    else if (ch === ",") {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  out.push(cur);
  return out;
}

const realTZ = process.env.TZ;
beforeAll(() => {
  process.env.TZ = "UTC";
});
afterAll(() => {
  process.env.TZ = realTZ;
});

describe("verdict: consensus is ≥ 5 votes and ≥ 70% on one answer", () => {
  it("no votes: unreviewed", () => {
    expect(verdict(undefined).state).toBe("unreviewed");
    expect(verdict(c()).state).toBe("unreviewed");
  });

  it("needs five votes, however unanimous", () => {
    const v = verdict(c(4));
    expect(v).toMatchObject({ state: "in_review", answer: null, leading: "mover", share: 1, votes: 4 });
    expect(verdict(c(5))).toMatchObject({ state: "consensus", answer: "mover", top: 5 });
  });

  it("70% exactly is enough; just under is not", () => {
    expect(verdict(c(7, 0, 3)).state).toBe("consensus"); // 7 of 10
    expect(verdict(c(0, 14, 6)).answer).toBe("changing"); // 14 of 20
    expect(verdict(c(4, 0, 2)).state).toBe("in_review"); // 4 of 6 = 66.7%
    expect(verdict(c(6, 0, 3)).state).toBe("in_review"); // 6 of 9 = 66.7%
    expect(verdict(c(0, 0, 4, 1)).answer).toBe("junk"); // 4 of 5 = 80%
  });

  it("a tie at the top has no leading answer", () => {
    expect(verdict(c(3, 0, 3))).toMatchObject({ state: "in_review", leading: null, share: 0.5 });
  });

  it("the rule's numbers are the ones in DESIGN.md", () => {
    expect(CONSENSUS).toEqual({ minVotes: 5, share: 0.7 });
  });
});

describe("cleanVotes", () => {
  it("drops malformed rows and keeps one vote per voter and candidate (the earliest)", () => {
    const rows = cleanVotes([
      { candidate_id: "a", answer: "junk", voter: "x_voter_1", ts: 2000 },
      { candidate_id: "a", answer: "mover", voter: "x_voter_1", ts: 1000 },
      { candidate_id: "a", answer: "maybe", voter: "y_voter_1", ts: 1000 },
      { candidate_id: "", answer: "mover", voter: "y_voter_1", ts: 1000 },
      { candidate_id: "b", answer: "unsure", voter: "y_voter_1", ts: null },
      { candidate_id: "b", answer: "mover", voter: 7, ts: 1 },
      { candidate_id: "c", answer: "mover", voter: "z_voter_1", ts: { toMillis: () => 3000 } },
      null,
      "nope",
    ]);
    expect(rows).toEqual([
      { candidate_id: "a", answer: "mover", voter: "x_voter_1", ts: 1000 },
      { candidate_id: "c", answer: "mover", voter: "z_voter_1", ts: 3000 },
      { candidate_id: "b", answer: "unsure", voter: "y_voter_1", ts: null },
    ]);
  });
});

describe("summarize", () => {
  it("counts votes, candidates, distinct voters and consensus by answer", () => {
    const votes = [
      ...votesFor("m1", ["mover", "mover", "mover", "mover", "junk"]), // consensus mover (80%)
      ...votesFor("ch", ["changing", "changing", "changing", "changing", "changing"], T0, "w"), // consensus changing
      ...votesFor("j1", ["junk", "junk", "junk", "junk", "junk", "mover"], T0, "u"), // consensus junk
      ...votesFor("open", ["mover", "junk"]), // in review
    ];
    const s = summarize(votes);
    expect(s.votes).toBe(18);
    expect(s.reviewed).toBe(4);
    // voters v0..v4 (shared by m1 and open), w0..w4, u0..u5
    expect(s.people).toBe(16);
    expect(s.consensus).toBe(3);
    expect(s.consensusBy).toEqual(c(1, 1, 1, 0));
    expect(s.forAstronomers).toBe(2);
    expect(s.inReview).toBe(1);
    expect(s.first).toBe(T0);
    expect(s.last).toBe(T0 + 5 * 60_000);
  });

  it("is all zeros without votes", () => {
    expect(summarize([])).toMatchObject({ votes: 0, reviewed: 0, people: 0, consensus: 0, first: null, last: null });
  });
});

describe("activity", () => {
  it("bins by the hour up to now, with at least twelve bins", () => {
    const votes: VoteRow[] = [
      { candidate_id: "a", answer: "mover", voter: "p_voter_1", ts: T0 + 5 * 60_000 },
      { candidate_id: "a", answer: "junk", voter: "p_voter_2", ts: T0 + 50 * 60_000 },
      { candidate_id: "b", answer: "junk", voter: "p_voter_2", ts: T0 + 2 * HOUR + 1 },
      { candidate_id: "b", answer: "junk", voter: "p_voter_3", ts: null },
    ];
    const a = activity(votes, T0 + 3 * HOUR + 10);
    expect(a.unit).toBe("hour");
    expect(a.undated).toBe(1);
    expect(a.bins.length).toBe(12);
    const last = a.bins[a.bins.length - 1];
    expect(last.start).toBe(T0 + 3 * HOUR);
    const at = (t: number) => a.bins.find((b) => b.start === t)?.count;
    expect(at(T0)).toBe(2);
    expect(at(T0 + HOUR)).toBe(0);
    expect(at(T0 + 2 * HOUR)).toBe(1);
    expect(a.bins.reduce((s, b) => s + b.count, 0)).toBe(3);
    // contiguous hours
    a.bins.slice(1).forEach((b, i) => expect(b.start - a.bins[i].start).toBe(HOUR));
  });

  it("switches to days past two days, capped at sixty", () => {
    const votes: VoteRow[] = [
      { candidate_id: "a", answer: "mover", voter: "p_voter_1", ts: T0 - 100 * 24 * HOUR },
      { candidate_id: "a", answer: "mover", voter: "p_voter_2", ts: T0 - 3 * 24 * HOUR },
      { candidate_id: "a", answer: "mover", voter: "p_voter_3", ts: T0 },
    ];
    const a = activity(votes, T0 + HOUR);
    expect(a.unit).toBe("day");
    expect(a.bins.length).toBe(60);
    expect(a.bins[a.bins.length - 1].start).toBe(floorTo(T0, "day"));
    expect(a.bins.reduce((s, b) => s + b.count, 0)).toBe(2); // the vote 100 days ago is outside the window
  });

  it("is empty without dated votes", () => {
    expect(activity([], T0).bins).toEqual([]);
  });
});

describe("mostDebated", () => {
  it("lists split candidates not at consensus, most evenly split first", () => {
    const counts = new Map<string, Counts>([
      ["even3", c(2, 0, 2, 2)], // share 1/3
      ["half", c(3, 0, 3)], // 1/2
      ["half-more", c(4, 0, 4)], // 1/2 with more votes
      ["settled", c(8, 0, 1)], // consensus
      ["one-sided", c(4)], // no disagreement
      ["few", c(1, 1)], // under 3 votes
    ]);
    expect(mostDebated(counts).map((d) => d.id)).toEqual(["even3", "half-more", "half"]);
    expect(mostDebated(counts, { limit: 1 }).map((d) => d.id)).toEqual(["even3"]);
  });
});

describe("agreement (this browser)", () => {
  it("compares this browser's answers with consensus", () => {
    const counts = new Map<string, Counts>([
      ["a", c(5)], // consensus mover
      ["b", c(0, 0, 5)], // consensus junk
      ["d", c(0, 0, 4, 1)], // consensus junk
      ["e", c(2, 0, 2)], // open
    ]);
    const a = agreement({ a: "mover", b: "mover", d: "junk", e: "junk", f: "unsure" }, counts);
    expect(a).toMatchObject({ voted: 5, settled: 3, agreed: 2 });
    expect(a.rate).toBeCloseTo(2 / 3);
    expect(a.answers).toEqual(c(2, 0, 2, 1));
  });

  it("has no rate before anything settles", () => {
    expect(agreement({ x: "mover" }, new Map()).rate).toBeNull();
    expect(agreement({}, new Map())).toMatchObject({ voted: 0, rate: null });
  });
});

describe("the list for astronomers", () => {
  const info: Record<string, CandInfo> = {
    "pilot-1": { id: "pilot-1", kind: "mover", ra: 122.5541536, dec: 17.0035701, mjd: 60974.0935626, clock: "hours", det: 3, why: "Seen twice." },
    "pilot-2": { id: "pilot-2", kind: "high_pm", ra: 120.1, dec: -5.25, mjd: 61141.2, clock: "months", det: 6, why: "Moves, no Gaia star." },
    fs9: { id: "fs9", kind: "flag", ra: 10, dec: 20, mjd: 60800, clock: "hours", det: null, why: null },
  };
  const votes = [
    ...votesFor("pilot-1", ["mover", "mover", "mover", "mover", "mover"]),
    ...votesFor("pilot-2", ["mover", "mover", "mover", "mover", "junk", "mover", "mover"], T0, "q"),
    ...votesFor("fs9", ["changing", "changing", "changing", "changing", "changing", "unsure"], T0, "r"),
    ...votesFor("junk-1", ["junk", "junk", "junk", "junk", "junk"], T0, "s"),
    ...votesFor("open", ["mover", "mover", "mover", "mover"], T0, "t"),
  ];
  const rows = astronomersList(tally(votes), (id) => info[id]);

  it("holds consensus movers and changing sources only, strongest agreement first", () => {
    expect(rows.map((r) => [r.id, r.answer, r.agree, r.votes])).toEqual([
      ["pilot-1", "mover", 5, 5],
      ["pilot-2", "mover", 6, 7],
      ["fs9", "changing", 5, 6],
    ]);
  });

  it("routes solar-system movers to the MPC and the rest to Backyard Worlds", () => {
    expect(rows.map((r) => r.next)).toEqual(["mpc", "backyard_worlds", "mpc"]);
    expect(nextStep(undefined)).toBe("backyard_worlds");
    expect(nextStep({ kind: "spectral", clock: "months" })).toBe("backyard_worlds");
  });

  it("exports CSV with a header, one row each, and links to the candidate page", () => {
    const csv = toAstroCsv(rows, "https://skyshift.web.app/?ra=1&dec=2#/community");
    const lines = csv.trimEnd().split("\r\n");
    expect(lines[0]).toBe(ASTRO_CSV_COLUMNS.join(","));
    expect(lines).toHaveLength(4);
    const first = parseCsvLine(lines[1]);
    expect(first).toHaveLength(ASTRO_CSV_COLUMNS.length);
    const col = (name: (typeof ASTRO_CSV_COLUMNS)[number]) => first[ASTRO_CSV_COLUMNS.indexOf(name)];
    expect(col("id")).toBe("pilot-1");
    expect(col("kind")).toBe("mover");
    expect(col("consensus")).toBe("mover");
    expect(col("agree_pct")).toBe("100");
    expect(col("ra_deg")).toBe("122.554154");
    expect(col("dec_deg")).toBe("17.00357");
    expect(col("date_utc")).toBe("26 Oct 2025, 02:14 UTC");
    expect(col("next_step")).toBe("Minor Planet Center");
    expect(col("link")).toBe("https://skyshift.web.app/#/c/pilot-1");
    expect(csv.endsWith("\r\n")).toBe(true);
  });

  it("exports JSON with the rule, the source of the votes and every row", () => {
    const j = JSON.parse(toAstroJson(rows, { generated: "2026-11-14T15:00:00.000Z", votes_from: "local", data_release: "DR1+QR3" }, "https://skyshift.web.app/"));
    expect(j.rule).toEqual({ min_votes: 5, min_agree_pct: 70, answers: ["mover", "changing"] });
    expect(j.votes_from).toBe("local");
    expect(j.count).toBe(3);
    expect(j.candidates[1]).toMatchObject({
      id: "pilot-2",
      kind: "high_pm",
      consensus: "mover",
      votes: 7,
      agree: 6,
      agree_pct: 86,
      counts: { mover: 6, changing: 0, junk: 1, unsure: 0 },
      ra_deg: 120.1,
      dec_deg: -5.25,
      clock: "months",
      det: 6,
      next_step: "backyard_worlds",
      link: "https://skyshift.web.app/#/c/pilot-2",
    });
    expect(j.note).toMatch(/not confirmed discoveries/);
  });

  it("keeps rows whose position isn't known, with empty cells", () => {
    const r = astronomersList(tally(votesFor("lost", ["mover", "mover", "mover", "mover", "mover"])), () => undefined);
    expect(r[0]).toMatchObject({ id: "lost", kind: null, ra: null, next: "backyard_worlds" });
    const line = parseCsvLine(toAstroCsv(r, "https://x.test/").split("\r\n")[1]);
    expect(line).toEqual(["lost", "", "mover", "5", "5", "100", "5", "0", "0", "0", "", "", "", "", "", "", "Backyard Worlds", "https://x.test/#/c/lost"]);
  });

  it("encodes ids in links", () => {
    expect(candidateLink("https://x.test/a/?q=1#/about", "a b/c")).toBe("https://x.test/a/#/c/a%20b%2Fc");
  });

  it("makes the same file for the Community page and To check: name dated by generation, CSV or JSON", () => {
    const meta = { generated: "2026-11-15T00:30:00.000Z", votes_from: "firestore" as const, data_release: "DR1+QR3" };
    const csv = astroFile("csv", rows, meta, "https://skyshift.web.app/#/candidates/one=1");
    expect(csv).toMatchObject({ name: "skyshift-list-for-astronomers-2026-11-15.csv", type: "text/csv;charset=utf-8" });
    expect(csv.text).toBe(toAstroCsv(rows, "https://skyshift.web.app/#/candidates/one=1"));
    const json = astroFile("json", rows, meta, "https://skyshift.web.app/");
    expect(json).toMatchObject({ name: "skyshift-list-for-astronomers-2026-11-15.json", type: "application/json" });
    expect(JSON.parse(json.text).count).toBe(3);
  });
});
