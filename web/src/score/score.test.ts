import { afterEach, describe, expect, it, vi } from "vitest";
import validationFx from "../../../fixtures/api/validation.json";
import suspectsFx from "../../../fixtures/api/suspects.json";
import type { SuspectsResponse, ValidationResponse } from "../api/types";
import { CHECKS, countStatuses, withAllChecks } from "./checks";
import { blinkHref, csvField, formatCell, formatValue, suspectsCsv } from "./format";
import { mountScore, renderScore } from "./index";

// Fixtures are used here only, never copied into public/data.
const validation = validationFx as unknown as ValidationResponse;
const suspects = suspectsFx as unknown as SuspectsResponse;

function el(): HTMLElement {
  const d = document.createElement("div");
  document.body.appendChild(d);
  return d;
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("status counting", () => {
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
  it("adds missing V1–V8 as pending", () => {
    const all = withAllChecks(validation.checks.slice(0, 2));
    expect(all.map((c) => c.id)).toEqual(["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8"]);
    expect(all.slice(2).every((c) => c.status === "pending")).toBe(true);
  });
});

describe("rendering the fixtures", () => {
  it("prints every check with its headline, status word and expected value", () => {
    const d = el();
    renderScore(d, validation, suspects);
    const blocks = d.querySelectorAll(".sc-check");
    expect(blocks).toHaveLength(8);
    const v2 = d.querySelector("#sc-V2")!;
    expect(v2.querySelector(".sc-headline")!.textContent).toContain("10.2\"/yr at 356°");
    expect(v2.querySelector(".sc-badge")!.textContent).toContain("Pass");
    expect(v2.querySelector(".sc-value")!.textContent).toBe("10.21″/yr");
    expect(v2.textContent).toContain("within 15% and 10° of 10.39\"/yr");
    // a poor number is still printed
    expect(d.querySelector("#sc-V1 .sc-headline")!.textContent).toBe("4 of 5");
    expect(d.querySelector("#sc-V1 .sc-badge")!.textContent).toContain("Partial");
  });

  it("shows the summary counts, data release and generated date", () => {
    const d = el();
    renderScore(d, validation, suspects);
    const counts = [...d.querySelectorAll(".sc-count")].map((c) => c.textContent!.replace(/\s+/g, " ").trim());
    expect(counts).toEqual(["2 pass", "2 partial", "0 fail", "4 pending"]);
    const meta = d.querySelector(".sc-meta")!.textContent!;
    expect(meta).toContain("DR1+QR3");
    expect(meta).toContain("14 Nov 2026, 09:30 UTC");
    expect(d.querySelectorAll(".sc-tick")).toHaveLength(8);
  });

  it("makes rows expandable with keys as columns", () => {
    const d = el();
    renderScore(d, validation, suspects);
    const det = d.querySelector<HTMLDetailsElement>("#sc-V1 details")!;
    expect(det.open).toBe(false);
    expect(det.querySelector("summary")!.textContent).toBe("Show 5 rows");
    const heads = [...det.querySelectorAll("thead th")].map((t) => t.getAttribute("title"));
    expect(heads).toEqual(["slug", "visits", "detected", "offset_px"]);
    expect(det.querySelectorAll("tbody tr")).toHaveLength(5);
    expect(det.querySelector("tbody td.num")).not.toBeNull();
    // V4 is partial with no rows: says so plainly
    expect(d.querySelector("#sc-V4 .sc-norows")).not.toBeNull();
  });

  it("flags synthetic data", () => {
    const d = el();
    renderScore(d, validation, suspects);
    expect(d.querySelector(".sc-synthetic")).not.toBeNull();
  });

  it("lists suspects with blink links", () => {
    const d = el();
    renderScore(d, validation, suspects);
    const rows = d.querySelectorAll(".sc-suspect-table tbody tr");
    expect(rows).toHaveLength(3);
    expect(d.querySelector(".sc-suspects .sc-big")!.textContent).toBe("3");
    expect(d.querySelector(".sc-zone")!.textContent).toContain("hunt zone (synthetic)");
    const first = rows[0];
    expect(first.textContent).toContain("SYNTH-S1");
    expect(first.textContent).toContain("Moved between two exposures of one session");
    expect(first.textContent).toContain("none found");
    expect(rows[1].textContent).toContain("88.5″");
    const a = first.querySelector<HTMLAnchorElement>("a.sc-blink")!;
    expect(a.textContent).toBe("Blink this");
    expect(a.getAttribute("href")).toBe("?ra=125.45238&dec=5.55977&clock=hours&fov=20&det=2#/");
  });

  it("escapes HTML in the data", () => {
    const d = el();
    const bad = structuredClone(validation);
    bad.checks[0].headline = "<img src=x onerror=alert(1)>";
    bad.checks[0].rows = [{ "<b>": "<script>" }];
    renderScore(d, bad, null);
    expect(d.querySelector("img")).toBeNull();
    expect(d.querySelector("script")).toBeNull();
    expect(d.querySelector("#sc-V1 .sc-headline")!.textContent).toBe("<img src=x onerror=alert(1)>");
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

describe("pending state", () => {
  it("lists the eight checks by name and claim, all pending, when both files are missing", () => {
    const d = el();
    renderScore(d, null, null);
    const blocks = [...d.querySelectorAll(".sc-check")];
    expect(blocks.map((b) => b.querySelector(".sc-id")!.textContent)).toEqual(CHECKS.map((c) => c.id));
    expect(blocks.map((b) => b.querySelector(".sc-claim")!.textContent)).toEqual(CHECKS.map((c) => c.claim));
    expect(blocks.every((b) => b.classList.contains("sc-pending"))).toBe(true);
    expect(d.querySelector(".sc-count.sc-pending b")!.textContent).toBe("8");
    expect(d.querySelector(".sc-meta")!.textContent).toContain("not published yet");
    expect(d.querySelector(".sc-pending-note")).not.toBeNull();
    expect(d.querySelector(".sc-suspect-table")).toBeNull();
    expect(d.querySelector(".sc-suspects")!.textContent).toContain("published after the hunt-zone run");
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
    await vi.waitFor(() => expect(d.querySelectorAll(".sc-check")).toHaveLength(8));
    expect(d.querySelectorAll(".sc-check.sc-pending")).toHaveLength(8);
    h.destroy();
    expect(d.innerHTML).toBe("");
    vi.unstubAllGlobals();
  });

  it("says so when the suspect list is empty", () => {
    const d = el();
    renderScore(d, validation, { ...suspects, suspects: [] });
    expect(d.querySelector(".sc-suspects")!.textContent).toContain("No suspects in this zone");
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
  });
  it("builds the blink link", () => {
    expect(blinkHref({ ra: 1, dec: -2.5, det: 6 })).toBe("?ra=1.00000&dec=-2.50000&clock=hours&fov=20&det=6#/");
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
