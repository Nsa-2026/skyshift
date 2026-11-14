import { describe, expect, it } from "vitest";
import { ANSWERS, type KV } from "../vote/model";
import {
  PRACTICE,
  PRACTICE_KEY,
  PROGRESS_KEY,
  clearProgress,
  judge,
  markPracticeDone,
  practiceDone,
  practiceSummary,
  readProgress,
  saveProgress,
} from "./practice";

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

  it("has a hint for every plate that still doesn't name the object", () => {
    for (const item of PRACTICE) {
      expect(item.hint.length).toBeGreaterThan(60);
      const words = item.name.split(/\W+/).filter((w) => w.length > 3 && !/^asteroid$/i.test(w));
      for (const w of words) expect(item.hint).not.toContain(w);
      expect(item.hint).not.toMatch(/\bit's (a|an) /i);
    }
    // The two months plates need Difference to see a sub-pixel step or a change in size.
    expect(PRACTICE.filter((p) => p.hintMode === "difference").map((p) => p.slug)).toEqual(["barnard", "rwaur"]);
  });

  it("saves answers in order and reads them back, judged again against the key", () => {
    const kv = memKV();
    expect(readProgress(kv)).toEqual([]);
    const [a, b] = PRACTICE;
    saveProgress(kv, [judge(a, "unsure"), judge(b, b.answer)]);
    // only slug and answer are stored: never the score
    expect(JSON.parse(kv.data.get(PROGRESS_KEY)!)).toEqual([
      { slug: a.slug, answer: "unsure" },
      { slug: b.slug, answer: b.answer },
    ]);
    expect(readProgress(kv)).toEqual([
      { slug: a.slug, answer: "unsure", correct: false },
      { slug: b.slug, answer: b.answer, correct: true },
    ]);
    clearProgress(kv);
    expect(readProgress(kv)).toEqual([]);
  });

  it("keeps only a valid ordered prefix of saved answers", () => {
    const kv = memKV();
    kv.set(PROGRESS_KEY, "not json");
    expect(readProgress(kv)).toEqual([]);
    kv.set(PROGRESS_KEY, JSON.stringify({ slug: "elpis", answer: "mover" }));
    expect(readProgress(kv)).toEqual([]);
    // a forged score is ignored; a plate out of order ends the prefix
    kv.set(PROGRESS_KEY, JSON.stringify([{ slug: "elpis", answer: "junk", correct: true }, { slug: "rwaur", answer: "changing" }]));
    expect(readProgress(kv)).toEqual([{ slug: "elpis", answer: "junk", correct: false }]);
    // an unknown answer ends it too, and so does anything past the third plate
    kv.set(PROGRESS_KEY, JSON.stringify([{ slug: "elpis", answer: "maybe" }]));
    expect(readProgress(kv)).toEqual([]);
    const all = [...PRACTICE.map((p) => ({ slug: p.slug, answer: p.answer })), { slug: "extra", answer: "mover" }];
    kv.set(PROGRESS_KEY, JSON.stringify(all));
    expect(readProgress(kv)).toHaveLength(PRACTICE.length);
    kv.set(PROGRESS_KEY, JSON.stringify([null, 3]));
    expect(readProgress(kv)).toEqual([]);
  });
});
