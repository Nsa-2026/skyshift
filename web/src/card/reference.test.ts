// The DR1 reference spectrum on the card's chart (from PR #214, 春。 and Codex): on request, static
// first, only for the five fields with science products, gone when the target changes.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ObjectResponse, SpectrumPoint } from "../api/types";
import { targetBySlug, viewForTarget } from "../app/state";
import { mountCard } from "./index";
import { drawableReference, renderSpectrum, spectrumDomain } from "./spectrum";

const pub = join(__dirname, "../../public");
const object = (slug: string): ObjectResponse => JSON.parse(readFileSync(join(pub, `data/${slug}/object.json`), "utf8"));
const science = (slug: string) => JSON.parse(readFileSync(join(pub, `science-data/${slug}/science.json`), "utf8"));
const view = (slug: string) => viewForTarget(targetBySlug(slug)!);
const settle = async () => {
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
};
afterEach(() => vi.unstubAllGlobals());

describe("reference points on the spectrum", () => {
  const sp: SpectrumPoint[] = [{ um: 2, flux_ujy: 10, err_ujy: 1, det: 3, mjd: 60000, visit: 1, saturated: false }];

  it("draws only usable points, on the same flux axis, without touching the measured points", () => {
    const ref = [
      { um: 4.5, flux_ujy: 1000 },
      { um: 1, flux_ujy: -1 },
      { um: 9, flux_ujy: 5 },
      { um: 3, flux_ujy: Number.NaN },
    ];
    expect(drawableReference(ref)).toEqual([{ um: 4.5, flux_ujy: 1000 }]);
    const chart = renderSpectrum(sp, { width: 400, mode: "det", id: "t", reference: ref });
    expect(chart.points).toHaveLength(1);
    expect(chart.svg).not.toContain("NaN");
    expect(chart.svg).toContain('class="cd-refpts"');
    expect(chart.svg).toContain("DR1 reference: 4.50 µm");
    expect(chart.svg).toContain("Dashed line: 1 points of the DR1 all-sky reference");
    // the domain grows to hold the reference
    expect(spectrumDomain(sp).max).toBeLessThan(1000);
    const without = renderSpectrum(sp, { width: 400, mode: "det", id: "t" });
    expect(without.svg).not.toContain("cd-refpts");
  });
});

describe("the card's DR1 reference button", () => {
  it("loads the static reference on request and shows it on the chart", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (u: string) => {
        urls.push(String(u));
        return { ok: true, status: 200, json: async () => science("epsindib") };
      }),
    );
    const el = document.createElement("section");
    const card = mountCard(el);
    card.show(view("epsindib"), object("epsindib"), null);
    expect(urls).toEqual([]);
    const btn = el.querySelector<HTMLButtonElement>(".cd-refbtn")!;
    expect(btn.textContent).toBe("Load DR1 reference spectrum");
    btn.click();
    expect(el.querySelector(".cd-refbtn")!.textContent).toContain("Loading");
    await settle();
    expect(urls).toEqual(["/science-data/epsindib/science.json"]);
    expect(el.querySelector(".cd-refpts")).not.toBeNull();
    expect(el.querySelector(".cd-keys")!.textContent).toContain("DR1 reference");
    expect(el.querySelector(".cd-refbtn")!.textContent).toBe("Hide the DR1 reference");
    el.querySelector<HTMLButtonElement>(".cd-refbtn")!.click();
    expect(el.querySelector(".cd-refpts")).toBeNull();
    el.querySelector<HTMLButtonElement>(".cd-refbtn")!.click();
    expect(el.querySelector(".cd-refpts")).not.toBeNull();

    // A new target drops it at once; the next one starts from the button again.
    card.status("loading");
    card.show(view("wise0855"), object("wise0855"), null);
    expect(el.querySelector(".cd-refpts")).toBeNull();
    expect(el.querySelector(".cd-refbtn")!.textContent).toBe("Load DR1 reference spectrum");
  });

  it("says when the cube has nothing here, and when the service fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => science("rdra") })));
    const el = document.createElement("section");
    const card = mountCard(el);
    card.show(view("rdra"), object("rdra"), null);
    el.querySelector<HTMLButtonElement>(".cd-refbtn")!.click();
    await settle();
    expect(el.textContent).toContain("gives no usable reference spectrum");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 502, statusText: "", json: async () => ({ error: { code: "upstream", message: "x" } }) })),
    );
    const el2 = document.createElement("section");
    const card2 = mountCard(el2);
    card2.show(view("barnard"), object("barnard"), null);
    el2.querySelector<HTMLButtonElement>(".cd-refbtn")!.click();
    await settle();
    expect(el2.textContent).toContain("didn't answer");
    expect(el2.querySelector(".cd-refbtn")!.textContent).toBe("Try the DR1 reference again");
  });

  it("never late-draws one target's reference on the next", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_u: string, init?: { signal?: AbortSignal }) => {
        await gate;
        if (init?.signal?.aborted) throw new DOMException("aborted", "AbortError");
        return { ok: true, status: 200, json: async () => science("epsindib") };
      }),
    );
    const el = document.createElement("section");
    const card = mountCard(el);
    card.show(view("epsindib"), object("epsindib"), null);
    el.querySelector<HTMLButtonElement>(".cd-refbtn")!.click();
    card.status("loading");
    card.show(view("wise0855"), object("wise0855"), null);
    release();
    await settle();
    expect(el.querySelector(".cd-refpts")).toBeNull();
  });

  it("has no button where no science product exists", () => {
    const el = document.createElement("section");
    mountCard(el).show(view("elpis"), object("elpis"), null);
    expect(el.querySelector(".cd-spec")).not.toBeNull();
    expect(el.querySelector(".cd-refbtn")).toBeNull();
  });
});
