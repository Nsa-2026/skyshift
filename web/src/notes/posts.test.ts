import { describe, expect, it } from "vitest";
import type { KV } from "../vote/model";
import {
  BODY_MAX,
  KEEP,
  addPost,
  authorName,
  checkBody,
  cleanPosts,
  newPost,
  objectsDiscussed,
  postsAbout,
  readPosts,
  removePost,
  timeAgo,
  toPost,
  type Post,
} from "./posts";

function memKV(): KV {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v) };
}

const post = (over: Partial<Post> = {}): Post => ({
  id: "p-1",
  about: null,
  name: "Amara",
  body: "Worth another look.",
  ts: 1_000_000,
  ...over,
});

describe("toPost", () => {
  it("keeps a well-formed note", () => {
    expect(toPost(post())).toEqual(post());
  });

  it("drops anything without an id, a body or a time", () => {
    expect(toPost(null)).toBeNull();
    expect(toPost("a note")).toBeNull();
    expect(toPost({ ...post(), id: "" })).toBeNull();
    expect(toPost({ ...post(), body: "   " })).toBeNull();
    expect(toPost({ ...post(), ts: "yesterday" })).toBeNull();
    expect(toPost({ ...post(), ts: Number.NaN })).toBeNull();
  });

  it("squeezes whitespace and caps the length", () => {
    const p = toPost({ ...post(), name: "  Amara\n B ", body: `  a\n\n  b  ${"x".repeat(BODY_MAX)}` });
    expect(p!.name).toBe("Amara B");
    expect(p!.body.length).toBe(BODY_MAX);
    expect(p!.body.startsWith("a b x")).toBe(true);
  });

  it("reads a missing or empty author as anonymous, and a blank about as none", () => {
    const p = toPost({ ...post(), name: undefined, about: "" })!;
    expect(p.name).toBe("");
    expect(authorName(p)).toBe("Anonymous");
    expect(p.about).toBeNull();
  });
});

describe("cleanPosts", () => {
  it("drops malformed entries, de-duplicates by id and sorts newest first", () => {
    const got = cleanPosts([
      post({ id: "a", ts: 10 }),
      null,
      post({ id: "b", ts: 30 }),
      { id: "c" },
      post({ id: "a", ts: 99, body: "a repeat of the same id" }),
      post({ id: "d", ts: 20 }),
    ]);
    expect(got.map((p) => p.id)).toEqual(["b", "d", "a"]);
  });

  it("breaks a tie on time by id, so the order never wobbles", () => {
    expect(cleanPosts([post({ id: "z", ts: 5 }), post({ id: "a", ts: 5 })]).map((p) => p.id)).toEqual(["a", "z"]);
  });
});

describe("checkBody", () => {
  it("asks for something to be written", () => {
    expect(checkBody("")).toBe("empty");
    expect(checkBody("   \n ")).toBe("empty");
  });

  it("refuses a note past the limit, counted after the whitespace is squeezed", () => {
    expect(checkBody("x".repeat(BODY_MAX))).toBeNull();
    expect(checkBody("x".repeat(BODY_MAX + 1))).toBe("too_long");
    expect(checkBody(`${"x".repeat(BODY_MAX)}     `)).toBeNull();
  });
});

describe("newPost", () => {
  it("carries the time it was written and a unique id", () => {
    const a = newPost({ body: "one" }, 5_000);
    const b = newPost({ body: "two" }, 5_000);
    expect(a.ts).toBe(5_000);
    expect(a.id).not.toBe(b.id);
    expect(a.about).toBeNull();
  });

  it("keeps the object it is about", () => {
    expect(newPost({ about: "hunt-1", body: "x" }, 1).about).toBe("hunt-1");
  });
});

describe("postsAbout / objectsDiscussed", () => {
  const posts = [
    post({ id: "1", about: "hunt-a" }),
    post({ id: "2", about: null }),
    post({ id: "3", about: "hunt-a" }),
    post({ id: "4", about: "hunt-b" }),
  ];

  it("finds the notes on one object", () => {
    expect(postsAbout(posts, "hunt-a").map((p) => p.id)).toEqual(["1", "3"]);
    expect(postsAbout(posts, "nobody")).toEqual([]);
  });

  it("counts the objects talked about, not the notes", () => {
    expect(objectsDiscussed(posts)).toBe(2);
    expect(objectsDiscussed([post({ about: null })])).toBe(0);
  });
});

describe("timeAgo", () => {
  const now = Date.UTC(2026, 10, 15, 12, 0, 0);
  const ago = (ms: number) => timeAgo(now - ms, now);

  it("reads in plain words", () => {
    expect(ago(0)).toBe("just now");
    expect(ago(59_000)).toBe("just now");
    expect(ago(12 * 60_000)).toBe("12 min ago");
    expect(ago(60 * 60_000)).toBe("1 hour ago");
    expect(ago(3 * 3_600_000)).toBe("3 hours ago");
    expect(ago(26 * 3_600_000)).toBe("1 day ago");
    expect(ago(3 * 86_400_000)).toBe("3 days ago");
  });

  it("falls back to a date once a week has passed", () => {
    expect(ago(10 * 86_400_000)).toMatch(/\d+ \w+/);
  });

  it("a clock that is behind reads as just now, never as the future", () => {
    expect(timeAgo(now + 60_000, now)).toBe("just now");
  });
});

describe("storage", () => {
  it("round-trips through the browser store", () => {
    const kv = memKV();
    expect(readPosts(kv)).toEqual([]);
    addPost(kv, post({ id: "a", ts: 1 }));
    addPost(kv, post({ id: "b", ts: 2 }));
    expect(readPosts(kv).map((p) => p.id)).toEqual(["b", "a"]);
  });

  it("reads unusable storage as no notes instead of throwing", () => {
    const broken: KV = {
      get: () => "{not json",
      set: () => {
        throw new Error("full");
      },
    };
    expect(readPosts(broken)).toEqual([]);
    expect(() => addPost(broken, post())).not.toThrow();
  });

  it("keeps at most KEEP notes, dropping the oldest", () => {
    const kv = memKV();
    for (let i = 0; i < KEEP + 5; i++) addPost(kv, post({ id: `p${i}`, ts: i }));
    const all = readPosts(kv);
    expect(all.length).toBe(KEEP);
    expect(all[0].id).toBe(`p${KEEP + 4}`);
    expect(all.some((p) => p.id === "p0")).toBe(false);
  });

  it("removes one note and leaves the rest", () => {
    const kv = memKV();
    addPost(kv, post({ id: "a", ts: 1 }));
    addPost(kv, post({ id: "b", ts: 2 }));
    expect(removePost(kv, "a").map((p) => p.id)).toEqual(["b"]);
    expect(readPosts(kv).map((p) => p.id)).toEqual(["b"]);
  });
});
