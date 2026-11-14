import { describe, expect, it } from "vitest";
import rules from "../../../infra/firebase/firestore.rules?raw";
import {
  BrowserMemory,
  CSV_COLUMNS,
  KEYS,
  VOTER_RE,
  VoteError,
  candidateDoc,
  candidateFields,
  consensus,
  countVotes,
  csvCell,
  fieldRadiusArcsec,
  nearby,
  toCandidate,
  toCsv,
  toMillis,
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
    for (const a of ["mover", "changing", "junk", "unsure"] as const) expect(voteDoc("abc", a, "voter_12345", 0).answer).toBe(a);
    expect(rules).toContain("['mover', 'changing', 'junk', 'unsure']");
    expect(rules).toContain("^[A-Za-z0-9_-]{8,64}$");
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
