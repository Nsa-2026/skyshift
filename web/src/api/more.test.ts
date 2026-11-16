// The Less likely files (contracts/api.md v3.24 `more`): the index read defensively, each file read
// once and only when asked, and which files are on the list. Synthetic files: ../review/checks.more-*.fixture.json.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import hunt from "../review/checks.more-hunt.fixture.json";
import {
  _resetMoreForTests,
  isFromMore,
  isMoreLoaded,
  isMoreOpen,
  loadMore,
  loadedMore,
  moreOf,
  moreTotal,
  moreUrl,
  moreVersion,
  openedMore,
  setMoreOpen,
} from "./more";

const entry = { zone: "hunt", kind: "mover" as const, grade: "C" as const, count: 3, url: "candidates-more/hunt.json" };

describe("candidates.json `more`", () => {
  it("reads as [] when absent or unreadable", () => {
    expect(moreOf({})).toEqual([]);
    expect(moreOf(null)).toEqual([]);
    expect(moreOf({ more: "x" })).toEqual([]);
  });

  it("keeps the entries a person can open: a zone, a url and a count above 0, once per url", () => {
    const got = moreOf({
      more: [
        entry,
        { ...entry, zone: "ecl04", url: "candidates-more/ecl04.json", count: 2.4, kind: "comet", grade: "Z" },
        { ...entry, url: "candidates-more/hunt.json" },
        { ...entry, zone: "", url: "x.json" },
        { ...entry, zone: "ecl08", url: "" },
        { ...entry, zone: "ecl11", url: "candidates-more/ecl11.json", count: 0 },
        null,
      ],
    });
    expect(got).toEqual([entry, { zone: "ecl04", kind: "mover", grade: null, count: 2, url: "candidates-more/ecl04.json" }]);
    expect(moreTotal(got)).toBe(5);
  });

  it("resolves a file's url against candidates.json, like frames_url", () => {
    expect(moreUrl(entry)).toBe(new URL("/data/candidates-more/hunt.json", location.origin).href);
  });
});

describe("reading a Less likely file", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    _resetMoreForTests();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("reads it once, and tells its candidates apart", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(hunt)));
    expect(isMoreLoaded(entry)).toBe(false);
    expect(loadedMore([entry])).toEqual([]);
    const [a, b] = await Promise.all([loadMore(entry), loadMore(entry)]);
    expect(a).toBe(b);
    expect(a.map((c) => c.id)).toEqual(hunt.candidates.map((c) => c.id));
    await loadMore(entry);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(moreUrl(entry));
    expect(isMoreLoaded(entry)).toBe(true);
    expect(loadedMore([entry])).toHaveLength(3);
    expect(isFromMore("hunt-664250090001-87801720")).toBe(true);
    expect(isFromMore("hunt-664250069368-87801613")).toBe(false);
  });

  it("doesn't keep a failure: the next try reads it again", async () => {
    fetchMock.mockResolvedValueOnce(new Response("", { status: 404 }));
    await expect(loadMore(entry)).rejects.toThrow("404");
    expect(isMoreLoaded(entry)).toBe(false);
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(hunt)));
    expect(await loadMore(entry)).toHaveLength(3);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("puts a file on the list only once it's read, and says when that changes", async () => {
    const v0 = moreVersion();
    setMoreOpen(entry, true);
    expect(isMoreOpen(entry)).toBe(false);
    expect(moreVersion()).toBe(v0);
    fetchMock.mockResolvedValue(new Response(JSON.stringify(hunt)));
    await loadMore(entry);
    setMoreOpen(entry, true);
    setMoreOpen(entry, true);
    expect(isMoreOpen(entry)).toBe(true);
    expect(openedMore([entry, { ...entry, zone: "ecl04", url: "candidates-more/ecl04.json" }])).toEqual([entry]);
    expect(moreVersion()).toBe(v0 + 1);
    setMoreOpen(entry, false);
    expect(isMoreOpen(entry)).toBe(false);
    expect(moreVersion()).toBe(v0 + 2);
    // Taken off the list, still read: opening it again costs nothing.
    expect(isMoreLoaded(entry)).toBe(true);
  });
});
