import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DotPick, View } from "../app/modules";
import { mountVoteWith } from "./index";
import type { KV } from "./model";
import { LocalStore, VOTED_EVENT, voteApi } from "./store";

function memKV(): KV {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v) };
}
const view: View = { ra: 269.45, dec: 4.69, clock: "months", fov: 5, det: 2, slug: "barnard", name: "Barnard's Star" };
const dot: DotPick = { ra: 269.451, dec: 4.6905, mjd: 60800.25, clock: "months", det: 2, epoch_id: "v2" };
const fields = { ra: dot.ra, dec: dot.dec, mjd: dot.mjd, clock: dot.clock, source: "user" as const };
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("vote panel (local store)", () => {
  let el: HTMLElement;
  let kv: KV;
  let store: LocalStore;
  beforeEach(() => {
    document.body.innerHTML = "";
    el = document.createElement("section");
    document.body.append(el);
    kv = memKV();
    store = new LocalStore(kv);
  });
  const click = (sel: string) => (el.querySelector(sel) as HTMLButtonElement).click();
  const desk = () => el.querySelector<HTMLElement>(".vt-desk")!;

  it("flags a dot, votes once, and shows counts and the forum link on the desk", async () => {
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    expect(el.querySelector(".vt-title")?.textContent).toBe("Flag what moved");
    expect(el.textContent).toContain("Nothing flagged near here yet");
    expect(desk().hidden).toBe(true);
    expect(el.querySelector<HTMLButtonElement>(".vt-csv")?.hidden).toBe(true);

    v.pick(dot);
    expect(desk().hidden).toBe(false);
    expect(el.querySelector(".vt-pick .vt-say")?.textContent).toMatch(/^Flag the dot at .+ on the D2\s+visit of/);
    expect(el.querySelector(".vt-pick .vt-mark")).not.toBeNull();
    expect(el.querySelector(".vt-pick [data-act=flag]")?.classList.contains("btn-primary")).toBe(true);
    click("[data-act=flag]");
    await flush();
    expect(store.written.candidates).toEqual([{ ...fields, created: expect.any(Date) }]);
    expect(el.querySelectorAll(".vt-item")).toHaveLength(1);
    expect(el.querySelector(".vt-src")?.textContent).toContain("flagged by you");
    expect(el.querySelector(".vt-item")?.getAttribute("aria-current")).toBe("true");

    // The ballot replaces the flag prompt on the desk: four answers, each with its meaning.
    const answers = el.querySelectorAll<HTMLButtonElement>(".vt-desk .vt-answer");
    expect([...answers].map((b) => b.dataset.answer)).toEqual(["mover", "changing", "junk", "unsure"]);
    expect(answers[0].querySelector(".vt-answer-means")?.textContent).toBe("It changes place.");
    expect(document.activeElement).toBe(answers[0]);
    click(".vt-answer[data-answer=mover]");
    await flush();
    await flush();
    expect(store.written.votes).toHaveLength(1);
    expect(el.querySelector(".vt-result .vt-say")?.textContent).toBe("You said mover.");
    expect(el.querySelector(".vt-legend .a-mover dd")?.textContent).toBe("1");
    expect(el.querySelector(".vt-result .vt-bar")?.getAttribute("aria-label")).toBe("1 vote: 1 mover");
    expect(el.querySelector<HTMLAnchorElement>(".vt-after a")?.href).toContain("backyard-worlds-planet-9/talk");
    expect(el.querySelector(".vt-mine")?.textContent).toContain("mover");
    // The row offers to change it now, not to vote again.
    expect(el.querySelector(".vt-item [data-act=vote]")?.textContent).toBe("Change");

    // A fresh mount in the same browser remembers the vote.
    const el2 = document.createElement("section");
    const v2 = mountVoteWith(el2, { store, kv });
    v2.view(view);
    await v2.settle();
    expect(el2.querySelector(".vt-mine")?.textContent).toContain("mover");
    expect(el2.querySelector(".vt-item [data-act=vote]")?.textContent).toBe("Change");
  });

  it("changes the answer and resets it: new documents, counts move by one, memory follows", async () => {
    const c = await store.flag(fields);
    await store.vote(c.id, "mover", "someone_else_1");
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    click(`.vt-item[data-id="${c.id}"] [data-act=vote]`);
    click(".vt-answer[data-answer=mover]");
    await vi.waitFor(() => expect(el.querySelector(".vt-result .vt-say")?.textContent).toBe("You said mover."));
    expect(el.querySelector(".vt-legend .a-mover dd")?.textContent).toBe("2");

    // Change answer: the ballot comes back with this browser's answer pressed.
    click(".vt-result [data-act=vote]");
    expect(el.querySelector(".vt-ballot .vt-say")?.textContent).toBe("Change your answer?");
    expect(el.querySelector(".vt-answer[data-answer=mover]")?.getAttribute("aria-pressed")).toBe("true");
    expect(el.querySelector(".vt-answer[data-answer=junk]")?.getAttribute("aria-pressed")).toBe("false");
    click(".vt-answer[data-answer=junk]");
    await vi.waitFor(() => expect(el.querySelector(".vt-result .vt-say")?.textContent).toBe("You said junk."));
    expect(el.querySelector(".vt-result .vt-bar")?.getAttribute("aria-label")).toBe("2 votes: 1 mover, 1 junk");

    // Reset answer: the vote no longer counts and the ballot is offered again.
    click(".vt-result [data-act=reset]");
    await vi.waitFor(() => expect(el.querySelector(".vt-ballot .vt-say")?.textContent).toBe("What is this dot?"));
    expect(el.querySelector(".vt-status")?.textContent).toBe("Answer reset. Your vote no longer counts.");
    expect(el.querySelector(".vt-ballot [data-act=reset]")).toBeNull();
    expect(el.querySelector(".vt-item-n")?.textContent).toBe("1 mover");
    expect(el.querySelector(".vt-item [data-act=vote]")?.textContent).toBe("Vote");
    expect(store.written.votes.map((x) => x.answer)).toEqual(["mover", "mover", "junk", "withdrawn"]);
    expect(voteApi(store, kv).myVote(c.id)).toBeNull();
  });

  it("moves its counts when a vote is changed on another page", async () => {
    const c = await store.flag(fields);
    const api = voteApi(store, kv);
    await api.castVote(c.id, "mover");
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    expect(el.querySelector(".vt-item-n")?.textContent).toContain("1 mover");
    await api.withdrawVote(c.id); // fires skyshift:voted with answer null
    await vi.waitFor(() => expect(el.querySelector(".vt-item-n")?.textContent).toBe("No votes yet"));
    expect(el.querySelector(".vt-mine")).toBeNull();
  });

  it("drops a picked dot when the page moves to another spot, as the blink does (#57)", async () => {
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    v.pick(dot);
    v.view({ ...view, det: 5 }); // same spot, another detector: the dot is still on screen
    expect(el.querySelector(".vt-pick [data-act=flag]")).not.toBeNull();
    v.view({ ...view, ra: 133.75969, dec: -7.24216, slug: "wise0855", name: "WISE 0855−0714" });
    expect(desk().hidden).toBe(true);
    expect(el.querySelector("[data-act=flag]")).toBeNull();
    await v.settle();
    expect(store.written.candidates ?? []).toEqual([]);
  });

  it("opens the ballot from a list row, and Not now closes it", async () => {
    const c = await store.flag(fields);
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    expect(el.querySelector(".vt-item-n")?.textContent).toBe("No votes yet");
    click(`.vt-item[data-id="${c.id}"] [data-act=vote]`);
    expect(el.querySelector(".vt-desk .vt-ballot")).not.toBeNull();
    expect(el.querySelector(`.vt-answer[data-id="${c.id}"]`)).not.toBeNull();
    click("[data-act=close]");
    expect(desk().hidden).toBe(true);
    expect(store.written.votes).toHaveLength(0);
  });

  it("asks to vote on an existing dot instead of a twin", async () => {
    const c = await store.flag(fields);
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    v.pick({ ...dot, ra: dot.ra + 0.0003 });
    expect(el.querySelector(".vt-twin")?.textContent).toContain("already flagged");
    click("[data-act=twin]");
    expect(el.querySelector(".vt-pick")).toBeNull();
    expect(el.querySelector(`.vt-desk .vt-ballot .vt-answer[data-id="${c.id}"]`)).not.toBeNull();
  });

  it("marks pipeline suspects with a small label", async () => {
    const pipe = { id: "pipeline-s1", ra: dot.ra, dec: dot.dec, mjd: 61000.2, clock: "hours" as const, source: "pipeline" as const, created: null };
    const v = mountVoteWith(el, { store: new LocalStore(kv, async () => [pipe]), kv });
    v.view(view);
    await v.settle();
    expect(el.querySelector(".vt-item[data-source=pipeline] .vt-tag")?.textContent).toBe("Pipeline suspect");
    expect(el.querySelector(".vt-item-when")?.textContent).toMatch(/^Exposure of .+ UTC$/);
  });

  it("Show dispatches skyshift:goto", async () => {
    await store.flag(fields);
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    const seen = vi.fn();
    window.addEventListener("skyshift:goto", (e) => seen((e as CustomEvent).detail), { once: true });
    click("[data-act=show]");
    expect(seen).toHaveBeenCalledWith({ ra: dot.ra, dec: dot.dec, clock: "months" });
  });

  it("refreshes counts when a vote is cast on another page", async () => {
    const c = await store.flag(fields);
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    await store.vote(c.id, "junk", "someone_else_1");
    window.dispatchEvent(new CustomEvent(VOTED_EVENT, { detail: { candidateId: c.id, answer: "junk" } }));
    await vi.waitFor(() => expect(el.querySelector(".vt-item-n")?.textContent).toBe("1 junk"));
  });

  it("explains errors without throwing", async () => {
    const broken = new LocalStore(kv);
    broken.flag = () => Promise.reject({ code: "permission-denied" });
    broken.near = () => Promise.reject({ code: "unavailable" });
    const v = mountVoteWith(el, { store: broken, kv });
    v.view(view);
    await v.settle();
    expect(el.querySelector(".vt-status")?.textContent).toContain("Can't reach the vote server");
    expect(el.querySelector<HTMLElement>(".vt-status")?.dataset.kind).toBe("error");
    v.pick(dot);
    click("[data-act=flag]");
    await flush();
    expect(el.querySelector(".vt-status")?.textContent).toContain("refused");
    expect(desk().hidden).toBe(false);
    expect(el.querySelector(".vt-pick")).not.toBeNull();

    v.pick({ ...dot, mjd: 1 });
    expect(el.querySelector(".vt-status")?.textContent).toContain("outside the SPHEREx mission");
    expect(desk().hidden).toBe(true);
  });

  it("labels local mode", async () => {
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    expect(el.querySelector(".vt-mode")?.textContent).toContain("Local mode");
    expect(el.querySelector<HTMLElement>(".vt-mode")?.hidden).toBe(false);
  });

  it("shares this browser's memory with the page-wide vote API", async () => {
    const c = await store.flag(fields);
    await voteApi(store, kv).castVote(c.id, "changing");
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    expect(el.querySelector(".vt-mine")?.textContent).toContain("changing");
  });
});
