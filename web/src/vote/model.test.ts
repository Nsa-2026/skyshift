import { describe, expect, it } from "vitest";
import rules from "../../../infra/firebase/firestore.rules?raw";
import {
  BrowserMemory,
  CSV_COLUMNS,
  KEYS,
  VOTER_RE,
  VoteError,
  candidateDoc,
  countVotes,
  csvCell,
  fieldRadiusArcsec,
  nearby,
  toCsv,
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
  it("candidate has exactly ra, dec, mjd, clock, source", () => {
    const d = candidateDoc({ ...pick, det: 2, epoch_id: "v1" } as typeof pick);
    expect(Object.keys(d).sort()).toEqual(["clock", "dec", "mjd", "ra", "source"]);
    expect(d).toEqual({ ...pick, source: "user" });
    expect(Object.keys(d).sort()).toEqual(ruleFields()[0]);
  });

  it("vote has exactly candidate_id, answer, ts, voter", () => {
    const ts = { sentinel: true };
    const d = voteDoc("abc123", "mover", "voter_12345", ts);
    expect(Object.keys(d).sort()).toEqual(["answer", "candidate_id", "ts", "voter"]);
    expect(d.ts).toBe(ts);
    expect(Object.keys(d).sort()).toEqual(ruleFields()[1]);
  });

  it("follows the rules' ranges and values", () => {
    expect(candidateDoc({ ...pick, ra: -10 }).ra).toBeCloseTo(350);
    expect(() => candidateDoc({ ...pick, dec: 91 })).toThrow(VoteError);
    expect(() => candidateDoc({ ...pick, mjd: 59000 })).toThrow(VoteError);
    expect(() => candidateDoc({ ...pick, clock: "days" as never })).toThrow(VoteError);
    expect(() => candidateDoc({ ...pick, ra: NaN })).toThrow(VoteError);
    expect(() => voteDoc("", "mover", "voter_12345", 0)).toThrow(VoteError);
    expect(() => voteDoc("a".repeat(129), "mover", "voter_12345", 0)).toThrow(VoteError);
    expect(() => voteDoc("abc", "maybe" as never, "voter_12345", 0)).toThrow(VoteError);
    expect(() => voteDoc("abc", "junk", "me@mail.com", 0)).toThrow(VoteError);
    for (const a of ["mover", "changing", "junk", "unsure"] as const) expect(voteDoc("abc", a, "voter_12345", 0).answer).toBe(a);
    expect(rules).toContain("['mover', 'changing', 'junk', 'unsure']");
    expect(rules).toContain("^[A-Za-z0-9_-]{8,64}$");
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

describe("nearby", () => {
  const view = { ra: 269.45, dec: 4.69, fov: 5 }; // 5′ field: radius 212″
  const c = (id: string, dra: number, ddec: number, mjd: number): Candidate => ({
    id,
    ra: view.ra + dra,
    dec: view.dec + ddec,
    mjd,
    clock: "months",
    source: "user",
  });

  it("uses half the field's diagonal", () => {
    expect(fieldRadiusArcsec({ fov: 5 })).toBeCloseTo(212.13, 1);
  });

  it("keeps candidates inside the field, newest first", () => {
    const list = [c("old", 0, 0.01, 60500), c("new", 0.01, 0, 60900), c("far", 0, 0.1, 60950), c("pipe", 0, -0.02, 60700)];
    expect(nearby(list, view).map((x) => x.id)).toEqual(["new", "pipe", "old"]);
  });

  it("puts this browser's own flags first, latest first", () => {
    const list = [c("a", 0, 0, 60900), c("b", 0, 0, 60500), c("d", 0, 0, 60400)];
    const at: Record<string, number> = { b: 10, d: 20 };
    expect(nearby(list, view, (id) => at[id] ?? null).map((x) => x.id)).toEqual(["d", "b", "a"]);
  });

  it("works across RA 0/360", () => {
    const v = { ra: 0.001, dec: 0, fov: 5 };
    const x: Candidate = { id: "w", ra: 359.999, dec: 0, mjd: 60800, clock: "hours", source: "pipeline" };
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
      { id: "x,1", ra: 269.4521234567, dec: -4.6933, mjd: 60800.5, clock: "months", source: "user" },
      { id: "y", ra: 10, dec: 20, mjd: 60900, clock: "hours", source: "pipeline" },
    ];
    const csv = toCsv(cands, new Map([["x,1", { mover: 3, changing: 1, junk: 0, unsure: 2 }]]));
    const lines = csv.trimEnd().split("\r\n");
    expect(lines[0]).toBe(CSV_COLUMNS.join(","));
    expect(lines).toHaveLength(3);
    expect(lines[1].startsWith('"x,1",user,269.452123,-4.6933,60800.5,"')).toBe(true);
    expect(lines[1].endsWith(",months,3,1,0,2,6")).toBe(true);
    expect(lines[2].endsWith(",hours,0,0,0,0,0")).toBe(true);
  });
});
