import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import candidatesFx from "../../../fixtures/api/candidates.json";
import type { PageContext } from "../app/modules";
import { SECTIONS, formatWhen, mountTools, zoneText } from "./index";

const flush = () => new Promise((r) => setTimeout(r, 0));
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;
let saved: { name: string; text: string }[];
let el: HTMLElement;
let ctx: PageContext;

function setup(opts: { candidates?: boolean; frames?: (url: string) => Response | Promise<Response> } = {}) {
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/data/candidates.json")) return opts.candidates ? json(candidatesFx) : json({}, 404);
    if (url.includes("/data/validation.json")) return json({ generated: "2026-11-14", data_release: "DR1+QR3", checks: [{ id: "V1", status: "pass" }] });
    if (url.includes("/data/suspects.json")) return json({ generated: "2026-11-14T12:13:19Z", data_release: "DR1", zone: "pilot", suspects: [{}, {}] });
    if (url.includes("/api/frames") && opts.frames) return opts.frames(url);
    return json({ error: { code: "not_found", message: "nope" } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  saved = [];
  // jsdom's Blob can't be read back: keep the parts instead.
  class FakeBlob {
    constructor(readonly parts: string[]) {}
  }
  vi.stubGlobal("Blob", FakeBlob);
  // jsdom has no object URLs.
  Object.assign(URL, {
    createObjectURL: (b: unknown) => {
      saved.push({ name: "", text: (b as FakeBlob).parts.join("") });
      return "blob:x";
    },
    revokeObjectURL: () => {},
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    if (this.download) saved.push({ name: this.download, text: "" });
  });
  el = document.createElement("main");
  document.body.append(el);
  ctx = { navigate: vi.fn(), explore: vi.fn() };
  const page = mountTools(el, ctx);
  page.show(null);
  return page;
}

beforeEach(() => {
  document.body.innerHTML = "";
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Tools page", () => {
  it("renders the six sections with deep links, and reads only static files on load", async () => {
    setup({ candidates: true });
    await flush();
    await flush();
    for (const s of SECTIONS) {
      expect(el.querySelector(`#tl-${s.id}-h`)?.textContent).toBe(s.title);
      expect(el.querySelector(`.tl-toc a[href="#/tools/${s.id}"]`)).not.toBeNull();
    }
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls.every((u) => u.includes("/data/"))).toBe(true);
    expect(el.querySelectorAll(".tl-ep")).toHaveLength(5);
    expect(el.textContent).toContain(
      "This publication makes use of data products from the Spectro-Photometer for the History of the Universe",
    );
    expect(el.querySelector('a[href="https://doi.org/10.26131/IRSA678"]')).not.toBeNull();
  });

  it("explains plainly when the candidate list isn't published", async () => {
    setup({ candidates: false });
    await flush();
    await flush();
    expect(el.querySelector(".tl-dl")?.textContent).toContain("The list of objects to check isn't published yet");
    expect(el.querySelector("[data-dl]")).toBeNull();
    expect(el.querySelector(".tl-dl a[href='/data/suspects.json']")).not.toBeNull();
  });

  it("summarises the list and downloads the chosen kind", async () => {
    setup({ candidates: true });
    await flush();
    await flush();
    expect(el.querySelector(".tl-sum")?.textContent).toContain("194 objects to check from the pilot zone: 27 fast movers, 167 moving stars, 0 odd spectra");
    expect(el.querySelector<HTMLButtonElement>('[data-kind="spectral"]')!.disabled).toBe(true);
    el.querySelector<HTMLButtonElement>('[data-kind="mover"]')!.click();
    expect(el.querySelector('[data-kind="mover"]')!.getAttribute("aria-pressed")).toBe("true");
    el.querySelector<HTMLButtonElement>('[data-dl="csv"]')!.click();
    el.querySelector<HTMLButtonElement>('[data-dl="vot"]')!.click();
    await flush();
    const names = saved.filter((s) => s.name).map((s) => s.name);
    expect(names).toEqual(["skyshift-candidates-2026-11-14-fast-movers.csv", "skyshift-candidates-2026-11-14-fast-movers.vot"]);
    const csv = saved.find((s) => s.text.startsWith("id,kind,ra,dec"))!.text;
    expect(csv.trim().split("\r\n")).toHaveLength(27 + 1);
  });

  it("builds the batch table, explains bad lines and opens the blink in place", async () => {
    setup();
    const ta = el.querySelector<HTMLTextAreaElement>("#tl-batch-in")!;
    ta.value = "Somewhere, 150.1, 2.2\nBarnard's Star, 269.4462, 4.76883\nNowhere, 400, 95";
    el.querySelector<HTMLButtonElement>('[data-act="read"]')!.click();
    const rows = el.querySelectorAll(".tl-table tbody tr");
    expect(rows).toHaveLength(3);
    expect(el.querySelector(".tl-result .tl-sum")?.textContent).toContain("2 positions read. 1 line couldn't be read");
    expect(rows[2].textContent).toContain("RA must be from 0° up to 360°.");
    // Featured fields need no check; others offer one.
    expect(rows[0].querySelector("[data-check]")).not.toBeNull();
    expect(rows[1].querySelector("[data-check]")).toBeNull();
    expect(rows[1].querySelector<HTMLAnchorElement>("a[data-blink]")!.getAttribute("href")).toBe("?t=barnard#/explore");
    const ev = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    rows[0].querySelector<HTMLAnchorElement>("a[data-blink]")!.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    expect(ctx.explore).toHaveBeenCalledWith({ ra: 150.1, dec: 2.2, clock: "months", fov: 5, det: 2 });
    expect(rows[0].querySelector<HTMLAnchorElement>('a[href^="#/sky/"]')!.getAttribute("href")).toBe("#/sky/150.10000,2.20000,0.5");
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/api/"))).toBe(false);
  });

  it("checks coverage for one row at a time, on request", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const epoch = (v: number, start: number) => ({ id: `d2-v${v}`, kind: "visit", det: 2, mjd_mid: start + 7, mjd_start: start, mjd_end: start + 14 });
    setup({
      frames: async () => {
        await gate;
        return json({ clock: "months", epochs: [epoch(1, 60800), epoch(2, 60983), epoch(3, 61166)], data_release: "DR1+QR3", archive_checked: "2026-11-14" });
      },
    });
    const ta = el.querySelector<HTMLTextAreaElement>("#tl-batch-in")!;
    ta.value = "150.1 2.2\n10 20";
    el.querySelector<HTMLButtonElement>('[data-act="read"]')!.click();
    el.querySelector<HTMLButtonElement>('[data-check="1"]')!.click();
    await flush();
    expect(el.querySelector('tr[data-line="1"] .tl-cov')?.textContent).toContain("Checking");
    expect(el.querySelector<HTMLButtonElement>('[data-check="2"]')!.disabled).toBe(true);
    const call = String(fetchMock.mock.calls.find((c) => String(c[0]).includes("/api/frames"))![0]);
    expect(call).toContain("ra=150.1&dec=2.2&clock=months&fov=5&det=2");
    release();
    await vi.waitFor(() => expect(el.querySelector('tr[data-line="1"] .tl-cov')?.textContent).toContain("3 visits in D2"));
    expect(el.querySelector<HTMLButtonElement>('[data-check="2"]')!.disabled).toBe(false);
  });

  it("switches every example between curl and Python", () => {
    setup();
    const visible = () => [...el.querySelectorAll<HTMLElement>("[data-lang-block]")].filter((b) => !b.hidden).map((b) => b.dataset.langBlock);
    expect(new Set(visible())).toEqual(new Set(["curl"]));
    el.querySelector<HTMLButtonElement>('[data-lang="python"]')!.click();
    expect(new Set(visible())).toEqual(new Set(["python"]));
    expect(el.querySelector('[data-lang="python"]')!.getAttribute("aria-pressed")).toBe("true");
  });
});

describe("helpers", () => {
  it("formats times and zones", () => {
    expect(formatWhen("2026-11-14T13:12:18Z")).toBe("14 Nov 2026, 13:12 UTC");
    expect(formatWhen("2026-11-14")).toBe("14 Nov 2026");
    expect(formatWhen(null)).toBe("unknown");
    expect(zoneText(["pilot"])).toBe("pilot zone");
    expect(zoneText(["pilot", "hunt"])).toBe("pilot and hunt zones");
    expect(zoneText([])).toBe("pipeline");
  });
});
