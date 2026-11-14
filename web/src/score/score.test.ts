import { afterEach, describe, expect, it, vi } from "vitest";
import validationFx from "../../../fixtures/api/validation.json";
import suspectsFx from "../../../fixtures/api/suspects.json";
import publishedJson from "../../public/data/validation.json";
import type { SuspectsResponse, ValidationResponse } from "../api/types";
import { CHECKS, countStatuses, summarySentence, withAllChecks } from "./checks";
import {
  bigValue,
  blinkHref,
  candidateHref,
  csvField,
  formatCell,
  formatGenerated,
  formatValue,
  keyLabel,
  noteText,
  prettyText,
  sentence,
  suspectsCsv,
} from "./format";
import { mountScore, renderScore } from "./index";

// Fixtures are used here only, never copied into public/data.
const validation = validationFx as unknown as ValidationResponse;
const suspects = suspectsFx as unknown as SuspectsResponse;
// What scripts/validate published on main: assertions on it are derived, never pinned.
const published = publishedJson as unknown as ValidationResponse;

const RANGE = "–⁠";

function el(): HTMLElement {
  const d = document.createElement("div");
  document.body.appendChild(d);
  return d;
}

const text = (n: Element | null) => (n?.textContent ?? "").replace(/\s+/g, " ").trim();

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("checks and counts", () => {
  it("lists V1–V11 with the contract's claims", () => {
    expect(CHECKS.map((c) => c.id)).toEqual(["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "V10", "V11"]);
    expect(CHECKS.find((c) => c.id === "V10")!.claim).toBe("Galaxy distances match SDSS");
    expect(CHECKS.find((c) => c.id === "V11")!.unit).toBe("ratio");
  });
  it("counts the fixture's checks", () => {
    expect(countStatuses(validation.checks)).toEqual({ pass: 2, partial: 2, fail: 0, pending: 4 });
  });
  it("treats an unknown status as pending", () => {
    expect(countStatuses([{ status: "pass" }, { status: "weird" as never }, { status: "fail" }])).toEqual({
      pass: 1,
      partial: 0,
      fail: 1,
      pending: 1,
    });
  });
  it("adds any missing V1–V11 as pending, after the file's own checks", () => {
    const all = withAllChecks(validation.checks.slice(0, 2));
    expect(all.map((c) => c.id)).toEqual(CHECKS.map((c) => c.id));
    expect(all.slice(2).every((c) => c.status === "pending")).toBe(true);
    expect(withAllChecks(validation.checks).map((c) => c.id).slice(8)).toEqual(["V9", "V10", "V11"]);
  });
  it("writes one summary sentence from the counts", () => {
    expect(summarySentence({ pass: 8, partial: 1, fail: 0, pending: 2 })).toBe(
      "8 of 11 checks pass, 1 passes in part and 2 have not run.",
    );
    expect(summarySentence({ pass: 5, partial: 2, fail: 0, pending: 1 })).toBe(
      "5 of 8 checks pass, 2 pass in part and 1 has not run.",
    );
    expect(summarySentence({ pass: 1, partial: 0, fail: 1, pending: 0 })).toBe("1 of 2 checks pass and 1 fails.");
    expect(summarySentence({ pass: 3, partial: 0, fail: 0, pending: 0 })).toBe("All 3 checks pass.");
    expect(summarySentence({ pass: 0, partial: 0, fail: 0, pending: 11 })).toMatch(/^None of the eleven checks has run yet\./);
  });
});

describe("rendering the fixtures", () => {
  it("prints every check with its figure, measured sentence, target and status word", () => {
    const d = el();
    renderScore(d, validation, suspects);
    expect(d.querySelectorAll(".sc-check")).toHaveLength(11);
    const v2 = d.querySelector("#sc-V2")!;
    expect(text(v2.querySelector(".sc-claim"))).toBe("Motion matches Gaia");
    expect(text(v2.querySelector(".sc-status"))).toBe("Pass");
    expect(text(v2.querySelector(".sc-num"))).toBe("10.21″/yr");
    expect(text(v2.querySelector(".sc-measured"))).toBe("Barnard's Star: 10.2″/yr at 356°; Gaia says 10.39″/yr at 355.6°.");
    expect(text(v2.querySelector(".sc-target"))).toBe("Target set before the run: Within 15% and 10° of 10.39″/yr.");
    // a poor number is still printed, as the large figure
    const v1 = d.querySelector("#sc-V1")!;
    expect(text(v1.querySelector(".sc-num-v"))).toBe("4 of 5");
    expect(text(v1.querySelector(".sc-num-u"))).toBe("targets");
    expect(text(v1.querySelector(".sc-status"))).toBe("Partial");
    expect(v1.classList.contains("sc-lead")).toBe(true);
    expect(d.querySelectorAll(".sc-lead")).toHaveLength(1);
  });

  it("gives the status a shape as well as a colour", () => {
    const d = el();
    renderScore(d, validation, suspects);
    for (const id of ["V1", "V2", "V5"]) {
      const mark = d.querySelector(`#sc-${id} .sc-status svg`)!;
      expect(mark.getAttribute("aria-hidden")).toBe("true");
    }
    // tick, half ring, dashed ring: three different drawings
    const shapes = ["V2", "V1", "V5"].map((id) => d.querySelector(`#sc-${id} .sc-status svg`)!.innerHTML);
    expect(new Set(shapes).size).toBe(3);
  });

  it("shows one summary sentence with counts from the data, the release and the date", () => {
    const d = el();
    renderScore(d, validation, suspects);
    expect(text(d.querySelector(".sc-summary"))).toBe("2 of 11 checks pass, 2 pass in part and 7 have not run.");
    expect(d.querySelectorAll(".sc-summary svg")).toHaveLength(3);
    const meta = text(d.querySelector(".sc-meta"));
    expect(meta).toContain("DR1+QR3");
    expect(meta).toContain("14 Nov 2026, 09:30 UTC");
  });

  it("puts rows behind a disclosure whose label says what a click does", () => {
    const d = el();
    renderScore(d, validation, suspects);
    const det = d.querySelector<HTMLDetailsElement>("#sc-V1 details")!;
    expect(det.open).toBe(false);
    expect(text(det.querySelector(".sc-show"))).toBe("Show the 5 rows");
    expect(text(det.querySelector(".sc-hide"))).toBe("Hide the rows");
    expect(text(d.querySelector("#sc-V2 .sc-show"))).toBe("Show the row");
    const heads = [...det.querySelectorAll("thead th")];
    expect(heads.map((t) => t.getAttribute("title"))).toEqual(["slug", "visits", "detected", "offset_px"]);
    expect(heads.map((t) => text(t))).toEqual(["Target", "Visits", "Detected", "Offset (px)"]);
    expect(det.querySelectorAll("tbody tr")).toHaveLength(5);
    expect(text(det.querySelector("tbody td"))).toBe("Barnard's Star");
    expect(det.querySelector("tbody td.num")).not.toBeNull();
    // V4 is partial with no rows: no disclosure at all
    expect(d.querySelector("#sc-V4 details")).toBeNull();
  });

  it("drops the note's row-key list and falls back to plain words", () => {
    const d = el();
    renderScore(d, validation, suspects);
    // fixture V1's note is only "rows: …": the plain description shows instead
    expect(text(d.querySelector("#sc-V1 .sc-note"))).toBe(CHECKS[0].plain);
    // pending checks say what they will do, in the main column
    expect(text(d.querySelector("#sc-V6 .sc-plain"))).toBe(CHECKS[5].plain);
    expect(text(d.querySelector("#sc-V6 .sc-measured"))).toBe("Not run yet.");
  });

  it("flags synthetic data in one calm sentence", () => {
    const d = el();
    renderScore(d, validation, suspects);
    expect(text(d.querySelector(".sc-synthetic"))).toMatch(/synthetic/);
  });

  it("lists suspects with blink and candidate links", () => {
    const d = el();
    renderScore(d, validation, suspects);
    const rows = d.querySelectorAll(".sc-suspect-table tbody tr");
    expect(rows).toHaveLength(3);
    expect(text(d.querySelector(".sc-zone"))).toContain("3 suspects from the hunt zone (synthetic)");
    const first = rows[0];
    expect(first.textContent).toContain("Moved between two exposures of one session");
    expect(first.textContent).toContain("none within 600″");
    expect(rows[1].textContent).toContain("88.5″");
    const id = first.querySelector<HTMLAnchorElement>("a.sc-sid-id")!;
    expect(id.textContent).toBe("SYNTH-S1");
    expect(id.getAttribute("href")).toBe("#/c/SYNTH-S1");
    const a = first.querySelector<HTMLAnchorElement>("a.sc-blink")!;
    expect(a.textContent).toBe("Blink this");
    expect(a.getAttribute("href")).toBe("?ra=125.45238&dec=5.55977&clock=hours&fov=20&det=2#/explore");
    expect(d.querySelector(".sc-csv")!.classList.contains("btn-ghost")).toBe(true);
  });

  it("escapes HTML in the data", () => {
    const d = el();
    const bad = structuredClone(validation);
    bad.checks[0].headline = "<img src=x onerror=alert(1)>";
    bad.checks[0].rows = [{ "<b>": "<script>" }];
    renderScore(d, bad, null);
    expect(d.querySelector("img")).toBeNull();
    expect(d.querySelector("script")).toBeNull();
    expect(d.querySelector("#sc-V1 .sc-measured")!.textContent).toBe("<img src=x onerror=alert(1)>.");
  });

  it("downloads the suspects as CSV", () => {
    const d = el();
    renderScore(d, validation, suspects);
    const created: Blob[] = [];
    (URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL = (b: Blob) => {
      created.push(b);
      return "blob:x";
    };
    (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => {};
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    d.querySelector<HTMLButtonElement>("[data-csv]")!.click();
    expect(click).toHaveBeenCalledOnce();
    expect(created).toHaveLength(1);
    expect(created[0].type).toContain("text/csv");
  });
});

describe("the published validation.json", () => {
  it("renders every check it holds, with counts derived from it", () => {
    const d = el();
    renderScore(d, published, null);
    const checks = withAllChecks(published.checks);
    expect(d.querySelectorAll(".sc-check")).toHaveLength(checks.length);
    expect(text(d.querySelector(".sc-summary"))).toBe(summarySentence(countStatuses(checks)));
    // whatever headline P1 wrote is printed
    for (const c of published.checks) {
      if (c.status !== "pending" && c.headline) {
        expect(text(d.querySelector(`#sc-${c.id} .sc-measured`))).toBe(sentence(prettyText(c.headline)));
      }
    }
    expect(d.querySelector(".sc-synthetic")).toBeNull();
  });
});

describe("pending state", () => {
  it("lists all eleven checks by id and claim, pending, when both files are missing", () => {
    const d = el();
    renderScore(d, null, null);
    const blocks = [...d.querySelectorAll(".sc-check")];
    expect(blocks.map((b) => text(b.querySelector(".sc-id")))).toEqual(CHECKS.map((c) => c.id));
    expect(blocks.map((b) => text(b.querySelector(".sc-claim")))).toEqual(CHECKS.map((c) => c.claim));
    expect(blocks.every((b) => b.classList.contains("sc-pending"))).toBe(true);
    expect(d.querySelector(".sc-lead")).toBeNull();
    expect(d.querySelector(".sc-num")).toBeNull();
    expect(text(d.querySelector(".sc-summary"))).toMatch(/^None of the eleven checks has run yet\./);
    expect(d.querySelector(".sc-meta")).toBeNull();
    expect(text(d.querySelector("#sc-V6 .sc-measured"))).toBe("Cut from the one-day build.");
    expect(d.querySelector(".sc-suspect-table")).toBeNull();
    expect(text(d.querySelector(".sc-suspects"))).toContain("The list of suspects comes after the hunt-zone run.");
    expect(d.querySelector(".sc-synthetic")).toBeNull();
  });

  it("mountScore shows pending when the files are missing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<!doctype html>", { status: 404, statusText: "Not Found" })),
    );
    const d = el();
    const h = mountScore(d);
    expect(d.textContent).toContain("Loading");
    await vi.waitFor(() => expect(d.querySelectorAll(".sc-check")).toHaveLength(CHECKS.length));
    expect(d.querySelectorAll(".sc-check.sc-pending")).toHaveLength(CHECKS.length);
    h.destroy();
    expect(d.innerHTML).toBe("");
    vi.unstubAllGlobals();
  });

  it("says so when the suspect list is empty", () => {
    const d = el();
    renderScore(d, validation, { ...suspects, suspects: [] });
    expect(d.querySelector(".sc-suspects")!.textContent).toContain("No suspects here");
    expect(d.querySelector<HTMLButtonElement>("[data-csv]")!.disabled).toBe(true);
  });
});

describe("formatting", () => {
  it("formats cells", () => {
    expect(formatCell(null)).toBe("—");
    expect(formatCell(3)).toBe("3");
    expect(formatCell(0.123456)).toBe("0.1235");
    expect(formatCell(10.2149)).toBe("10.215");
    expect(formatCell(60968.284842)).toBe("60968.28");
    expect(formatCell(-1.5)).toBe("−1.5");
    expect(formatCell(true)).toBe("yes");
    expect(formatCell("barnard")).toBe("barnard");
  });
  it("formats measured values with units", () => {
    expect(formatValue(null, null)).toBe("—");
    expect(formatValue(0.9, "arcsec")).toBe("0.9″");
    expect(formatValue(4, "targets")).toBe("4 targets");
    expect(formatValue(0.926, "recall")).toBe("93% recall");
    expect(formatValue(1.0103, "ratio")).toBe("1.01×");
    expect(formatValue(216.072, "mas/yr")).toBe("216.07 mas/yr");
  });
  it("reads a count as 'N of M' when the headline gives the total", () => {
    expect(bigValue(5, "targets", "5 of 5 test objects found, in 19 of 20 visits")).toEqual({ num: "5 of 5", unit: "targets" });
    expect(bigValue(60, "asteroids", "Re-found 60 of 60 asteroids")).toEqual({ num: "60 of 60", unit: "asteroids" });
    expect(bigValue(6, "targets", "16 of 20 visits")).toEqual({ num: "6", unit: "targets" });
    expect(bigValue(10.83, "arcsec", "")).toEqual({ num: "10.83″", unit: "" });
    expect(bigValue(null, "arcsec", "")).toBeNull();
  });
  it("sets JSON text in the site's typography without changing its meaning", () => {
    expect(prettyText('moved 10.83" ± 0.36" at PA 357.8°')).toBe("moved 10.83″ ± 0.36″ at PA 357.8°");
    expect(prettyText("control 3' away")).toBe("control 3′ away");
    expect(prettyText("S/N >= 5, Pass <= 0.05")).toBe("S/N ≥ 5, Pass ≤ 0.05");
    expect(prettyText("within 15% and 10 deg; a 5 sigma source")).toBe("within 15% and 10°; a 5σ source");
    expect(prettyText("D5-D6 only; G 13-17; 1.99-2.31 um")).toBe(`D5${RANGE}D6 only; G 13${RANGE}17; 1.99${RANGE}2.31 µm`);
    expect(prettyText("diameter 1.06x published")).toBe("diameter 1.06× published");
    expect(prettyText("WISE 0855-0714")).toBe("WISE 0855−0714");
    expect(prettyText("on 2026-11-14, shift -6.7")).toBe("on 2026-11-14, shift -6.7");
  });
  it("makes sentences without mangling symbols", () => {
    expect(sentence("pilot: 63 of 68")).toBe("Pilot: 63 of 68.");
    expect(sentence("σ_NMAD ~0.02")).toBe("σ_NMAD ~0.02.");
    expect(sentence("Done.")).toBe("Done.");
  });
  it("drops the row-key list from a note", () => {
    expect(noteText("rows: slug, visits, detected, offset_px")).toBeNull();
    expect(noteText(null)).toBeNull();
    expect(noteText("Shift = last minus first. Rows: slug, shift_arcsec, pa_deg. WISE 0855: 2 of 3 visits.")).toBe(
      "Shift = last minus first. WISE 0855: 2 of 3 visits.",
    );
    expect(noteText("Zone pilot. Rows: axis, n, bias, pull_scatter (mas/yr).")).toBe("Zone pilot.");
  });
  it("labels columns with their units", () => {
    expect(keyLabel("slug")).toBe("Target");
    expect(keyLabel("median_offset_px")).toBe("Median offset (px)");
    expect(keyLabel("rate_arcsec_yr")).toBe("Rate (″/yr)");
    expect(keyLabel("pa_deg")).toBe("PA (°)");
    expect(keyLabel("sigma_nmad")).toBe("σ NMAD");
    expect(keyLabel("D_km")).toBe("D (km)");
  });
  it("prints a date-only generated stamp without a made-up time", () => {
    expect(formatGenerated("2026-11-14")).toBe("14 Nov 2026");
    expect(formatGenerated("2026-11-14T09:30:00Z")).toBe("14 Nov 2026, 09:30 UTC");
  });
  it("builds the explore and candidate links", () => {
    expect(blinkHref({ ra: 1, dec: -2.5, det: 6 })).toBe("?ra=1.00000&dec=-2.50000&clock=hours&fov=20&det=6#/explore");
    expect(candidateHref("pilot-642020070317-87802695")).toBe("#/c/pilot-642020070317-87802695");
    expect(candidateHref("a b/c")).toBe("#/c/a%20b%2Fc");
  });
});

describe("CSV", () => {
  it("escapes commas, quotes, newlines and edge spaces", () => {
    expect(csvField("plain")).toBe("plain");
    expect(csvField("a,b")).toBe('"a,b"');
    expect(csvField('say "hi"')).toBe('"say ""hi"""');
    expect(csvField("two\nlines")).toBe('"two\nlines"');
    expect(csvField(" pad")).toBe('" pad"');
    expect(csvField(null)).toBe("");
    expect(csvField(-5.5)).toBe("-5.5");
  });
  it("defuses spreadsheet formulas", () => {
    expect(csvField("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvField("@sum")).toBe("'@sum");
    expect(csvField("-cmd")).toBe("'-cmd");
    expect(csvField("-12")).toBe("-12");
  });
  it("writes a header and one line per suspect", () => {
    const csv = suspectsCsv(suspects.suspects);
    const lines = csv.trimEnd().split("\r\n");
    expect(lines).toHaveLength(4);
    expect(lines[0]).toBe(
      "id,ra,dec,mjd,date_utc,clock,det,evidence,n_dots,snr,flux_ujy,cell,nearest_known_arcsec",
    );
    expect(lines[1].startsWith("SYNTH-S1,125.45238,5.559772,60968.284842,2025-")).toBe(true);
    expect(lines[1].endsWith(",two_exposures,2,10.1,262.3,573350074917,")).toBe(true);
    expect(lines[2].endsWith(",88.5")).toBe(true);
  });
});
