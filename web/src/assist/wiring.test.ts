// The assistant as the Candidate page (src/review/assist.ts, candidate.ts) and the header's Details
// switch (src/prefs/details.ts) use it. Those files are not ours: this only drives their public API.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import { DETAILS_EVENT, setDetails } from "../prefs/details";
import { AssistSlot, loadAssist, votesSummary } from "../review/assist";
import { VOTED_EVENT as STORE_VOTED_EVENT } from "../vote/store";
import type { KV } from "../vote/model";
import { VOTED_EVENT, mountChecklist } from "./checklist";
import type { AssistCandidate } from "./types";
import { mountWhatNext } from "./whatnext";

const all = fixture.candidates as unknown as AssistCandidate[];
const mover = all.find((c) => c.id === "pilot-642020070317-87802695")!;
const star = all.find((c) => c.id === "pilot-pm-0000")!;
const kv = (): KV => {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v) };
};
const counts = (mover: number, junk = 0) => ({ mover, changing: 0, junk, unsure: 0 });

let host: HTMLElement;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
});
afterEach(() => {
  host.remove();
  setDetails(false);
});

describe("on the Candidate page (src/review)", () => {
  it("is found by loadAssist and mounts through AssistSlot as candidate.ts does", async () => {
    const mod = await loadAssist();
    expect(mod?.mountChecklist).toBeTypeOf("function");
    expect(mod?.mountWhatNext).toBeTypeOf("function");
    const checkEl = host.appendChild(document.createElement("div"));
    const nextEl = host.appendChild(document.createElement("div"));
    const check = new AssistSlot(checkEl);
    const next = new AssistSlot(nextEl);
    expect(check.mount((el) => mod!.mountChecklist!(el, mover, { details: false }))).toBe(true);
    expect(next.mount((el) => mod!.mountWhatNext!(el, mover, votesSummary(counts(0), null)))).toBe(true);
    expect(checkEl.querySelectorAll("li.as-step")).toHaveLength(6);
    expect(nextEl.querySelector(".wn-lede")!.textContent).toContain("No votes yet");

    // candidate.ts updateWhatNext(): update(sum) with the summary only.
    expect(next.update(votesSummary(counts(6, 1), "mover"))).toBe(true);
    expect(nextEl.querySelector(".wn-lede")!.textContent).toBe("The community agrees: mover (86% of 7 votes). Now report it to the Minor Planet Center.");
    expect(nextEl.querySelector(".wn-url")!.getAttribute("value")).toContain(`#/c/${mover.id}`);
    expect(nextEl.querySelector('[data-block="mpc"] .wn-draft')!.hasAttribute("hidden")).toBe(false);

    check.clear();
    next.clear();
    expect(host.querySelectorAll(".as, .wn")).toHaveLength(0);
  });
});

describe("page events", () => {
  it("follows the header's Details switch unless told not to", () => {
    mountChecklist(host, mover, { kv: kv() });
    const pinned = document.createElement("div");
    host.append(pinned);
    mountChecklist(pinned, mover, { kv: kv(), followDetails: false });
    expect(host.querySelector(".as-details")).toBeNull();
    setDetails(true);
    expect(host.querySelector(".as")!.classList.contains("is-details")).toBe(true);
    expect(pinned.querySelector(".as-details")).toBeNull();
    window.dispatchEvent(new CustomEvent(DETAILS_EVENT, { detail: { on: false } }));
    expect(host.querySelector(".as-details")).toBeNull();
  });

  it("starts in Details mode when the switch is on", () => {
    setDetails(true);
    mountChecklist(host, mover, { kv: kv() });
    expect(host.querySelector(".as-details")).not.toBeNull();
  });

  it("shows a vote cast elsewhere on the page on step 6, for this candidate only", () => {
    expect(VOTED_EVENT).toBe(STORE_VOTED_EVENT);
    const h = mountChecklist(host, mover, { kv: kv(), onVote: () => {} });
    const call = () => host.querySelector<HTMLElement>('li[data-step="call"]')!;
    window.dispatchEvent(new CustomEvent(VOTED_EVENT, { detail: { candidateId: star.id, answer: "junk" } }));
    window.dispatchEvent(new CustomEvent(VOTED_EVENT, { detail: { candidateId: mover.id, answer: "maybe" } }));
    expect(call().dataset.status).toBe("ask");
    window.dispatchEvent(new CustomEvent(VOTED_EVENT, { detail: { candidateId: mover.id, answer: "junk" } }));
    expect(call().dataset.status).toBe("voted");
    expect(call().querySelector(".as-find")!.textContent).toContain("You voted junk.");
    expect(call().querySelector("[data-vote]")).toBeNull();
    // Changed, then taken back (v3.16: answer null): step 6 asks again.
    window.dispatchEvent(new CustomEvent(VOTED_EVENT, { detail: { candidateId: mover.id, answer: "mover", previous: "junk" } }));
    expect(call().querySelector(".as-find")!.textContent).toContain("You voted mover.");
    window.dispatchEvent(new CustomEvent(VOTED_EVENT, { detail: { candidateId: mover.id, answer: null, previous: "mover" } }));
    expect(call().dataset.status).toBe("ask");
    expect(call().querySelector("[data-vote]")).not.toBeNull();
    window.dispatchEvent(new CustomEvent(VOTED_EVENT, { detail: { candidateId: mover.id, answer: "junk" } }));
    h.update(star);
    expect(call().dataset.status).toBe("ask");
    h.destroy();
    window.dispatchEvent(new CustomEvent(VOTED_EVENT, { detail: { candidateId: star.id, answer: "mover" } }));
    expect(host.querySelector(".as")).toBeNull();
  });

  it("What next takes new counts alone, or a new candidate with or without counts", () => {
    const h = mountWhatNext(host, mover, null, { base: "https://skyshift.web.app/", kv: kv() });
    const lede = () => host.querySelector(".wn-lede")!.textContent;
    h.update(counts(1, 5));
    expect(lede()).toContain("Most voters call this junk");
    expect(host.querySelector('[data-block="mpc"]')).not.toBeNull();
    h.update(null);
    expect(lede()).toContain("When the community agrees");
    h.update(star, counts(6, 1));
    expect(host.querySelector('[data-block="forum"]')).not.toBeNull();
    expect(lede()).toContain("Now tell the Backyard Worlds forum.");
  });
});
