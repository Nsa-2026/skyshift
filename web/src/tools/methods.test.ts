// Data and methods: the facts match CLAUDE.md's domain facts and the published checks, and the
// validation line is computed from validation.json, never written by hand.
import { describe, expect, it } from "vitest";
import validation from "../../public/data/validation.json";
import type { ValidationResponse } from "../api/types";
import { CHECKS } from "../score/checks";
import { MASKED_FLAGS, METHODS, RECORDED_FLAGS, methodsHtml, validationHtml } from "./methods";

const all = METHODS.flatMap((g) => g.rows.map((r) => r.text)).join("\n");
const row = (term: string) => METHODS.flatMap((g) => g.rows).find((r) => r.term === term)!;
const textOf = (html: string) => {
  const d = document.createElement("div");
  d.innerHTML = html;
  return d.textContent!.replace(/\s+/g, " ").trim();
};

describe("Data and methods", () => {
  it("states the domain facts as CLAUDE.md has them", () => {
    expect(row("Visit").text).toMatch(/more than 60 days starts a new visit/);
    expect(row("Visit").text).toMatch(/longer than 75 days .* at most 30 days/);
    expect(row("Session").text).toMatch(/less than 10 minutes apart: 1–5 exposures, 2\.1 minutes apart/);
    expect(row("One photo").text).toMatch(/113 s .* 2040 × 2040 pixels of 6\.15″, in MJy\/sr/);
    expect(row("One photo").text).toContain("FWHM 7.53″");
    expect(row("Detectors").text).toContain("D1 0.75–1.1, D2 1.1–1.6, D3 1.6–2.4, D4 2.4–3.8, D5 3.8–4.4, and D6 4.4–5.0 µm, 102 channels");
    expect(row("Detectors").text).toMatch(/never from the file name/);
    expect(row("Time").text).toMatch(/MJD-AVG .* UTC/);
    expect(row("Time").text).toMatch(/creation date is never used/);
    expect(row("Brightness").text).toMatch(/radius 2 pixels holds 96\.8% .* 1\.033\. 1 MJy\/sr is 889 µJy in one 6\.15″ pixel/);
    expect(row("Detection").text).toMatch(/zodiacal-light model .* not subtracted/);
    expect(row("Stars").text).toMatch(/epoch 2016\.0/);
    expect(row("Asteroids and comets").text).toMatch(/Minor Planet Center orbits, propagated with kete/);
    expect(row("Position errors").text).toMatch(/1\.4–1\.8× too small against Gaia .* 0\.43″/);
    for (const [band, mag] of [["D1", "J_AB 10.5"], ["D2", "J_AB 10.8"], ["D3", "K_AB 11.0"], ["D4", "W1_AB 11.1"], ["D5", "W2_AB 9.6"], ["D6", "W2_AB 9.0"]])
      expect(row("Saturation").text).toContain(`${mag} in ${band}`);
  });

  it("masks the 13 flags of contracts/flags.md and only records the cautions", () => {
    expect(MASKED_FLAGS).toHaveLength(13);
    expect(RECORDED_FLAGS).toHaveLength(7);
    expect(MASKED_FLAGS.filter((f) => (RECORDED_FLAGS as readonly string[]).includes(f))).toEqual([]);
    for (const f of [...MASKED_FLAGS, ...RECORDED_FLAGS]) expect(row("Masked pixels").text).toContain(f);
    expect(row("Masked pixels").text).toMatch(/SOURCE, set on most pixels, is never masked/);
  });

  it("names only checks that exist, and keeps the honest words", () => {
    const ids = new Set(CHECKS.map((c) => c.id));
    for (const r of METHODS.flatMap((g) => g.rows)) for (const id of r.checks ?? []) expect(ids.has(id)).toBe(true);
    expect(all).not.toMatch(/\bcandidates?\b/i);
    expect(all).toMatch(/list of objects to check\. Most turn out to be detector artefacts/);
    expect(all).not.toMatch(/SkyBoT|SIMBAD/);
  });

  it("sums up the published checks from validation.json, pending ones included", () => {
    const text = textOf(validationHtml(validation as ValidationResponse));
    expect(text).toContain("9 of 11 checks pass and 2 have not run.");
    expect(text).toContain("Not run: V6 (fast stars) and V7 (brown dwarfs), cut for the one-day build.");
    expect(validationHtml(validation as ValidationResponse)).toContain('href="#/results/sure"');
  });

  it("gives each pending check its own reason when they differ, and counts failures", () => {
    const v = {
      generated: "2026-11-14",
      data_release: "DR1",
      checks: [
        { id: "V1", claim: "Five test objects recovered", status: "pass", headline: "5 of 5", value: 5, unit: "targets", expected: "", note: null, rows: [] },
        { id: "V2", claim: "Motion matches Gaia", status: "fail", headline: "off", value: null, unit: null, expected: "", note: null, rows: [] },
        { id: "V3", claim: "Position error floor", status: "pending", headline: "Not run yet", value: null, unit: null, expected: "", note: null, rows: [] },
      ],
    } as unknown as ValidationResponse;
    const text = textOf(validationHtml(v));
    expect(text).toMatch(/^1 of 11 checks pass, 1 fails and 9 have not run\./);
    expect(text).toContain("V3 (position error floor: not run yet)");
    expect(text).toContain("V7 (brown dwarfs: cut from the one-day build)");
  });

  it("says plainly when the checks aren't published", () => {
    expect(textOf(validationHtml(null))).toMatch(/^The checks haven't been published yet\./);
  });

  it("renders every group as a definition list with a loading line for the checks", () => {
    const d = document.createElement("div");
    d.innerHTML = methodsHtml();
    expect([...d.querySelectorAll("h3")].map((h) => h.textContent)).toEqual([...METHODS.map((g) => g.title), "The checks, in one line"]);
    expect(d.querySelectorAll("dl.tl-fields > div")).toHaveLength(METHODS.flatMap((g) => g.rows).length);
    expect(d.querySelector(".tl-validation")?.textContent).toContain("Loading the checks");
    // each check named under a step links to that check under "How sure we are"
    const links = [...d.querySelectorAll<HTMLAnchorElement>(".tl-tested a")];
    expect(links.length).toBeGreaterThan(5);
    for (const a of links) expect(a.getAttribute("href")).toBe(`#/results/sure/${a.textContent}`);
  });

  it("says what the viewer's false-alarm colours mean, and what they don't", () => {
    const t = row("Signals in the viewer").text;
    expect(t).toMatch(/Under 2% is green, 2–10% amber and over 10% red/);
    expect(t).toMatch(/not the probability that an object is real/);
  });
});
