// More photos of one hours visit (contracts/api.md v3.23 `page`): which answers page, and how a page joins.
import { describe, expect, it } from "vitest";
import type { Det, Epoch, FramesResponse, Measured } from "../api/types";
import { mergePage, pageable, pageUrl, pagesText } from "./pages";
import { visitSpan } from "./strip";

const ep = (id: string, mjd: number, det: Det = 2): Epoch => ({
  id, kind: "exposure", mjd_mid: mjd, mjd_start: mjd - 0.0007, mjd_end: mjd + 0.0007, det,
  um_min: 1.1, um_max: 1.6, n_exposures: 1, flag_frac: 0, sat_frac: 0,
  data_url: `/api/data/k0/frames/h20_d${det}_${id}.bin`, scale: 0.001, zero: 0,
});
const measured = (ids: string[]): Measured => ({
  zone: "hunt", stage: "final", zone_box: { ra: [0, 1], dec: [0, 1] }, clock: "hours",
  photos: ids.length, photos_missing: 0, truncated: false,
  counts: { star: ids.length, known: 0, mover: 0, suspect: 0, artifact: 0, other: 0 },
  detections: ids.map((id) => ({
    epoch_id: id, ra: 10, dec: 20, snr: 30, flux_ujy: 100, n_dots: 1, class: "star" as const,
    source_id: 1, gaia_id: "9", known_name: null, track_id: "t1", grade: null, reasons: [],
  })),
  tracks: [{ id: "t1", class: "mover", suspect_id: null, known_name: null, evidence: "two_exposures",
    dots: ids.map((id, k) => ({ ra: 10, dec: 20, mjd: 61000 + k, epoch_id: id })) }],
  shifts: [],
});
const resp = (eps: Epoch[], more?: boolean, total?: number): FramesResponse => ({
  clock: "hours", center: { ra: 10, dec: 20 }, pixel_arcsec: 6.15, size: [195, 195], epochs: eps,
  gaia: [{ id: "9", ra: 10, dec: 20, pmra: 0, pmdec: 0, parallax: null, g: 12 }],
  known: eps.map((e) => ({ name: "Vesta", epoch_id: e.id, ra: 10, dec: 20, v: 7, source: "mpc" as const })),
  data_release: "DR1+QR3", archive_checked: "2026-11-16",
  measured: measured(eps.map((e) => e.id)), measured_reason: null,
  ...(more === undefined ? {} : { more }), ...(total === undefined ? {} : { visit_exposures: total }),
});
const LIVE = "https://skyshift.test/api/frames?ra=10&dec=20&clock=hours&fov=20&det=2";

describe("hours pages", () => {
  it("pages only live raw hours answers", () => {
    const r = resp([ep("d2-a", 61000)], true, 57);
    expect(pageable(r, LIVE)).toBe(true);
    expect(pageable(resp([ep("d2-a", 61000)]), LIVE)).toBe(true); // cached before v3.23: one try tells
    expect(pageable({ ...r, more: false }, LIVE)).toBe(false);
    expect(pageable(r, "https://skyshift.test/data/barnard/frames_hours_20.json")).toBe(false); // static
    expect(pageable({ ...r, clock: "months" }, LIVE)).toBe(false);
    expect(pageable(r, `${LIVE}&view=resid`)).toBe(false);
    expect(pageable(r, `${LIVE}&page=2`)).toBe(false);
    expect(pageable({ ...r, epochs: [{ ...ep("d2-a", 61000), center: { ra: 10, dec: 20 } }] }, LIVE)).toBe(false);
    expect(pageUrl(`${LIVE}&mjd=61000.5`, 3)).toBe(`${LIVE}&mjd=61000.5&page=3`);
  });

  it("adds a page's exposures in time order, with their known objects and dots", () => {
    const p0 = resp([ep("d2-a", 61000), ep("d2-c", 61010)], true, 9);
    const p1 = resp([ep("d2-b", 61005), ep("d2-a", 61000), ep("d2-d", 61020)], false, 9);
    const url = pageUrl(LIVE, 1);
    const m = mergePage(p0, p1, url)!;
    expect(m.epochs.map((e) => e.id)).toEqual(["d2-a", "d2-b", "d2-c", "d2-d"]);
    expect(m.epochs.find((e) => e.id === "d2-b")!.data_url).toBe("https://skyshift.test/api/data/k0/frames/h20_d2_d2-b.bin");
    expect(m.epochs.find((e) => e.id === "d2-a")).toBe(p0.epochs[0]); // page 0's stay as they were
    expect(m.known.map((k) => k.epoch_id)).toEqual(["d2-a", "d2-c", "d2-b", "d2-d"]); // d2-a once
    expect(m.more).toBe(false);
    expect(m.visit_exposures).toBe(9);
    expect(m.measured!.photos).toBe(5);
    expect(m.measured!.tracks[0].dots.map((d) => d.epoch_id)).toHaveLength(5);
    expect(p0.epochs).toHaveLength(2); // pure
    // nothing new (an API without pages answers page 0 again), or another grid: null
    expect(mergePage(p0, p0, url)).toBeNull();
    expect(mergePage(p0, { ...p1, center: { ra: 10.01, dec: 20 } }, url)).toBeNull();
    expect(mergePage(p0, { ...p1, size: [300, 300] }, url)).toBeNull();
  });

  it("counts the photos of the visit", () => {
    expect(pagesText(6, 57)).toBe("6 of 57 photos of this visit");
    expect(pagesText(1, null)).toBe("1 photo of this visit");
    expect(pagesText(6, 4)).toBe("6 photos of this visit");
  });

  it("dates a months visit by its weeks", () => {
    // 12 and 28 Dec 2025 are MJD 61021 and 61037
    expect(visitSpan(61021.2, 61037.9)).toBe("12–28 Dec 2025");
    expect(visitSpan(61007.5, 61023.5)).toBe("28 Nov – 14 Dec 2025");
    expect(visitSpan(61029.5, 61046.5)).toBe("20 Dec 2025 – 6 Jan 2026");
    expect(visitSpan(61021.2, 61021.3)).toBe("12 Dec 2025");
  });
});
