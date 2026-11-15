// The colour filters (src/colour, the Viewer's "Add filters") on the review plates: To check one at
// a time (#/candidates/one=1) and #/c/<id>. The filter follows the queue from object to object,
// never shows the last object's picture, stops its reads, and keys stay with the menu or the ballot
// they were pressed in. NASA's map is a fake IRSA (fetcher injection): no network.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Candidate, CandidatesResponse, Det, Epoch, FramesResponse, LoadedFrames } from "../api/types";
import type { Fetcher } from "../colour/cube";
import { ang2pixNest } from "../colour/hpx";
import ref from "../colour/hpx_ref.json";
import type { VoteApi } from "../vote/store";
import { cand } from "./testkit";

// Two objects in different tiles of NASA's map, each well inside its tile (real tile headers in
// hpx_ref.json): WISE 0855's field and a spot at 100.0 +41.85.
const C1 = cand("one", { ra: 133.79, dec: -7.245, clock: "months", det: 2 });
const C2 = cand("two", { ra: 100.0, dec: 41.85, clock: "months", det: 2 });
const T1 = ang2pixNest(5, C1.ra, C1.dec);
const T2 = ang2pixNest(5, C2.ra, C2.dec);

const N = 40;
const epoch = (id: string, det: Det, mjd: number): Epoch => ({
  id,
  kind: "visit",
  mjd_mid: mjd,
  mjd_start: mjd - 10,
  mjd_end: mjd + 10,
  det,
  um_min: 1.1,
  um_max: 1.6,
  n_exposures: 20,
  flag_frac: 0,
  sat_frac: 0,
  data_url: `frames/${id}.bin`,
  scale: 0.001,
  zero: 0,
});
const framesAt = (c: Pick<Candidate, "ra" | "dec">): FramesResponse => ({
  clock: "months",
  center: { ra: c.ra, dec: c.dec },
  pixel_arcsec: 6.15,
  size: [N, N],
  epochs: [1, 2, 3].map((v) => epoch(`d2-v${v}`, 2, 60700 + 180 * v)),
  gaia: [],
  known: [],
  data_release: "test",
  archive_checked: "2026-11-15",
});
const loaded = (c: Pick<Candidate, "ra" | "dec" | "id">): LoadedFrames => ({
  frames: framesAt(c),
  jsonUrl: `https://example.test/data/cand/${c.id}/frames.json`,
  origin: "static",
});

// The review pages' data: two objects, this browser's votes, frames at once (no live wait).
const castVote = vi.fn(async (_id: string, _a: string) => {});
const api = {
  mode: "local",
  notice: null,
  countsFor: async () => new Map(),
  castVote: (id: string, a: string) => castVote(id, a),
  myVote: () => null,
  voterId: () => "test",
} as unknown as VoteApi;
vi.mock("./data", async (orig) => ({
  ...(await orig<typeof import("./data")>()),
  candidateSet: async () => ({ data: { candidates: [C1, C2] } as unknown as CandidatesResponse, candidates: [C1, C2], sample: false }),
  votes: async () => ({ api, kv: { get: (k: string) => (k === "skyshift.review.practice" ? "done" : null), set: () => {} } }),
  framesFor: async (c: Candidate) => loaded(c),
  hasFrames: () => true,
  preload: () => {},
  suspectFor: async () => null,
}));
// Practice plates (featured targets) load through the API client.
vi.mock("../api/client", async (orig) => ({
  ...(await orig<typeof import("../api/client")>()),
  loadFrames: async () => loaded({ id: "barnard", ra: 269.452, dec: 4.693 }),
}));

const { Plate } = await import("./plate");
const { mountQueue } = await import("./review");

// ---- NASA's map, as IRSA serves it --------------------------------------------------------------
const BLOCK = 2880;
const card = (k: string, v: string) => `${k.padEnd(8)}= ${v}`.padEnd(80);
const block = (cards: string[]) => {
  const s = cards.join("") + "END".padEnd(80);
  return s.padEnd(Math.ceil(s.length / BLOCK) * BLOCK);
};
/** A cube tile's primary and IMAGE headers, with the real tile's WCS. */
function tileHeader(tile: number): Uint8Array {
  const h = (ref.headers as Record<string, Record<string, number>>)[String(tile)];
  const wcs = ["CRPIX1", "CRPIX2", "PC1_1", "PC1_2", "PC2_1", "PC2_2", "CDELT1", "CDELT2"].map((k) => card(k, String(h[k])));
  const text =
    block([card("SIMPLE", "T"), card("BITPIX", "8"), card("NAXIS", "0"), card("DATE-BEG", "'2025-05-05T00:00:00'"), card("DATE-END", "'2026-05-14T00:00:00'")]) +
    block([
      card("XTENSION", "'IMAGE   '"),
      card("BITPIX", "-32"),
      card("NAXIS", "3"),
      card("NAXIS1", "512"),
      card("NAXIS2", "512"),
      card("NAXIS3", "102"),
      card("EXTNAME", "'IMAGE   '"),
      card("CTYPE1", "'RA---HPX'"),
      card("CTYPE2", "'DEC--HPX'"),
      ...wcs,
    ]);
  return Uint8Array.from(text, (ch) => ch.charCodeAt(0));
}
const SIZE = 2 * BLOCK + 512 * 512 * 102 * 4;
/** Bytes a..b of a tile: its headers, then faint noise in every pixel (float32, big-endian). */
function tileBytes(tile: number, a: number, b: number): Uint8Array {
  const head = tileHeader(tile);
  const out = new Uint8Array(b - a + 1);
  const f = new DataView(new ArrayBuffer(4));
  for (let o = a; o <= b; o++) {
    if (o < head.length) {
      out[o - a] = head[o];
      continue;
    }
    const k = o - head.length;
    if (k % 4 === 0 || o === a) f.setFloat32(0, ((Math.imul(k >> 2, 2654435761) >>> 0) % 1000) / 1000, false);
    out[o - a] = f.getUint8(k % 4);
  }
  return out;
}
interface Ask {
  tile: number;
  /** A read of pixels (not a header). */
  data: boolean;
  signal: AbortSignal | undefined;
}
/** IRSA: byte ranges (one as 206, several as multipart/byteranges). With `hold`, pixel reads never answer. */
function fakeIrsa() {
  const asks: Ask[] = [];
  const irsa = {
    asks,
    hold: false,
    fetch: (async (url, init) => {
      const tile = Number(/Npix(\d+)_cube\.fits/.exec(url)![1]);
      const ranges = (init.headers as Record<string, string>).Range.replace("bytes=", "")
        .split(",")
        .map((r) => r.split("-").map(Number) as [number, number]);
      const data = ranges[0][0] > 0;
      asks.push({ tile, data, signal: init.signal ?? undefined });
      if (data && irsa.hold) {
        return new Promise<Response>((_, reject) =>
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))),
        );
      }
      if (ranges.length === 1) {
        const [a, b] = ranges[0];
        return new Response(tileBytes(tile, a, b).buffer as ArrayBuffer, { status: 206, headers: { "content-range": `bytes ${a}-${b}/${SIZE}` } });
      }
      const enc = (t: string) => Uint8Array.from(t, (ch) => ch.charCodeAt(0));
      const parts: Uint8Array[] = [];
      for (const [a, b] of ranges) {
        parts.push(enc(`\r\n--XYZ\r\nContent-Type: application/fits\r\nContent-Range: bytes ${a}-${b}/${SIZE}\r\n\r\n`), tileBytes(tile, a, b));
      }
      parts.push(enc("\r\n--XYZ--\r\n"));
      const body = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
      let at = 0;
      for (const p of parts) {
        body.set(p, at);
        at += p.length;
      }
      return new Response(body, { status: 206, headers: { "content-type": "multipart/byteranges; boundary=XYZ" } });
    }) as Fetcher,
  };
  return irsa;
}

// ---- the page around it (jsdom has no canvas, layout or ResizeObserver) -------------------------
let irsa: ReturnType<typeof fakeIrsa>;
function stubPage() {
  irsa = fakeIrsa();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(private cb: () => void) {}
      observe() {
        this.cb();
      }
      disconnect() {}
    },
  );
  if (typeof ImageData === "undefined") {
    vi.stubGlobal(
      "ImageData",
      class {
        constructor(
          public data: Uint8ClampedArray,
          public width: number,
          public height: number,
        ) {}
      },
    );
  }
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 400 });
  // The blink's frames come from the site; NASA's map from IRSA (the queue's plate uses fetch).
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) =>
      /_cube\.fits$/.test(String(url))
        ? irsa.fetch(String(url), init ?? {})
        : new Response(Uint16Array.from({ length: N * N }, (_, i) => 1000 + ((i * 7919) % 41)).buffer),
    ),
  );
  const state: Record<string, unknown> = {};
  const ctx = new Proxy(
    {
      measureText: (s: string) => ({ width: s.length * 6 }),
      createImageData: (w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h }),
      getLineDash: () => [],
    } as Record<string, unknown>,
    {
      get: (t, k: string) => (k in t ? t[k] : k in state ? state[k] : () => undefined),
      set: (_t, k: string, v) => ((state[k] = v), true),
    },
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
  // On screen: the queue and the blink only take keys when their page is visible.
  vi.spyOn(Element.prototype, "getClientRects").mockReturnValue({ length: 1 } as unknown as DOMRectList);
}
function unstubPage() {
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
}

const $ = <T extends Element>(el: ParentNode, sel: string) => el.querySelector(sel) as T;
const choose = (el: ParentNode, mode: string) => {
  $<HTMLButtonElement>(el, ".flt-add").click();
  $<HTMLButtonElement>(el, `.flt-opts [data-flt-mode='${mode}']`).click();
};
const noteText = (el: ParentNode) => $<HTMLElement>(el, ".flt-note").textContent!.replace(/\s+/g, " ").trim();
const canvas = (el: ParentNode) => $<HTMLCanvasElement>(el, ".flt-canvas");
const tilesAsked = (data?: boolean) => [...new Set(irsa.asks.filter((a) => data == null || a.data === data).map((a) => a.tile))];

describe("the colour filters on the review plate", () => {
  let host: HTMLElement;
  let plate: InstanceType<typeof Plate>;
  beforeEach(() => {
    stubPage();
    host = document.createElement("section");
    document.body.append(host);
    plate = new Plate(host, {}, { fetcher: irsa.fetch });
    plate.mount();
  });
  afterEach(() => {
    plate.unmount();
    unstubPage();
  });

  it("has the Viewer's Add filters above the picture, and downloads nothing until a filter is chosen", async () => {
    expect(await plate.showCandidate(C1)).toBe(true);
    const add = $<HTMLButtonElement>(host, ".flt-add");
    expect(add.textContent).toContain("Add filters");
    expect(add.closest(".flt-bar")!.nextElementSibling).toBe($(host, ".bk-mat")); // just above the plate
    expect([...host.querySelectorAll(".flt-opts [data-flt-mode] b")].map((b) => b.textContent)).toEqual([
      "No filter",
      "Difference",
      "Temperature",
      "Star colours",
      "Single colour",
      "Molecules",
    ]);
    add.click();
    expect($<HTMLElement>(host, ".flt-menu").hidden).toBe(false);
    await new Promise((r) => setTimeout(r, 50));
    expect(irsa.asks).toHaveLength(0);
    expect($<HTMLElement>(host, ".flt-layer").hidden).toBe(true);
  });

  it("reads NASA's map at the object on the plate, and follows the queue to the next object", async () => {
    await plate.showCandidate(C1);
    choose(host, "wave");
    await vi.waitFor(() => expect(canvas(host).width).toBeGreaterThan(0), { timeout: 5000 });
    expect(tilesAsked()).toEqual([T1]);
    expect($<HTMLElement>(host, ".flt-layer").hidden).toBe(false);
    expect($<HTMLElement>(host, ".flt-key").hidden).toBe(false);
    expect($(host, ".flt-active-name").textContent).toBe("Filter: Single colour, 3.29 micrometres");

    // The next object: the last one's picture goes at once, before the new frames are even in.
    irsa.hold = true;
    const next = plate.showCandidate(C2);
    expect(canvas(host).width).toBe(0);
    expect(noteText(host)).toMatch(/Waiting for the new spot to load/);
    await next;
    // The same filter, read at the new object's position, and nothing more from the old one's tile.
    await vi.waitFor(() => expect(tilesAsked(true)).toContain(T2));
    const fromOld = irsa.asks.filter((a) => a.tile === T1).length;
    expect($(host, ".flt-active-name").textContent).toBe("Filter: Single colour, 3.29 micrometres");
    expect(noteText(host)).toMatch(/Reading from NASA's SPHEREx map/);
    expect(canvas(host).width).toBe(0); // its pixels are still on the way: no picture yet
    await new Promise((r) => setTimeout(r, 50));
    expect(irsa.asks.filter((a) => a.tile === T1).length).toBe(fromOld);
  });

  it("stops the last object's reads when the queue moves on", async () => {
    irsa.hold = true;
    await plate.showCandidate(C1);
    choose(host, "wave");
    await vi.waitFor(() => expect(tilesAsked(true)).toEqual([T1]));
    const old = irsa.asks.filter((a) => a.data);
    expect(old.every((a) => a.signal && !a.signal.aborted)).toBe(true);

    // At once, not when the next object's frames are in.
    const next = plate.showCandidate(C2);
    expect(old.every((a) => a.signal!.aborted)).toBe(true);
    await next;
    await vi.waitFor(() => expect(tilesAsked(true)).toContain(T2));
    expect(irsa.asks.filter((a) => a.data && a.tile === T2).every((a) => !a.signal!.aborted)).toBe(true);
    expect($<HTMLElement>(host, ".flt-note").dataset.kind).toBe("info"); // reading, not a failure from the old reads
  });

  it("goes with the plate when the page hides, and comes back fresh", async () => {
    irsa.hold = true;
    await plate.showCandidate(C1);
    choose(host, "wave");
    await vi.waitFor(() => expect(tilesAsked(true)).toEqual([T1]));
    const asked = irsa.asks.length;
    plate.unmount();
    expect(irsa.asks.filter((a) => a.data).every((a) => a.signal!.aborted)).toBe(true);
    expect(host.querySelector(".flt-bar")).toBeNull();
    await new Promise((r) => setTimeout(r, 50));
    expect(irsa.asks.length).toBe(asked);

    plate.mount(); // shows C1 again, with no filter until one is chosen
    await vi.waitFor(() => expect($(host, ".flt-add")?.textContent).toContain("Add filters"));
    expect($(host, "[data-flt-mode='none']").getAttribute("aria-pressed")).toBe("true");
  });

  it("stays in the practice round (featured targets), where Difference lives in the same menu", async () => {
    expect(await plate.showTarget("barnard")).toBe(true);
    expect($<HTMLElement>(host, ".flt-bar").hidden).toBe(false);
    choose(host, "diff");
    expect($(host, ".bk").getAttribute("data-mode")).toBe("difference");
    expect(irsa.asks).toHaveLength(0);
  });
});

describe("keys between the filter menu and the ballot (To check, one at a time)", () => {
  let host: HTMLElement;
  let queue: ReturnType<typeof mountQueue>;
  const shownId = () => $(host, ".rq-card .rv-id a").getAttribute("href");
  const press = (target: Element, key: string) =>
    target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));

  beforeEach(async () => {
    stubPage();
    castVote.mockClear();
    host = document.createElement("main");
    document.body.append(host);
    queue = mountQueue(host);
    queue.show(null);
    // The ballot opens once the plate's frames are on screen.
    await vi.waitFor(() => expect($<HTMLButtonElement>(host, ".rv-choice").disabled).toBe(false), { timeout: 5000 });
    expect(shownId()).toBe("#/c/one");
  });
  afterEach(() => {
    queue.destroy();
    unstubPage();
  });

  it("never votes, skips or moves on with keys pressed in the filter menu", () => {
    $<HTMLButtonElement>(host, ".flt-add").click();
    const option = $<HTMLButtonElement>(host, ".flt-opts [data-flt-mode='cold']");
    option.focus();
    for (const key of ["1", "2", "3", "4", "s", "ArrowRight", "ArrowLeft", "Enter"]) press(option, key);
    // The open menu's own button counts as the menu too.
    for (const key of ["1", "s", "ArrowRight"]) press($(host, ".flt-add"), key);
    expect(castVote).not.toHaveBeenCalled();
    expect(shownId()).toBe("#/c/one");
    expect($<HTMLElement>(host, ".flt-menu").hidden).toBe(false);
    expect([...host.querySelectorAll(".rv-choice")].every((b) => b.getAttribute("aria-pressed") === "false")).toBe(true);
    expect(irsa.asks).toHaveLength(0); // keys chose no filter either
  });

  it("keeps the slider's arrows for the slider, not for the queue", async () => {
    irsa.hold = true;
    choose(host, "wave");
    const slider = $<HTMLInputElement>(host, ".flt-frame");
    expect(document.activeElement).toBe(slider);
    press(slider, "ArrowRight");
    press(slider, "1");
    expect(shownId()).toBe("#/c/one");
    expect(castVote).not.toHaveBeenCalled();
  });

  it("votes with the ballot's keys without touching the filters, and the filter follows to the next object", async () => {
    $<HTMLButtonElement>(host, ".flt-add").click();
    press(document.body, "Escape"); // closes the menu; the keys are the queue's again
    expect($<HTMLElement>(host, ".flt-menu").hidden).toBe(true);
    const choice = $<HTMLButtonElement>(host, ".rv-choice[data-answer='changing']");
    choice.focus();
    press(choice, "2");
    await vi.waitFor(() => expect(castVote).toHaveBeenCalledWith("one", "changing"));
    expect($<HTMLElement>(host, ".flt-menu").hidden).toBe(true);
    expect($(host, "[data-flt-mode='none']").getAttribute("aria-pressed")).toBe("true");
    expect(irsa.asks).toHaveLength(0);

    // With a filter on, S moves the queue on and the filter reads at the next object.
    irsa.hold = true;
    choose(host, "wave");
    await vi.waitFor(() => expect(tilesAsked(true)).toEqual([T1]));
    press(document.body, "s");
    await vi.waitFor(() => expect(shownId()).toBe("#/c/two"));
    await vi.waitFor(() => expect(tilesAsked(true)).toContain(T2));
    expect(irsa.asks.filter((a) => a.data && a.tile === T1).every((a) => a.signal!.aborted)).toBe(true);
  });
});
