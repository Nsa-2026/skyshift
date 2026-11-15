import { describe, expect, it } from "vitest";
import { feedFor, objectsIn, roundup } from "./feed";
import type { Post } from "./posts";
import { sampleNotes } from "./sample";

const NOW = 1_800_000_000_000;

const post = (over: Partial<Post> = {}): Post => ({
  id: "p-1",
  about: "cand-a",
  name: "Amara",
  body: "Worth another look.",
  ts: NOW - 60_000,
  ...over,
});

describe("sampleNotes", () => {
  it("gives the same object the same comments every time", () => {
    expect(sampleNotes("cand-a")).toEqual(sampleNotes("cand-a"));
  });

  it("gives every object one or two, never none", () => {
    for (const id of ["a", "cand-a", "hunt-sp-62962007378500", "hunt-669350069105-87803475", "x".repeat(40)]) {
      const s = sampleNotes(id);
      expect(s.length).toBeGreaterThanOrEqual(1);
      expect(s.length).toBeLessThanOrEqual(2);
      for (const n of s) expect(n.body.length).toBeGreaterThan(20);
    }
  });

  it("never repeats a line or a handle within one object", () => {
    for (let i = 0; i < 200; i++) {
      const s = sampleNotes(`cand-${i}`);
      if (s.length < 2) continue;
      expect(s[0].body).not.toBe(s[1].body);
      expect(s[0].who).not.toBe(s[1].who);
    }
  });

  it("does not hand every object the same two comments", () => {
    const first = new Set(Array.from({ length: 60 }, (_, i) => sampleNotes(`cand-${i}`)[0].body));
    expect(first.size).toBeGreaterThan(4);
  });
});

describe("feedFor", () => {
  it("puts your own notes on this object in, and nobody else's object's", () => {
    const rows = feedFor("cand-a", [post(), post({ id: "p-2", about: "cand-b", body: "Elsewhere." })], NOW);
    expect(rows.some((r) => r.body === "Worth another look.")).toBe(true);
    expect(rows.some((r) => r.body === "Elsewhere.")).toBe(false);
  });

  it("marks every made-up row as a sample and no real one", () => {
    const rows = feedFor("cand-a", [post()], NOW);
    const mine = rows.filter((r) => !r.sample);
    expect(mine).toHaveLength(1);
    expect(mine[0].who).toBe("Amara");
    expect(rows.filter((r) => r.sample).length).toBe(sampleNotes("cand-a").length);
  });

  it("is newest first", () => {
    const rows = feedFor("cand-a", [post({ ts: NOW })], NOW);
    expect(rows[0].body).toBe("Worth another look.");
    for (let i = 1; i < rows.length; i++) expect(rows[i - 1].ts).toBeGreaterThanOrEqual(rows[i].ts);
  });

  it("shows samples even when nobody has written anything", () => {
    expect(feedFor("cand-a", [], NOW).length).toBeGreaterThan(0);
  });
});

describe("roundup", () => {
  it("mixes objects, newest first, and stops at the cap", () => {
    const ids = Array.from({ length: 20 }, (_, i) => `cand-${i}`);
    const rows = roundup(ids, [], NOW, 10);
    expect(rows).toHaveLength(10);
    for (let i = 1; i < rows.length; i++) expect(rows[i - 1].ts).toBeGreaterThanOrEqual(rows[i].ts);
    expect(objectsIn(rows)).toBeGreaterThan(1);
  });

  it("keeps your own note even when it is about an object not in the list", () => {
    const rows = roundup(["cand-x"], [post({ about: "cand-elsewhere", ts: NOW })], NOW, 5);
    expect(rows[0].body).toBe("Worth another look.");
    expect(rows[0].sample).toBe(false);
  });

  it("gives every row a key of its own, so a render never collides", () => {
    const rows = roundup(["a", "b", "c", "d"], [post(), post({ id: "p-2" })], NOW, 50);
    expect(new Set(rows.map((r) => r.key)).size).toBe(rows.length);
  });

  it("is empty only when there is nothing at all", () => {
    expect(roundup([], [], NOW, 10)).toEqual([]);
    expect(roundup([], [post()], NOW, 10)).toHaveLength(1);
  });

  it("asks for nothing when the cap is zero or negative", () => {
    expect(roundup(["a", "b"], [post()], NOW, 0)).toEqual([]);
    expect(roundup(["a", "b"], [post()], NOW, -3)).toEqual([]);
  });
});

describe("the roundup never reads as an echo", () => {
  it("shows a sample line once, however many objects would carry it", () => {
    const ids = Array.from({ length: 40 }, (_, i) => `cand-${i}`);
    const rows = roundup(ids, [], NOW, 40).filter((r) => r.sample);
    expect(new Set(rows.map((r) => r.body)).size).toBe(rows.length);
  });

  it("keeps two of your own notes even when they say the same thing", () => {
    const same = [post({ id: "p-1", ts: NOW }), post({ id: "p-2", ts: NOW - 1 })];
    expect(roundup([], same, NOW, 10)).toHaveLength(2);
  });

  it("spreads the times out instead of stacking them on a few slots", () => {
    const rows = roundup(Array.from({ length: 30 }, (_, i) => `cand-${i}`), [], NOW, 30);
    expect(new Set(rows.map((r) => r.ts)).size).toBeGreaterThan(rows.length - 3);
  });
});
