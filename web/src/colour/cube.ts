// Read wavelength planes of the SPHEREx DR1 all-sky cube (IRSA HiPS, FITS cube tiles at order 5)
// straight from the browser. Each tile file holds IMAGE (512 × 512 × 102 float32, MJy/sr), NHITS
// and FLAGS, 320 MB in all, so we never download a tile: HTTP Range requests fetch only the rows
// a field needs, one wavelength plane at a time (2 KB per row). IRSA answers ranges with CORS.
import { SPHEREX } from "../sky/layers";
import { BLOCK, dataLength, parseHeader, type Cards } from "./fits";
import { ang2pixNest, skyToTilePixel, type TileWcs } from "./hpx";

export const TILE_ORDER = 5;
/** Cube pixel size: hips_pixel_scale 0.0035786° from the cube's properties file. */
export const CUBE_PIXEL_ARCSEC = 0.0035786 * 3600;
/** Frames written as all-NaN planes (BLANKCH 'D3:17,D4:01': the dichroic channels), 0-based. */
export const BLANK_FRAMES: ReadonlySet<number> = new Set([50, 51]);

export function tileUrl(tile: number): string {
  return `${SPHEREX.url}/Norder${TILE_ORDER}/Dir${Math.floor(tile / 10000) * 10000}/Npix${tile}_cube.fits`;
}

/** OVERFLOW | NONLINEAR (contracts/flags.md SAT_BITS): the pixel was saturated in some exposure. */
export const SAT_BITS = 0x00008002;

export type IntHdu = "NHITS" | "FLAGS";

export interface TileInfo {
  tile: number;
  wcs: TileWcs;
  /** Byte offset of IMAGE's data. */
  dataStart: number;
  /** Byte offsets of the int32 HDUs' data (same shape as IMAGE), found on first use. */
  hdus?: Partial<Record<IntHdu, number>>;
  nx: number;
  ny: number;
  nz: number;
  dateBeg: string | null;
  dateEnd: string | null;
}

export type Fetcher = (url: string, init: RequestInit) => Promise<Response>;

export class RangeIgnored extends Error {}

/** Retries for dropped connections and 5xx; IRSA closes a socket now and then under load. */
const RETRIES = 3;

async function range(fetcher: Fetcher, url: string, a: number, b: number, signal?: AbortSignal): Promise<Uint8Array> {
  return withRetry(async () => {
    // no-store: Chromium's HTTP cache serialises requests to one URL (every read here is one tile file).
    const res = await fetcher(url, { headers: { Range: `bytes=${a}-${b}` }, cache: "no-store", signal });
    // A 200 would be the whole 320 MB tile: never read it.
    if (res.status === 200) {
      await res.body?.cancel();
      throw new RangeIgnored(`IRSA ignored the byte range for ${url}`);
    }
    if (res.status !== 206) throw Object.assign(new Error(`IRSA answered ${res.status}`), { retry: res.status >= 500 });
    const buf = new Uint8Array(await res.arrayBuffer());
    // Content-Range "bytes a-b/total": a range past the end of the file comes back shorter.
    const total = Number(/\/(\d+)$/.exec(res.headers.get("content-range") ?? "")?.[1] ?? NaN);
    const want = Math.min(b + 1, Number.isFinite(total) ? total : b + 1) - a;
    if (buf.length !== want) throw Object.assign(new Error("short read"), { retry: true });
    return buf;
  }, signal);
}

/** Read the primary and IMAGE headers of one tile. */
export async function readTileInfo(tile: number, fetcher: Fetcher, signal?: AbortSignal): Promise<TileInfo> {
  const url = tileUrl(tile);
  let bytes = await range(fetcher, url, 0, 5 * BLOCK - 1, signal);
  for (let tries = 0; tries < 4; tries++) {
    const primary = parseHeader(bytes, 0);
    const image = primary && parseHeader(bytes, primary.length + dataLength(primary.cards));
    if (primary && image) return toInfo(tile, primary.cards, image.cards, primary.length + dataLength(primary.cards) + image.length);
    bytes = await range(fetcher, url, 0, bytes.length * 2 - 1, signal);
  }
  throw new Error(`no IMAGE header in tile ${tile}`);
}

function toInfo(tile: number, primary: Cards, image: Cards, dataStart: number): TileInfo {
  if (image.EXTNAME !== "IMAGE" || image.BITPIX !== -32 || image.CTYPE1 !== "RA---HPX") {
    throw new Error(`tile ${tile}: unexpected IMAGE header`);
  }
  const num = (k: string, d?: number) => {
    const v = image[k] ?? d;
    if (typeof v !== "number") throw new Error(`tile ${tile}: missing ${k}`);
    return v;
  };
  return {
    tile,
    wcs: {
      crpix1: num("CRPIX1"),
      crpix2: num("CRPIX2"),
      pc11: num("PC1_1", 1),
      pc12: num("PC1_2", 0),
      pc21: num("PC2_1", 0),
      pc22: num("PC2_2", 1),
      cdelt1: num("CDELT1", 1),
      cdelt2: num("CDELT2", 1),
    },
    dataStart,
    nx: num("NAXIS1"),
    ny: num("NAXIS2"),
    nz: num("NAXIS3"),
    dateBeg: typeof primary["DATE-BEG"] === "string" ? (primary["DATE-BEG"] as string) : null,
    dateEnd: typeof primary["DATE-END"] === "string" ? (primary["DATE-END"] as string) : null,
  };
}

/** Byte offset of an int32 HDU's data (NHITS or FLAGS): walk the headers after IMAGE. Cached. */
export async function findHdu(info: TileInfo, name: IntHdu, fetcher: Fetcher, signal?: AbortSignal): Promise<number> {
  info.hdus ??= {};
  const known = info.hdus[name];
  if (known != null) return known;
  const url = tileUrl(info.tile);
  let at = info.dataStart + Math.ceil((info.nx * info.ny * info.nz * 4) / BLOCK) * BLOCK;
  for (let hdu = 0; hdu < 4; hdu++) {
    const bytes = await range(fetcher, url, at, at + 5 * BLOCK - 1, signal);
    const h = parseHeader(bytes);
    if (!h) break;
    if (h.cards.EXTNAME === name) {
      if (h.cards.BITPIX !== 32) break;
      info.hdus[name] = at + h.length;
      return at + h.length;
    }
    at += h.length + dataLength(h.cards);
  }
  throw new Error(`no ${name} in tile ${info.tile}`);
}

// ---- reading only the pixels a field needs ------------------------------------------------
// The tile grid is turned 45° to the sky, so a square field touches ~100 pixels of each 512-pixel
// row. One request per plane asks for just those pieces (a multi-range request, answered as
// multipart/byteranges; IRSA allows it, CORS preflight included). A server that won't do
// multi-range gets one contiguous block of whole rows instead.

/** Columns x0..x1 (inclusive) of row y. */
export interface Span {
  y: number;
  x0: number;
  x1: number;
}

/** Ranges per request: Apache's default MaxRanges is 200; stay well under. */
export const MAX_RANGES = 100;

interface Part {
  start: number;
  bytes: Uint8Array;
}

function indexOfAscii(buf: Uint8Array, text: string, from: number): number {
  const first = text.charCodeAt(0);
  outer: for (let i = from; i <= buf.length - text.length; i++) {
    if (buf[i] !== first) continue;
    for (let j = 1; j < text.length; j++) if (buf[i + j] !== text.charCodeAt(j)) continue outer;
    return i;
  }
  return -1;
}

/** Parts of a multipart/byteranges body, each with its absolute start offset. */
export function parseMultipart(buf: Uint8Array, boundary: string): Part[] {
  const parts: Part[] = [];
  const marker = `--${boundary}`;
  let i = indexOfAscii(buf, marker, 0);
  while (i >= 0) {
    i += marker.length;
    if (buf[i] === 45 && buf[i + 1] === 45) break; // "--": closing marker
    const headEnd = indexOfAscii(buf, "\r\n\r\n", i);
    if (headEnd < 0) break;
    const head = String.fromCharCode(...buf.subarray(i, headEnd));
    const m = /content-range:\s*bytes\s+(\d+)-(\d+)/i.exec(head);
    if (!m) throw new Error("multipart part without Content-Range");
    const start = Number(m[1]);
    const len = Number(m[2]) - start + 1;
    const dataStart = headEnd + 4;
    if (dataStart + len > buf.length) throw new Error("multipart part cut short");
    parts.push({ start, bytes: buf.subarray(dataStart, dataStart + len) });
    i = indexOfAscii(buf, marker, dataStart + len);
  }
  return parts;
}

async function withRetry<T>(job: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await job();
    } catch (e) {
      const retry = !signal?.aborted && !(e instanceof RangeIgnored) && ((e as { retry?: boolean }).retry ?? e instanceof TypeError);
      if (!retry || attempt >= RETRIES) throw e;
      await new Promise((r) => setTimeout(r, 400 * 2 ** attempt));
    }
  }
}

/** Several byte ranges of one file in one request. */
async function multiRange(fetcher: Fetcher, url: string, ranges: [number, number][], signal?: AbortSignal): Promise<Part[]> {
  if (ranges.length === 1) return [{ start: ranges[0][0], bytes: await range(fetcher, url, ranges[0][0], ranges[0][1], signal) }];
  return withRetry(async () => {
    const header = `bytes=${ranges.map(([a, b]) => `${a}-${b}`).join(",")}`;
    const res = await fetcher(url, { headers: { Range: header }, cache: "no-store", signal });
    if (res.status === 200) {
      await res.body?.cancel();
      throw new RangeIgnored("IRSA ignored the multi-range request");
    }
    if (res.status !== 206) throw Object.assign(new Error(`IRSA answered ${res.status}`), { retry: res.status >= 500 });
    const buf = new Uint8Array(await res.arrayBuffer());
    const type = res.headers.get("content-type") ?? "";
    const boundary = /boundary=("?)([^";]+)\1/i.exec(type)?.[2];
    if (boundary) return parseMultipart(buf, boundary);
    // One part: the server merged the ranges into one.
    const m = /bytes\s+(\d+)-/.exec(res.headers.get("content-range") ?? "");
    if (!m) throw new Error("206 without Content-Range");
    return [{ start: Number(m[1]), bytes: buf }];
  }, signal);
}

/**
 * The pixels of `spans` from one plane of an HDU whose data starts at `dataStart`, packed span
 * after span (4 bytes per pixel, big-endian as in the file).
 */
export async function readSpans(
  info: TileInfo,
  dataStart: number,
  plane: number,
  spans: readonly Span[],
  fetcher: Fetcher,
  signal?: AbortSignal,
): Promise<DataView> {
  const url = tileUrl(info.tile);
  const rowBytes = info.nx * 4;
  const at = (s: Span): [number, number] => {
    const a = dataStart + (plane * info.ny + s.y) * rowBytes + s.x0 * 4;
    return [a, a + (s.x1 - s.x0 + 1) * 4 - 1];
  };
  const total = spans.reduce((n, s) => n + (s.x1 - s.x0 + 1), 0);
  const out = new Uint8Array(total * 4);
  const dest: number[] = [];
  let pos = 0;
  for (const s of spans) {
    dest.push(pos);
    pos += (s.x1 - s.x0 + 1) * 4;
  }
  const copy = (parts: Part[], from: number, to: number) => {
    for (let i = from; i < to; i++) {
      const [a, b] = at(spans[i]);
      const p = parts.find((q) => q.start <= a && q.start + q.bytes.length > b);
      if (!p) throw new Error("a requested range is missing from IRSA's answer");
      out.set(p.bytes.subarray(a - p.start, b - p.start + 1), dest[i]);
    }
  };
  const groups: [number, number][] = [];
  for (let i = 0; i < spans.length; i += MAX_RANGES) groups.push([i, Math.min(spans.length, i + MAX_RANGES)]);
  await Promise.all(
    groups.map(async ([from, to]) => {
      try {
        copy(await multiRange(fetcher, url, spans.slice(from, to).map(at), signal), from, to);
      } catch (e) {
        if (!(e instanceof RangeIgnored)) throw e;
        // Fallback: whole rows from the first span's row to the last one's, in one range.
        const ys = spans.slice(from, to).map((s) => s.y);
        const y0 = Math.min(...ys);
        const y1 = Math.max(...ys);
        const a = dataStart + (plane * info.ny + y0) * rowBytes;
        const block = await range(fetcher, url, a, a + (y1 - y0 + 1) * rowBytes - 1, signal);
        copy([{ start: a, bytes: block }], from, to);
      }
    }),
  );
  return new DataView(out.buffer);
}

/** Where each output pixel of a field reads from: tile and nearest cube pixel, or none. */
export interface FieldPlan {
  n: number;
  pixelArcsec: number;
  /** Per output pixel (row-major, north up, east left): index into `tiles`, or −1. */
  tileOf: Int16Array;
  /** Per output pixel: index of its cube pixel in that tile's packed spans. */
  offset: Int32Array;
  tiles: { info: TileInfo; spans: Span[] }[];
}

/**
 * Sample a square field of `fovArcmin` (toSky gives the tangent plane around its centre) at the
 * cube's own pixel size, nearest neighbour: one output pixel per cube pixel, nothing invented.
 */
export async function planField(
  fovArcmin: number,
  toSky: (x: number, y: number, n: number, pixelArcsec: number) => { ra: number; dec: number },
  infoFor: (tile: number) => Promise<TileInfo>,
  /** Fit exactly `fovArcmin` (another picture's field): pixels within 5% of the cube's own size. */
  exact = false,
): Promise<FieldPlan> {
  const n = Math.max(9, Math.round((fovArcmin * 60) / CUBE_PIXEL_ARCSEC));
  const pixelArcsec = exact ? (fovArcmin * 60) / n : CUBE_PIXEL_ARCSEC;
  const sky = new Float64Array(2 * n * n);
  const tileIds = new Int32Array(n * n);
  const wanted = new Set<number>();
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const p = toSky(i, j, n, pixelArcsec);
      const k = j * n + i;
      sky[2 * k] = p.ra;
      sky[2 * k + 1] = p.dec;
      tileIds[k] = ang2pixNest(TILE_ORDER, p.ra, p.dec);
      wanted.add(tileIds[k]);
    }
  }
  const infos = new Map<number, TileInfo>();
  await Promise.all([...wanted].map(async (t) => infos.set(t, await infoFor(t))));
  const order = [...wanted];
  const xs = new Int32Array(n * n);
  const ys = new Int32Array(n * n);
  const tileOf = new Int16Array(n * n).fill(-1);
  // Per tile, per row: the columns needed.
  const rows = order.map(() => new Map<number, [number, number]>());
  for (let k = 0; k < n * n; k++) {
    const info = infos.get(tileIds[k])!;
    const xy = skyToTilePixel(info.wcs, sky[2 * k], sky[2 * k + 1], info.nx);
    if (!xy) continue;
    const x = Math.min(info.nx - 1, Math.max(0, Math.round(xy.x)));
    const y = Math.min(info.ny - 1, Math.max(0, Math.round(xy.y)));
    const t = order.indexOf(tileIds[k]);
    xs[k] = x;
    ys[k] = y;
    tileOf[k] = t;
    const r = rows[t].get(y);
    rows[t].set(y, r ? [Math.min(r[0], x), Math.max(r[1], x)] : [x, x]);
  }
  const tiles = order.map((t, ti) => ({
    info: infos.get(t)!,
    spans: [...rows[ti].entries()].sort((a, b) => a[0] - b[0]).map(([y, [x0, x1]]) => ({ y, x0, x1 })),
  }));
  // Packed index of each row's first pixel, per tile.
  const rowStart = tiles.map((t) => {
    const m = new Map<number, number>();
    let pos = 0;
    for (const s of t.spans) {
      m.set(s.y, pos - s.x0);
      pos += s.x1 - s.x0 + 1;
    }
    return m;
  });
  const offset = new Int32Array(n * n);
  for (let k = 0; k < n * n; k++) {
    if (tileOf[k] >= 0) offset[k] = rowStart[tileOf[k]].get(ys[k])! + xs[k];
  }
  return { n, pixelArcsec, tileOf, offset, tiles };
}

/**
 * The same field, reading only the pixels where `keep` is set (the rest come back as "no data").
 * For data that matters around a few sources only, such as exposure counts near bright stars.
 */
export function maskPlan(plan: FieldPlan, keep: Uint8Array): FieldPlan {
  // Where each packed index of the full plan sits in its tile: row y and column x.
  const starts = plan.tiles.map((t) => {
    const pos: number[] = [];
    let p = 0;
    for (const s of t.spans) {
      pos.push(p);
      p += s.x1 - s.x0 + 1;
    }
    return pos;
  });
  const locate = (t: number, o: number): { y: number; x: number } => {
    const pos = starts[t];
    let lo = 0;
    let hi = pos.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (pos[mid] <= o) lo = mid;
      else hi = mid - 1;
    }
    const span = plan.tiles[t].spans[lo];
    return { y: span.y, x: span.x0 + o - pos[lo] };
  };
  const total = plan.n * plan.n;
  const xs = new Int32Array(total);
  const ys = new Int32Array(total);
  const tileOf = new Int16Array(total).fill(-1);
  const rows = plan.tiles.map(() => new Map<number, [number, number]>());
  for (let k = 0; k < total; k++) {
    const t = plan.tileOf[k];
    if (t < 0 || !keep[k]) continue;
    const { x, y } = locate(t, plan.offset[k]);
    xs[k] = x;
    ys[k] = y;
    tileOf[k] = t;
    const r = rows[t].get(y);
    rows[t].set(y, r ? [Math.min(r[0], x), Math.max(r[1], x)] : [x, x]);
  }
  const tiles = plan.tiles.map((t, ti) => ({
    info: t.info,
    spans: [...rows[ti].entries()].sort((a, b) => a[0] - b[0]).map(([y, [x0, x1]]) => ({ y, x0, x1 })),
  }));
  const rowStart = tiles.map((t) => {
    const m = new Map<number, number>();
    let pos = 0;
    for (const s of t.spans) {
      m.set(s.y, pos - s.x0);
      pos += s.x1 - s.x0 + 1;
    }
    return m;
  });
  const offset = new Int32Array(total);
  for (let k = 0; k < total; k++) if (tileOf[k] >= 0) offset[k] = rowStart[tileOf[k]].get(ys[k])! + xs[k];
  return { n: plan.n, pixelArcsec: plan.pixelArcsec, tileOf, offset, tiles };
}

/** One wavelength plane sampled onto the field (NaN where the cube has no data). */
export async function samplePlane(
  plan: FieldPlan,
  plane: number,
  fetcher: Fetcher,
  signal?: AbortSignal,
): Promise<Float32Array> {
  const out = new Float32Array(plan.n * plan.n).fill(NaN);
  if (BLANK_FRAMES.has(plane)) return out;
  const views = await Promise.all(
    plan.tiles.map((t) => readSpans(t.info, t.info.dataStart, plane, t.spans, fetcher, signal)),
  );
  for (let k = 0; k < out.length; k++) {
    const t = plan.tileOf[k];
    if (t >= 0) out[k] = views[t].getFloat32(4 * plan.offset[k], false);
  }
  return out;
}

/** One plane of NHITS or FLAGS sampled onto the field (0 where the cube has no data). */
export async function sampleInt(
  plan: FieldPlan,
  name: IntHdu,
  plane: number,
  fetcher: Fetcher,
  signal?: AbortSignal,
): Promise<Int32Array> {
  const out = new Int32Array(plan.n * plan.n);
  if (BLANK_FRAMES.has(plane)) return out;
  const views = await Promise.all(
    plan.tiles.map(async (t) =>
      t.spans.length ? readSpans(t.info, await findHdu(t.info, name, fetcher, signal), plane, t.spans, fetcher, signal) : new DataView(new ArrayBuffer(0)),
    ),
  );
  for (let k = 0; k < out.length; k++) {
    const t = plan.tileOf[k];
    if (t >= 0) out[k] = views[t].getInt32(4 * plan.offset[k], false);
  }
  return out;
}

/** Run async jobs with at most `limit` in flight. */
export async function pool<T>(jobs: (() => Promise<T>)[], limit: number, onDone?: (done: number) => void): Promise<T[]> {
  const out: T[] = new Array(jobs.length);
  let next = 0;
  let done = 0;
  async function worker() {
    while (next < jobs.length) {
      const i = next++;
      out[i] = await jobs[i]();
      onDone?.(++done);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, jobs.length) }, worker));
  return out;
}
