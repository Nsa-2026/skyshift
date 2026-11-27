// The whole visit on the hours clock (contracts/api.md v3.23 `page`): where a plate's visit continues,
// how a page joins, which page to ask next, and the line under the strip.
import { describe, expect, it } from "vitest";
import type { Det, Epoch, FramesResponse, Measured } from "../api/types";
import { joinPage, liveVisitUrl, newStream, nextPage, pageUrl, pagesText, partialLacksPhotos, photosOf, retryDelay, streamDone, visitMjd, visitSource } from "./pages";
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
const STATIC = "https://skyshift.test/data/barnard/frames_hours_20.json";

describe("the whole hours visit", () => {
  it("continues a live answer from page 1, and a saved one live at its own visit from page 0", () => {
    const r = resp([ep("d2-a", 61000), ep("d2-b", 61002), ep("d2-c", 61010)], true, 57);
    expect(visitSource(r, LIVE, 20, 2)).toEqual({ url0: LIVE, start: 1, total: 57, complete: false });
    expect(visitSource({ ...r, more: false }, LIVE, 20, 2)).toMatchObject({ start: 1, complete: true });
    // a static file (a featured target, an object's saved frames): the same grid and visit, live
    const s = visitSource({ ...r, more: undefined, visit_exposures: undefined }, STATIC, 20, 2)!;
    expect(s).toMatchObject({ start: 0, total: null, complete: false });
    expect(s.url0).toBe(`${location.origin}/api/frames?ra=10&dec=20&clock=hours&fov=20&det=2&mjd=61002`); // the site's own API
    // an object's find decides the visit
    expect(visitSource(r, STATIC, 20, 2, 61010.5)!.url0).toContain("&mjd=61010.5");
    // its partner detector in the same static file: its own request at the same time
    expect(visitSource(resp([ep("d2-a", 61000), ep("d5-a", 61000, 5)]), STATIC, 20, 5, 61000)!.url0).toContain("det=5&mjd=61000");
    // nothing to continue: months, a derived view, a page already, frames that follow a mover, no such detector
    expect(visitSource({ ...r, clock: "months" }, LIVE, 20, 2)).toBeNull();
    expect(visitSource(r, `${LIVE}&view=resid`, 20, 2)).toBeNull();
    expect(visitSource(r, `${LIVE}&page=2`, 20, 2)).toBeNull();
    expect(visitSource({ ...r, epochs: [{ ...ep("d2-a", 61000), center: { ra: 10, dec: 20 } }] }, LIVE, 20, 2)).toBeNull();
    expect(visitSource(r, STATIC, 20, 4)).toBeNull();
    expect(visitMjd(r, 2)).toBe(61002);
    expect(liveVisitUrl(r, 60, 3, null)).toBe(`${location.origin}/api/frames?ra=10&dec=20&clock=hours&fov=60&det=3`);
    expect(pageUrl(`${LIVE}&mjd=61000.5`, 3)).toBe(`${LIVE}&mjd=61000.5&page=3`);
    expect(pageUrl(`${LIVE}&page=3`, 0)).toBe(LIVE);
  });

  it("joins a page's new exposures in time order, keeps what was there, and refuses another grid", () => {
    const p0 = resp([ep("d2-a", 61000), ep("d2-c", 61010)], true, 9);
    const p1 = resp([ep("d2-b", 61005), ep("d2-a", 61000), ep("d2-d", 61020)], false, 9);
    const url = pageUrl(LIVE, 1);
    const j = joinPage(p0, p1, url)!;
    const m = j.resp;
    expect(j.added).toBe(2);
    expect(m.epochs.map((e) => e.id)).toEqual(["d2-a", "d2-b", "d2-c", "d2-d"]);
    expect(m.epochs.find((e) => e.id === "d2-b")!.data_url).toBe("https://skyshift.test/api/data/k0/frames/h20_d2_d2-b.bin");
    expect(m.epochs.find((e) => e.id === "d2-a")).toBe(p0.epochs[0]); // what was there stays as it was
    expect(m.known.map((k) => k.epoch_id)).toEqual(["d2-a", "d2-c", "d2-b", "d2-d"]); // d2-a once
    // the page's dots of exposures already here are not counted twice
    expect(m.measured!.detections.map((d) => d.epoch_id).sort()).toEqual(["d2-a", "d2-b", "d2-c", "d2-d"]);
    expect(m.measured!.tracks[0].dots.map((d) => d.epoch_id)).toHaveLength(4);
    expect(p0.epochs).toHaveLength(2); // pure
    // nothing new (a static set that holds the page already): the same answer
    expect(joinPage(m, p1, url)).toEqual({ resp: m, added: 0 });
    // another grid: null
    expect(joinPage(p0, { ...p1, center: { ra: 10.01, dec: 20 } }, url)).toBeNull();
    expect(joinPage(p0, { ...p1, size: [300, 300] }, url)).toBeNull();
    expect(photosOf(m, 2)).toBe(4);
    expect(photosOf(m, 5)).toBe(0);
  });

  it("asks a few pages at once once the visit's size is known, and stops at its end", () => {
    // a live page 0 of 6 photos, 18 in the visit: pages 1 and 2 at once (6 + 2 × 6 = 18)
    const s = newStream(2, { url0: LIVE, start: 1, total: 18, complete: false });
    expect(nextPage(s, 6)).toBe(1);
    s.asking.add(1);
    expect(nextPage(s, 6)).toBe(2);
    s.asking.add(2);
    expect(nextPage(s, 6)).toBeNull();
    // page 1 brought only 4 (a short page): one more
    s.asking.delete(1);
    s.got.add(1);
    expect(nextPage(s, 10)).toBe(3);
    // page 2 said it was the last: done
    s.asking.delete(2);
    s.got.add(2);
    s.last = 2;
    expect(nextPage(s, 16)).toBeNull();
    expect(streamDone(s, 16)).toBe(true);
    // a static file: page 0 alone first (its answer says how many photos the visit has)
    const t = newStream(2, { url0: LIVE, start: 0, total: null, complete: false });
    expect(nextPage(t, 25)).toBe(0);
    t.asking.add(0);
    expect(nextPage(t, 25)).toBeNull();
    // the saved file already holds the whole visit: nothing more to ask
    t.asking.delete(0);
    t.got.add(0);
    t.total = 25;
    expect(nextPage(t, 25)).toBeNull();
    expect(streamDone(t, 25)).toBe(true);
    // a live answer that is the whole visit already
    expect(streamDone(newStream(2, { url0: LIVE, start: 1, total: 4, complete: true }), 4)).toBe(true);
  });

  it("says how much of the visit is loaded, and plainly what could not be", () => {
    expect(pagesText(6, 57, true)).toBe("6 of 57 photos of this visit loaded · loading…");
    expect(pagesText(25, null, true)).toBe("25 photos of this visit loaded · loading…");
    expect(pagesText(57, 57, false)).toBe("57 of 57 photos of this visit");
    expect(pagesText(51, 57, false, 1)).toBe(
      "51 of 57 photos of this visit loaded; 6 could not be loaded (SkyShift's live service did not answer for them)",
    );
    expect(pagesText(1, null, false)).toBe("1 photo of this visit");
    expect(pagesText(6, 4, false)).toBe("6 photos of this visit");
    // an object to check shows the day of its find
    expect(pagesText(19, 19, false, 0, 4)).toBe("19 of 19 photos of this visit · the plate shows the 4 within a day of the find");
    expect(pagesText(6, 19, true, 0, 4)).toBe("6 of 19 photos of this visit loaded · loading… · the plate shows the 4 within a day of the find");
    expect(pagesText(19, 19, false, 0, 19)).toBe("19 of 19 photos of this visit");
  });

  it("dates a months visit by its weeks", () => {
    // 12 and 28 Dec 2025 are MJD 61021 and 61037
    expect(visitSpan(61021.2, 61037.9)).toBe("12–28 Dec 2025");
    expect(visitSpan(61007.5, 61023.5)).toBe("28 Nov – 14 Dec 2025");
    expect(visitSpan(61029.5, 61046.5)).toBe("20 Dec 2025 – 6 Jan 2026");
    expect(visitSpan(61021.2, 61021.3)).toBe("12 Dec 2025");
  });
});

describe("asking again", () => {
  it("asks a partial page again only when cutouts came too late", () => {
    expect(partialLacksPhotos("2 of 6 cutouts not in time")).toBe(true);
    expect(partialLacksPhotos("Gaia failed; 1 of 6 cutouts not in time")).toBe(true);
    expect(partialLacksPhotos("Gaia failed")).toBe(false);
    expect(partialLacksPhotos("known objects not in time; measured layer unavailable")).toBe(false);
    expect(partialLacksPhotos(null)).toBe(false);
  });
  it("waits 3, 6 and 12 s between tries", () => {
    expect([1, 2, 3].map(retryDelay)).toEqual([3000, 6000, 12000]);
  });
});
