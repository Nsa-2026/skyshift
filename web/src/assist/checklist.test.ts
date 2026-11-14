import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import type { KV } from "../vote/model";
import { mountChecklist } from "./checklist";
import { blinkHref } from "./checks";
import { REPLIES_KEY, REPLY_EVENT, loadReplies, saveReply } from "./replies";
import type { AssistCandidate } from "./types";
import { STATUS_TEXT } from "./words";

const all = fixture.candidates as unknown as AssistCandidate[];
const mover = all.find((c) => c.id === "pilot-642020070317-87802695")!;
const star = all.find((c) => c.id === "pilot-pm-0000")!;

function memKV(): KV & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return { map, get: (k) => map.get(k) ?? null, set: (k, v) => void map.set(k, v) };
}

let host: HTMLElement;
let kv: ReturnType<typeof memKV>;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  kv = memKV();
});
afterEach(() => {
  host.remove();
});

const li = (id: string) => host.querySelector<HTMLLIElement>(`li[data-step="${id}"]`)!;
const click = (el: Element | null) => (el as HTMLElement).click();

describe("mountChecklist", () => {
  it("lists the six steps, each with a glyph and a status word", () => {
    mountChecklist(host, mover, { kv });
    const steps = [...host.querySelectorAll<HTMLLIElement>("li.as-step")];
    expect(steps.map((s) => s.dataset.step)).toEqual(["motion", "known", "artefact", "same", "colour", "call"]);
    expect(steps.map((s) => s.dataset.status)).toEqual(["ask", "pass", "pass", "ask", "unknown", "ask"]);
    for (const s of steps) {
      const word = s.querySelector(".as-word")!.textContent!;
      expect(word).toBe(STATUS_TEXT[s.dataset.status as keyof typeof STATUS_TEXT]);
      expect(s.querySelector(".as-state svg.as-glyph")).not.toBeNull();
      expect(s.querySelector(".as-state")!.getAttribute("aria-hidden")).toBe("true");
      // Screen readers get the status word in the finding itself.
      expect(s.querySelector(".as-find .visually-hidden")!.textContent).toBe(`${word}. `);
    }
    expect(host.querySelector("h2.as-title")!.textContent).toBe("Check it yourself");
    expect(host.querySelector(".as-sum")!.textContent).toBe("2 good signs, 3 for you to check and 1 unknown.");
    expect(steps[1].querySelector("h3")!.textContent).toBe("Step 2: Is it already known?");
  });

  it("uses only real buttons, and leaves the heading out when asked", () => {
    mountChecklist(host, mover, { kv, title: "" });
    expect(host.querySelector("h2")).toBeNull();
    expect(host.querySelector("section")!.getAttribute("aria-label")).toBe("Check it yourself");
    for (const b of host.querySelectorAll("button")) expect(b.getAttribute("type")).toBe("button");
    expect(host.querySelectorAll("[onclick], div[role=button], span[role=button]")).toHaveLength(0);
  });

  it("opens and closes 'How to check this yourself' with aria-expanded", () => {
    mountChecklist(host, mover, { kv });
    const btn = li("motion").querySelector<HTMLButtonElement>(".as-how")!;
    const box = host.querySelector<HTMLElement>(`[id="${btn.getAttribute("aria-controls")}"]`)!;
    expect(btn.textContent!.trim()).toBe("How to check this yourself");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(box.hidden).toBe(true);
    click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(box.hidden).toBe(false);
    expect(box.querySelectorAll("ol > li").length).toBeGreaterThanOrEqual(2);
    expect(box.querySelector("a")!.getAttribute("href")).toBe(blinkHref(mover));
    click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(box.hidden).toBe(true);
  });

  it("links to Blink except on the catalogue and vote steps, and hides the link on request", () => {
    mountChecklist(host, mover, { kv });
    expect(li("same").querySelector(".as-howto a")).not.toBeNull();
    expect(li("known").querySelector(".as-howto a")).toBeNull();
    expect(li("call").querySelector(".as-howto a")).toBeNull();
    host.innerHTML = "";
    mountChecklist(host, mover, { kv, blinkHref: null });
    expect(host.querySelector(".as-howto a")).toBeNull();
  });

  it("saves a reply per candidate, changes the status and announces it", () => {
    mountChecklist(host, mover, { kv });
    const yes = li("motion").querySelector<HTMLButtonElement>('[data-reply="yes"]')!;
    expect(yes.getAttribute("aria-pressed")).toBe("false");
    expect(li("known").querySelector("[data-reply]")).toBeNull(); // data decides, not the person
    click(yes);
    expect(JSON.parse(kv.map.get(REPLIES_KEY)!)).toEqual({ [mover.id]: { motion: "yes" } });
    expect(li("motion").dataset.status).toBe("pass");
    expect(li("motion").querySelector('[data-reply="yes"]')!.getAttribute("aria-pressed")).toBe("true");
    expect(li("motion").querySelector(".as-note")!.textContent).toContain("saved in this browser");
    expect(host.querySelector("[data-live]")!.textContent).toBe("Saved your answer: yes.");
    expect(document.activeElement).toBe(li("motion").querySelector('[data-reply="yes"]'));
    expect(host.querySelector(".as-sum")!.textContent).toBe("3 good signs, 2 for you to check and 1 unknown.");

    click(li("same").querySelector('[data-reply="no"]'));
    expect(li("same").dataset.status).toBe("fail");
    expect(loadReplies(mover.id, kv)).toEqual({ motion: "yes", same: "no" });
    expect(loadReplies(star.id, kv)).toEqual({});
  });

  it("clears a reply when its button is pressed again", () => {
    mountChecklist(host, mover, { kv });
    click(li("motion").querySelector('[data-reply="unsure"]'));
    expect(li("motion").dataset.status).toBe("unknown");
    click(li("motion").querySelector('[data-reply="unsure"]'));
    expect(li("motion").dataset.status).toBe("ask");
    expect(host.querySelector("[data-live]")!.textContent).toBe("Cleared your answer.");
    expect(kv.map.get(REPLIES_KEY)).toBe("{}");
  });

  it("restores saved replies and follows replies made elsewhere on the page", () => {
    saveReply(mover.id, "same", "yes", kv);
    mountChecklist(host, mover, { kv });
    expect(li("same").dataset.status).toBe("pass");
    saveReply(mover.id, "motion", "no", kv); // e.g. from a second checklist on the page
    expect(li("motion").dataset.status).toBe("fail");
  });

  it("ignores stored junk", () => {
    kv.set(REPLIES_KEY, JSON.stringify({ [mover.id]: { motion: "maybe", same: "yes", bogus: "no" } }));
    expect(loadReplies(mover.id, kv)).toEqual({ same: "yes" });
    kv.set(REPLIES_KEY, "not json");
    expect(loadReplies(mover.id, kv)).toEqual({});
    mountChecklist(host, mover, { kv });
    expect(li("motion").dataset.status).toBe("ask");
  });

  it("shows field names and raw values in details mode, through update()", () => {
    const h = mountChecklist(host, mover, { kv });
    expect(host.querySelector(".as-details")).toBeNull();
    h.update(mover, { details: true });
    expect(host.querySelector("section")!.classList.contains("is-details")).toBe(true);
    expect(host.querySelector(".as-head .as-details")!.textContent).toContain(`Candidate ${mover.id}, kind mover, zone pilot, pipeline score null`);
    expect(li("known").querySelector(".as-details")!.textContent).toContain("nearest_known_arcsec=338.3");
    click(li("motion").querySelector('[data-reply="yes"]'));
    expect(li("motion").querySelector(".as-details")!.textContent).toContain("reply=yes, data status=ask");
    h.update(mover, { details: false });
    expect(host.querySelector(".as-details")).toBeNull();
  });

  it("keeps open disclosures for the same candidate and closes them for another", () => {
    const h = mountChecklist(host, mover, { kv });
    click(li("artefact").querySelector(".as-how"));
    h.update(mover);
    expect(li("artefact").querySelector(".as-how")!.getAttribute("aria-expanded")).toBe("true");
    h.update(star);
    expect(li("artefact").querySelector(".as-how")!.getAttribute("aria-expanded")).toBe("false");
    expect(li("motion").dataset.status).toBe("pass");
    expect(li("motion").querySelector("[data-reply]")).toBeNull();
  });

  it("offers a vote button that calls onVote, and shows the person's vote", () => {
    const onVote = vi.fn();
    const h = mountChecklist(host, mover, { kv, onVote });
    click(li("call").querySelector("[data-vote]"));
    expect(onVote).toHaveBeenCalledTimes(1);
    h.update(mover, { myVote: "mover" });
    expect(li("call").dataset.status).toBe("voted");
    expect(li("call").querySelector(".as-word")!.textContent).toBe("Voted");
    expect(li("call").querySelector(".as-find")!.textContent).toContain("You voted mover.");
    expect(li("call").querySelector("[data-vote]")).toBeNull();
    host.innerHTML = "";
    mountChecklist(host, mover, { kv });
    expect(host.querySelector("[data-vote]")).toBeNull();
  });

  it("escapes candidate text", () => {
    const evil = { ...mover, id: '<img src=x onerror="alert(1)">', zone: "<b>z</b>" };
    mountChecklist(host, evil, { kv, details: true });
    expect(host.querySelector("img")).toBeNull();
    expect(host.querySelector("b")).toBeNull();
    expect(host.querySelector(".as-head .as-details")!.textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it("removes itself and its listener on destroy", () => {
    const spy = vi.spyOn(window, "removeEventListener");
    const h = mountChecklist(host, mover, { kv });
    h.destroy();
    expect(host.querySelector("section")).toBeNull();
    expect(spy).toHaveBeenCalledWith(REPLY_EVENT, expect.any(Function));
    expect(() => saveReply(mover.id, "motion", "yes", kv)).not.toThrow();
    expect(host.children).toHaveLength(0);
    spy.mockRestore();
  });
});
