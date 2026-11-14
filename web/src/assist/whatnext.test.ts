import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import type { Counts, KV } from "../vote/model";
import { copyText, downloadText, safeFileName } from "./clipboard";
import { DRAFT_LINE, citation, mpcDraft, permalink } from "./reports";
import { saveReply } from "./replies";
import type { AssistCandidate } from "./types";
import { leadSentence, mountWhatNext } from "./whatnext";

const all = fixture.candidates as unknown as AssistCandidate[];
const mover = all.find((c) => c.id === "pilot-642020070317-87802695")!;
const star = all.find((c) => c.id === "pilot-pm-0000")!;
const spectral: AssistCandidate = { ...star, id: "t-spec", kind: "spectral", pm_mas_yr: null, pm_err_mas_yr: null, pa_deg: null };
const BASE = "https://skyshift.web.app/";
const today = new Date("2026-11-14T12:00:00Z");

const V = (mover: number, junk = 0, unsure = 0, changing = 0): Counts => ({ mover, changing, junk, unsure });

function memKV(): KV {
  const map = new Map<string, string>();
  return { get: (k) => map.get(k) ?? null, set: (k, v) => void map.set(k, v) };
}

let host: HTMLElement;
let kv: KV;
const fetchSpy = vi.fn();
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  kv = memKV();
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => {
  host.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  // Nothing in the assistant ever calls the network.
  expect(fetchSpy).not.toHaveBeenCalled();
});

const blocks = () => [...host.querySelectorAll<HTMLElement>(".wn-block")].map((b) => b.dataset.block);
const block = (k: string) => host.querySelector<HTMLElement>(`[data-block="${k}"]`)!;
const flush = () => new Promise((r) => setTimeout(r, 0));
const mount = (c: AssistCandidate, votes: Counts | null = null) => mountWhatNext(host, c, votes, { base: BASE, kv, today });

describe("leadSentence", () => {
  it("speaks to the votes so far", () => {
    expect(leadSentence(mover, null)).toBe("When the community agrees on it, report it to the Minor Planet Center. Until then the drafts are here to check.");
    expect(leadSentence(star, null)).toContain("tell the Backyard Worlds forum");
    expect(leadSentence(spectral, null)).toContain("share it");
    expect(leadSentence(mover, V(0))).toBe("No votes yet: a consensus needs 5 votes with 70% on one answer. Check it, vote, then come back.");
    expect(leadSentence(mover, V(1))).toBe("1 vote so far: a consensus needs 5 votes with 70% on one answer. Vote, then come back.");
    expect(leadSentence(mover, V(3, 2))).toContain("5 votes so far");
    expect(leadSentence(mover, V(6, 1))).toBe("The community agrees: mover (86% of 7 votes). Now report it to the Minor Planet Center.");
    expect(leadSentence(mover, V(1, 5))).toBe("Most voters call this junk (83% of 6 votes), so there's nothing to report. The citation and link are below.");
    expect(leadSentence(mover, V(0, 0, 5))).toBe("Voters can't tell (100% of 5 votes say not sure). Check it again, or share it with someone who can.");
    expect(leadSentence(star, V(0, 0, 0, 5))).toBe(
      "Voters see its brightness change, not motion (100% of 5 votes), so there's no motion to report. The citation and link are below.",
    );
  });
});

describe("mountWhatNext", () => {
  it("offers an MPC draft for movers, a forum post for moving stars, and a citation and link for all", () => {
    mount(mover);
    expect(blocks()).toEqual(["mpc", "cite", "link"]);
    host.innerHTML = "";
    mount(star);
    expect(blocks()).toEqual(["forum", "cite", "link"]);
    host.innerHTML = "";
    mount(spectral);
    expect(blocks()).toEqual(["cite", "link"]);
    expect(host.querySelector("h2.wn-title")!.textContent).toBe("What next");
  });

  it("keeps the MPC draft closed behind a disclosure until the community calls it a mover", () => {
    mount(mover);
    const btn = block("mpc").querySelector<HTMLButtonElement>("[data-open]")!;
    const box = host.querySelector<HTMLElement>(`[id="${btn.getAttribute("aria-controls")}"]`)!;
    expect(btn.textContent!.trim()).toBe("Show the draft");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(box.hidden).toBe(true);
    btn.click();
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(box.hidden).toBe(false);
    const pre = box.querySelector("pre.is-psv")!;
    expect(pre.textContent).toBe(mpcDraft(mover, { base: BASE }).text);
    expect(pre.textContent).toContain(DRAFT_LINE);
    expect(box.querySelector(".wn-flag")!.textContent).toBe("Draft, not submitted. Check it before sending.");
    expect(box.querySelectorAll(".wn-check li").length).toBe(mpcDraft(mover).missing.length);
    expect(block("mpc").querySelector(".wn-p")!.textContent).toContain("observatory code 288): 1 line, from the mean position.");
  });

  it("opens the drafts by itself on a mover consensus and warns off sending otherwise", () => {
    mount(mover, V(6, 1));
    expect(block("mpc").querySelector("[data-open]")).toBeNull();
    expect(block("mpc").querySelector<HTMLElement>(".wn-draft")!.hidden).toBe(false);
    host.innerHTML = "";
    mount(mover, V(1, 5));
    expect(block("mpc").querySelector(".wn-p")!.textContent).toMatch(/^Voters don't call this a mover, so don't send it\./);
    expect(block("mpc").querySelector<HTMLElement>(".wn-draft")!.hidden).toBe(true);
  });

  it("links the MPC and Backyard Worlds pages in new tabs and never sends anything", () => {
    mount(mover);
    const a = block("mpc").querySelector("a")!;
    expect(a.href).toBe("https://www.minorplanetcenter.net/iau/info/ADES.html");
    expect(a.rel).toBe("noopener noreferrer");
    host.innerHTML = "";
    mount(star);
    expect(block("forum").querySelector("a")!.href).toContain("zooniverse.org/projects/marckuchner/backyard-worlds-planet-9/talk/");
    expect(host.querySelectorAll("form")).toHaveLength(0);
  });

  it("copies with the clipboard API and says so", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    mount(mover);
    block("mpc").querySelector<HTMLButtonElement>('[data-copy="mpc"]')!.click();
    await flush();
    expect(writeText).toHaveBeenCalledWith(mpcDraft(mover, { base: BASE }).text);
    const said = block("mpc").querySelector('[data-said="mpc"]')!;
    expect(said.getAttribute("role")).toBe("status");
    expect(said.textContent).toBe("Copied the MPC draft.");
    block("link").querySelector<HTMLButtonElement>('[data-copy="link"]')!.click();
    await flush();
    expect(writeText).toHaveBeenLastCalledWith(`${BASE}#/c/${mover.id}`);
    expect(block("link").querySelector('[data-said="link"]')!.textContent).toBe("Copied the link.");
  });

  it("falls back to a hidden textarea when the clipboard API is blocked", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    let copied = "";
    const exec = vi.fn(() => {
      copied = (document.activeElement as HTMLTextAreaElement | null)?.value ?? document.querySelector("textarea")?.value ?? "";
      return true;
    });
    Object.defineProperty(document, "execCommand", { value: exec, configurable: true });
    expect(await copyText("hello")).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(copied).toBe("hello");
    expect(document.querySelector("textarea")).toBeNull();

    exec.mockReturnValue(false);
    mount(star);
    block("cite").querySelector<HTMLButtonElement>('[data-copy="cite"]')!.click();
    await flush();
    expect(block("cite").querySelector('[data-said="cite"]')!.textContent).toBe("Couldn't copy. Select the text and copy it yourself.");
    delete (document as unknown as Record<string, unknown>).execCommand;
  });

  it("downloads a .txt named after the candidate", () => {
    const create = vi.fn(() => "blob:skyshift/1");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }));
    const clicks: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push(this);
    });
    mount(star);
    block("forum").querySelector<HTMLButtonElement>('[data-dl="forum"]')!.click();
    expect(create).toHaveBeenCalledTimes(1);
    expect(clicks[0].download).toBe("skyshift-pilot-pm-0000-backyard-worlds.txt");
    expect(clicks[0].href).toBe("blob:skyshift/1");
    expect(block("forum").querySelector('[data-said="forum"]')!.textContent).toBe("Saved skyshift-pilot-pm-0000-backyard-worlds.txt.");
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("switches the citation between plain text and BibTeX", () => {
    mount(mover);
    const pre = () => block("cite").querySelector("pre")!;
    expect(pre().textContent).toBe(citation(mover, { base: BASE, today }).plain);
    expect(block("cite").querySelector('[data-cite="plain"]')!.getAttribute("aria-pressed")).toBe("true");
    block("cite").querySelector<HTMLButtonElement>('[data-cite="bibtex"]')!.click();
    expect(pre().textContent).toBe(citation(mover, { base: BASE, today }).bibtex);
    expect(block("cite").querySelector('[data-cite="bibtex"]')!.getAttribute("aria-pressed")).toBe("true");
    expect(block("cite").querySelector('[data-dl="cite"]')!.textContent).toBe("Download .bib");
    expect(document.activeElement).toBe(block("cite").querySelector('[data-cite="bibtex"]'));
  });

  it("shows the permalink in a read-only field", () => {
    mount(mover);
    const input = block("link").querySelector("input")!;
    expect(input.readOnly).toBe(true);
    expect(input.value).toBe(permalink(mover, BASE));
    expect(host.querySelector(`[id="${input.getAttribute("aria-labelledby")}"]`)).not.toBeNull();
  });

  it("follows new votes, new candidates and the person's replies", () => {
    const h = mount(star);
    const lede = () => host.querySelector(".wn-lede")!.textContent;
    expect(lede()).toContain("When the community agrees");
    h.update(star, V(6, 1));
    expect(lede()).toContain("The community agrees: mover");
    h.update(star); // same candidate, counts kept
    expect(lede()).toContain("The community agrees: mover");
    h.update(mover); // another candidate: counts not known yet
    expect(lede()).toContain("When the community agrees");
    expect(blocks()).toEqual(["mpc", "cite", "link"]);

    h.update(star, V(6, 1));
    saveReply(star.id, "same", "yes", kv);
    expect(block("forum").querySelector("pre")!.textContent).toContain("Is the moving dot equally dark at each visit? Yes.");
  });

  it("reads counts from a votes object on the candidate when none are passed", () => {
    mount({ ...mover, votes: V(5, 1) } as AssistCandidate);
    expect(host.querySelector(".wn-lede")!.textContent).toContain("The community agrees: mover (83% of 6 votes)");
  });

  it("removes itself on destroy", () => {
    const h = mount(mover);
    h.destroy();
    expect(host.children).toHaveLength(0);
  });
});

describe("clipboard helpers", () => {
  it("makes safe file names", () => {
    expect(safeFileName("pilot-pm-0000")).toBe("pilot-pm-0000");
    expect(safeFileName("a b/c:d")).toBe("a_b_c_d");
    expect(safeFileName("///")).toBe("skyshift");
    expect(safeFileName("x".repeat(200))).toHaveLength(80);
  });

  it("reports a failed download instead of throwing", () => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => { throw new Error("no blobs"); } }));
    expect(downloadText("a.txt", "x")).toBe(false);
  });
});
