// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import type { KV } from "../vote/model";
import { mountNotesPanel } from "./panel";
import { NICKNAME_KEY, readPosts } from "./posts";

function memKV(): KV {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v) };
}

const NOW = 1_800_000_000_000;
let el: HTMLElement;
let kv: KV;

function mount() {
  el = document.createElement("section");
  el.hidden = true;
  document.body.append(el);
  return mountNotesPanel(el, { kv, now: () => NOW });
}

const write = (text: string) => {
  const box = el.querySelector<HTMLTextAreaElement>(".nt-body")!;
  box.value = text;
  el.querySelector<HTMLFormElement>(".nt-form")!.dispatchEvent(new Event("submit", { cancelable: true }));
};

beforeEach(() => {
  document.body.innerHTML = "";
  kv = memKV();
});

describe("the notes panel on an object", () => {
  it("stays hidden until an object is named, and hides again when it is not", () => {
    const panel = mount();
    expect(el.hidden).toBe(true);
    panel.show("cand-a");
    expect(el.hidden).toBe(false);
    panel.hide();
    expect(el.hidden).toBe(true);
  });

  it("shows example comments, every one of them tagged", () => {
    const panel = mount();
    panel.show("cand-a");
    const samples = el.querySelectorAll(".nt-note.nt-sample");
    expect(samples.length).toBeGreaterThan(0);
    for (const s of samples) expect(s.querySelector(".nt-tag")!.textContent).toBe("Sample");
    expect(el.querySelector(".nt-fine")!.textContent).toMatch(/made up/i);
    expect(el.querySelector(".nt-fine")!.textContent).toMatch(/no one wrote them/i);
  });

  it("keeps a note against the object on show, and reads it back", () => {
    const panel = mount();
    panel.show("cand-a");
    write("The dot sits on a diffraction spike.");
    const kept = readPosts(kv);
    expect(kept).toHaveLength(1);
    expect(kept[0].about).toBe("cand-a");
    expect(kept[0].body).toBe("The dot sits on a diffraction spike.");
    const mine = el.querySelector(".nt-note:not(.nt-sample)")!;
    expect(mine.textContent).toContain("The dot sits on a diffraction spike.");
    expect(mine.querySelector(".nt-tag")).toBeNull();
    expect(el.querySelector(".nt-count")!.textContent).toBe("1 note of yours");
  });

  it("signs a note with the nickname from this device", () => {
    kv.set(NICKNAME_KEY, "Plate reader");
    const panel = mount();
    panel.show("cand-a");
    write("Looks clean.");
    expect(readPosts(kv)[0].name).toBe("Plate reader");
    expect(el.querySelector(".nt-note:not(.nt-sample) .nt-who")!.textContent).toBe("Plate reader");
  });

  it("escapes what someone typed instead of running it", () => {
    const panel = mount();
    panel.show("cand-a");
    write('<img src=x onerror="alert(1)">');
    expect(el.querySelector("img")).toBeNull();
    expect(el.querySelector(".nt-note:not(.nt-sample)")!.textContent).toContain("<img");
  });

  it("refuses an empty note and says why, without keeping anything", () => {
    const panel = mount();
    panel.show("cand-a");
    write("   ");
    expect(readPosts(kv)).toEqual([]);
    expect(el.querySelector(".nt-problem")!.textContent).toMatch(/Write something/);
  });

  it("removes only your own note, and only the one asked for", () => {
    const panel = mount();
    panel.show("cand-a");
    write("First.");
    write("Second.");
    expect(readPosts(kv)).toHaveLength(2);
    // Both were written in the same pinned millisecond, so take whichever row is on top.
    const row = el.querySelector(".nt-note:not(.nt-sample)")!;
    const dropped = row.querySelector(".nt-body-text")!.textContent;
    row.querySelector<HTMLButtonElement>(".nt-del")!.click();
    const left = readPosts(kv);
    expect(left).toHaveLength(1);
    expect(left[0].body).not.toBe(dropped);
    expect(el.querySelectorAll(".nt-del")).toHaveLength(1);
    expect(el.textContent).not.toContain(dropped);
  });

  it("shows another object's notes when it is shown, not this one's", () => {
    const panel = mount();
    panel.show("cand-a");
    write("Only on A.");
    panel.show("cand-b");
    expect(el.textContent).not.toContain("Only on A.");
    expect(el.querySelector(".nt-count")!.textContent).toBe("");
    panel.show("cand-a");
    expect(el.textContent).toContain("Only on A.");
  });

  it("clears a half-written note when the object changes, so it cannot land on the wrong one", () => {
    const panel = mount();
    panel.show("cand-a");
    el.querySelector<HTMLTextAreaElement>(".nt-body")!.value = "half a thought";
    panel.show("cand-b");
    expect(el.querySelector<HTMLTextAreaElement>(".nt-body")!.value).toBe("");
  });

  it("leaves nothing behind when destroyed", () => {
    const panel = mount();
    panel.show("cand-a");
    panel.destroy();
    expect(el.innerHTML).toBe("");
  });
});
