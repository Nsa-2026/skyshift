import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { DEFAULT_SLOTS, SLOT_IDS, aspectRatio, creditLine, loadManifest, parseManifest, sizeProblem, slotUrl, type SlotId } from "./manifest";

const dir = join(__dirname, "../../public/opening");
const raw = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8"));

/** Width and height of a PNG, WebP or JPEG file, from its header. */
function imageSize(buf: Buffer): [number, number] {
  if (buf.readUInt32BE(0) === 0x89504e47) return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
  if (buf.toString("ascii", 0, 4) === "RIFF" && buf.toString("ascii", 8, 12) === "WEBP") {
    const chunk = buf.toString("ascii", 12, 16);
    if (chunk === "VP8 ") return [buf.readUInt16LE(26) & 0x3fff, buf.readUInt16LE(28) & 0x3fff];
    if (chunk === "VP8L") {
      const b = buf.readUInt32LE(21);
      return [(b & 0x3fff) + 1, ((b >> 14) & 0x3fff) + 1];
    }
    if (chunk === "VP8X") return [buf.readUIntLE(24, 3) + 1, buf.readUIntLE(27, 3) + 1];
  }
  if (buf[0] === 0xff && buf[1] === 0xd8) {
    let i = 2;
    while (i < buf.length) {
      const marker = buf[i + 1];
      const len = buf.readUInt16BE(i + 2);
      if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) return [buf.readUInt16BE(i + 7), buf.readUInt16BE(i + 5)];
      i += 2 + len;
    }
  }
  throw new Error("not a PNG, WebP or JPEG");
}

describe("public/opening/manifest.json", () => {
  const { slots, problems } = parseManifest(raw);

  it("has no problems and a slot for every id", () => {
    expect(problems).toEqual([]);
    expect(raw.slots.map((s: { id: string }) => s.id).sort()).toEqual([...SLOT_IDS].sort());
  });

  it("gives every slot an alt text, a brief, a shape and a credit", () => {
    for (const id of SLOT_IDS) {
      const s = slots[id];
      expect(s.alt.length, id).toBeGreaterThan(10);
      expect(s.brief.length, id).toBeGreaterThan(100);
      expect(aspectRatio(s.aspect), id).toBeGreaterThan(0);
      expect(s.credit.length, id).toBeGreaterThan(3);
    }
  });

  it.each(SLOT_IDS.filter((id) => raw.slots.find((s: { id: string }) => s.id === id)?.file))("%s: the file exists, big enough and the right shape", (id) => {
    const slot = slots[id as SlotId];
    const path = join(dir, slot.file!);
    expect(existsSync(path), path).toBe(true);
    const [w, h] = imageSize(readFileSync(path));
    expect(sizeProblem(slot, w, h)).toBe("");
  });

  it("keeps the sky real SPHEREx data, credited", () => {
    expect(slots.sky.kind).toBe("real");
    expect(slots.sky.credit).toMatch(/SPHEREx/);
    expect(slots.sky.credit).toMatch(/NASA\/JPL-Caltech\/IPAC \(IRSA\)/);
  });

  it("matches the code's fallback for the sky", () => {
    expect(slots.sky).toMatchObject({ ...DEFAULT_SLOTS.sky, brief: expect.any(String) });
  });
});

describe("parseManifest", () => {
  const sky = raw.slots.find((s: { id: string }) => s.id === "sky");

  it("falls back to today's slots for a missing or broken manifest", () => {
    expect(parseManifest(null).slots).toEqual(DEFAULT_SLOTS);
    expect(parseManifest({ slots: "no" }).problems).toEqual(["manifest has no slots list"]);
  });

  it("ignores unknown slots, and refuses paths outside /opening/", () => {
    const { slots, problems } = parseManifest({
      slots: [
        { ...sky, id: "moon" },
        { ...sky, file: "../data/x.webp" },
      ],
    });
    expect(slots.sky).toEqual(DEFAULT_SLOTS.sky);
    expect(problems).toHaveLength(2);
    expect(parseManifest({ slots: [{ ...sky, file: "https://example.com/sky.webp" }] }).slots.sky).toEqual(DEFAULT_SLOTS.sky);
  });

  it("shows no image without a credit", () => {
    const { slots, problems } = parseManifest({ slots: [{ ...sky, file: "new.webp", credit: "" }] });
    expect(slots.sky.file).toBe("spherex-sky.webp");
    expect(problems[0]).toMatch(/credit/);
  });

  it("labels anything not marked real as illustration", () => {
    const { slots } = parseManifest({ slots: [{ ...sky, kind: "data" }] });
    expect(slots.sky.kind).toBe("illustration");
    expect(creditLine(slots, new Set(["sky"]))).toMatch(/^Illustration: Sky: SPHEREx/);
  });

  it("swaps the file and keeps the slot's own words", () => {
    const { slots, problems } = parseManifest({ slots: [{ ...sky, file: "sky/v2.png", alt: "New sky." }] });
    expect(problems).toEqual([]);
    expect(slotUrl(slots.sky)).toBe("/opening/sky/v2.png");
    expect(slots.sky.alt).toBe("New sky.");
    expect(slotUrl(slots.earth)).toBeNull();
  });
});

describe("creditLine", () => {
  it("credits real images as written and labels everything else", () => {
    expect(creditLine(DEFAULT_SLOTS, new Set())).toBe("Illustration: Earth, hills and figure drawn in code.");
    const slots = {
      ...DEFAULT_SLOTS,
      earth: { ...DEFAULT_SLOTS.earth, kind: "real" as const, file: "e.webp", credit: "Earth: NASA DSCOVR EPIC" },
      horizon: { ...DEFAULT_SLOTS.horizon, file: "h.webp", credit: "hills, made with Higgsfield." },
    };
    expect(creditLine(slots, new Set<SlotId>(["sky", "earth", "horizon"]))).toBe(
      "Sky: SPHEREx DR1 at 1.35 µm, NASA/JPL-Caltech/IPAC (IRSA). Earth: NASA DSCOVR EPIC. Illustration: hills, made with Higgsfield; figure drawn in code.",
    );
  });
});

describe("sizeProblem", () => {
  it("flags small or misshapen images", () => {
    expect(sizeProblem(DEFAULT_SLOTS.sky, 1600, 1000)).toBe("");
    expect(sizeProblem(DEFAULT_SLOTS.sky, 3200, 2000)).toBe("");
    expect(sizeProblem(DEFAULT_SLOTS.sky, 800, 500)).toMatch(/smaller/);
    expect(sizeProblem(DEFAULT_SLOTS.sky, 2000, 1000)).toMatch(/not 16:10/);
  });
});

describe("loadManifest", () => {
  it("reads the manifest, revalidating the cache", async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify(raw)));
    const slots = await loadManifest(fetchFn as typeof fetch);
    expect(fetchFn).toHaveBeenCalledWith("/opening/manifest.json", expect.objectContaining({ cache: "no-cache" }));
    expect(slots.sky.file).toBe("spherex-sky.webp");
  });

  it("falls back on a 404, bad JSON or a network error", async () => {
    expect(await loadManifest((async () => new Response("", { status: 404 })) as typeof fetch)).toBe(DEFAULT_SLOTS);
    expect(await loadManifest((async () => new Response("{nope")) as typeof fetch)).toBe(DEFAULT_SLOTS);
    expect(await loadManifest((async () => Promise.reject(new TypeError("offline"))) as typeof fetch)).toBe(DEFAULT_SLOTS);
  });

  it("gives up on a slow manifest", async () => {
    vi.useFakeTimers();
    const hang = ((_u: string, init: RequestInit) =>
      new Promise((_r, reject) => init.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))))) as typeof fetch;
    const done = loadManifest(hang, 2000);
    vi.advanceTimersByTime(2000);
    expect(await done).toBe(DEFAULT_SLOTS);
    vi.useRealTimers();
  });
});
