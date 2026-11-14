// The card's spectrum and brightness charts say which blink visits they lack (QA, 14 Nov: WISE 0855's
// charts covered 2 visits while its blink had 3).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { FramesResponse, ObjectResponse, SpectrumPoint } from "../api/types";
import { targetBySlug, viewForTarget } from "../app/state";
import { missingVisitsSentence, visitsMissingFromSpectrum } from "./format";
import { mountCard } from "./index";

const dataDir = join(__dirname, "../../public/data");
const read = <T>(slug: string, file: string): T => JSON.parse(readFileSync(join(dataDir, slug, file), "utf8")) as T;

function card(slug: string, frames: FramesResponse | null): HTMLElement {
  const el = document.createElement("section");
  mountCard(el).show(viewForTarget(targetBySlug(slug)!), read<ObjectResponse>(slug, "object.json"), frames);
  return el;
}

const at = { ra: 10, dec: 10 };
const ep = (det: number, visit: number, mjd: number) =>
  ({ id: `d${det}-v${visit}`, kind: "visit", det, mjd_mid: mjd, mjd_start: mjd - 5, mjd_end: mjd + 5 }) as FramesResponse["epochs"][number];
const months = (epochs: FramesResponse["epochs"]) => ({ clock: "months", center: at, epochs }) as unknown as FramesResponse;
const pt = (det: number, visit: number): SpectrumPoint => ({ um: 1, flux_ujy: 100, err_ujy: 10, det: det as SpectrumPoint["det"], mjd: 0, visit, saturated: false });

describe("visits the blink has and the spectrum lacks", () => {
  const frames = months([ep(2, 1, 60800), ep(5, 1, 60800), ep(2, 2, 61000), ep(5, 2, 61000), ep(2, 3, 61160), ep(5, 3, 61164)]);

  it("finds them over every detector, or in one", () => {
    expect(visitsMissingFromSpectrum(frames, at, 5, [pt(2, 1), pt(5, 2)])).toEqual([{ visit: 3, mjd: 61162 }]);
    expect(visitsMissingFromSpectrum(frames, at, 5, [pt(2, 1), pt(5, 2)], 5)).toEqual([{ visit: 1, mjd: 60800 }, { visit: 3, mjd: 61164 }]);
    expect(visitsMissingFromSpectrum(frames, at, 5, [pt(2, 1), pt(2, 2), pt(2, 3)])).toEqual([]);
  });

  it("says nothing without months frames near the view, or without a spectrum", () => {
    expect(visitsMissingFromSpectrum(null, at, 5, [pt(2, 1)])).toEqual([]);
    expect(visitsMissingFromSpectrum({ ...frames, clock: "hours" } as FramesResponse, at, 5, [pt(2, 1)])).toEqual([]);
    expect(visitsMissingFromSpectrum({ ...frames, center: { ra: 12, dec: 10 } }, at, 5, [pt(2, 1)])).toEqual([]);
    expect(visitsMissingFromSpectrum(frames, at, 5, [])).toEqual([]);
  });

  it("reads as one plain sentence", () => {
    expect(missingVisitsSentence([])).toBe("");
    expect(missingVisitsSentence([{ visit: 3, mjd: 61163.6 }])).toBe(
      "Visit 3, May 2026, is in the blink but not here: none of its exposures could be measured cleanly.",
    );
    expect(missingVisitsSentence([{ visit: 1, mjd: 60800 }, { visit: 3, mjd: 61164 }], 5)).toBe(
      "Visits 1, May 2025 and 3, May 2026 are in the blink but not here: none of their D5 exposures could be measured cleanly.",
    );
  });
});

describe("the featured targets' cards with their months blink", () => {
  it("WISE 0855−0714: the charts say visit 3 is in the blink but not in them", () => {
    const el = card("wise0855", read<FramesResponse>("wise0855", "frames_months_5.json"));
    expect(el.querySelector(".cd-spec .cd-cap")?.textContent).toMatch(/over 2 visits\..*Visit 3, May 2026, is in the blink but not here/);
    expect(el.querySelector(".cd-lc .cd-cap")?.textContent).toMatch(/Visit 3, May 2026, is in the blink but not here: none of its D\d exposures/);
  });

  it("says nothing when the spectrum has every blink visit, or when there is no months blink", () => {
    for (const slug of ["barnard", "epsindib", "rwaur", "rdra"]) {
      expect(card(slug, read<FramesResponse>(slug, "frames_months_5.json")).querySelector(".cd-missing"), slug).toBeNull();
    }
    expect(card("wise0855", null).querySelector(".cd-missing")).toBeNull();
    expect(card("wise0855", read<FramesResponse>("wise0855", "frames_hours_20.json")).querySelector(".cd-missing")).toBeNull();
  });
});
