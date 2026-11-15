import { describe, expect, it } from "vitest";
import rules from "../../../infra/firebase/firestore.rules?raw";
import {
  ANSWERS,
  ANSWER_TEXT,
  BrowserMemory,
  CSV_COLUMNS,
  KEYS,
  STORED_ANSWERS,
  VOTER_RE,
  VoteError,
  WITHDRAWN,
  candidateDoc,
  candidateFields,
  consensus,
  countVotes,
  csvCell,
  fieldRadiusArcsec,
  latestVotes,
  nearby,
  shiftCounts,
  toCandidate,
  toCsv,
  toMillis,
  toStoredVote,
  toVoteRecord,
  voteDoc,
  voterId,
  type Candidate,
  type KV,
} from "./model";

function memKV(init: Record<string, string> = {}): KV & { data: Map<string, string> } {
  const data = new Map(Object.entries(init));
  return { data, get: (k) => data.get(k) ?? null, set: (k, v) => void data.set(k, v) };
}

/** Field lists from the rules file's hasOnly([...]) clauses, in order: candidates, votes. */
function ruleFields(): string[][] {
  return [...rules.matchAll(/hasOnly\(\[([^\]]+)\]\)/g)].map((m) =>
    m[1].split(",").map((s) => s.trim().replace(/'/g, "")).sort(),
  );
}

/** The answers the rules allow on a vote (`answer in [...]`). */
function ruleAnswers(): string[] {
  const m = /data\.answer in \[([^\]]+)\]/.exec(rules);
  return m ? m[1].split(",").map((s) => s.trim().replace(/'/g, "")) : [];
}

const pick = { ra: 269.4521, dec: 4.6933, mjd: 60800.25, clock: "months" as const };

describe("document shapes (contracts/api.md, firestore.rules)", () => {
  it("candidate has exactly ra, dec, mjd, clock, source, created (v2.1)", () => {
    const ts = { sentinel: true };
    const d = candidateDoc({ ...pick, det: 2, epoch_id: "v1" } as typeof pick, ts);
    expect(Object.keys(d).sort()).toEqual(["clock", "created", "dec", "mjd", "ra", "source"]);
    expect(d).toEqual({ ...pick, source: "user", created: ts });
    expect(d.created).toBe(ts);
    expect(Object.keys(d).sort()).toEqual(ruleFields()[0]);
    expect(rules).toContain("request.resource.data.created == request.time");
  });

  it("vote has exactly candidate_id, answer, ts, voter", () => {
    const ts = { sentinel: true };
    const d = voteDoc("abc123", "mover", "voter_12345", ts);
    expect(Object.keys(d).sort()).toEqual(["answer", "candidate_id", "ts", "voter"]);
    expect(d.ts).toBe(ts);
    expect(Object.keys(d).sort()).toEqual(ruleFields()[1]);
  });

  it("follows the rules' ranges and values", () => {
    expect(candidateFields({ ...pick, ra: -10 }).ra).toBeCloseTo(350);
    expect(() => candidateFields({ ...pick, dec: 91 })).toThrow(VoteError);
    expect(() => candidateDoc({ ...pick, mjd: 59000 }, 0)).toThrow(VoteError);
    expect(() => candidateFields({ ...pick, clock: "days" as never })).toThrow(VoteError);
    expect(() => candidateFields({ ...pick, ra: NaN })).toThrow(VoteError);
    expect(() => voteDoc("", "mover", "voter_12345", 0)).toThrow(VoteError);
    expect(() => voteDoc("a".repeat(129), "mover", "voter_12345", 0)).toThrow(VoteError);
    expect(() => voteDoc("abc", "maybe" as never, "voter_12345", 0)).toThrow(VoteError);
    expect(() => voteDoc("abc", "junk", "me@mail.com", 0)).toThrow(VoteError);
    for (const a of ["mover", "changing", "junk", "unsure", "withdrawn"] as const) expect(voteDoc("abc", a, "voter_12345", 0).answer).toBe(a);
    expect(rules).toContain("^[A-Za-z0-9_-]{8,64}$");
  });

  it("allows the same five stored answers as the rules (v3.16: withdrawn takes a vote back)", () => {
    expect(ruleAnswers()).toEqual([...STORED_ANSWERS]);
    expect(STORED_ANSWERS).toEqual(["mover", "changing", "junk", "unsure", "withdrawn"]);
    expect(WITHDRAWN).toBe("withdrawn");
    // The rules stay create-only.
    expect(rules.match(/allow update, delete: if false;/g)).toHaveLength(2);
  });

  it("shows four answers: withdrawn is stored only, never a label", () => {
    expect(ANSWERS).toEqual(["mover", "changing", "junk", "unsure"]);
    expect(Object.keys(ANSWER_TEXT)).toEqual([...ANSWERS]);
    expect(ANSWERS as readonly string[]).not.toContain("withdrawn");
  });
});

describe("created", () => {
  it("reads Firestore Timestamps, Dates, ms and ISO strings; anything else is null", () => {
    expect(toMillis({ toMillis: () => 1700000000000 })).toBe(1700000000000);
    expect(toMillis(new Date(1700000000000))).toBe(1700000000000);
    expect(toMillis(1700000000000)).toBe(1700000000000);
    expect(toMillis("2026-11-14T13:00:00.000Z")).toBe(Date.UTC(2026, 10, 14, 13));
    for (const bad of [undefined, null, "soon", NaN, {}, { toMillis: () => "x" }, new Date(NaN)]) expect(toMillis(bad)).toBeNull();
  });
  it("is null on candidates made before v2.1", () => {
    const base = { ra: 1, dec: 2, mjd: 60800, clock: "months", source: "user" };
    expect(toCandidate("old", base)?.created).toBeNull();
    expect(toCandidate("new", { ...base, created: { toMillis: () => 5 } })?.created).toBe(5);
  });
});

describe("voter id", () => {
  it("is made once and kept", () => {
    const kv = memKV();
    const a = voterId(kv);
    expect(a).toMatch(VOTER_RE);
    expect(kv.data.get(KEYS.voter)).toBe(a);
    expect(voterId(kv)).toBe(a);
    expect(new BrowserMemory(kv).voter).toBe(a);
  });
  it("differs between browsers and replaces a broken one", () => {
    expect(voterId(memKV())).not.toBe(voterId(memKV()));
    const kv = memKV({ [KEYS.voter]: "bad id!" });
    expect(voterId(kv)).toMatch(VOTER_RE);
  });
});

describe("browser memory", () => {
  it("remembers one vote per candidate and flagged times", () => {
    const kv = memKV();
    const m = new BrowserMemory(kv);
    expect(m.votedFor("c1")).toBeNull();
    m.rememberVote("c1", "junk");
    expect(new BrowserMemory(kv).votedFor("c1")).toBe("junk");
    m.rememberFlag("c2", 123);
    expect(new BrowserMemory(kv).flaggedAt("c2")).toBe(123);
  });
  it("follows the latest answer: a change replaces it, a take-back forgets it", () => {
    const kv = memKV();
    const m = new BrowserMemory(kv);
    m.rememberVote("c1", "junk");
    m.rememberVote("c2", "mover");
    m.rememberVote("c1", "changing");
    expect(m.votedFor("c1")).toBe("changing");
    m.forgetVote("c1");
    expect(new BrowserMemory(kv).votedFor("c1")).toBeNull();
    expect(JSON.parse(kv.data.get(KEYS.voted)!)).toEqual({ c2: "mover" });
    m.forgetVote("nothing-here");
    expect(m.votedFor("c2")).toBe("mover");
  });
  it("never reads withdrawn back as an answer", () => {
    expect(new BrowserMemory(memKV({ [KEYS.voted]: JSON.stringify({ c1: "withdrawn" }) })).votedFor("c1")).toBeNull();
  });
  it("survives junk in storage", () => {
    const m = new BrowserMemory(memKV({ [KEYS.voted]: "{nope", [KEYS.flagged]: "[1,2]" }));
    expect(m.votedFor("x")).toBeNull();
    expect(m.flaggedAt("x")).toBeNull();
  });
});

describe("counts", () => {
  it("counts per candidate and answer, ignoring unknown answers", () => {
    const c = countVotes([
      { candidate_id: "a", answer: "mover" },
      { candidate_id: "a", answer: "mover" },
      { candidate_id: "a", answer: "junk" },
      { candidate_id: "b", answer: "unsure" },
      { candidate_id: "b", answer: "nope" as never },
    ]);
    expect(c.get("a")).toEqual({ mover: 2, changing: 0, junk: 1, unsure: 0 });
    expect(c.get("b")).toEqual({ mover: 0, changing: 0, junk: 0, unsure: 1 });
    expect(c.get("z")).toBeUndefined();
  });
});

describe("each voter's latest vote counts (contracts/api.md v3.16)", () => {
  const v = (candidate_id: string, answer: string, voter: string, ts: unknown) => ({ candidate_id, answer, voter, ts });

  it("a change from A to B counts once, as B", () => {
    const c = countVotes([v("a", "mover", "voter_one", 1000), v("a", "junk", "voter_one", 2000)]);
    expect(c.get("a")).toEqual({ mover: 0, changing: 0, junk: 1, unsure: 0 });
  });

  it("a take-back counts as no vote, and a vote after it counts again", () => {
    expect(countVotes([v("a", "mover", "voter_one", 1000), v("a", "withdrawn", "voter_one", 2000)]).get("a")).toBeUndefined();
    const again = countVotes([v("a", "mover", "voter_one", 1000), v("a", "withdrawn", "voter_one", 2000), v("a", "unsure", "voter_one", 3000)]);
    expect(again.get("a")).toEqual({ mover: 0, changing: 0, junk: 0, unsure: 1 });
  });

  it("goes by ts, not by the order documents are read in", () => {
    const c = countVotes([v("a", "junk", "voter_one", 2000), v("a", "mover", "voter_one", 1000)]);
    expect(c.get("a")).toEqual({ mover: 0, changing: 0, junk: 1, unsure: 0 });
  });

  it("a pending ts (null, not set yet) is the latest", () => {
    expect(countVotes([v("a", "withdrawn", "voter_one", null), v("a", "mover", "voter_one", 5000)]).get("a")).toBeUndefined();
    expect(countVotes([v("a", "junk", "voter_one", { toMillis: () => 9000 }), v("a", "changing", "voter_one", undefined)]).get("a")).toEqual({
      mover: 0,
      changing: 1,
      junk: 0,
      unsure: 0,
    });
  });

  it("a tie in ts goes to the later document", () => {
    expect(countVotes([v("a", "mover", "voter_one", 1000), v("a", "junk", "voter_one", 1000)]).get("a")?.junk).toBe(1);
  });

  it("keeps voters and candidates apart", () => {
    const c = countVotes([
      v("a", "mover", "voter_one", 1000),
      v("a", "mover", "voter_two", 1100),
      v("a", "junk", "voter_two", 1200), // voter two changes: mover → junk
      v("b", "mover", "voter_one", 1300), // the same voter on another candidate counts on its own
      v("a", "withdrawn", "voter_three", 1400), // a take-back with nothing before it takes nothing
    ]);
    expect(c.get("a")).toEqual({ mover: 1, changing: 0, junk: 1, unsure: 0 });
    expect(c.get("b")).toEqual({ mover: 1, changing: 0, junk: 0, unsure: 0 });
  });

  it("latestVotes keeps the input order and drops withdrawn and unknown answers", () => {
    const rows = [
      v("a", "mover", "voter_one", 1),
      v("b", "maybe", "voter_one", 2),
      v("a", "junk", "voter_two", 3),
      v("b", "withdrawn", "voter_two", 4),
      v("a", "changing", "voter_one", 5),
    ];
    expect(latestVotes(rows)).toEqual([rows[2], rows[4]]);
  });

  it("shifts counts by one browser's change, never below zero", () => {
    const k = { mover: 2, changing: 0, junk: 1, unsure: 0 };
    expect(shiftCounts(k, null, "mover")).toEqual({ mover: 3, changing: 0, junk: 1, unsure: 0 });
    expect(shiftCounts(k, "mover", "junk")).toEqual({ mover: 1, changing: 0, junk: 2, unsure: 0 });
    expect(shiftCounts(k, "junk", null)).toEqual({ mover: 2, changing: 0, junk: 0, unsure: 0 });
    expect(shiftCounts(null, "unsure", "changing")).toEqual({ mover: 0, changing: 1, junk: 0, unsure: 0 });
    expect(k).toEqual({ mover: 2, changing: 0, junk: 1, unsure: 0 }); // a copy, never the input
  });
});

describe("consensus (DESIGN.md: ≥ 5 votes, ≥ 70 % on one answer)", () => {
  const c = (mover: number, changing = 0, junk = 0, unsure = 0) => ({ mover, changing, junk, unsure });
  it("needs five votes", () => {
    expect(consensus(c(4))).toBeNull();
    expect(consensus(c(5))).toEqual({ answer: "mover", share: 1, votes: 5 });
  });
  it("needs 70 % on one answer", () => {
    expect(consensus(c(7, 0, 3))).toEqual({ answer: "mover", share: 0.7, votes: 10 });
    expect(consensus(c(6, 0, 4))).toBeNull();
    expect(consensus(c(0, 1, 8, 1))?.answer).toBe("junk");
  });
});

describe("vote records", () => {
  it("read back defensively", () => {
    expect(toVoteRecord({ candidate_id: "a", answer: "junk", ts: { toMillis: () => 9 }, voter: "v_12345678" })).toEqual({
      candidate_id: "a",
      answer: "junk",
      ts: 9,
      voter: "v_12345678",
    });
    expect(toVoteRecord({ candidate_id: "a", answer: "junk", ts: null })?.ts).toBeNull();
    expect(toVoteRecord({ candidate_id: "a", answer: "maybe" })).toBeNull();
    expect(toVoteRecord({ answer: "junk" })).toBeNull();
    expect(toVoteRecord("x")).toBeNull();
  });
  it("keep withdrawn as a stored vote, never as an answer", () => {
    const d = { candidate_id: "a", answer: "withdrawn", ts: 5, voter: "v_12345678" };
    expect(toStoredVote(d)).toEqual(d);
    expect(toVoteRecord(d)).toBeNull();
    expect(toStoredVote({ candidate_id: "a", answer: "maybe" })).toBeNull();
  });
});

describe("nearby", () => {
  const view = { ra: 269.45, dec: 4.69, fov: 5 }; // 5′ field: radius 212″
  const c = (id: string, dra: number, ddec: number, mjd: number, created: number | null = null): Candidate => ({
    id,
    ra: view.ra + dra,
    dec: view.dec + ddec,
    mjd,
    clock: "months",
    source: "user",
    created,
  });

  it("uses half the field's diagonal", () => {
    expect(fieldRadiusArcsec({ fov: 5 })).toBeCloseTo(212.13, 1);
  });

  it("keeps candidates inside the field, newest first by created, docs without it last", () => {
    const list = [
      c("legacy-old", 0, 0.01, 60500),
      c("legacy-new", 0.01, 0, 60900),
      c("far", 0, 0.1, 60950, 9e12),
      c("made-1", 0, -0.02, 60100, 1000),
      c("made-2", 0, 0.02, 60000, 2000),
    ];
    expect(nearby(list, view).map((x) => x.id)).toEqual(["made-2", "made-1", "legacy-new", "legacy-old"]);
  });

  it("puts this browser's own flags first, latest first", () => {
    const list = [c("a", 0, 0, 60900), c("b", 0, 0, 60500), c("d", 0, 0, 60400)];
    const at: Record<string, number> = { b: 10, d: 20 };
    expect(nearby(list, view, (id) => at[id] ?? null).map((x) => x.id)).toEqual(["d", "b", "a"]);
  });

  it("works across RA 0/360", () => {
    const v = { ra: 0.001, dec: 0, fov: 5 };
    const x: Candidate = { id: "w", ra: 359.999, dec: 0, mjd: 60800, clock: "hours", source: "pipeline", created: null };
    expect(nearby([x], v)).toHaveLength(1);
  });
});

describe("CSV", () => {
  it("escapes quotes, commas, newlines and formula starts", () => {
    expect(csvCell("plain")).toBe("plain");
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell("a,b")).toBe('"a,b"');
    expect(csvCell("line\nbreak")).toBe('"line\nbreak"');
    expect(csvCell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvCell("-1+2")).toBe("'-1+2");
    expect(csvCell(-4.5)).toBe("-4.5");
    expect(csvCell(null)).toBe("");
    expect(csvCell(NaN)).toBe("");
  });

  it("writes one row per candidate with counts", () => {
    const cands: Candidate[] = [
      { id: "x,1", ra: 269.4521234567, dec: -4.6933, mjd: 60800.5, clock: "months", source: "user", created: Date.UTC(2026, 10, 14, 13) },
      { id: "y", ra: 10, dec: 20, mjd: 60900, clock: "hours", source: "pipeline", created: null },
    ];
    const csv = toCsv(cands, new Map([["x,1", { mover: 3, changing: 1, junk: 0, unsure: 2 }]]));
    const lines = csv.trimEnd().split("\r\n");
    expect(lines[0]).toBe(CSV_COLUMNS.join(","));
    expect(lines).toHaveLength(3);
    expect(lines[1].startsWith('"x,1",user,269.452123,-4.6933,60800.5,"')).toBe(true);
    expect(lines[1].endsWith(",months,3,1,0,2,6,2026-11-14T13:00:00.000Z")).toBe(true);
    expect(lines[2].endsWith(",hours,0,0,0,0,0,")).toBe(true);
  });
});
