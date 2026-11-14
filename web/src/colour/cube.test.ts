import { describe, expect, it } from "vitest";
import { parseMultipart, readSpans, readTileInfo, sampleInt, samplePlane, planField, type Fetcher, type TileInfo } from "./cube";
import { BLOCK, parseHeader } from "./fits";

function card(k: string, v: string): string {
  return (k.padEnd(8) + "= " + v).padEnd(80);
}
function header(cards: string[]): string {
  const s = cards.join("") + "END".padEnd(80);
  return s.padEnd(Math.ceil(s.length / BLOCK) * BLOCK);
}

/** A tiny fake tile: 4 × 3 × 2 cube, value = 100·plane + 10·y + x. */
function fakeTile(): Uint8Array {
  const prim = header([card("SIMPLE", "T"), card("BITPIX", "8"), card("NAXIS", "0"), card("DATE-BEG", "'2025-08-08T13:46:32'")]);
  const img = header([
    card("XTENSION", "'IMAGE   '"),
    card("BITPIX", "-32"),
    card("NAXIS", "3"),
    card("NAXIS1", "4"),
    card("NAXIS2", "3"),
    card("NAXIS3", "2"),
    card("EXTNAME", "'IMAGE   '"),
    card("CTYPE1", "'RA---HPX'"),
    card("CRPIX1", "6144.5"),
    card("CRPIX2", "13824.5"),
    card("PC1_1", "-0.00274658203125"),
  ]);
  const data = new DataView(new ArrayBuffer(4 * 4 * 3 * 2));
  let i = 0;
  for (let z = 0; z < 2; z++) for (let y = 0; y < 3; y++) for (let x = 0; x < 4; x++) data.setFloat32(4 * i++, 100 * z + 10 * y + x, false);
  const hits = header([
    card("XTENSION", "'IMAGE   '"),
    card("BITPIX", "32"),
    card("NAXIS", "3"),
    card("NAXIS1", "4"),
    card("NAXIS2", "3"),
    card("NAXIS3", "2"),
    card("EXTNAME", "'NHITS   '"),
  ]);
  const hv = new DataView(new ArrayBuffer(4 * 4 * 3 * 2));
  for (let k = 0; k < 24; k++) hv.setInt32(4 * k, k < 12 ? 7 : 9, false);
  const padded = Math.ceil(data.byteLength / BLOCK) * BLOCK;
  const bytes = new Uint8Array(prim.length + img.length + padded + hits.length + hv.byteLength);
  bytes.set([...prim].map((c) => c.charCodeAt(0)), 0);
  bytes.set([...img].map((c) => c.charCodeAt(0)), prim.length);
  bytes.set(new Uint8Array(data.buffer), prim.length + img.length);
  bytes.set([...hits].map((c) => c.charCodeAt(0)), prim.length + img.length + padded);
  bytes.set(new Uint8Array(hv.buffer), prim.length + img.length + padded + hits.length);
  return bytes;
}

const file = fakeTile();
const enc = (t: string) => Uint8Array.from([...t].map((c) => c.charCodeAt(0)));

/** A server like IRSA: single ranges as 206, several as multipart/byteranges. */
function server(opts: { multi: "multipart" | "merge" | "refuse" } = { multi: "multipart" }): Fetcher {
  return async (_url, init) => {
    const spec = (init.headers as Record<string, string>).Range.replace("bytes=", "");
    const ranges = spec.split(",").map((r) => r.split("-").map(Number));
    if (ranges.length === 1) {
      const [a, b] = ranges[0];
      const body = file.slice(a, b + 1);
      return new Response(body, { status: 206, headers: { "content-range": `bytes ${a}-${a + body.length - 1}/${file.length}` } });
    }
    if (opts.multi === "refuse") return new Response(file.slice(), { status: 200 });
    if (opts.multi === "merge") {
      const a = Math.min(...ranges.map((r) => r[0]));
      const b = Math.max(...ranges.map((r) => r[1]));
      return new Response(file.slice(a, b + 1), { status: 206, headers: { "content-range": `bytes ${a}-${b}/${file.length}` } });
    }
    const chunks: Uint8Array[] = [];
    for (const [a, b] of ranges) {
      chunks.push(enc(`\r\n--XYZ\r\nContent-Type: application/fits\r\nContent-Range: bytes ${a}-${b}/${file.length}\r\n\r\n`), file.slice(a, b + 1));
    }
    chunks.push(enc("\r\n--XYZ--\r\n"));
    const body = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
    let pos = 0;
    for (const c of chunks) {
      body.set(c, pos);
      pos += c.length;
    }
    return new Response(body, { status: 206, headers: { "content-type": "multipart/byteranges; boundary=XYZ" } });
  };
}
const fetcher = server();

describe("FITS header", () => {
  it("parses strings, numbers and logicals, and pads to whole blocks", () => {
    const h = parseHeader(file)!;
    expect(h.cards.SIMPLE).toBe(true);
    expect(h.cards.NAXIS).toBe(0);
    expect(h.cards["DATE-BEG"]).toBe("2025-08-08T13:46:32");
    expect(h.length).toBe(BLOCK);
  });
  it("returns null when END is not in the bytes yet", () => {
    expect(parseHeader(file.subarray(0, 160))).toBeNull();
  });
});

describe("cube reads", () => {
  it("finds IMAGE's data and reads big-endian rows of one plane", async () => {
    const info = await readTileInfo(7, fetcher);
    expect(info.dataStart).toBe(2 * BLOCK);
    expect([info.nx, info.ny, info.nz]).toEqual([4, 3, 2]);
    expect(info.dateBeg).toBe("2025-08-08T13:46:32");
    const spans = [{ y: 1, x0: 1, x1: 2 }, { y: 2, x0: 0, x1: 0 }];
    for (const multi of ["multipart", "merge", "refuse"] as const) {
      const dv = await readSpans(info, info.dataStart, 1, spans, server({ multi }));
      expect([0, 1, 2].map((i) => dv.getFloat32(4 * i, false))).toEqual([111, 112, 120]);
    }
  });
  it("parses multipart/byteranges with absolute offsets", () => {
    const body = enc("--B\r\nContent-Range: bytes 10-12/99\r\n\r\nabc\r\n--B\r\nContent-Range: bytes 50-51/99\r\n\r\nde\r\n--B--");
    const parts = parseMultipart(body, "B");
    expect(parts.map((p) => [p.start, String.fromCharCode(...p.bytes)])).toEqual([[10, "abc"], [50, "de"]]);
  });
  it("samples a plane through a plan", async () => {
    const info: TileInfo = { ...(await readTileInfo(7, fetcher)) };
    const plan = {
      n: 2,
      pixelArcsec: 12.9,
      tileOf: Int16Array.from([0, 0, 0, -1]),
      offset: Int32Array.from([0, 3, 4, 0]),
      tiles: [{ info, spans: [{ y: 1, x0: 0, x1: 3 }, { y: 2, x0: 0, x1: 3 }] }],
    };
    const v = await samplePlane(plan, 0, fetcher);
    expect(Array.from(v.slice(0, 3))).toEqual([10, 13, 20]);
    expect(Number.isNaN(v[3])).toBe(true);
    expect(Array.from((await samplePlane(plan, 50, fetcher)).slice(0, 1)).every(Number.isNaN)).toBe(true);
  });
  it("reads NHITS planes by walking the headers after IMAGE", async () => {
    const info = await readTileInfo(7, fetcher);
    const plan = { n: 1, pixelArcsec: 12.9, tileOf: Int16Array.from([0]), offset: Int32Array.from([0]), tiles: [{ info, spans: [{ y: 0, x0: 1, x1: 1 }] }] };
    expect((await sampleInt(plan, "NHITS", 0, fetcher))[0]).toBe(7);
    expect((await sampleInt(plan, "NHITS", 1, fetcher))[0]).toBe(9);
  });
  it("refuses a server that ignores the byte range (would be the whole 320 MB tile)", async () => {
    const whole: Fetcher = async () => new Response(file.buffer as ArrayBuffer, { status: 200 });
    await expect(readTileInfo(7, whole)).rejects.toThrow(/ignored the byte range/);
  });
  it("retries a dropped connection", async () => {
    let calls = 0;
    const flaky: Fetcher = async (u, i) => {
      if (calls++ === 0) throw new TypeError("fetch failed");
      return fetcher(u, i);
    };
    expect((await readTileInfo(7, flaky)).nx).toBe(4);
    expect(calls).toBe(2);
  });
  it("planField covers the field at the cube's pixel size", async () => {
    const plan = await planField(
      2,
      () => ({ ra: 0, dec: 0 }),
      async (t) => ({ ...(await readTileInfo(t, fetcher)), nx: 512, ny: 512 }),
    );
    expect(plan.n).toBe(Math.round(120 / plan.pixelArcsec));
  });
});
