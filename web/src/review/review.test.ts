// The practice round on the Review page: hints behind a disclosure, answers kept in this browser
// (and picked up again), never sent as votes. The plate and the data are stand-ins.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { KV } from "../vote/model";

const mem = new Map<string, string>();
const kv: KV = { get: (k) => mem.get(k) ?? null, set: (k, v) => void mem.set(k, v) };
const castVote = vi.fn();
const display = vi.fn();
const shown: string[] = [];

vi.mock("./data", () => ({
  candidateSet: async () => ({ candidates: [], sample: false }),
  votes: async () => ({ api: { mode: "local", myVote: () => null, countsFor: async () => new Map(), vote: castVote }, kv }),
  preload: () => {},
  suspectFor: async () => null,
}));

vi.mock("./plate", () => ({
  Plate: class {
    constructor(
      _host: HTMLElement,
      private events: { onReady?(ready: boolean): void },
    ) {}
    mount() {}
    unmount() {}
    clear() {}
    display(mode: string) {
      display(mode);
    }
    async showTarget(slug: string) {
      shown.push(slug);
      this.events.onReady?.(false);
      await Promise.resolve();
      this.events.onReady?.(true);
      return true;
    }
    async showCandidate() {
      return true;
    }
  },
}));

const { mountReview } = await import("./review");
const { PROGRESS_KEY, PRACTICE_KEY } = await import("./practice");

const flush = async () => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
};

let page: ReturnType<typeof mountReview> | null = null;
let el: HTMLElement;

async function open() {
  el = document.createElement("main");
  document.body.append(el);
  page = mountReview(el, { navigate: vi.fn(), explore: vi.fn() });
  page.show(null);
  await flush();
}
const click = (sel: string) => {
  const b = el.querySelector<HTMLButtonElement>(sel);
  if (!b) throw new Error(`no ${sel}`);
  b.click();
};
const progress = () => el.querySelector(".rq-progress")!.textContent ?? "";
const saved = () => JSON.parse(mem.get(PROGRESS_KEY) ?? "[]");

beforeEach(() => {
  mem.clear();
  shown.length = 0;
  display.mockClear();
  castVote.mockClear();
  document.body.innerHTML = "";
});
afterEach(() => {
  page?.destroy();
  page = null;
});

describe("Review: practice round", () => {
  it("keeps the hint closed until asked, and offers Difference only where it helps", async () => {
    await open();
    expect(el.querySelector(".rq-title")?.textContent).toBe("Practice round");
    expect(progress()).toContain("never count as votes");
    const toggle = el.querySelector<HTMLButtonElement>("[data-act='hint']")!;
    const body = el.querySelector<HTMLElement>("#rq-hint-body")!;
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(body.hidden).toBe(true);
    toggle.click();
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.textContent).toBe("Hide the hint");
    expect(body.hidden).toBe(false);
    expect(body.textContent).toContain("violet diamond");
    // Elpis needs no Difference; Barnard's Star does
    expect(el.querySelector("[data-act='hint-mode']")).toBeNull();
    click("[data-answer='mover']");
    click("[data-act='practice-next']");
    await flush();
    expect(display).toHaveBeenCalledWith("blink");
    expect(el.querySelector<HTMLElement>("#rq-hint-body")!.hidden).toBe(true);
    const mode = el.querySelector<HTMLButtonElement>("[data-act='hint-mode']")!;
    expect(mode.disabled).toBe(false);
    mode.click();
    expect(display).toHaveBeenLastCalledWith("difference");
    expect(mode.textContent).toBe("Back to the blink");
    mode.click();
    expect(display).toHaveBeenLastCalledWith("blink");
    expect(mode.textContent).toBe("Show Difference");
  });

  it("saves each answer in this browser, never as a vote, and picks up where it was left", async () => {
    await open();
    click("[data-answer='unsure']");
    expect(saved()).toEqual([{ slug: "elpis", answer: "unsure" }]);
    click("[data-act='practice-next']");
    await flush();
    click("[data-answer='mover']");
    expect(saved()).toEqual([
      { slug: "elpis", answer: "unsure" },
      { slug: "barnard", answer: "mover" },
    ]);
    page!.destroy();
    page = null;
    document.body.innerHTML = "";

    // Back later: the third plate, with the first two answers kept for the recap.
    await open();
    expect(shown.at(-1)).toBe("rwaur");
    expect(progress()).toMatch(/^Plate 3 of 3, picked up where you left off\./);
    click("[data-answer='changing']");
    click("[data-act='practice-next']");
    await flush();
    expect(el.querySelector(".rq-card-title")?.textContent).toBe("You got 2 of 3");
    expect(el.querySelectorAll(".rq-recap li")).toHaveLength(3);
    expect(el.querySelector(".rq-card")?.textContent).toContain("never count as votes");
    expect(mem.get(PRACTICE_KEY)).toBe("done");
    expect(castVote).not.toHaveBeenCalled();
  });

  it("starts again from the first plate on Practice again", async () => {
    mem.set(PRACTICE_KEY, "done");
    mem.set(PROGRESS_KEY, JSON.stringify([{ slug: "elpis", answer: "mover" }]));
    await open();
    // practice done and no objects to check: the empty state offers the round
    click("[data-act='practice']");
    await flush();
    expect(progress()).toMatch(/^Plate 1 of 3\./);
    expect(shown.at(-1)).toBe("elpis");
    expect(saved()).toEqual([]);
  });

  it("goes straight to the recap when all three were answered before", async () => {
    mem.set(
      PROGRESS_KEY,
      JSON.stringify([
        { slug: "elpis", answer: "mover" },
        { slug: "barnard", answer: "junk" },
        { slug: "rwaur", answer: "changing" },
      ]),
    );
    await open();
    expect(el.querySelector(".rq-card-title")?.textContent).toBe("You got 2 of 3");
    expect(mem.get(PRACTICE_KEY)).toBe("done");
  });
});
