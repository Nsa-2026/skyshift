import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DotPick, View } from "../app/modules";
import { mountVoteWith } from "./index";
import type { KV } from "./model";
import { LocalStore } from "./store";

function memKV(): KV {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v) };
}
const view: View = { ra: 269.45, dec: 4.69, clock: "months", fov: 5, det: 2, slug: "barnard", name: "Barnard's Star" };
const dot: DotPick = { ra: 269.451, dec: 4.6905, mjd: 60800.25, clock: "months", det: 2, epoch_id: "v2" };
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

  it("flags a dot, votes once, and shows counts and the forum link", async () => {
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    expect(el.textContent).toContain("Nothing flagged in this field yet");

    v.pick(dot);
    expect(el.querySelector(".vt-pick-q")?.textContent).toBe("Flag this dot?");
    click("[data-act=flag]");
    await flush();
    expect(store.written.candidates).toEqual([{ ra: dot.ra, dec: dot.dec, mjd: dot.mjd, clock: "months", source: "user" }]);
    expect(el.querySelectorAll(".vt-item")).toHaveLength(1);
    expect(el.querySelector(".vt-src")?.textContent).toBe("Flagged by you");

    const answers = el.querySelectorAll<HTMLButtonElement>(".vt-answer");
    expect([...answers].map((b) => b.dataset.answer)).toEqual(["mover", "changing", "junk", "unsure"]);
    click(".vt-answer[data-answer=mover]");
    await flush();
    await flush();
    expect(store.written.votes).toHaveLength(1);
    expect(el.querySelector(".a-mover dd")?.textContent).toBe("1");
    expect(el.querySelector(".vt-mine")?.textContent).toContain("Mover");
    expect(el.querySelector<HTMLAnchorElement>(".vt-after a")?.href).toContain("backyard-worlds-planet-9/talk");
    expect(el.querySelector("[data-act=vote]")).toBeNull();

    // A fresh mount in the same browser remembers the vote.
    const el2 = document.createElement("section");
    const v2 = mountVoteWith(el2, { store, kv });
    v2.view(view);
    await v2.settle();
    expect(el2.querySelector(".vt-mine")?.textContent).toContain("Mover");
  });

  it("asks to vote on an existing dot instead of a twin", async () => {
    const c = await store.flag({ ...dot, source: "user" });
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    v.pick({ ...dot, ra: dot.ra + 0.0003 });
    expect(el.querySelector(".vt-twin")?.textContent).toContain("already flagged");
    click("[data-act=twin]");
    expect(el.querySelector(`.vt-item[data-id="${c.id}"] .vt-ballot`)).not.toBeNull();
  });

  it("Show dispatches skyshift:goto", async () => {
    await store.flag({ ...dot, source: "user" });
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    const seen = vi.fn();
    window.addEventListener("skyshift:goto", (e) => seen((e as CustomEvent).detail), { once: true });
    click("[data-act=show]");
    expect(seen).toHaveBeenCalledWith({ ra: dot.ra, dec: dot.dec, clock: "months" });
  });

  it("explains errors without throwing", async () => {
    const broken = new LocalStore(kv);
    broken.flag = () => Promise.reject({ code: "permission-denied" });
    broken.near = () => Promise.reject({ code: "unavailable" });
    const v = mountVoteWith(el, { store: broken, kv });
    v.view(view);
    await v.settle();
    expect(el.querySelector(".vt-status")?.textContent).toContain("Can't reach the vote server");
    v.pick(dot);
    click("[data-act=flag]");
    await flush();
    expect(el.querySelector(".vt-status")?.textContent).toContain("refused");
    expect(el.querySelector(".vt-pick")?.hasAttribute("hidden")).toBe(false);

    v.pick({ ...dot, mjd: 1 });
    expect(el.querySelector(".vt-status")?.textContent).toContain("outside the SPHEREx mission");
    expect(el.querySelector(".vt-pick")?.hasAttribute("hidden")).toBe(true);
  });

  it("labels local mode", async () => {
    const v = mountVoteWith(el, { store, kv });
    v.view(view);
    await v.settle();
    expect(el.querySelector(".vt-mode")?.textContent).toContain("Local mode");
  });
});
