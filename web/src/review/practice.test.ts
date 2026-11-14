import { describe, expect, it } from "vitest";
import { ANSWERS, type KV } from "../vote/model";
import { PRACTICE, PRACTICE_KEY, judge, markPracticeDone, practiceDone, practiceSummary } from "./practice";

function memKV(): KV & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, get: (k) => data.get(k) ?? null, set: (k, v) => void data.set(k, v) };
}

describe("practice round", () => {
  it("has three featured targets with feedback for every answer", () => {
    expect(PRACTICE).toHaveLength(3);
    for (const item of PRACTICE) {
      for (const a of ANSWERS) expect(item.feedback[a].length).toBeGreaterThan(20);
      // the right answer's feedback says so first; the others never start with "Yes"
      expect(item.feedback[item.answer].startsWith("Yes")).toBe(true);
      for (const a of ANSWERS) if (a !== item.answer) expect(item.feedback[a].startsWith("Yes")).toBe(false);
    }
  });

  it("never gives the answer away in the question", () => {
    for (const item of PRACTICE) {
      const asked = `${item.title} ${item.prompt}`.toLowerCase();
      expect(asked).not.toContain(item.name.toLowerCase());
      expect(asked).not.toMatch(/asteroid|barnard|aurigae/);
    }
  });

  it("judges an answer against the key", () => {
    const elpis = PRACTICE.find((p) => p.slug === "elpis")!;
    expect(judge(elpis, "mover")).toMatchObject({ slug: "elpis", correct: true });
    expect(judge(elpis, "mover").text).toMatch(/^Yes — that's asteroid \(59\) Elpis/);
    expect(judge(elpis, "junk")).toMatchObject({ correct: false, answer: "junk" });
  });

  it("scores the round", () => {
    const [a, b, c] = PRACTICE;
    expect(practiceSummary([judge(a, a.answer), judge(b, b.answer), judge(c, c.answer)])).toMatchObject({ right: 3, of: 3 });
    const some = practiceSummary([judge(a, a.answer), judge(b, "junk"), judge(c, "unsure")]);
    expect(some).toMatchObject({ right: 1, of: 3 });
    expect(some.line).toMatch(/^1 of 3\./);
    expect(practiceSummary([judge(a, "junk")]).right).toBe(0);
    expect(practiceSummary([]).line).toBe("Practice skipped.");
  });

  it("remembers that practice is done", () => {
    const kv = memKV();
    expect(practiceDone(kv)).toBe(false);
    markPracticeDone(kv);
    expect(practiceDone(kv)).toBe(true);
    expect(kv.data.get(PRACTICE_KEY)).toBe("done");
  });
});
